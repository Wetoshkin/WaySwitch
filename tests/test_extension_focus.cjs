const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname,
    '../extension/wayswitch@siberia.ru/extension.js'), 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace('export default class WaySwitchExtension', 'class WaySwitchExtension');
const context = vm.createContext({Extension: class {}, global: {display: {focus_window: null}}});
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
