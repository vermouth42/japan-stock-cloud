/** Candidate replacement for sbi_v77.gs. Copy-only until native tests pass.
 * Records evidence of an SBI confirmation; never grants GO or sends orders.
 * Keep the existing installed edit/reset triggers. Do not run an installer.
 */
const SBI_V77_SHEET = 'SBI連動_v77';
const ASTRA_RECEIPT_PREFIX = 'astra.sbi.v1.';
const ASTRA_INPUT_COLS = [5, 6, 8, 9, 10, 11];

function astraSymbol_(value) {
  const s = String(value).trim().toUpperCase();
  return /^[0-9]{3}[0-9A-Z]$/.test(s) ? s : '';
}

function astraJstDay_(ms) {
  return new Date(ms + 9 * 3600000).toISOString().slice(0, 10);
}

function astraSnapshot_(row) {
  // B:K, excluding calculated VWAP flag G. JSON preserves native types.
  return JSON.stringify([row[0], row[1], row[2], row[3], row[4],
    row[6], row[7], row[8], row[9]]);
}

function astraInputsReady_(row, inputs, now) {
  const symbol = astraSymbol_(row[0]);
  if (!symbol || !['A', 'A+'].includes(row[1]) ||
      !Number.isInteger(row[2]) || row[2] < 100 || row[2] % 100) return false;
  if (![row[3], row[4]].every(x => typeof x === 'number' && Number.isFinite(x) && x > 0) ||
      row[3] < row[4] || !row.slice(6, 10).every(x => x === true)) return false;
  return ASTRA_INPUT_COLS.every(col => {
    const entry = inputs[String(col)];
    return entry && entry.symbol === symbol && entry.value === row[col - 2] &&
      Number.isFinite(entry.at) && entry.at <= now && now - entry.at <= 300000 &&
      astraJstDay_(entry.at) === astraJstDay_(now);
  });
}

function astraReceiptValid_(row, record, timestampMs, now) {
  // Called by a future common gate with the CURRENT row, not a cached row.
  // Formula-driven sorting does not emit onEdit; value comparison is mandatory.
  return !!record && row[10] === true &&
    record.symbol === astraSymbol_(row[0]) &&
    record.snapshot === astraSnapshot_(row) &&
    record.at === timestampMs && Number.isFinite(record.at) &&
    record.at <= now && now - record.at <= 300000 &&
    astraJstDay_(record.at) === astraJstDay_(now) &&
    astraInputsReady_(row, record.inputs || {}, now);
}

function astraReadRecord_(properties, key) {
  try { return JSON.parse(properties.getProperty(key) || '{}'); }
  catch (_) { return {}; }
}

function astraInvalidateRow_(sheet, row, properties) {
  sheet.getRange(row, 12).setValue(false);
  sheet.getRange(row, 14).clearContent();
  properties.deleteProperty(ASTRA_RECEIPT_PREFIX + 'receipt.' + row);
}

function handleSbiV77Edit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() !== SBI_V77_SHEET) return;
  const first = Math.max(4, e.range.getRow());
  const last = Math.min(13, e.range.getLastRow());
  const left = e.range.getColumn(), right = e.range.getLastColumn();
  if (first > last || right < 2 || left > 14) return;
  const properties = PropertiesService.getDocumentProperties();
  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(10000)) {
    // Invalidate rather than leave a prior green checkbox on lock contention.
    for (let r = first; r <= last; r++) astraInvalidateRow_(sheet, r, properties);
    return;
  }
  try {
    const now = Date.now();
    const singleConfirmation = first === last && e.range.getNumRows() === 1 &&
      e.range.getNumColumns() === 1 && left === 12;
    for (let r = first; r <= last; r++) {
      const row = sheet.getRange(r, 2, 1, 13).getValues()[0];
      const key = ASTRA_RECEIPT_PREFIX + 'inputs.' + r;
      let inputs = astraReadRecord_(properties, key);
      if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) inputs = {};
      if (singleConfirmation && row[10] === true && astraInputsReady_(row, inputs, now)) {
        const receipt = {symbol: astraSymbol_(row[0]), snapshot: astraSnapshot_(row), at: now, inputs};
        properties.setProperty(ASTRA_RECEIPT_PREFIX + 'receipt.' + r, JSON.stringify(receipt));
        sheet.getRange(r, 14).setValue(new Date(now));
        // Re-read formulas after writing: reject a concurrent ranking change.
        const current = sheet.getRange(r, 2, 1, 13).getValues()[0];
        if (!astraReceiptValid_(current, receipt, now, Date.now()))
          astraInvalidateRow_(sheet, r, properties);
      } else {
        astraInvalidateRow_(sheet, r, properties);
        // Direct edits to identity, derived columns or timestamp cannot attest data.
        if (left <= 4 || (left <= 7 && right >= 7) || right >= 13) inputs = {};
        else for (const col of ASTRA_INPUT_COLS) {
          if (left <= col && col <= right) inputs[String(col)] = {
            symbol: astraSymbol_(row[0]), value: row[col - 2], at: now};
        }
        properties.setProperty(key, JSON.stringify(inputs));
      }
    }
  } catch (error) {
    for (let r = first; r <= last; r++) astraInvalidateRow_(sheet, r, properties);
    throw error;
  } finally { lock.releaseLock(); }
}

function resetSbiV77Daily() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SBI_V77_SHEET);
  if (!sheet) throw new Error('SBI連動シートが見つかりません');
  const lock = LockService.getDocumentLock();
  lock.waitLock(10000);
  try {
    sheet.getRange('H4:L13').setValue(false);
    sheet.getRange('N4:N13').clearContent();
    sheet.getRange('E4:F13').clearContent();
    const properties = PropertiesService.getDocumentProperties();
    Object.keys(properties.getProperties()).filter(k => k.startsWith(ASTRA_RECEIPT_PREFIX))
      .forEach(k => properties.deleteProperty(k));
  } finally { lock.releaseLock(); }
}
