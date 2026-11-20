import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const helper = html.match(/function interpolateTouch\([\s\S]*?\r?\n}\r?\nwindow\.interpolateTouch = interpolateTouch;/);
assert.ok(helper, 'interpolation helper exists');
const context = { window: {} };
vm.runInNewContext(helper[0], context);
const f = context.window.interpolateTouch;
const k = [1, 2, 4, 20], odds = [.9, .7, .3, .01];
assert.equal(f(0, k, odds), .9);
assert.equal(f(1, k, odds), .9);
assert.equal(f(2, k, odds), .7);
assert.ok(Math.abs(f(3, k, odds) - .5) < 1e-12);
assert.ok(Math.abs(f(20, k, odds) - .01) < 1e-12);
assert.equal(f(30, k, odds), .01);
console.log('touch interpolation tests passed');


