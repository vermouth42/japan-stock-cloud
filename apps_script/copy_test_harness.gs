/** COPY ONLY. Place in the copy's otherwise empty コード.gs.
 * Simple onEdit: no installer, scopes, alert or production writes.
 * M4:M13 becomes a TEST receipt status. Restore original formulas after testing.
 */
const ASTRA_TEST_COPY_ID = '1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc';

function onEdit(e) {
  if (!e || !e.source || e.source.getId() !== ASTRA_TEST_COPY_ID) return;
  const sheet = e.source.getSheetByName(SBI_V77_SHEET);
  const properties = PropertiesService.getDocumentProperties();
  if (e.range.getSheet().getName() === SBI_V77_SHEET &&
      e.range.getA1Notation() === 'A1' && e.range.getValue() === 'ASTRA_CLEAR_TEST') {
    Object.keys(properties.getProperties()).filter(k => k.startsWith(ASTRA_RECEIPT_PREFIX))
      .forEach(k => properties.deleteProperty(k));
    return;
  }
  handleSbiV77Edit(e);
  for (let r = 4; r <= 13; r++) {
    const row = sheet.getRange(r, 2, 1, 13).getValues()[0];
    const record = astraReadRecord_(properties, ASTRA_RECEIPT_PREFIX + 'receipt.' + r);
    const stamp = row[12] instanceof Date ? row[12].getTime() : NaN;
    const valid = astraReceiptValid_(row, record, stamp, Date.now());
    sheet.getRange(r, 13).setValue(valid ? '検証記録OK・GO禁止' : '検証記録無効・GO禁止');
  }
}
