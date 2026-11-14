const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname,
    '../extension/wayswitch@wetoshkin.github.io/extension.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace('export default class WaySwitchExtension', 'class WaySwitchExtension');
const context = vm.createContext({
    Extension: class {},
    global: {display: {focus_window: null}},
    Main: {modalCount: 0, inputMethod: null},
    GLib: {
        get_monotonic_time: () => 1000000,
        Variant: class {
            constructor(signature, value) {
                this.signature = signature;
                this.value = value;
            }
        },
    },
});
vm.runInContext(source + '\nthis.extension = new WaySwitchExtension();', context);
assert.deepEqual(Array.from(context.extension.GetFocusedApp()), []);
context.global.display.focus_window = {
    get_wm_class: () => 'jetbrains-idea',
    get_gtk_application_id: () => null,
    get_sandboxed_app_id: () => null,
    get_title: () => { throw Error('Window titles must not be read'); },
};
assert.deepEqual(Array.from(context.extension.GetFocusedApp()), ['jetbrains-idea']);
context.global.display.focus_window.get_gtk_application_id = () => 'jetbrains-idea';
context.global.display.focus_window.get_sandboxed_app_id = () => 'com.jetbrains.IntelliJ-IDEA';
assert.deepEqual(Array.from(context.extension.GetFocusedApp()),
    ['jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA']);

assert.equal(typeof context.extension.GetFocusedContext, 'function');
assert.equal(context.extension.GetVersion(), 5);
assert.equal(typeof context.extension.GetCapabilities, 'function');
assert.deepEqual(Array.from(context.extension.GetCapabilities()), ['focused_context_payload_v2']);
const plainContext = () => JSON.parse(JSON.stringify(context.extension.GetFocusedContext()));
context.global.display.focus_window.get_pid = () => 1234;
assert.deepEqual(plainContext(), [
    ['jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA'], false, false, 0, 0, 1234,
]);
context.Main.inputMethod = {
    currentFocus: {}, content_purpose: 8, content_hints: 128,
    get surrounding_text() { throw Error('Surrounding text must not be read'); },
    getSurroundingText() { throw Error('Surrounding text must not be read'); },
};
assert.deepEqual(plainContext(), [
    ['jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA'], false, true, 8, 128, 1234,
]);
context.Main.inputMethod.currentFocus = null;
Object.defineProperty(context.Main.inputMethod, 'content_purpose', {
    get() { throw Error('Cached purpose without IME focus must not be read'); },
    configurable: true,
});
context.Main.modalCount = 1;
assert.deepEqual(plainContext(), [
    ['jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA'], true, false, 0, 0, 1234,
]);
context.Main.inputMethod = {currentFocus: {}, content_purpose: -1, content_hints: 0};
context.Main.modalCount = 0;
context.global.display.focus_window.get_pid = () => -1;
assert.deepEqual(plainContext(), [
    ['jetbrains-idea', 'com.jetbrains.IntelliJ-IDEA'], false, false, 0, 0, 0,
]);
context.global.display.focus_window = null;
context.Main.inputMethod = null;
assert.deepEqual(plainContext(), [[], false, false, 0, 0, 0]);

assert.equal(typeof context.extension._watchFocusedContext, 'function');
const subscriptions = [];
const disconnected = [];
const signals = [];
const emitter = () => ({
    connect(signal, callback) {
        const id = subscriptions.length + 1;
        subscriptions.push({signal, callback, id});
        return id;
    },
    disconnect(id) { disconnected.push(id); },
});
context.global.display = emitter();
context.global.stage = emitter();
context.Main.inputMethod = emitter();
context.extension._dbus = {
    emit_signal(name, args) { signals.push([name, args]); },
    unexport() {},
};
context.extension._watchFocusedContext();
assert.deepEqual(subscriptions.map(item => item.signal), [
    'notify::focus-window', 'notify::key-focus',
    'notify::content-purpose', 'notify::content-hints',
]);
for (const item of subscriptions)
    item.callback(new Proxy({}, {get() { throw Error('Event payload must not be read'); }}));
assert.deepEqual(signals, Array.from({length: 4}, () => ['FocusedContextChanged', null]));
context.extension.disable();
assert.deepEqual(disconnected, [1, 2, 3, 4]);
assert.equal(context.extension._focusSignals.length, 0);
const ifaceXml = vm.runInContext('IFACE_XML', context);
assert.match(ifaceXml, /<signal name="FocusedContextChanged"\s*\/>/);
assert.match(ifaceXml, /<method name="GetCapabilities">\s*<arg type="as" name="capabilities" direction="out"\/>\s*<\/method>/);
assert.match(
    ifaceXml,
    /<signal name="FocusedContextChanged2">\s*<arg type="as" name="ids"\/>\s*<arg type="b" name="modal"\/>\s*<arg type="b" name="ime_focus"\/>\s*<arg type="u" name="purpose"\/>\s*<arg type="u" name="hints"\/>\s*<arg type="u" name="pid"\/>\s*<\/signal>/,
);

subscriptions.length = 0;
disconnected.length = 0;
signals.length = 0;
const displayEmitter = emitter();
const stageEmitter = emitter();
const imEmitter = emitter();
context.global.display = Object.assign(displayEmitter, {
    focus_window: {
        get_wm_class: () => 'org.mozilla.firefox',
        get_gtk_application_id: () => 'org.mozilla.firefox',
        get_sandboxed_app_id: () => null,
        get_pid: () => 4321,
        get_title: () => { throw Error('Window titles must not be read'); },
    },
});
context.global.stage = stageEmitter;
context.Main.modalCount = 0;
context.Main.inputMethod = Object.assign(imEmitter, {
    currentFocus: {},
    content_purpose: 0,
    content_hints: 0,
    getSurroundingText() { throw Error('Surrounding text must not be read'); },
});
Object.defineProperty(context.Main.inputMethod, 'surrounding_text', {
    get() { throw Error('Surrounding text must not be read'); },
    configurable: true,
});
context.extension._dbus = {
    emit_signal(name, args) { signals.push([name, args]); },
    unexport() {},
};
context.extension._watchFocusedContext();
assert.deepEqual(subscriptions.map(item => item.signal), [
    'notify::focus-window', 'notify::key-focus',
    'notify::content-purpose', 'notify::content-hints',
]);

// All 4 notify signals emit legacy empty signal FIRST, then FocusedContextChanged2 with method parity.
for (const item of subscriptions) {
    signals.length = 0;
    item.callback(new Proxy({}, {get() { throw Error('Event payload must not be read'); }}));
    assert.equal(signals.length, 2, item.signal);
    assert.deepEqual(signals[0], ['FocusedContextChanged', null]);
    assert.equal(signals[1][0], 'FocusedContextChanged2');
    assert.equal(signals[1][1].signature, '(asbbuuu)');
    assert.deepEqual(JSON.parse(JSON.stringify(signals[1][1].value)), plainContext());
}

// Normal, password, modal, pid=0, and absent focus_window parity cases.
for (const setup of [
    () => {
        context.Main.modalCount = 0;
        context.Main.inputMethod.content_purpose = 0;
        context.Main.inputMethod.content_hints = 0;
        context.global.display.focus_window.get_pid = () => 4321;
    },
    () => {
        context.Main.modalCount = 0;
        context.Main.inputMethod.content_purpose = 8;
        context.Main.inputMethod.content_hints = 128;
        context.global.display.focus_window.get_pid = () => 4321;
    },
    () => {
        context.Main.modalCount = 1;
        context.Main.inputMethod.content_purpose = 0;
        context.Main.inputMethod.content_hints = 0;
        context.global.display.focus_window.get_pid = () => 4321;
    },
    () => {
        context.Main.modalCount = 0;
        context.global.display.focus_window.get_pid = () => 0;
    },
    () => {
        context.Main.modalCount = 0;
        context.global.display.focus_window = null;
    },
]) {
    setup();
    signals.length = 0;
    subscriptions[0].callback();
    assert.equal(signals.length, 2);
    assert.deepEqual(signals[0], ['FocusedContextChanged', null]);
    assert.equal(signals[1][0], 'FocusedContextChanged2');
    assert.equal(signals[1][1].signature, '(asbbuuu)');
    assert.deepEqual(JSON.parse(JSON.stringify(signals[1][1].value)), plainContext());
}

// Bounded max (16 ids, 256 BMP codepoints, 256 non-BMP codepoints, uint32 max).
const savedGetFocusedApp = context.extension.GetFocusedApp;
context.global.display.focus_window = {
    get_wm_class: () => 'app',
    get_gtk_application_id: () => null,
    get_sandboxed_app_id: () => null,
    get_pid: () => 0xFFFFFFFF,
    get_title: () => { throw Error('Window titles must not be read'); },
};
context.Main.inputMethod.content_purpose = 0xFFFFFFFF;
context.Main.inputMethod.content_hints = 0xFFFFFFFF;
context.extension.GetFocusedApp = () => Array.from({length: 16}, (_, i) => `${i}:` + 'a'.repeat(256 - `${i}:`.length));
signals.length = 0;
subscriptions[0].callback();
assert.equal(signals.length, 2);
assert.deepEqual(signals[0], ['FocusedContextChanged', null]);
assert.deepEqual(JSON.parse(JSON.stringify(signals[1][1].value)), plainContext());

context.extension.GetFocusedApp = () => ['🚀'.repeat(256)];
signals.length = 0;
subscriptions[0].callback();
assert.equal(signals.length, 2);
assert.deepEqual(signals[0], ['FocusedContextChanged', null]);
assert.deepEqual(JSON.parse(JSON.stringify(signals[1][1].value)), plainContext());

// Oversized or malformed payloads must NOT be truncated/sanitized; emit legacy signal only.
for (const breakPayload of [
    () => { context.extension.GetFocusedApp = () => Array.from({length: 17}, (_, i) => `app-${i}`); },
    () => { context.extension.GetFocusedApp = () => ['a'.repeat(257)]; },
    () => { context.extension.GetFocusedApp = () => ['🚀'.repeat(257)]; },
    () => { context.extension.GetFocusedApp = () => [123]; },
    () => { context.extension.GetFocusedApp = () => ['ok']; context.Main.inputMethod.content_purpose = -1; },
    () => { context.Main.inputMethod.content_purpose = 0x100000000; },
    () => { context.Main.inputMethod.content_purpose = 1.5; },
    () => { context.Main.inputMethod.content_purpose = '8'; },
    () => { context.Main.inputMethod.content_purpose = Number.NaN; },
    () => { context.Main.inputMethod.content_purpose = 0; context.Main.inputMethod.content_hints = -1; },
    () => { context.Main.inputMethod.content_hints = 0x100000000; },
    () => { context.Main.inputMethod.content_hints = 1.5; },
    () => { context.Main.inputMethod.content_hints = '0'; },
    () => { context.Main.inputMethod.content_hints = Number.NaN; },
    () => { context.Main.inputMethod.content_hints = 0; context.global.display.focus_window.get_pid = () => -1; },
    () => { context.global.display.focus_window.get_pid = () => 0x100000000; },
    () => { context.global.display.focus_window.get_pid = () => 1.5; },
    () => { context.global.display.focus_window.get_pid = () => '123'; },
    () => { context.global.display.focus_window.get_pid = () => Number.NaN; },
]) {
    breakPayload();
    signals.length = 0;
    subscriptions[0].callback();
    assert.deepEqual(signals, [['FocusedContextChanged', null]]);
}
context.extension.GetFocusedApp = savedGetFocusedApp;

// Unknown/absent IME emits legacy signal only while GetFocusedContext preserves fallback.
context.global.display.focus_window.get_pid = () => 1234;
context.Main.inputMethod = {
    currentFocus: null,
    get content_purpose() { throw Error('Cached purpose without IME focus must not be read'); },
    get content_hints() { throw Error('Cached hints without IME focus must not be read'); },
};
signals.length = 0;
subscriptions[0].callback();
assert.deepEqual(signals, [['FocusedContextChanged', null]]);
assert.deepEqual(plainContext(), [['app'], false, false, 0, 0, 1234]);

context.Main.inputMethod = null;
signals.length = 0;
subscriptions[0].callback();
assert.deepEqual(signals, [['FocusedContextChanged', null]]);
assert.deepEqual(plainContext(), [['app'], false, false, 0, 0, 1234]);

// Failure while collecting new payload must never suppress legacy invalidation.
context.global.display.focus_window = {
    get_wm_class() { throw Error('boom'); },
    get_gtk_application_id: () => null,
    get_sandboxed_app_id: () => null,
    get_pid: () => 1234,
};
signals.length = 0;
subscriptions[0].callback();
assert.deepEqual(signals, [['FocusedContextChanged', null]]);

context.extension.disable();
assert.deepEqual(disconnected, [1, 2, 3, 4]);
assert.equal(context.extension._focusSignals.length, 0);

const keyEvents = [];
const inputEvents = [];
context.Clutter = {KEY_BackSpace: 65288, unicode_to_keysym: value => value,
    get_default_backend: () => ({get_default_seat: () => ({
        get_keymap: () => ({get_caps_lock_state: () => false}),
    })})};
context.GLib.unichar_tolower = ch => ch.toLowerCase();
context.extension._tap = keyval => keyEvents.push(keyval);
context.extension._daemon = ':test-daemon';
context.Main.inputMethod = {
    currentFocus: {}, content_purpose: 0, content_hints: 0,
    commit(text) { inputEvents.push(['commit', text]); },
    forward_key(keyval, code, state, time, pressed) {
        if (pressed) inputEvents.push(['key', keyval, code]);
    },
    delete_surrounding() { throw Error('Cached IME cursor must not be used'); },
    getSurroundingText() { throw Error('Surrounding text must not be read'); },
};
const invoke = (method, value) => {
    const result = {};
    context.extension[method]([value], {
        get_sender: () => ':test-daemon',
        return_value: value => { result.reply = value; },
        return_dbus_error: (name, message) => { result.error = [name, message]; },
    });
    return result;
};
assert.equal(invoke('TypeTextAsync', 'привет').reply, null);
assert.deepEqual(inputEvents, []);
assert.deepEqual(keyEvents, Array.from('привет', ch => ch.codePointAt(0)));
inputEvents.length = 0;
keyEvents.length = 0;
assert.equal(invoke('BackspaceAsync', 3).reply, null);
assert.deepEqual(inputEvents, []);
assert.deepEqual(keyEvents, [65288, 65288, 65288]);
inputEvents.length = 0;
keyEvents.length = 0;
context.Main.inputMethod.currentFocus = null;
assert.equal(invoke('TypeTextAsync', 'x').reply, null);
assert.equal(invoke('BackspaceAsync', 1).reply, null);
assert.deepEqual(keyEvents, [120, 65288]);
context.Main.inputMethod.currentFocus = {};
context.Main.inputMethod.content_purpose = 8;
keyEvents.length = 0;
assert.equal(invoke('TypeTextAsync', 'x').reply, null);
assert.deepEqual(inputEvents, []);
assert.deepEqual(keyEvents, [120]);
context.Main.modalCount = 1;
keyEvents.length = 0;
assert.equal(invoke('TypeTextAsync', 'x').error?.[0], 'org.freedesktop.DBus.Error.AccessDenied');
assert.deepEqual(keyEvents, []);
