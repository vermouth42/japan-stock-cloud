/** Server-side, manual-entry decision. No order, webhook or messaging calls.
 * A GO is a time-limited snapshot, not a continuously verified quote.
 * Deploy alongside sbi_receipt.gs and final_gate_config.gs only after tests.
 */
const ASTRA_FINAL_SHEET = '発注前資金確認';
const ASTRA_FINAL_PREFIX = 'astra.final.v1.';
const ASTRA_FINAL_INPUT_ROWS = [10,11,12,13,14,15,16,17,18];
const ASTRA_FINAL_BOOKS = ['1AQ0CcqOnyLHWSOnHR_m0FsvcvNFZIbJ8dRYzKuKU6Zk',
  '1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc'];

function astraRequire_(ok, message) { if (!ok) throw new Error(message); }
function astraDay_(value) {
  if (value instanceof Date) {
    astraRequire_(Number.isFinite(+value), '日付不正');
    const shifted = new Date(+value + 32400000);
    astraRequire_(shifted.toISOString().slice(11) === '00:00:00.000Z', '日付に時刻が混在');
    return shifted.toISOString().slice(0,10);
  }
  astraRequire_(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value), '日付形式不正');
  const ms = Date.parse(value + 'T00:00:00Z');
  astraRequire_(Number.isFinite(ms) && new Date(ms).toISOString().slice(0,10) === value, '日付不正');
  return value;
}
function astraMoveDay_(day, delta) { return new Date(Date.parse(day+'T00:00:00Z')+delta*86400000).toISOString().slice(0,10); }
function astraTradingDay_(day) {
  const y = Number(astraDay_(day).slice(0,4));
  astraRequire_(y >= ASTRA_GATE_CALENDAR.first_year && y <= ASTRA_GATE_CALENDAR.last_year, 'JPX休日範囲外');
  const wd = new Date(day+'T00:00:00Z').getUTCDay();
  return wd !== 0 && wd !== 6 && !ASTRA_GATE_CALENDAR.closed_dates.includes(day);
}
function astraExpectedDay_(now) {
  const local = new Date(now+32400000);
  let day = local.toISOString().slice(0,10);
  if (local.getUTCHours()<18) day=astraMoveDay_(day,-1);
  while (!astraTradingDay_(day)) day=astraMoveDay_(day,-1);
  return day;
}
function astraThirdSession_(day) {
  astraTradingDay_(day);
  for(let n=0;n<3;) {day=astraMoveDay_(day,1); if(astraTradingDay_(day))n++;}
  return day;
}
function astraEntryWindow_(now) {
  const local=new Date(now+32400000), m=local.getUTCHours()*60+local.getUTCMinutes();
  return astraTradingDay_(astraJstDay_(now)) && (m>=545&&m<685 || m>=750&&m<895);
}
function astraCanonical_(v) { return v instanceof Date ? v.toISOString() : v; }
function astraSerial_(v) { return (+v+32400000)/86400000+25569; }
function astraNum_(v) {return typeof v==='number'&&Number.isFinite(v);}
function astraFresh_(at, now) {return astraNum_(at)&&at<=now&&now-at<=300000&&astraJstDay_(at)===astraJstDay_(now);}
function astraHash_(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,JSON.stringify(value),Utilities.Charset.UTF_8)
    .map(b=>(b<0?b+256:b).toString(16).padStart(2,'0')).join('');
}

function astraAuditRows_(status, raw, imported, now) {
  const expected=astraExpectedDay_(now), codes=ASTRA_GATE_CODES;
  astraRequire_(codes.length===100&&new Set(codes).size===100,'監視対象不正');
  astraRequire_(status&&status.stocks_total===100&&status.stocks_success===100&&status.stocks_failed===0&&
    status.rows===raw.length&&Array.isArray(status.failed)&&status.failed.length===0,'取得ステータス不正');
  astraRequire_(typeof status.updated_at_jst==='string'&&/([+-]\d{2}:\d{2}|Z)$/.test(status.updated_at_jst)&&
    Number.isFinite(Date.parse(status.updated_at_jst))&&Date.parse(status.updated_at_jst)<=now,'取得日時不正');
  astraRequire_(status.latest_market_date===expected,'日足が未更新・未来日');
  const per={}, seen=new Set(); codes.forEach(c=>per[c]=[]);
  for(const r of raw) {
    astraRequire_(Array.isArray(r)&&r.length===17&&codes.includes(r[1]),'CSV列・銘柄不正');
    const day=astraDay_(r[0]), key=day+'|'+r[1];
    astraRequire_(day<=expected&&!seen.has(key),'CSV未来日・重複'); seen.add(key);
    astraRequire_(r.slice(2).every(v=>v===''||typeof v==='string'&&/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(v)), 'CSV数値書式不正');
    const numeric=r.slice(2).map(v=>v===''?'':Number(v));
    astraRequire_(numeric.every(v=>v===''||astraNum_(v)),'CSV数値不正');
    const [o,h,l,c]=numeric, volume=numeric[8];
    astraRequire_([o,h,l,c].every(v=>astraNum_(v)&&v>0)&&l<=Math.min(o,c)&&Math.max(o,c)<=h&&
      Number.isInteger(volume)&&volume>=0,'OHLCV不正');
    per[r[1]].push([day,r[1],numeric]);
  }
  const target=new Map();
  for(const code of codes) {
    const rs=per[code].sort((a,b)=>b[0].localeCompare(a[0]));
    astraRequire_(rs.length>=45&&rs[0][0]===expected,'銘柄別の最新日・行数不足');
    for(const [day,c,nums] of rs.slice(0,45)) target.set(day+'|'+c,[day,'',c,...nums]);
  }
  astraRequire_(Array.isArray(imported)&&imported.length===4500,'取込4500行不足');
  const used=new Set();
  for(const r of imported) {
    astraRequire_(Array.isArray(r)&&r.length===18,'取込列数不正');
    const day=r[0] instanceof Date?astraDay_(r[0]):astraDay_(new Date((r[0]-25569)*86400000-32400000));
    const code=astraSymbol_(r[2]),key=day+'|'+code, ref=target.get(key);
    astraRequire_(ref&&!used.has(key),'取込銘柄・日付・重複不正'); used.add(key);
    astraRequire_(r[1]==='','取込B列不正');
    for(let i=3;i<18;i++) {
      astraRequire_(ref[i]==='' ? r[i]==='' : astraNum_(r[i])&&Math.abs(ref[i]-r[i])<=1e-8,'取込内容不一致');
    }
  }
  return {market_date:expected, rows:4500, stocks:100, duplicates:0};
}

function astraEvaluateFinal_(state, meta, sbiReceipt, now) {
  const f=state.form, r=state.sbi, t=state.top, code=astraSymbol_(f[2]);
  astraRequire_(astraEntryWindow_(now),'新規候補の確認時間外');
  astraRequire_(code&&ASTRA_GATE_CODES.includes(code)&&code===astraSymbol_(f[9])&&
    code===astraSymbol_(r[0])&&code===astraSymbol_(t[1]),'銘柄不一致');
  astraRequire_(['A','A+'].includes(t[3])&&r[1]===t[3]&&r[2]===t[12],'A/A+・株数情報不一致');
  astraRequire_(astraReceiptValid_(r,sbiReceipt,r[12] instanceof Date?+r[12]:NaN,now),'SBI確認なし・変更・期限切れ');
  for(const row of ASTRA_FINAL_INPUT_ROWS) {
    const entry=meta[String(row)];
    astraRequire_(entry&&entry.symbol===code&&entry.value===astraCanonical_(f[row-1])&&astraFresh_(entry.at,now),'資金・イベント入力の再確認が必要');
  }
  const [stop,allowed,remaining,cash,cost]=f.slice(10,15),price=r[3],vwap=r[4],qty=r[2];
  astraRequire_([stop,allowed,remaining,cash,cost,price,vwap].every(astraNum_)&&
    Math.min(stop,allowed,remaining,cash,price,vwap)>0&&cost>=0&&stop<price&&price>=vwap,'価格・損切り・余力不正');
  astraRequire_(Number.isInteger(qty)&&qty>=100&&qty%100===0,'100株単位外');
  const through=astraDay_(f[15]),today=astraJstDay_(now),limit=astraThirdSession_(today);
  astraTradingDay_(through);
  astraRequire_(through>=limit&&typeof f[16]==='string'&&/^https:\/\/[^\s]+$/.test(f[16])&&
    typeof f[17]==='string'&&f[17].trim().length>=3,'イベント調査期限・根拠不足');
  for(const e of state.events) {
    if(e.every(v=>v===''))continue;
    const eventCode=astraSymbol_(e[0]);
    astraRequire_(eventCode,'イベントコード不明');
    if(eventCode!==code&&eventCode!=='0000')continue;
    const day=astraDay_(e[2] instanceof Date?e[2]:String(e[2]).replace(/\//g,'-'));
    astraTradingDay_(day);
    astraRequire_(day<today||day>limit,'重要イベント3営業日以内');
  }
  const risk=price-stop+cost;
  const shares=Math.min(qty,Math.floor(Math.min(allowed,remaining)/(risk*100))*100,
    Math.floor(cash/((price+cost)*100))*100);
  astraRequire_(shares>=100,'余力・損失予算が100株未満');
  const times=[sbiReceipt.at,...Object.values(sbiReceipt.inputs).map(x=>x.at),...Object.values(meta).map(x=>x.at)];
  const local=new Date(now+32400000),endMinute=local.getUTCHours()<12?685:895;
  const end=Date.parse(today+'T00:00:00+09:00')+endMinute*60000;
  // One-minute display buffer before the true 5-minute evidence TTL.
  const expires=Math.min(...times.map(x=>x+240000),end-60000);
  astraRequire_(expires>now,'確認期限間近・入力を更新');
  return {symbol:code,shares,planned_loss:shares*risk,required_cash:shares*(price+cost),at:now,expires};
}

function astraReadFinalState_(ss) {
  astraRequire_(ASTRA_FINAL_BOOKS.includes(ss.getId())&&ss.getSpreadsheetTimeZone()==='Asia/Tokyo','対象ブック・時差不正');
  const form=ss.getSheetByName(ASTRA_FINAL_SHEET).getRange('B1:B18').getValues().flat();
  const code=astraSymbol_(form[2]);
  const allSbi=ss.getSheetByName(SBI_V77_SHEET).getRange('B4:N13').getValues();
  const allTop=ss.getSheetByName('実戦候補TOP10').getRange('A2:Q11').getValues();
  const sbis=allSbi.map((r,i)=>({r,i})).filter(x=>astraSymbol_(x.r[0])===code);
  const tops=allTop.map((r,i)=>({r,i})).filter(x=>astraSymbol_(x.r[1])===code);
  astraRequire_(code&&sbis.length===1&&tops.length===1,'候補コードがない・重複');
  astraRequire_(new Set(allSbi.map(r=>astraSymbol_(r[0]))).size===10&&
    new Set(allTop.map(r=>astraSymbol_(r[1]))).size===10&&
    allTop.every(t=>allSbi.some(s=>astraSymbol_(s[0])===astraSymbol_(t[1]))),'TOP10とSBIの不整合');
  const holidayDates=ss.getSheetByName('JPX休場日').getRange('A3:A43').getValues().flat().map(astraDay_).sort();
  astraRequire_(JSON.stringify(holidayDates)===JSON.stringify([...ASTRA_GATE_CALENDAR.closed_dates].sort()),'JPX休日表不一致');
  const ev=ss.getSheetByName('イベント管理').getRange('A2:I100');
  const eventCells=ev.getDisplayValues().flat();
  astraRequire_(eventCells.every(v=>!v.includes('␞')), 'イベント区切り文字が混在');
  sbis[0].r[11]=''; // M is a dependent verdict; exclude it to avoid a circular snapshot.
  return {form,sbi:sbis[0].r,sbiRow:sbis[0].i+4,top:tops[0].r,topRow:tops[0].i+2,
    events:ev.getValues(),eventDisplay:eventCells.join('␞'),holidays:holidayDates};
}
function astraFetchAudit_(ss, now) {
  function fetchJson(url) {
    const response=UrlFetchApp.fetch(url,{muteHttpExceptions:true});
    astraRequire_(response.getResponseCode()===200,'市場データ取得失敗');
    return JSON.parse(response.getContentText('UTF-8'));
  }
  const commit=fetchJson('https://api.github.com/repos/vermouth42/japan-stock-cloud/commits/main');
  astraRequire_(typeof commit.sha==='string'&&/^[a-f0-9]{40}$/.test(commit.sha),'取得元の版不明');
  const base='https://raw.githubusercontent.com/vermouth42/japan-stock-cloud/'+commit.sha+'/';
  const responses=UrlFetchApp.fetchAll(['status.json','prices.csv'].map(p=>({url:base+p,muteHttpExceptions:true})));
  astraRequire_(responses.every(r=>r.getResponseCode()===200),'市場データ取得失敗');
  const status=JSON.parse(responses[0].getContentText('UTF-8'));
  const rows=Utilities.parseCsv(responses[1].getContentText('UTF-8').replace(/^\uFEFF/,''));
  astraRequire_(JSON.stringify(rows.shift())===JSON.stringify(ASTRA_GATE_HEADER),'CSVヘッダー不一致');
  if(rows.length&&rows[rows.length-1].length===1&&rows[rows.length-1][0]==='')rows.pop();
  const imported=ss.getSheetByName('株価データ取込').getRange('A3:R4502').getValues();
  const audit=astraAuditRows_(status,rows,imported,now);
  return {...audit,commit:commit.sha,content_hash:astraHash_(imported)};
}
// Optional editor diagnostic: read-only, no approval record or GO output.
function verifyFinalMarketData() {
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  astraRequire_(ASTRA_FINAL_BOOKS.includes(ss.getId()),'対象ブック不正');
  const audit=astraFetchAudit_(ss,Date.now());
  console.log(JSON.stringify(audit));
  return audit;
}
function astraClearFinal_(ss, reason) {
  const form=ss.getSheetByName(ASTRA_FINAL_SHEET); if(!form)return;
  form.getRange('B34').setValue(false);
  form.getRange('B35:B40').clearContent();
  form.getRange('B26').setValue((reason||'最終確認なし')+'・GO禁止');
  PropertiesService.getDocumentProperties().deleteProperty(ASTRA_FINAL_PREFIX+'receipt');
  PropertiesService.getDocumentProperties().setProperty(ASTRA_FINAL_PREFIX+'generation',Utilities.getUuid());
}
function astraResetFinal_() {
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  if(!ASTRA_FINAL_BOOKS.includes(ss.getId()))return;
  astraClearFinal_(ss,'日次リセット後は再確認');
  const form=ss.getSheetByName(ASTRA_FINAL_SHEET);
  if(form)form.getRange('B10:B18').clearContent();
  PropertiesService.getDocumentProperties().deleteProperty(ASTRA_FINAL_PREFIX+'inputs');
}
function astraGuardFormula_(state, decision) {
  const lit=v=>v instanceof Date?String(astraSerial_(v)):
    typeof v==='number'?String(v):typeof v==='boolean'?(v?'TRUE':'FALSE'):'"'+String(v).replace(/"/g,'""')+'"';
  const same=(ref,v)=>v instanceof Date?'AND(ISNUMBER('+ref+'),ABS('+ref+'-'+lit(v)+')<0.00000001)':
    typeof v==='number'?'AND(ISNUMBER('+ref+'),'+ref+'='+lit(v)+')':
    typeof v==='boolean'?'AND(ISLOGICAL('+ref+'),'+ref+'='+lit(v)+')':'EXACT(TO_TEXT('+ref+'),'+lit(v)+')';
  const checks=['B34=TRUE','NOW()>='+astraSerial_(new Date(decision.at)),
    'NOW()<'+astraSerial_(new Date(decision.expires)),same('B3',state.form[2])];
  for(const r of ASTRA_FINAL_INPUT_ROWS)checks.push(same('B'+r,state.form[r-1]));
  for(const col of [2,3,4,5,6,8,9,10,11,12,14])checks.push(same("'SBI連動_v77'!"+String.fromCharCode(64+col)+state.sbiRow,state.sbi[col-2]));
  for(const col of [2,4,13])checks.push(same("'実戦候補TOP10'!"+String.fromCharCode(64+col)+state.topRow,state.top[col-1]));
  astraRequire_(state.eventDisplay.length<25000&&!state.eventDisplay.includes('\u0000'),'イベント記録が上限超過');
  // Sheets strips control characters such as CHAR(29); use a visible delimiter.
  checks.push('EXACT(TEXTJOIN("␞",FALSE,ARRAYFORMULA(TO_TEXT(\'イベント管理\'!A2:I100))),'+lit(state.eventDisplay)+')');
  checks.push(same('B35',new Date(decision.at)),same('B36',new Date(decision.expires)),same('B37',decision.shares));
  const label=decision.is_copy?'検証GO（実注文禁止）':'GO候補（確認時点）';
  return '=IFERROR(IF(AND('+checks.join(',')+'),"'+label+'・'+decision.shares+'株","変更・期限切れ・GO禁止"),"確認異常・GO禁止")';
}

function astraHandleFinalEdit_(e) {
  if(!e||!e.range)return;
  const ss=e.range.getSheet().getParent();
  if(!ASTRA_FINAL_BOOKS.includes(ss.getId())||!ss.getSheetByName(ASTRA_FINAL_SHEET))return;
  const name=e.range.getSheet().getName(), props=PropertiesService.getDocumentProperties();
  if(name!==ASTRA_FINAL_SHEET) {astraClearFinal_(ss,'関連シート編集後は再確認'); return;}
  const form=ss.getSheetByName(ASTRA_FINAL_SHEET),left=e.range.getColumn(),right=e.range.getLastColumn();
  const first=e.range.getRow(),last=e.range.getLastRow();
  const single=first===34&&left===2&&e.range.getNumRows()===1&&e.range.getNumColumns()===1&&form.getRange('B34').getValue()===true;
  astraClearFinal_(ss,'未確認');
  const generation=props.getProperty(ASTRA_FINAL_PREFIX+'generation');
  const lock=LockService.getDocumentLock();
  if(!lock.tryLock(10000))return;
  let syncLock;
  try {
    const now=Date.now(),code=astraSymbol_(form.getRange('B3').getValue());
    let inputs=astraReadRecord_(props,ASTRA_FINAL_PREFIX+'inputs');
    if(!inputs||typeof inputs!=='object'||Array.isArray(inputs))inputs={};
    if(first<=3&&last>=3&&left<=2&&right>=2) {
      form.getRange('B10:B18').clearContent(); inputs={};
    } else if(!single&&left<=2&&right>=2) {
      for(const row of ASTRA_FINAL_INPUT_ROWS)if(first<=row&&row<=last)
        inputs[String(row)]={symbol:code,value:astraCanonical_(form.getRange(row,2).getValue()),at:now};
    }
    props.setProperty(ASTRA_FINAL_PREFIX+'inputs',JSON.stringify(inputs));
    if(!single)return;
    const state=astraReadFinalState_(ss),receipt=astraReadRecord_(props,ASTRA_RECEIPT_PREFIX+'receipt.'+state.sbiRow);
    astraEvaluateFinal_(state,inputs,receipt,now);
    syncLock=LockService.getScriptLock(); astraRequire_(syncLock.tryLock(10000),'同期処理中・再確認');
    const audit=astraFetchAudit_(ss,now);
    const current=astraReadFinalState_(ss);
    astraRequire_(props.getProperty(ASTRA_FINAL_PREFIX+'generation')===generation&&JSON.stringify(state)===JSON.stringify(current),'確認中に入力・候補が変更');
    const decision=astraEvaluateFinal_(current,inputs,astraReadRecord_(props,ASTRA_RECEIPT_PREFIX+'receipt.'+state.sbiRow),Date.now());
    decision.is_copy=ss.getId()!==ASTRA_FINAL_BOOKS[0];
    props.setProperty(ASTRA_FINAL_PREFIX+'receipt',JSON.stringify({...decision,audit,state_hash:astraHash_(state)}));
    form.getRange('B35:B40').setValues([[new Date(decision.at)],[new Date(decision.expires)],
      [decision.shares],[audit.commit],[audit.market_date],['100銘柄・4500行・全18列一致']]);
    form.getRange('B34').setValue(true);
    form.getRange('B26').setFormula(astraGuardFormula_(state,decision));
    SpreadsheetApp.flush();
    astraRequire_(props.getProperty(ASTRA_FINAL_PREFIX+'generation')===generation&&JSON.stringify(state)===JSON.stringify(astraReadFinalState_(ss)),'確定中に入力変更');
  } catch(error) {
    const safe=typeof error.message==='string'&&/^[\u3000-\u9fff\u3040-\u30ffA-Z0-9a-z・＋／\-]+$/.test(error.message)?error.message:'最終照合に失敗';
    astraClearFinal_(ss,safe);
  } finally {if(syncLock&&syncLock.hasLock())syncLock.releaseLock(); lock.releaseLock();}
}
