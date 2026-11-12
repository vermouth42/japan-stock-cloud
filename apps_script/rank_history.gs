/** Append-only observations of the user's Sheet rankings. No TV feed, orders or GO.
 * Requires automatic_candidates.gs and its shared functions.
 * An observation proves only what was seen at recorded_at, never earlier knowledge.
 */
const ASTRA_HISTORY_NAME = '判定履歴';
const ASTRA_HISTORY_HEADER = ['記録UTC','日足基準日','内容SHA256','前記録SHA256','記録SHA256','観測内容JSON'];
const ASTRA_HISTORY_HANDLER = 'astraRankHistoryTick';
function astraHistoryHash_(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,value,Utilities.Charset.UTF_8)
    .map(b=>('0'+((b+256)%256).toString(16)).slice(-2)).join('');
}
function astraHistoryPayload_(bundle, now) {
  const result=astraAutoProject_(bundle,now);
  const ranks=astraAutoTable_(bundle.ranking,ASTRA_AUTO_RANK_HEADER)
    .map(r=>({symbol:r['コード'],grade:r['BUY'],score:r['最終Score']})).sort((a,b)=>a.symbol.localeCompare(b.symbol));
  const events=astraAutoTable_(bundle.events,ASTRA_AUTO_EVENT_HEADER)
    .map(e=>({symbol:e['コード'],day:astraAutoDate_(e['イベント日'])})).sort((a,b)=>(a.symbol+a.day).localeCompare(b.symbol+b.day));
  // No free text, external feed or account values are persisted here.
  return {schema:'astra.rank-observation.v1',source:'Ver7.5 Sheet',market_date:result.market_date,
    universe:ranks.map(r=>r.symbol),ranks,candidates:result.candidates.map(c=>({symbol:c.symbol,grade:c.grade,shares:c.shares})),
    known_events:events,event_coverage_verified:false,rank_first_published_at:null,go_allowed:false};
}
function astraHistoryAppend_(ss,payload,now) {
  astraRequire_(ASTRA_FINAL_BOOKS.includes(ss.getId()),'履歴出力先不正');
  astraRequire_(payload.schema==='astra.rank-observation.v1'&&payload.go_allowed===false,'履歴スキーマ不正');
  const content=JSON.stringify(payload),digest=astraHistoryHash_(content),at=new Date(now).toISOString();
  astraRequire_(content.length<45000,'履歴サイズ上限');
  let sh=ss.getSheetByName(ASTRA_HISTORY_NAME);
  if(!sh){sh=ss.insertSheet(ASTRA_HISTORY_NAME);sh.getRange(1,1,1,6).setValues([ASTRA_HISTORY_HEADER]);sh.setFrozenRows(1);}
  astraRequire_(JSON.stringify(sh.getRange(1,1,1,6).getValues()[0])===JSON.stringify(ASTRA_HISTORY_HEADER),'履歴見出し不一致');
  const last=sh.getLastRow();let previous='GENESIS';
  if(last>1){
    const row=sh.getRange(last,1,1,6).getValues()[0];
    astraRequire_(row.every(v=>typeof v==='string')&&row[4]===astraHistoryHash_(JSON.stringify([row[0],row[1],row[2],row[3],row[5]]))&&row[2]===astraHistoryHash_(row[5]),'直前履歴破損');
    astraRequire_(Date.parse(row[0])<=now,'履歴時刻逆転');
    // A daily heartbeat records continuity, not an earlier publication time.
    if(row[2]===digest&&astraJstDay_(Date.parse(row[0]))===astraJstDay_(now))return {appended:false,unchanged:true,go_allowed:false};
    previous=row[4];
  }
  const hash=astraHistoryHash_(JSON.stringify([at,payload.market_date,digest,previous,content]));
  const record=[at,payload.market_date,digest,previous,hash,content];
  const range=sh.getRange(last+1,1,1,6);range.setNumberFormat('@').setValues([record]);SpreadsheetApp.flush();
  astraRequire_(JSON.stringify(range.getValues()[0])===JSON.stringify(record),'履歴読み戻し不一致');
  return {appended:true,row:last+1,observed_at:at,stocks:payload.universe.length,go_allowed:false};
}
function astraRecordRankHistory() {
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {skipped:true,go_allowed:false};
  try{
    const ss=astraAutoBook_(),bundle=astraAutoRead_(ss),now=Date.now();
    const result=astraHistoryAppend_(ss,astraHistoryPayload_(bundle,now),now);
    PropertiesService.getScriptProperties().setProperty('astra.history.verified',JSON.stringify({book:ss.getId(),at:Date.now()}));
    return result;
  }catch(e){PropertiesService.getScriptProperties().deleteProperty('astra.history.verified');throw new Error('判定履歴の記録失敗・既存履歴は保持');}
  finally{lock.releaseLock();}
}
function astraRankHistoryTick(){
  const now=Date.now(),h=new Date(now+32400000).getUTCHours();
  if(!astraTradingDay_(astraJstDay_(now))||h<7||h>=22)return;
  return astraRecordRankHistory();
}
function installAstraRankHistoryTrigger(){
  const lock=LockService.getScriptLock();astraRequire_(lock.tryLock(1000),'別処理実行中');
  try{
    const ss=astraAutoBook_(),v=JSON.parse(PropertiesService.getScriptProperties().getProperty('astra.history.verified')||'null');
    astraRequire_(v&&v.book===ss.getId()&&v.at<=Date.now()&&Date.now()-v.at<300000,'先に履歴記録成功が必要');
    const existing=ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()===ASTRA_HISTORY_HANDLER);
    astraRequire_(existing.length<=1,'履歴トリガー重複');
    if(existing.length)return '既存の履歴トリガーを維持';
    ScriptApp.newTrigger(ASTRA_HISTORY_HANDLER).timeBased().everyMinutes(15).create();
    return '履歴記録を15分間隔に設定';
  }finally{lock.releaseLock();}
}
