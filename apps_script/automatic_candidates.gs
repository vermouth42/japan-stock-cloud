/** Candidate observation only. No quotes, messaging, orders or live GO.
 * Deploy with final_gate_config.gs, final_gate.gs and sbi_receipt.gs.
 * First validate on the named copy. Installer requires a recent successful run.
 */
const ASTRA_AUTO_SHEET = '自動候補照合';
const ASTRA_AUTO_MARKER = 'Astra 自動候補照合 v1｜実戦GO禁止';
const ASTRA_AUTO_PREFIX = 'astra.candidates.v1.';
const ASTRA_AUTO_HANDLER = 'astraCandidateTick';
const ASTRA_AUTO_RANK_HEADER = ['順位','コード','最新日','終値','前日比%','出来高倍率','VWAP判定','25日高値','ATR%','売買代金','監視Score','アクション','材料確認','材料カテゴリ','有効材料Score','最終Score','BUY','信用'];
const ASTRA_AUTO_TOP_HEADER = ['順位','コード','最終Score','BUY','信用','終値','ATR%','損切り%','100株資金','100株損失','リスク上限株数','資金上限株数','推奨株数','実戦判定','材料状況','材料カテゴリ','有効材料Score'];
const ASTRA_AUTO_EVENT_HEADER = ['コード','銘柄名','イベント日','種別','時刻','確度','残日数','警戒','メモ'];

function astraAutoDate_(v) {
  if (typeof v === 'number') {
    astraRequire_(Number.isInteger(v) && v>0 && v<100000,'日付数値不正');
    v=new Date((v-25569)*86400000-32400000);
  }
  return astraDay_(typeof v==='string' ? v.replace(/\//g,'-') : v);
}
function astraAutoTable_(rows, header) {
  astraRequire_(Array.isArray(rows)&&JSON.stringify(rows[0])===JSON.stringify(header),'見出し不一致');
  return rows.slice(1).filter(r=>r.some(v=>v!==''&&v!==null)).map(r=>{
    astraRequire_(Array.isArray(r)&&r.length<=header.length,'列数不正');
    const code=astraSymbol_(r[header.indexOf('コード')]);
    astraRequire_(code,'銘柄コード不正');
    const obj={};header.forEach((k,i)=>obj[k]=r[i]===undefined?null:r[i]);
    obj['コード']=code;return obj;
  });
}
function astraAutoIndex_(rows) {
  const out=new Map();
  rows.forEach(r=>{astraRequire_(!out.has(r['コード']),'銘柄重複');out.set(r['コード'],r);});
  return out;
}
function astraAutoProject_(bundle, now) {
  astraRequire_(ASTRA_FINAL_BOOKS.includes(bundle.book)&&bundle.timezone==='Asia/Tokyo','対象ブック・時差不正');
  astraRequire_([bundle.begin,bundle.end,now].every(astraNum_)&&bundle.begin<=bundle.end&&bundle.end<=now&&
    bundle.end-bundle.begin<=30000&&now-bundle.begin<300000,'取得時刻・鮮度不正');
  const codes=ASTRA_GATE_CODES, expected=astraExpectedDay_(now),today=astraJstDay_(now),limit=astraThirdSession_(today);
  astraRequire_(codes.length===100&&new Set(codes).size===100,'監視対象不正');
  const ranks=astraAutoIndex_(astraAutoTable_(bundle.ranking,ASTRA_AUTO_RANK_HEADER));
  const tops=astraAutoIndex_(astraAutoTable_(bundle.top10,ASTRA_AUTO_TOP_HEADER));
  const events=astraAutoTable_(bundle.events,ASTRA_AUTO_EVENT_HEADER);
  astraRequire_(ranks.size===100&&codes.every(c=>ranks.has(c))&&tops.size===10,'100銘柄・候補数不正');
  ranks.forEach(r=>astraRequire_(astraAutoDate_(r['最新日'])===expected&&astraNum_(r['最終Score'])&&
    ['A+','A','B','C'].includes(r['BUY']),'基準日・Score・ランク不正'));
  events.forEach(e=>{
    astraRequire_(codes.includes(e['コード'])||e['コード']==='0000','イベント銘柄不正');
    e.day=astraAutoDate_(e['イベント日']);astraTradingDay_(e.day);
  });
  const candidates=[],scores=[];
  tops.forEach((t,code)=>{
    const r=ranks.get(code),qty=t['推奨株数'];
    astraRequire_(r&&['BUY','最終Score','終値','信用'].every(k=>t[k]===r[k]),'候補と元データ不一致');
    astraRequire_(Number.isInteger(qty)&&qty>=0&&qty%100===0,'推奨株数不正');scores.push(t['最終Score']);
    const near=events.filter(e=>(e['コード']===code||e['コード']==='0000')&&e.day>=today&&e.day<=limit);
    const reasons=[];
    if(!['A','A+'].includes(t['BUY']))reasons.push('A/A+以外');
    if(qty<100)reasons.push('100株未満');
    if(near.length)reasons.push('イベント3営業日以内: '+near.map(e=>e.day).join(', '));
    candidates.push({symbol:code,grade:t['BUY'],shares:qty,screening:reasons.length===0,reasons,go_allowed:false});
  });
  const best=[...ranks.values()].map(r=>r['最終Score']).sort((a,b)=>b-a).slice(0,10);
  astraRequire_(JSON.stringify(scores)===JSON.stringify(best),'100銘柄からのTOP10抽出不一致');
  return {ok:true,market_date:expected,at:bundle.end,expires:bundle.end+600000,stocks:100,
    screening_count:candidates.filter(c=>c.screening).length,candidates,go_allowed:false};
}
function astraAutoBook_() {
  const p=PropertiesService.getScriptProperties(),id=p.getProperty(ASTRA_AUTO_PREFIX+'book');
  const active=SpreadsheetApp.getActiveSpreadsheet();
  astraRequire_(!id||!active||active.getId()===id,'実行元と登録ブックが不一致');
  const ss=id?SpreadsheetApp.openById(id):active;
  astraRequire_(ss&&ASTRA_FINAL_BOOKS.includes(ss.getId())&&ss.getSpreadsheetTimeZone()==='Asia/Tokyo','対象ブック不明');
  return ss;
}
function astraAutoRead_(ss) {
  const begin=Date.now();
  function read(name,range){const s=ss.getSheetByName(name);astraRequire_(s,'参照シート不足');return s.getRange(range).getValues();}
  const ranking=read('監視ランキング','A3:R103'),top10=read('実戦候補TOP10','A1:Q11'),events=read('イベント管理','A1:I100');
  return {book:ss.getId(),timezone:ss.getSpreadsheetTimeZone(),begin,end:Date.now(),ranking,top10,events};
}
function astraAutoWrite_(ss,result) {
  let sh=ss.getSheetByName(ASTRA_AUTO_SHEET);
  if(sh)astraRequire_(sh.getRange('A1').getValue()===ASTRA_AUTO_MARKER,'出力先に別の内容あり・変更中止');
  else {
    sh=ss.insertSheet(ASTRA_AUTO_SHEET);
    sh.getRange('A1:F16').setFontSize(11).setVerticalAlignment('middle').setWrap(true);
    sh.setFrozenRows(6);sh.setColumnWidths(1,4,110);sh.setColumnWidth(5,300);sh.setColumnWidth(6,100);
    sh.getRange('A1:F1').setBackground('#17365d').setFontColor('#ffffff').setFontWeight('bold');
    sh.getRange('A6:F6').setBackground('#d9e7f5').setFontWeight('bold');
    sh.getRange('B3').setNumberFormat('yyyy/mm/dd hh:mm:ss');sh.getRange('D3').setNumberFormat('yyyy/mm/dd hh:mm:ss');
  }
  const rows=Array.from({length:16},()=>Array(6).fill(''));
  rows[0][0]=ASTRA_AUTO_MARKER;
  rows[1][0]='条件照合のみ。価格・板・口座・イベント調査の接続は未完了。';
  rows[2]=['最終取得',new Date(result.at),'有効期限',result.ok?new Date(result.expires):'無効','監視数',result.ok?100:0];
  rows[3]=['取得状態',result.ok?'照合済（GO禁止）':'異常（GO禁止）','日足基準日',result.market_date||'','条件通過',result.ok?result.screening_count:0];
  rows[4][0]=result.ok?'有効期限を過ぎた表示は無効。取得時刻は売買条件の確認日時ではありません。':'取得・照合に失敗しました。過去の候補を無効化しました。';
  rows[5]=['コード','BUY','推奨株数','条件照合','見送り理由','実戦GO'];
  (result.candidates||[]).forEach((c,i)=>rows[i+6]=[c.symbol,c.grade,c.shares,c.screening?'観測対象':'見送り',
    c.reasons.length?c.reasons.join(' / '):'実価格・板・口座・調査範囲の接続待ち','禁止']);
  rows.forEach(r=>r.forEach((v,i)=>{if(typeof v==='string'&&v.startsWith('='))r[i]="'"+v;}));
  // Sheets otherwise coerces numeric stock codes and ISO day strings on write.
  // Apply on every run, including an output sheet created by an older version.
  sh.getRange('A7:A16').setNumberFormat('@');
  sh.getRange('D4').setNumberFormat('@');
  sh.getRange('A1:F16').setValues(rows);
  SpreadsheetApp.flush();
  const written=sh.getRange('A1:F16').getValues();
  astraRequire_(JSON.stringify(written)===JSON.stringify(rows),'候補出力の読み戻し不一致');
}
function astraRefreshCandidates() {
  const lock=LockService.getScriptLock();
  if(!lock.tryLock(1000))return {ok:false,skipped:'同期・別の照合が実行中',go_allowed:false};
  let ss;
  try {
    ss=astraAutoBook_();
    const result=astraAutoProject_(astraAutoRead_(ss),Date.now());
    astraAutoWrite_(ss,result);
    PropertiesService.getScriptProperties().setProperty(ASTRA_AUTO_PREFIX+'verified',JSON.stringify({book:ss.getId(),at:Date.now()}));
    return result;
  } catch(e) {
    PropertiesService.getScriptProperties().deleteProperty(ASTRA_AUTO_PREFIX+'verified');
    if(ss)astraAutoWrite_(ss,{ok:false,at:Date.now(),candidates:[],go_allowed:false});
    // Never put upstream content, credentials or arbitrary cell text in logs.
    throw new Error('自動候補照合に失敗・GO禁止');
  } finally {lock.releaseLock();}
}
function astraCandidateTick() {
  const now=Date.now(),local=new Date(now+32400000),hour=local.getUTCHours();
  if(!astraTradingDay_(astraJstDay_(now))||hour<7||hour>=22)return;
  return astraRefreshCandidates();
}
function installAstraCandidateTrigger() {
  const lock=LockService.getScriptLock();astraRequire_(lock.tryLock(1000),'別の処理が実行中');
  try {
  const ss=astraAutoBook_(),p=PropertiesService.getScriptProperties();
  const verified=JSON.parse(p.getProperty(ASTRA_AUTO_PREFIX+'verified')||'null');
  astraRequire_(verified&&verified.book===ss.getId()&&astraNum_(verified.at)&&verified.at<=Date.now()&&Date.now()-verified.at<300000,'先に候補照合の実行と確認が必要');
  const existing=ScriptApp.getProjectTriggers().filter(t=>t.getHandlerFunction()===ASTRA_AUTO_HANDLER);
  astraRequire_(existing.length<=1,'候補照合のトリガー重複・手動点検が必要');
  p.setProperty(ASTRA_AUTO_PREFIX+'book',ss.getId());
  if(existing.length)return '候補照合トリガーは既に存在（既存設定を維持）';
  ScriptApp.newTrigger(ASTRA_AUTO_HANDLER).timeBased().everyMinutes(5).create();
  return '候補照合を5分間隔に設定。既存の同期・通知トリガーは変更なし。';
  } finally {lock.releaseLock();}
}
