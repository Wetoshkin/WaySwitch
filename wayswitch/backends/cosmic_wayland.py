"""Минимальный клиент libwayland. Весь обмен с сокетом и передача fd — в libwayland."""

import ctypes as C
import errno
import select
import time

from wayswitch.backends.base import BackendError


class Argument(C.Union):
    _fields_ = [("u", C.c_uint32), ("h", C.c_int32), ("s", C.c_char_p), ("o", C.c_void_p)]


class Interface(C.Structure):
    pass


class Message(C.Structure):
    _fields_ = [("name", C.c_char_p), ("signature", C.c_char_p),
                ("types", C.POINTER(C.POINTER(Interface)))]


Interface._fields_ = [("name", C.c_char_p), ("version", C.c_int),
                      ("method_count", C.c_int), ("methods", C.POINTER(Message)),
                      ("event_count", C.c_int), ("events", C.POINTER(Message))]
Dispatcher = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_uint32,
                         C.POINTER(Message), C.POINTER(Argument))
MANAGER = b"zcosmic_keyboard_layout_manager_v1"


def _library():
    lib = C.CDLL("libwayland-client.so.0", use_errno=True)
    ptr = C.c_void_p
    signatures = {
        "wl_display_connect": (ptr, [C.c_char_p]),
        "wl_display_disconnect": (None, [ptr]),
        "wl_display_get_fd": (C.c_int, [ptr]),
        "wl_display_prepare_read": (C.c_int, [ptr]),
        "wl_display_read_events": (C.c_int, [ptr]),
        "wl_display_cancel_read": (None, [ptr]),
        "wl_display_dispatch_pending": (C.c_int, [ptr]),
        "wl_display_flush": (C.c_int, [ptr]),
        "wl_proxy_marshal_array_flags": (ptr, [ptr, C.c_uint32, C.POINTER(Interface),
                                               C.c_uint32, C.c_uint32, C.POINTER(Argument)]),
        "wl_proxy_add_dispatcher": (C.c_int, [ptr, Dispatcher, ptr, ptr]),
        "wl_proxy_destroy": (None, [ptr]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
    return lib


class CosmicTransport:
    def __init__(self, backend):
        self.backend = backend
        self.lib = _library()
        self.proxies = []
        self.seats = {}
        self.manager = self.keyboard = self.layout = self.keyboard_seat = None
        self.manager_name = None
        self.dispatcher = Dispatcher(self._dispatch)
        self.registry_type, self.seat_type, self.keyboard_type = [
            Interface.in_dll(self.lib, f"wl_{name}_interface")
            for name in ("registry", "seat", "keyboard")]
        # ABI zcosmic_keyboard_layout_{manager_,}v1, cosmic-protocols
        # c0cff4db/unstable/cosmic-keyboard-layout-unstable-v1.xml.
        scalar = (C.POINTER(Interface) * 1)()
        self.layout_type = Interface(
            b"zcosmic_keyboard_layout_v1", 1, 2,
            (Message * 2)(Message(b"set_group", b"u", scalar), Message(b"destroy", b"", None)),
            1, (Message * 1)(Message(b"group", b"u", scalar)))
        types = (C.POINTER(Interface) * 2)(C.pointer(self.layout_type),
                                           C.pointer(self.keyboard_type))
        self.manager_type = Interface(
            MANAGER, 1, 2, (Message * 2)(Message(b"get_keyboard_layout", b"no", types),
                                         Message(b"destroy", b"", None)), 0, None)
        self.display = self.lib.wl_display_connect(None)
        if not self.display:
            raise BackendError("нет подключения к WAYLAND_DISPLAY")
        try:
            self.fd = self.lib.wl_display_get_fd(self.display)
            self.registry = self._create(self.display, 1, self.registry_type, Argument())
        except Exception:
            self.close()
            raise

    def _create(self, proxy, opcode, interface, *args):
        child = self.lib.wl_proxy_marshal_array_flags(
            proxy, opcode, C.byref(interface), 1, 0, (Argument * len(args))(*args))
        if not child:
            raise BackendError("не удалось создать объект Wayland")
        self.proxies.append(child)
        if self.lib.wl_proxy_add_dispatcher(child, self.dispatcher, None, None) != 0:
            raise BackendError("не удалось подписаться на объект Wayland")
        return child

    def _bind(self, name, interface):
        return self._create(self.registry, 0, interface, Argument(u=name),
                            Argument(s=interface.name), Argument(u=1), Argument())

    def _dispatch(self, _impl, proxy, opcode, _msg, args):
        try:
            if proxy == self.keyboard and opcode == 0:
                # Получивший fd клиент обязан закрыть его даже при ошибке протокола.
                self.backend.keyboard_map(args[0].u, args[1].h, args[2].u)
            elif not self.backend.error:
                if proxy == self.registry:
                    self._registry_event(opcode, args)
                elif proxy in self.seats and opcode == 0:
                    self._seat_caps(proxy, args[0].u)
                elif proxy == self.layout and opcode == 0:
                    self.backend.group(args[0].u)
        except Exception as exc:  # исключение нельзя пропускать через C callback
            self.backend.invalidate(str(exc))
        return 0

    def _registry_event(self, opcode, args):
        name = args[0].u
        if opcode == 0:
            interface = args[1].s
            if interface == MANAGER:
                if self.manager:
                    raise BackendError("несколько менеджеров раскладки COSMIC")
                self.manager = self._bind(name, self.manager_type)
                self.manager_name = name
                self._subscribe()
            elif interface == b"wl_seat":
                seat = self._bind(name, self.seat_type)
                self.seats[seat] = name
        elif name == self.manager_name or name in self.seats.values():
            raise BackendError("изменился состав Wayland seat/менеджер COSMIC")

    def _seat_caps(self, seat, caps):
        if caps & 2:
            if self.keyboard_seat is None:
                self.keyboard_seat = seat
                self.keyboard = self._create(seat, 1, self.keyboard_type, Argument())
                self._subscribe()
            elif self.keyboard_seat != seat:
                raise BackendError("несколько seat с клавиатурой: выбор неоднозначен")
        elif self.keyboard_seat == seat:
            raise BackendError("seat потерял клавиатуру")

    def _subscribe(self):
        if self.manager and self.keyboard and not self.layout:
            self.layout = self._create(self.manager, 0, self.layout_type,
                                       Argument(), Argument(o=self.keyboard))

    def _flush(self):
        if self.lib.wl_display_flush(self.display) >= 0:
            return True
        if C.get_errno() not in (errno.EAGAIN, errno.EWOULDBLOCK):
            raise BackendError("не удалось отправить запрос Wayland")
        return False

    def set_group(self, index):
        self.lib.wl_proxy_marshal_array_flags(self.layout, 0, None, 1, 0,
                                              (Argument * 1)(Argument(u=index)))
        self._flush()

    def pump(self, timeout):
        # У этого display один поток, одна очередь и ровно один читатель.
        deadline = time.monotonic() + timeout
        if self.lib.wl_display_prepare_read(self.display) != 0:
            self._dispatch_pending()
            return
        prepared = True
        try:
            writable = [] if self._flush() else [self.fd]
            readable, _, _ = select.select([self.fd], writable, [],
                                           max(0, deadline - time.monotonic()))
            if readable:
                prepared = False
                if self.lib.wl_display_read_events(self.display) < 0:
                    raise BackendError("композитор закрыл соединение Wayland")
                self._dispatch_pending()
        finally:
            if prepared:
                self.lib.wl_display_cancel_read(self.display)
        self._flush()

    def _dispatch_pending(self):
        if self.lib.wl_display_dispatch_pending(self.display) < 0:
            raise BackendError("ошибка обработки событий Wayland")

    def close(self):
        if self.display:
            for proxy in reversed(self.proxies):
                self.lib.wl_proxy_destroy(proxy)
            self.lib.wl_display_disconnect(self.display)
            self.proxies.clear()
            self.display = None
            self.dispatcher = None
