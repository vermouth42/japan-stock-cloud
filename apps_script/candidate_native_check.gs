/** Manual native test, bound TEST COPY only; production read-only. No triggers. */
function verifyAstraCandidateNativeCopy() {
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  astraRequire_(ss && ss.getId()==='1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc','検証コピー専用');
  const prod=SpreadsheetApp.openById('1AQ0CcqOnyLHWSOnHR_m0FsvcvNFZIbJ8dRYzKuKU6Zk');
  const bundle=astraAutoRead_(prod);
  const result=astraAutoProject_(bundle,Date.now());
  astraRequire_(result.stocks===100 && result.candidates.length===10 && result.go_allowed===false,'件数不一致');
  astraAutoWrite_(ss,result);
  astraRequire_(ss.getSheetByName(ASTRA_AUTO_SHEET).getRange('F7:F16').getValues().every(r=>r[0]==='禁止'),'GO禁止確認失敗');
  // The copy has an older daily dataset; actual refresh must reject it.
  let rejected=false;
  try { astraRefreshCandidates(); } catch(e) { rejected=true; }
  astraRequire_(rejected,'古いコピーが拒否されない');
  const sh=ss.getSheetByName(ASTRA_AUTO_SHEET);
  astraRequire_(sh.getRange('B4').getValue()==='異常（GO禁止）','無効化状態不一致');
  astraRequire_(sh.getRange('A7:F16').getValues().every(r=>r.every(v=>v==='')),'過去候補残留');
  astraRequire_(!PropertiesService.getScriptProperties().getProperty(ASTRA_AUTO_PREFIX+'verified'),'失敗後の検証記録残留');
  console.log(JSON.stringify({native_test:'PASS',production_read_only:true,valid_snapshot_written_and_read_back:true,stale_copy_rejected:true,previous_candidates_cleared:true,stocks:100,candidates:10,go_allowed:false}));
}
