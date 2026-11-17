import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

const MAX_QUEUE = 256;
const MAX_DEVICES = 32;
const MAX_FRAME = 128;
const MAX_RATE = 1024;
const nodePath = path => typeof path === 'string' && /^\/dev\/input\/event[0-9]+$/.test(path);
const absent = value => value === null || value === '';
const typeName = object => GObject.type_name(object.constructor.$gtype);

export class CompositorInput {
    static capabilities(seat) {
        try {
            if (typeName(seat) !== 'MetaSeatNative' ||
                typeof Clutter.Event.add_filter !== 'function' ||
                typeof Clutter.Event.remove_filter !== 'function' ||
                !['get_event_code', 'get_source_device', 'get_flags', 'get_state', 'type']
                    .every(name => typeof Clutter.Event.prototype[name] === 'function') ||
                !['get_device_node', 'get_vendor_id', 'get_product_id', 'get_seat',
                    'get_device_mode', 'get_device_type']
                    .every(name => typeof Clutter.InputDevice.prototype[name] === 'function') ||
                !['FLAG_REPEATED', 'FLAG_SYNTHETIC', 'FLAG_INPUT_METHOD']
                    .every(name => Number.isInteger(Clutter.EventFlags[name])) ||
                !['DEVICE_ADDED', 'DEVICE_REMOVED', 'KEY_PRESS', 'KEY_RELEASE',
                    'BUTTON_PRESS', 'SCROLL'].every(name => Number.isInteger(Clutter.EventType[name])) ||
                !Number.isInteger(Clutter.InputMode.PHYSICAL) ||
                !Number.isInteger(Clutter.ModifierType.LOCK_MASK))
                return [];
            return ['compositor-key-events', 'device-node', 'key-repeat',
                'new-device-state', 'uinput-only'];
        } catch (_) {
            return [];
        }
    }

    constructor(seat, ownNode, watched, generation, send) {
        if (!nodePath(ownNode) || !CompositorInput.capabilities(seat).length)
            throw Error('Unsupported compositor input');
        this.seat = seat;
        this.ownNode = ownNode;
        this.watched = watched;
        this.generation = generation;
        this.send = send;
        this.devices = new Map();
        this.nextId = -1;
        this.queue = [];
        this.idle = 0;
        this.rate = this.frame = 0;
        this.fault = false;
        this.rateAt = this.frameAt = GLib.get_monotonic_time();
        this.filter = Clutter.Event.add_filter(global.stage,
            event => this._event(event), null);
        if (!this.filter)
            throw Error('Compositor filter registration failed');
    }

    _eligible(source) {
        return source && source.get_seat() === this.seat &&
            typeName(source) === 'MetaInputDeviceNative' &&
            source.get_device_type() === Clutter.InputDeviceType.KEYBOARD_DEVICE &&
            source.get_device_mode() === Clutter.InputMode.PHYSICAL &&
            absent(source.get_device_node()) && absent(source.get_vendor_id()) &&
            absent(source.get_product_id());
    }

    _event(event) {
        try {
            const type = event.type();
            if (this.fault && type !== Clutter.EventType.DEVICE_REMOVED)
                return Clutter.EVENT_PROPAGATE;
            if (![Clutter.EventType.DEVICE_ADDED, Clutter.EventType.DEVICE_REMOVED,
                Clutter.EventType.KEY_PRESS, Clutter.EventType.KEY_RELEASE,
                Clutter.EventType.BUTTON_PRESS, Clutter.EventType.SCROLL].includes(type))
                return Clutter.EVENT_PROPAGATE;
            const source = event.get_source_device();
            if (source?.get_seat() !== this.seat) {
                this._invalidate(false);
                return Clutter.EVENT_PROPAGATE;
            }
            if (type === Clutter.EventType.DEVICE_REMOVED) {
                const device = this.devices.get(source);
                if (device) {
                    this._invalidate();
                    this._emit('CompositorDeviceGone', '(iuu)', [device.id, 1, this.generation]);
                    this.devices.delete(source);
                }
            } else if (type === Clutter.EventType.DEVICE_ADDED) {
                // Только новый источник имеет доказанно пустое начальное состояние.
                if (this._eligible(source) && this.devices.size < MAX_DEVICES &&
                    !this.devices.has(source))
                    this.devices.set(source, {id: this.nextId--, held: new Set(),
                        sequence: 0, blocked: false, complete: true});
            } else if (type === Clutter.EventType.BUTTON_PRESS || type === Clutter.EventType.SCROLL) {
                if (this._budget())
                    this._enqueue('CompositorBoundary', '(uu)',
                        [this.generation, type === Clutter.EventType.SCROLL ? 1 : 0]);
                else
                    this._invalidate();
            } else if (type === Clutter.EventType.KEY_PRESS || type === Clutter.EventType.KEY_RELEASE) {
                const node = source?.get_device_node();
                if (node === this.ownNode || this.watched.has(node))
                    return Clutter.EVENT_PROPAGATE;
                const device = this.devices.get(source);
                if (!device) {
                    this._invalidate(false);
                    return Clutter.EVENT_PROPAGATE;
                }
                if (!this._eligible(source)) {
                    this._invalidate(false);
                    return Clutter.EVENT_PROPAGATE;
                }
                this._key(event, device, type === Clutter.EventType.KEY_PRESS);
            }
        } catch (_) {
            this._invalidate(false);
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _key(event, device, press) {
        const code = event.get_event_code();
        const flags = event.get_flags();
        const modifiers = event.get_state();
        const repeat = Boolean(flags & Clutter.EventFlags.FLAG_REPEATED);
        if (!Number.isInteger(code) || code < 1 || code > 127 ||
            !Number.isInteger(flags) || flags < 0 || flags > 0xFFFFFFFF ||
            !Number.isInteger(modifiers) || modifiers < 0 || modifiers > 0xFFFFFFFF ||
            device.sequence >= 0xFFFFFFFF) {
            this._invalidate(false);
            return;
        }
        const known = device.held.has(code);
        if (press)
            device.held.add(code);
        else
            device.held.delete(code);
        device.sequence++;
        const invalid = (press ? known !== repeat : !known || repeat) ||
            Boolean(flags & (Clutter.EventFlags.FLAG_SYNTHETIC | Clutter.EventFlags.FLAG_INPUT_METHOD));
        if (invalid || !this._budget())
            this._invalidate();
        if (!device.blocked) {
            const caps = Boolean(modifiers & Clutter.ModifierType.LOCK_MASK);
            this._enqueue('CompositorKey', '(uuiuuub)',
                [code, press ? (repeat ? 2 : 1) : 0, device.id, 1,
                    this.generation, device.sequence, caps]);
        }
        if (!device.held.size && device.complete) {
            device.blocked = false;
            this._enqueue('CompositorResync', '(iuuuaub)',
                [device.id, 1, this.generation, device.sequence, [], true]);
        }
    }

    _budget() {
        const now = GLib.get_monotonic_time();
        if (now - this.rateAt >= 1000000) {
            this.rateAt = now;
            this.rate = 0;
        }
        if (now - this.frameAt >= 16000) {
            this.frameAt = now;
            this.frame = 0;
        }
        return ++this.rate <= MAX_RATE && ++this.frame <= MAX_FRAME;
    }

    _emit(name, signature, args) {
        this.send(name, new GLib.Variant(signature, args));
    }

    _invalidate(complete = true) {
        this.queue = [];
        if (this.fault || complete && [...this.devices.values()].every(device => device.blocked))
            return;
        if (this.generation < 0xFFFFFFFF)
            this.generation++;
        else
            complete = false;
        for (const device of this.devices.values()) {
            device.blocked = true;
            device.complete &&= complete;
            this._emit('CompositorResync', '(iuuuaub)', [device.id, 1, this.generation,
                device.sequence, [...device.held], device.complete]);
        }
        if (!complete) {
            this.fault = true;
            this._emit('CompositorFault', '(u)', [this.generation]);
        }
    }

    _enqueue(name, signature, args) {
        if (this.queue.length >= MAX_QUEUE) {
            this._invalidate();
            return;
        }
        this.queue.push({name, signature, args});
        if (!this.idle) {
            this.idle = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
                for (const item of this.queue.splice(0, 64))
                    this._emit(item.name, item.signature, item.args);
                if (this.queue.length)
                    return GLib.SOURCE_CONTINUE;
                this.idle = 0;
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    close() {
        if (this.filter) {
            Clutter.Event.remove_filter(this.filter);
            this.filter = 0;
        }
        if (this.idle) {
            GLib.source_remove(this.idle);
            this.idle = 0;
        }
        this.queue = [];
        for (const device of this.devices.values())
            this._emit('CompositorDeviceGone', '(iuu)', [device.id, 1, this.generation]);
        this.devices.clear();
    }
}
