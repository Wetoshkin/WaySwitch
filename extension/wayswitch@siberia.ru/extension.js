// Расширение WaySwitch: отдаёт демону индекс текущей раскладки, переключает её
// синхронно и печатает произвольные keysym-ы через виртуальное устройство
// Clutter — независимо от раскладки, поэтому демону не нужно ждать смены.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Keyboard from 'resource:///org/gnome/shell/ui/status/keyboard.js';

const BUS_NAME = 'ru.siberia.WaySwitch.Shell';
const OBJECT_PATH = '/ru/siberia/WaySwitch/Shell';
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
    <signal name="LayoutChanged"><arg type="u" name="index"/></signal>
  </interface>
</node>`;

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
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._vdev = null;
        this._ism = null;
    }

    // --- D-Bus методы (имена совпадают с XML) ---------------------------------

    GetLayout() {
        const src = this._ism.currentSource;
        // 0xFFFFFFFF — сигнальное значение «раскладка неизвестна»: индекс 0
        // был бы неотличим от настоящего первого источника ввода.
        return src ? [src.index, src.id] : [0xFFFFFFFF, ''];
    }

    SetLayout(index) {
        const src = this._ism.inputSources[index];
        if (!src)
            throw new Error(`нет источника ввода с индексом ${index}`);
        src.activate(true);
    }

    TypeText(text) {
        log(`WaySwitch: TypeText ${text.length} символов ${JSON.stringify(text)}`);
        for (const ch of text) {
            const keyval = Clutter.unicode_to_keysym(ch.codePointAt(0));
            this._tap(keyval);
        }
    }

    Backspace(count) {
        log(`WaySwitch: Backspace ${count}`);
        for (let i = 0; i < count; i++)
            this._tap(Clutter.KEY_BackSpace);
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
