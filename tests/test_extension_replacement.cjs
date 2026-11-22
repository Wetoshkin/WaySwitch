const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname,
    '../extension/wayswitch@wetoshkin.github.io/extension.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace('export default class WaySwitchExtension', 'class WaySwitchExtension');
let now = 1000000;
let surrounding = ['prefix ult jy suffix', 13, 13];
const writes = [];
const focus = {};
const window = {get_pid: () => 123};
const keyCallbacks = [];
class InputContext {
    process_key_event_async(...args) {
        if (typeof args.at(-1) === 'function')
            keyCallbacks.push(args.at(-1));
        else
            return new Promise(resolve => keyCallbacks.push(resolve));
    }
}
const context = vm.createContext({
    Extension: class {},
    IBus: {InputContext},
    GLib: {get_monotonic_time: () => now, Variant: class {
        constructor(type, value) { this.value = value; }
    }},
    Clutter: {EventType: {KEY_PRESS: 1}, InputContentPurpose: {NORMAL: 0},
        InputContentHintFlags: {SENSITIVE_DATA: 128, HIDDEN_TEXT: 64}},
    Main: {modalCount: 0, inputMethod: {
        _context: new InputContext(),
        currentFocus: focus, content_purpose: 0, content_hints: 0,
        hasPreedit: () => false,
        getSurroundingText: () => surrounding,
        delete_surrounding: (...args) => writes.push(['delete', ...args]),
        commit: text => writes.push(['commit', text]),
    }},
    global: {display: {focus_window: window}},
});
vm.runInContext(source + '\nthis.extension = new WaySwitchExtension();', context);
const extension = context.extension;
extension._daemon = ':daemon';
extension._replacementSerial = 0;
extension._replacementSupported = true;
extension._ism = {inputSources: {1: {activate: () => writes.push(['layout', 1])}}};

function invoke(method, args, sender = ':daemon') {
    let result;
    extension[method](args, {
        get_sender: () => sender,
        return_value: reply => { result = reply.value[0]; },
        return_dbus_error: name => { result = name; },
    });
    return result;
}

function prepare() {
    const ticket = invoke('PrepareReplacementAsync', [123, 'ult jy', 'где он']);
    assert.ok(Number.isInteger(ticket) && ticket > 0);
    return ticket;
}
assert.equal(typeof extension.PrepareReplacementAsync, 'function');
assert.equal(invoke('PrepareReplacementAsync', [123, 'ult jy', 'где он'], ':other'),
    'org.freedesktop.DBus.Error.AccessDenied');
assert.equal(writes.length, 0);
let ticket = prepare();
assert.equal(writes.length, 0);
assert.equal(invoke('CommitReplacementAsync', [ticket, 1]), true);
assert.deepEqual(writes.splice(0), [['layout', 1], ['delete', -6, 6], ['commit', 'где он']]);
assert.equal(invoke('CommitReplacementAsync', [ticket, 1]), false);

for (const change of [
    () => extension._invalidateReplacement(),
    () => { context.Main.inputMethod.currentFocus = {}; },
    () => { context.global.display.focus_window = {get_pid: () => 123}; },
    () => { surrounding = ['prefix ult jyX suffix', 14, 14]; },
    () => { surrounding = ['prefix ult jy suffix', 7, 13]; },
    () => { context.Main.inputMethod.content_purpose = 8; },
    () => { context.Main.inputMethod.content_hints = 128; },
    () => { context.Main.modalCount = 1; },
    () => { now += 1000001; },
]) {
    surrounding = ['prefix ult jy suffix', 13, 13];
    context.Main.inputMethod.currentFocus = focus;
    context.global.display.focus_window = window;
    context.Main.inputMethod.content_purpose = 0;
    context.Main.inputMethod.content_hints = 0;
    context.Main.modalCount = 0;
    ticket = prepare();
    change();
    const result = invoke('CommitReplacementAsync', [ticket, 1]);
    assert.ok(result === false || result === 'org.freedesktop.DBus.Error.AccessDenied');
    assert.equal(writes.length, 0);
}

surrounding = ['🚀 ult ', 6, 6];
context.Main.modalCount = 0;
context.Main.inputMethod.content_hints = 0;
context.Main.inputMethod.content_purpose = 0;
context.Main.inputMethod.currentFocus = focus;
context.global.display.focus_window = window;
ticket = invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']);
assert.ok(ticket > 0);
assert.equal(invoke('CommitReplacementAsync', [ticket, 0xFFFFFFFF]), true);
assert.deepEqual(writes.splice(0), [['delete', -4, 4], ['commit', 'где ']]);

const forwarded = [];
const im = context.Main.inputMethod;
const originalKey = im.notify_key_event = event => { forwarded.push(event); return 19; };
const originalReset = im._fullReset = () => forwarded.push('reset');
const originalProcess = InputContext.prototype.process_key_event_async;
extension._watchReplacements();
ticket = invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']);
let processed = 0;
im._context.process_key_event_async(1, 1, 0, -1, null, () => processed++);
assert.equal(invoke('CommitReplacementAsync', [ticket, 1]), false);
assert.equal(invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']), 0);
keyCallbacks.shift()();
assert.equal(processed, 1);
ticket = invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']);
assert.ok(ticket > 0);
const key = {type: () => 1};
assert.equal(im.notify_key_event(key), 19);
assert.deepEqual(forwarded.splice(0), [key]);
assert.equal(invoke('CommitReplacementAsync', [ticket, 1]), false);
ticket = invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']);
im.currentFocus = null;
im._fullReset();
im.currentFocus = focus;
assert.equal(invoke('CommitReplacementAsync', [ticket, 1]), false);
assert.deepEqual(forwarded.splice(0), ['reset']);
(async () => {
    const processedKey = im._context.process_key_event_async(1, 1, 0, -1, null);
    assert.equal(invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']), 0);
    keyCallbacks.shift()(true);
    assert.equal(await processedKey, true);
    assert.ok(invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']) > 0);
    extension._unwatchReplacements();
    assert.equal(im.notify_key_event, originalKey);
    assert.equal(im._fullReset, originalReset);
    assert.equal(InputContext.prototype.process_key_event_async, originalProcess);
    assert.equal(invoke('PrepareReplacementAsync', [123, 'ult ', 'где ']), 0);
})();
