// Расширение WaySwitch: отдаёт демону индекс текущей раскладки, переключает её
// синхронно, печатает keysym-ы через виртуальное устройство Clutter и отдаёт
// выделенный текст для «Исправить выделенное». Keysym печатается, только
// если он есть в текущей раскладке (mutter ищет keycode в активной группе),
// поэтому демон сначала переключает раскладку и проверяет, что каждый символ
// в ней есть.
//
// Печатать, стирать и читать выделение может только сам демон WaySwitch
// (владелец имени ru.siberia.WaySwitch на сессионной шине): иначе любое
// приложение с доступом к шине, в том числе из песочницы Flatpak, могло бы
// печатать в чужие окна. Текст, который печатается, в журнал не пишется.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Keyboard from 'resource:///org/gnome/shell/ui/status/keyboard.js';

const BUS_NAME = 'ru.siberia.WaySwitch.Shell';
const OBJECT_PATH = '/ru/siberia/WaySwitch/Shell';
const DAEMON_NAME = 'ru.siberia.WaySwitch';
// Ограничения на один вызов: исправление слова — десятки символов, выделенный
// фрагмент — до нескольких тысяч.
const MAX_TEXT = 4000;
const MAX_BACKSPACE = 512;
const IFACE_XML = `
<node>
  <interface name="ru.siberia.WaySwitch.Shell">
    <method name="GetLayout">
      <arg type="u" name="index" direction="out"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="SetLayout"><arg type="u" name="index" direction="in"/></method>
    <method name="TypeText"><arg type="s" name="text" direction="in"/></method>
    <method name="Backspace"><arg type="u" name="count" direction="in"/></method>
    <method name="GetSelection"><arg type="s" name="text" direction="out"/></method>
    <method name="GetVersion"><arg type="u" name="version" direction="out"/></method>
    <signal name="LayoutChanged"><arg type="u" name="index"/></signal>
  </interface>
</node>`;
// Версия протокола: демон сверяет её и просит перелогиниться, если Shell
// держит старую копию расширения (на Wayland оно перезагружается только так).
const PROTOCOL_VERSION = 2;

// GNOME 51 убрал Clutter.get_default_backend() — берём бэкенд у контекста
// сцены (get_backend() есть с GNOME 48), а старый путь оставляем для 46–47.
function defaultBackend() {
    const ctx = global.stage?.context;
    if (ctx && typeof ctx.get_backend === 'function')
        return ctx.get_backend();
    return Clutter.get_default_backend();
}

export default class WaySwitchExtension extends Extension {
    enable() {
        this._ism = Keyboard.getInputSourceManager();
        const seat = defaultBackend().get_default_seat();
        this._vdev = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._daemon = null;
        this._watchId = Gio.DBus.session.watch_name(DAEMON_NAME,
            Gio.BusNameWatcherFlags.NONE,
            (_conn, _name, owner) => {
                this._daemon = owner;
            },
            () => {
                this._daemon = null;
            });
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.DBus.session.own_name(BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
        this._changedId = this._ism.connect('current-source-changed', () => {
            const src = this._ism.currentSource;
            if (src)
                this._dbus.emit_signal('LayoutChanged', new GLib.Variant('(u)', [src.index]));
        });
    }

    disable() {
        if (this._changedId) {
            this._ism.disconnect(this._changedId);
            this._changedId = null;
        }
        if (this._nameId) {
            Gio.DBus.session.unown_name(this._nameId);
            this._nameId = null;
        }
        if (this._watchId) {
            Gio.DBus.session.unwatch_name(this._watchId);
            this._watchId = null;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._vdev = null;
        this._ism = null;
        this._daemon = null;
    }

    // --- D-Bus методы (имена совпадают с XML) ---------------------------------
    // Методы с суффиксом Async получают invocation: по нему видно, кто зовёт.

    _allowed(invocation) {
        // watch_name отвечает асинхронно: демон мог занять имя и сразу
        // позвать нас, а _daemon ещё не заполнен — спрашиваем шину сами.
        // Пока демон не запущен, имя может занять любой процесс без песочницы,
        // но такому процессу и так доступен uinput — нового он не получает.
        if (!this._daemon) {
            try {
                const reply = Gio.DBus.session.call_sync('org.freedesktop.DBus',
                    '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner',
                    new GLib.Variant('(s)', [DAEMON_NAME]), null,
                    Gio.DBusCallFlags.NONE, 500, null);
                this._daemon = reply.deepUnpack()[0];
            } catch (e) {
                this._daemon = null;
            }
        }
        if (this._daemon && invocation.get_sender() === this._daemon)
            return true;
        invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied',
            'только демон WaySwitch может печатать через расширение');
        return false;
    }

    GetLayout() {
        const src = this._ism.currentSource;
        // 0xFFFFFFFF — сигнальное значение «раскладка неизвестна»: индекс 0
        // был бы неотличим от настоящего первого источника ввода.
        return src ? [src.index, src.id] : [0xFFFFFFFF, ''];
    }

    GetVersion() {
        return PROTOCOL_VERSION;
    }

    SetLayoutAsync([index], invocation) {
        if (!this._allowed(invocation))
            return;
        const src = this._ism.inputSources[index];
        if (!src) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.InvalidArgs',
                `нет источника ввода с индексом ${index}`);
            return;
        }
        src.activate(true);
        invocation.return_value(null);
    }

    TypeTextAsync([text], invocation) {
        if (!this._allowed(invocation))
            return;
        if (text.length > MAX_TEXT) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.LimitsExceeded',
                `не больше ${MAX_TEXT} символов за раз`);
            return;
        }
        for (const ch of text) {
            const keyval = Clutter.unicode_to_keysym(ch.codePointAt(0));
            this._tap(keyval);
        }
        invocation.return_value(null);
    }

    BackspaceAsync([count], invocation) {
        if (!this._allowed(invocation))
            return;
        const n = Math.min(count, MAX_BACKSPACE);
        for (let i = 0; i < n; i++)
            this._tap(Clutter.KEY_BackSpace);
        invocation.return_value(null);
    }

    GetSelectionAsync(_params, invocation) {
        if (!this._allowed(invocation))
            return;
        // PRIMARY — то, что сейчас выделено (без Ctrl+C), во всех тулкитах
        // на Wayland. Буфер обмена человека не трогаем.
        St.Clipboard.get_default().get_text(St.ClipboardType.PRIMARY, (_clip, text) => {
            invocation.return_value(new GLib.Variant('(s)', [text ?? '']));
        });
    }

    _tap(keyval) {
        // Время для виртуального устройства — микросекунды монотонных часов.
        // press и release обязаны иметь РАЗНОЕ время: с одинаковой меткой
        // последнее нажатие в серии (чаще всего это пробел в конце
        // исправленного слова) до приложения не доходило. GNOME Shell в своей
        // экранной клавиатуре тоже разносит их по времени, а не шлёт парой.
        const now = GLib.get_monotonic_time();
        this._lastTime = Math.max(now, (this._lastTime || 0) + 1);
        const press = this._lastTime;
        this._lastTime += 1;
        this._vdev.notify_keyval(press, keyval, Clutter.KeyState.PRESSED);
        this._vdev.notify_keyval(this._lastTime, keyval, Clutter.KeyState.RELEASED);
    }
}
