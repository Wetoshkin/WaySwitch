// Расширение WaySwitch: отдаёт демону индекс текущей раскладки, переключает её
// синхронно, печатает через очередь ввода GNOME и отдаёт выделенный текст.
// Используются keysym-ы виртуального устройства Clutter:
// демон сначала переключает раскладку и проверяет наличие каждого символа.
//
// Печатать, стирать и читать выделение может только сам демон WaySwitch
// (владелец имени io.github.Wetoshkin.WaySwitch на сессионной шине): иначе любое
// приложение с доступом к шине, в том числе из песочницы Flatpak, могло бы
// печатать в чужие окна. Текст, который печатается, в журнал не пишется.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import IBus from 'gi://IBus';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Keyboard from 'resource:///org/gnome/shell/ui/status/keyboard.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'io.github.Wetoshkin.WaySwitch.Shell';
const OBJECT_PATH = '/io/github/Wetoshkin/WaySwitch/Shell';
const DAEMON_NAME = 'io.github.Wetoshkin.WaySwitch';
// Ограничения на один вызов: исправление слова — десятки символов, выделенный
// фрагмент — до нескольких тысяч.
const MAX_TEXT = 4000;
const MAX_BACKSPACE = 512;
const IFACE_XML = `
<node>
  <interface name="io.github.Wetoshkin.WaySwitch.Shell">
    <method name="GetLayout">
      <arg type="u" name="index" direction="out"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="GetFocusedApp"><arg type="as" name="ids" direction="out"/></method>
    <method name="GetFocusedContext">
      <arg type="as" name="ids" direction="out"/>
      <arg type="b" name="modal" direction="out"/>
      <arg type="b" name="ime_focus" direction="out"/>
      <arg type="u" name="purpose" direction="out"/>
      <arg type="u" name="hints" direction="out"/>
      <arg type="u" name="pid" direction="out"/>
    </method>
    <method name="SetLayout"><arg type="u" name="index" direction="in"/></method>
    <method name="TypeText"><arg type="s" name="text" direction="in"/></method>
    <method name="Backspace"><arg type="u" name="count" direction="in"/></method>
    <method name="PrepareReplacement">
      <arg type="u" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="u" direction="out"/>
    </method>
    <method name="CommitReplacement">
      <arg type="u" direction="in"/><arg type="u" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="GetSelection"><arg type="s" name="text" direction="out"/></method>
    <method name="GetVersion"><arg type="u" name="version" direction="out"/></method>
    <signal name="LayoutChanged"><arg type="u" name="index"/></signal>
    <signal name="FocusedContextChanged"/>
  </interface>
</node>`;
// Версия протокола: демон сверяет её и просит перелогиниться, если Shell
// держит старую копию расширения (на Wayland оно перезагружается только так).
const PROTOCOL_VERSION = 5;

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
                this._invalidateReplacement();
            });
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.DBus.session.own_name(BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
        this._changedId = this._ism.connect('current-source-changed', () => {
            this._invalidateReplacement();
            const src = this._ism.currentSource;
            if (src)
                this._dbus.emit_signal('LayoutChanged', new GLib.Variant('(u)', [src.index]));
        });
        this._watchFocusedContext();
        this._watchReplacements();
    }

    disable() {
        this._unwatchReplacements();
        for (const [object, id] of this._focusSignals ?? [])
            object.disconnect(id);
        this._focusSignals = [];
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
        if (Main.modalCount > 0) {
            invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied',
                'исправление недоступно в системном диалоге');
            return false;
        }
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

    GetFocusedApp() {
        const window = global.display.focus_window;
        if (!window)
            return [];
        return [...new Set([window.get_wm_class(), window.get_gtk_application_id(),
            window.get_sandboxed_app_id()].filter(Boolean))];
    }

    GetFocusedContext() {
        const im = Main.inputMethod;
        const hasFocus = Boolean(im?.currentFocus);
        const purpose = hasFocus ? im.content_purpose : 0;
        const hints = hasFocus ? im.content_hints : 0;
        const validIME = hasFocus && Number.isInteger(purpose) && Number.isInteger(hints) &&
            purpose >= 0 && purpose <= 0xFFFFFFFF && hints >= 0 && hints <= 0xFFFFFFFF;
        const pid = global.display.focus_window?.get_pid?.() ?? 0;
        const validPid = Number.isInteger(pid) && pid > 0 && pid <= 0xFFFFFFFF;
        return [this.GetFocusedApp(), Main.modalCount > 0, validIME,
            validIME ? purpose : 0, validIME ? hints : 0, validPid ? pid : 0];
    }

    _watchFocusedContext() {
        this._focusSignals = [];
        for (const [object, signal] of [
            [global.display, 'notify::focus-window'],
            [global.stage, 'notify::key-focus'],
            [Main.inputMethod, 'notify::content-purpose'],
            [Main.inputMethod, 'notify::content-hints'],
        ]) {
            if (object) {
                const id = object.connect(signal, () => {
                    this._invalidateReplacement();
                    this._dbus?.emit_signal('FocusedContextChanged', null);
                });
                this._focusSignals.push([object, id]);
            }
        }
    }

    _invalidateReplacement() {
        this._replacement = null;
    }

    _watchReplacements() {
        const im = Main.inputMethod;
        this._replacementSerial = 0;
        this._replacementHooks = [];
        this._replacementPending = new WeakMap();
        this._replacementSupported = Boolean(im &&
            typeof im.notify_key_event === 'function' && typeof im._fullReset === 'function' &&
            typeof im.getSurroundingText === 'function' && typeof im.hasPreedit === 'function' &&
            typeof IBus.InputContext.prototype.process_key_event_async === 'function');
        if (!this._replacementSupported)
            return;
        for (const name of ['notify_key_event', '_fullReset']) {
            const original = im[name];
            const wrapper = (...args) => {
                if (name === '_fullReset' ? !im.currentFocus :
                    args[0].type() === Clutter.EventType.KEY_PRESS)
                    this._invalidateReplacement();
                return original.apply(im, args);
            };
            im[name] = wrapper;
            this._replacementHooks.push([im, name, original, wrapper]);
        }
        // IBus отвечает асинхронно: замена не должна обогнать ещё не доставленную клавишу.
        const object = IBus.InputContext.prototype;
        const original = object.process_key_event_async;
        const pending = this._replacementPending;
        const extension = this;
        const wrapper = function (...args) {
            if (this !== Main.inputMethod?._context)
                return original.apply(this, args);
            extension._invalidateReplacement();
            pending.set(this, (pending.get(this) ?? 0) + 1);
            let finished = false;
            const complete = () => {
                if (!finished)
                    pending.set(this, pending.get(this) - 1);
                finished = true;
            };
            const callback = args.at(-1);
            try {
                if (typeof callback !== 'function')
                    return original.apply(this, args).finally(complete);
                args[args.length - 1] = (...result) => {
                    complete();
                    return callback(...result);
                };
                return original.apply(this, args);
            } catch (error) {
                complete();
                throw error;
            }
        };
        object.process_key_event_async = wrapper;
        this._replacementHooks.push([object, 'process_key_event_async', original, wrapper]);
    }

    _unwatchReplacements() {
        this._invalidateReplacement();
        this._replacementSupported = false;
        for (const [object, name, original, wrapper] of this._replacementHooks ?? []) {
            if (object[name] === wrapper)
                object[name] = original;
        }
        this._replacementHooks = [];
    }

    _replacementContext(pid) {
        const im = Main.inputMethod;
        return this._replacementSupported && Main.modalCount === 0 && pid > 0 &&
            !this._replacementPending?.get(im?._context) &&
            global.display.focus_window?.get_pid() === pid && im?.currentFocus &&
            im.content_purpose === Clutter.InputContentPurpose.NORMAL &&
            Number.isInteger(im.content_hints) && im.content_hints >= 0 &&
            (im.content_hints & (Clutter.InputContentHintFlags.SENSITIVE_DATA |
                Clutter.InputContentHintFlags.HIDDEN_TEXT)) === 0 && !im.hasPreedit();
    }

    PrepareReplacementAsync([pid, source, target], invocation) {
        if (!this._allowed(invocation))
            return;
        this._invalidateReplacement();
        const reply = token => invocation.return_value(new GLib.Variant('(u)', [token]));
        if (!this._replacementContext(pid) || !source.length || !target.length ||
            source.length > MAX_BACKSPACE || target.length > MAX_BACKSPACE) {
            reply(0);
            return;
        }
        const im = Main.inputMethod;
        const [text, cursor, anchor] = im.getSurroundingText();
        const chars = typeof text === 'string' ? Array.from(text) : [];
        const count = Array.from(source).length;
        if (chars.length > MAX_TEXT || !Number.isInteger(cursor) || cursor !== anchor ||
            cursor < count || cursor > chars.length ||
            chars.slice(cursor - count, cursor).join('').replaceAll('\u00a0', ' ') !== source) {
            reply(0);
            return;
        }
        this._replacementSerial = (this._replacementSerial ?? 0) % 0xFFFFFFFF + 1;
        this._replacement = {token: this._replacementSerial, pid, target, count,
            text, cursor, focus: im.currentFocus, window: global.display.focus_window,
            expires: GLib.get_monotonic_time() + 1000000};
        reply(this._replacementSerial);
    }

    CommitReplacementAsync([token, group], invocation) {
        if (!this._allowed(invocation))
            return;
        const pending = this._replacement;
        this._invalidateReplacement();
        const reply = result => invocation.return_value(new GLib.Variant('(b)', [result]));
        if (!pending || pending.token !== token || !this._replacementContext(pending.pid) ||
            pending.expires < GLib.get_monotonic_time() ||
            pending.window !== global.display.focus_window ||
            pending.focus !== Main.inputMethod.currentFocus ||
            group !== 0xFFFFFFFF && !this._ism.inputSources[group]) {
            reply(false);
            return;
        }
        const im = Main.inputMethod;
        const [text, cursor, anchor] = im.getSurroundingText();
        if (text !== pending.text || cursor !== pending.cursor || anchor !== cursor) {
            reply(false);
            return;
        }
        if (group !== 0xFFFFFFFF)
            this._ism.inputSources[group].activate(false);
        im.delete_surrounding(-pending.count, pending.count);
        im.commit(pending.target);
        reply(true);
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
        const caps = defaultBackend().get_default_seat().get_keymap().get_caps_lock_state();
        // IME commit может обогнать Backspace в очереди приложения (в том числе Qt).
        for (const ch of text) {
            // Mutter подбирает Shift по keysym без учёта активного Caps Lock.
            const lower = GLib.unichar_tolower(ch);
            const key = caps ? (ch === lower ? GLib.unichar_toupper(ch) : lower) : ch;
            const keyval = Clutter.unicode_to_keysym(key.codePointAt(0));
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
        // Устарело (используется AT-SPI): сохранено в окне 1.x для старых демонов до 2.0.
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
