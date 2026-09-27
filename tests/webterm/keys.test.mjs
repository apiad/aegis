// Run: node tests/webterm/keys.test.mjs
import assert from "node:assert";
import { KEYS, ctrl } from "../../src/aegis/webterm/static/keys.js";

assert.strictEqual(KEYS.esc, "\x1b");
assert.strictEqual(KEYS.tab, "\t");
assert.strictEqual(KEYS.up, "\x1b[A");
assert.strictEqual(KEYS.left, "\x1b[D");
assert.strictEqual(ctrl("c"), "\x03");
assert.strictEqual(ctrl("C"), "\x03");
assert.strictEqual(ctrl("t"), "\x14");     // Ctrl+T opens a tab in aegis
assert.strictEqual(ctrl("["), "\x1b");
assert.strictEqual(ctrl("1"), "1");        // no control code: sent as typed
assert.strictEqual(ctrl("ab"), "ab");      // a paste is not a keystroke
console.log("keys.test.mjs: ok");
