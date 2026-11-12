const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
    '../extension/wayswitch@wetoshkin.github.io/extension.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace('export default class WaySwitchExtension', 'class WaySwitchExtension');
let text = 'ghbdtn ';
let caps = false;
const swapCase = ch => ch === ch.toLowerCase() ? ch.toUpperCase() : ch.toLowerCase();
const inputQueue = [];
const pendingKeys = [];
const applyKey = key => {
    const ch = String.fromCodePoint(key);
    text = key === 65288 ? text.slice(0, -1) : text + (caps ? swapCase(ch) : ch);
};
const context = vm.createContext({
    Extension: class {},
    global: {stage: null},
    GLib: {get_monotonic_time: () => 1000000,
        unichar_tolower: ch => ch.toLowerCase(),
        unichar_toupper: ch => ch.toUpperCase()},
    Clutter: {KEY_BackSpace: 65288, unicode_to_keysym: value => value,
        get_default_backend: () => ({get_default_seat: () => ({
            get_keymap: () => ({get_caps_lock_state: () => caps}),
        })})},
    Main: {modalCount: 0, inputMethod: {
        currentFocus: {},
        forward_key(key, code, state, time, pressed) {
            assert.equal(code, 22);
            assert.equal(time, 1000000);
            if (pressed) pendingKeys.push(() => applyKey(key));
        },
        commit(value) { inputQueue.push(() => { text += value; }); },
        delete_surrounding() { throw Error('Cached IME cursor must not be used'); },
    }},
});
vm.runInContext(source + '\nthis.extension = new WaySwitchExtension();', context);
context.extension._daemon = ':daemon';
// Qt can deliver IME commits before queued key events, including forwarded keys.
context.extension._tap = key => {
    pendingKeys.push(() => applyKey(key));
};
function invoke(method, value) {
    context.extension[method]([value], {
        get_sender: () => ':daemon',
        return_value() {},
        return_dbus_error(name) { throw Error(name); },
    });
}
for (const [prefix, original, replacement] of [
    ['', 'ghbdtn ', 'привет '],
    ['', 'руддщ ', 'hello '],
    ['🚀: ', 'ghbdtn ', 'привет '],
]) {
    text = prefix + original;
    inputQueue.length = 0;
    pendingKeys.length = 0;
    invoke('BackspaceAsync', original.length);
    invoke('TypeTextAsync', replacement);
    for (const callback of [...inputQueue, ...pendingKeys]) callback();
    assert.equal(text, prefix + replacement);
    assert.equal(inputQueue.length, 0);
}

// Mutter derives Shift from the keysym level while the seat CapsLock stays active.
for (const enabled of [false, true]) {
    caps = enabled;
    for (const replacement of ['ПРИВЕТ ', 'привет ', 'Привет ', 'HELLO ', 'hello ', 'Hello, 123!']) {
        text = '';
        pendingKeys.length = 0;
        invoke('TypeTextAsync', replacement);
        for (const callback of pendingKeys) callback();
        assert.equal(text, replacement, `CapsLock=${enabled}`);
        assert.equal(caps, enabled);
    }
}
