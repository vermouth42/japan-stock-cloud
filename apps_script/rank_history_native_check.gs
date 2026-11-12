/** Run only on the isolated copy. Production read-only; no test trigger. */
function verifyAstraRankHistoryNativeCopy(){
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  astraRequire_(ss.getId()==='1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc','検証コピー限定');
  const source=SpreadsheetApp.openById('1AQ0CcqOnyLHWSOnHR_m0FsvcvNFZIbJ8dRYzKuKU6Zk');
  const bundle=astraAutoRead_(source),now=Date.now(),payload=astraHistoryPayload_(bundle,now);
  const first=astraHistoryAppend_(ss,payload,now),again=astraHistoryAppend_(ss,payload,now+1);
  astraRequire_(again.unchanged===true,'重複抑止失敗');
  const sh=ss.getSheetByName(ASTRA_HISTORY_NAME),rows=sh.getRange(2,1,sh.getLastRow()-1,6).getValues();
  let parent='GENESIS';
  rows.forEach(r=>{astraRequire_(r[3]===parent&&r[2]===astraHistoryHash_(r[5])&&r[4]===astraHistoryHash_(JSON.stringify([r[0],r[1],r[2],r[3],r[5]])),'履歴チェーン不一致');parent=r[4];});
  console.log(JSON.stringify({native_history:'PASS',stocks:payload.universe.length,records:rows.length,deduplicated:true,production_read_only:true,go_allowed:false}));
}
