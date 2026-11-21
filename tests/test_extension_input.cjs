const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let now = 1000000;
let filter = null;
let nextIdle = 0;
let virtualOutputs = 0;
const pending = new Map();
const sent = [];
const seat = {constructor: {$gtype: 'MetaSeatNative'},
    create_virtual_device() {
        virtualOutputs++;
        return {notify_keyval() {}};
    }};
const EventType = {DEVICE_ADDED: 1, DEVICE_REMOVED: 2, KEY_PRESS: 3,
    KEY_RELEASE: 4, BUTTON_PRESS: 5, SCROLL: 6};
class Event {}
for (const name of ['get_event_code', 'get_source_device', 'get_flags', 'get_state', 'type'])
    Event.prototype[name] = function () {};
Event.add_filter = (_stage, callback) => { filter = callback; return 1; };
Event.remove_filter = () => { filter = null; };
class InputDevice {
    constructor(node = null, vendor = null) { this.node = node; this.vendor = vendor; }
    get_device_node() { return this.node; }
    get_vendor_id() { return this.vendor; }
    get_product_id() { return this.vendor ? '0001' : null; }
    get_seat() { return seat; }
    get_device_mode() { return 1; }
    get_device_type() { return 1; }
}
InputDevice.$gtype = 'MetaInputDeviceNative';
const Clutter = {Event, InputDevice, EventType, EVENT_PROPAGATE: false,
    EventFlags: {FLAG_REPEATED: 4, FLAG_SYNTHETIC: 1, FLAG_INPUT_METHOD: 2},
    ModifierType: {LOCK_MASK: 2}, InputMode: {PHYSICAL: 1},
    KeyState: {PRESSED: 1, RELEASED: 0},
    InputDeviceType: {KEYBOARD_DEVICE: 1},
    get_default_backend: () => ({get_default_seat: () => seat})};
const GLib = {get_monotonic_time: () => now, PRIORITY_DEFAULT: 0,
    SOURCE_CONTINUE: true, SOURCE_REMOVE: false,
    idle_add(_priority, fn) { pending.set(++nextIdle, fn); return nextIdle; },
    source_remove(id) { pending.delete(id); },
    Variant: class { constructor(signature, args) { this.signature = signature; this.args = args; } }};
const context = vm.createContext({Clutter, GLib,
    GObject: {type_name: type => type}, global: {stage: {}},
    Extension: class {}, Main: {modalCount: 0},
    Gio: {DBus: {session: {emit_signal(...args) { sent.push(args); }}}}});
const directory = path.join(__dirname, '../extension/wayswitch@wetoshkin.github.io');
const source = fs.readFileSync(path.join(directory, 'compositorInput.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace('export class CompositorInput', 'class CompositorInput');
vm.runInContext(source + '\nthis.Input = CompositorInput;', context);
const extension = fs.readFileSync(path.join(directory, 'extension.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace('export default class WaySwitchExtension', 'class WaySwitchExtension');
vm.runInContext(extension + '\nthis.ExtensionClass = WaySwitchExtension;', context);

function drain() {
    while (pending.size) {
        const [id, callback] = pending.entries().next().value;
        if (!callback()) pending.delete(id);
    }
}
function event(source, type, code = 30, flags = 0) {
    return {type: () => type, get_source_device: () => source, get_event_code: () => code,
        get_flags: () => flags, get_state: () => 2};
}
function capture(source, type, code = 30, flags = 0) {
    assert.equal(filter(event(source, type, code, flags)), false);
}
const output = [];
let stream = new context.Input(seat, '/dev/input/event9', new Set(['/dev/input/event3']), 7,
    (name, params) => output.push({name, args: Array.from(params.args)}));
const remote = new InputDevice();
// Existing no-node devices have no proven initial pressed state.
capture(remote, EventType.KEY_PRESS);
drain();
assert(output.some(item => item.name === 'CompositorFault'));
stream.close();
output.length = 0;
stream = new context.Input(seat, '/dev/input/event9', new Set(['/dev/input/event3']), 7,
    (name, params) => output.push({name, args: Array.from(params.args)}));
for (const source of [new InputDevice('/dev/input/event3', '1234'),
    new InputDevice('/dev/input/event9', '0fac')]) {
    capture(source, EventType.DEVICE_ADDED);
    capture(source, EventType.KEY_PRESS);
}
assert.equal(stream.devices.size, 0);
capture(remote, EventType.DEVICE_ADDED);
capture(remote, EventType.KEY_PRESS);
capture(remote, EventType.KEY_PRESS, 30, 4);
capture(remote, EventType.KEY_RELEASE);
drain();
const keys = output.filter(item => item.name === 'CompositorKey').map(item => item.args);
assert.deepEqual(keys.map(args => args.slice(0, 6)), [
    [30, 1, -1, 1, 7, 1], [30, 2, -1, 1, 7, 2], [30, 0, -1, 1, 7, 3]]);
assert(keys.every(args => args[6] === true));
output.length = 0;
// Ambiguous repeat discards queued keys and requires release-all.
capture(remote, EventType.KEY_PRESS);
capture(remote, EventType.KEY_PRESS);
drain();
assert(!output.some(item => item.name === 'CompositorKey'));
assert(output.some(item => item.name === 'CompositorResync' && item.args[4].length));
capture(remote, EventType.KEY_RELEASE);
drain();
assert.equal(output.at(-1).name, 'CompositorResync');
assert.equal(output.at(-1).args[4].length, 0);
// Invalid/unknown key codes never claim complete pressed state.
capture(remote, EventType.KEY_PRESS, 255);
assert.equal(output.filter(item => item.name === 'CompositorResync').at(-1).args[5], false);
capture(remote, EventType.DEVICE_REMOVED);
assert.equal(stream.devices.size, 0);
stream.close();
assert.equal(filter, null);

// A name/vendor resemblance without an exact watched node never authorises input.
for (const source of [new InputDevice(null, '0fac'),
    new InputDevice('/dev/input/event21', '1234')]) {
    output.length = 0;
    stream = new context.Input(seat, '/dev/input/event9', new Set(), 7,
        (name, params) => output.push({name, args: Array.from(params.args)}));
    capture(source, EventType.DEVICE_ADDED);
    capture(source, EventType.KEY_PRESS);
    drain();
    assert(output.some(item => item.name === 'CompositorFault'));
    assert(!output.some(item => item.name === 'CompositorKey'));
    stream.close();
}

// Overflow invalidates the generation instead of replaying a truncated queue.
output.length = 0;
stream = new context.Input(seat, '/dev/input/event9', new Set(), 7,
    (name, params) => output.push({name, args: Array.from(params.args)}));
const queuedRemote = new InputDevice();
capture(queuedRemote, EventType.DEVICE_ADDED);
for (let i = 0; i < 90; i++) {
    now += 20000;
    capture(queuedRemote, EventType.KEY_PRESS);
    capture(queuedRemote, EventType.KEY_RELEASE);
}
assert(stream.generation > 7);
assert(stream.queue.length <= 256);
assert(stream.queue.filter(item => item.name === 'CompositorKey')
    .every(item => item.args[4] === stream.generation));
stream.close();
assert.equal(pending.size, 0);

const ext = new context.ExtensionClass();
ext._daemon = ':daemon';
ext._vdev = ext._input = null;
ext._inputGeneration = 0;
ext._watchedDevices = new Set();
let error;
const invoke = sender => ({get_sender: () => sender, return_value() {},
    return_dbus_error(name) { error = name; }});
ext.EnableCompositorInputAsync(['/dev/input/event9'], invoke(':forged'));
assert.equal(error, 'org.freedesktop.DBus.Error.AccessDenied');
assert.equal(filter, null);
assert.deepEqual(Array.from(ext.GetInputCapabilities()), []);
ext.EnableCompositorInputAsync(['/dev/input/event9'], invoke(':daemon'));
assert.equal(error, 'org.freedesktop.DBus.Error.NotSupported');
assert.equal(filter, null);

// Exercise the owner and output guards independently of transport availability.
ext.GetInputCapabilities = () => context.Input.capabilities(seat);
ext.EnableCompositorInputAsync(['/dev/input/event9'], invoke(':daemon'));
assert(filter);
assert.equal(virtualOutputs, 0);
error = null;
ext.TypeTextAsync(['hello'], invoke(':daemon'));
assert.equal(error, 'org.freedesktop.DBus.Error.NotSupported');
ext.BackspaceAsync([1], invoke(':daemon'));
assert.equal(error, 'org.freedesktop.DBus.Error.NotSupported');
ext.DisableCompositorInputAsync([], invoke(':daemon'));
assert.equal(filter, null);
ext._tap(97);
assert.equal(virtualOutputs, 1);
error = null;
ext.EnableCompositorInputAsync(['/dev/input/event9'], invoke(':daemon'));
assert.equal(error, 'org.freedesktop.DBus.Error.NotSupported');
assert.equal(filter, null);
