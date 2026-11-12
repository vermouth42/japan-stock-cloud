'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('apps_script/sbi_receipt.gs', 'utf8');
let grid, store, acquired = true, failWrite = false;
const properties = {
  getProperty: k => store[k] || null,
  setProperty: (k, v) => { if (failWrite) throw Error('store failed'); store[k] = v; },
  deleteProperty: k => { delete store[k]; }, getProperties: () => ({...store})
};
const sheet = {getName: () => 'SBI連動_v77', getRange: (r, c, nr = 1, nc = 1) => ({
  getValues: () => grid.slice(r - 1, r - 1 + nr).map(row => row.slice(c - 1, c - 1 + nc)),
  setValue: value => { grid[r - 1][c - 1] = value; },
  clearContent: () => { grid[r - 1][c - 1] = ''; }
})};
const ctx = vm.createContext({Date, JSON, Number, String, Object, Array, Error,
  PropertiesService: {getDocumentProperties: () => properties},
  LockService: {getDocumentLock: () => ({tryLock: () => acquired, releaseLock: () => {}})}
});
vm.runInContext(source, ctx);
function reset() {
  grid = Array.from({length: 13}, () => Array(15).fill(''));
  store = {}; acquired = true; failWrite = false;
  for (let r = 4; r <= 13; r++) grid[r - 1] = ['', String(9900 + r), 'A', 100,
    100, 100, true, true, true, true, true, false, '', '', ''];
}
function edit(r, c, nr = 1, nc = 1) {
  ctx.handleSbiV77Edit({range: {getSheet: () => sheet, getRow: () => r,
    getLastRow: () => r + nr - 1, getColumn: () => c, getLastColumn: () => c + nc - 1,
    getNumRows: () => nr, getNumColumns: () => nc}});
}
function prime(r = 4) { edit(r, 5, 1, 2); edit(r, 8, 1, 4); }
function confirm(r = 4) { grid[r - 1][11] = true; edit(r, 12); }
function record(r = 4) { return JSON.parse(store['astra.sbi.v1.receipt.' + r] || 'null'); }
function valid(r = 4, now = Date.now()) {
  return ctx.astraReceiptValid_(grid[r - 1].slice(1, 14), record(r),
    grid[r - 1][13] instanceof Date ? +grid[r - 1][13] : NaN, now);
}
let count = 0;
function test(name, fn) { reset(); fn(); count++; console.log('ok ' + name); }
test('single checkbox alone cannot certify preexisting data', () => {
  confirm(); assert.equal(grid[3][11], false); assert.equal(record(), null);
});
test('explicit inputs and single confirmation create matching receipt', () => {
  prime(); confirm(); assert.equal(valid(), true);
});
test('bulk confirmation never attests multiple rows', () => {
  prime(); prime(5); grid[3][11] = grid[4][11] = true; edit(4, 12, 2);
  assert.equal(record(), null); assert.equal(record(5), null);
});
test('paste spanning header invalidates every affected row', () => {
  prime(); confirm(); prime(5); confirm(5); edit(2, 5, 4, 2);
  assert.equal(record(), null); assert.equal(record(5), null);
  assert.equal(grid[3][13], ''); assert.equal(grid[4][13], '');
});
test('formula-driven symbol change invalidates existing evidence', () => {
  prime(); confirm(); grid[3][1] = '285A'; assert.equal(valid(), false);
});
test('new symbol must have every field reentered', () => {
  prime(); grid[3][1] = '285A'; edit(4, 5); confirm(); assert.equal(record(), null);
  prime(); confirm(); assert.equal(valid(), true);
});
test('changing current price invalidates confirmation', () => {
  prime(); confirm(); grid[3][4] = 101; edit(4, 5); assert.equal(record(), null);
});
test('programmatic changes without onEdit cannot reuse receipt', () => {
  prime(); confirm(); grid[3][9] = false; assert.equal(valid(), false);
});
test('B rank and undersized shares cannot certify', () => {
  prime(); grid[3][2] = 'B'; confirm(); assert.equal(record(), null);
  grid[3][2] = 'A'; grid[3][3] = 99; confirm(); assert.equal(record(), null);
});
test('negative or text prices cannot certify', () => {
  grid[3][4] = grid[3][5] = -1; prime(); confirm(); assert.equal(record(), null);
  grid[3][4] = grid[3][5] = '100'; prime(); confirm(); assert.equal(record(), null);
});
test('TTL, timestamp mutation and midnight expire evidence', () => {
  prime(); confirm(); const rec = record(); assert.equal(valid(4, rec.at + 300001), false);
  grid[3][13] = new Date(rec.at + 1); assert.equal(valid(), false);
  assert.notEqual(ctx.astraJstDay_(Date.parse('2026-11-11T14:59:59Z')),
    ctx.astraJstDay_(Date.parse('2026-11-11T15:00:00Z')));
});
test('lock contention clears prior confirmation', () => {
  prime(); confirm(); acquired = false; edit(4, 5); assert.equal(record(), null);
});
test('property write failure clears checkbox and timestamp', () => {
  prime(); failWrite = true; assert.throws(() => confirm());
  assert.equal(grid[3][11], false); assert.equal(grid[3][13], '');
});
test('malformed stored inputs fail closed', () => {
  store['astra.sbi.v1.inputs.4'] = '{broken'; confirm(); assert.equal(record(), null);
});
console.log(count + ' scenarios passed');
