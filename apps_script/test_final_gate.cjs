'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const ctx=vm.createContext({Date,Number,String,Object,Array,JSON,Math,Set,Map,Error,
 Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},
 computeDigest:(_,s)=>Array.from(crypto.createHash('sha256').update(s).digest()),getUuid:()=>crypto.randomUUID()}});
for(const p of ['sbi_receipt.gs','final_gate_config.gs','final_gate.gs'])vm.runInContext(fs.readFileSync('apps_script/'+p,'utf8'),ctx);
const codes=JSON.parse(fs.readFileSync('astra/watchlist.json'));
const now=Date.parse('2026-11-11T10:05:00+09:00');
function fixture(){
 const code=codes[0],at=now-1000;
 const sbi=[code,'A',1000,1000,990,true,true,true,true,true,true,'',new Date(at)];
 const inputs=Object.fromEntries([5,6,8,9,10,11].map(col=>[String(col),{symbol:code,value:sbi[col-2],at}]));
 const receipt={symbol:code,snapshot:ctx.astraSnapshot_(sbi),at,inputs};
 const form=Array(18).fill('');form[2]=code;
 [code,950,20000,15000,1500000,2,new Date('2026-11-16T00:00:00+09:00'),'https://example.com/ir','調査用の架空根拠'].forEach((x,i)=>form[9+i]=x);
 const top=Array(17).fill('');top[1]=code;top[3]='A';top[12]=1000;
 const meta=Object.fromEntries([10,11,12,13,14,15,16,17,18].map(row=>[String(row),{symbol:code,value:ctx.astraCanonical_(form[row-1]),at}]));
 return {state:{form,sbi,top,events:[],sbiRow:4,topRow:2,eventDisplay:'',holidays:[]},meta,receipt};
}
function evaluate(f=fixture(),clock=now){return ctx.astraEvaluateFinal_(f.state,f.meta,f.receipt,clock);}
function change(f,index,value){f.state.form[index]=value;f.meta[String(index+1)].value=ctx.astraCanonical_(value);}
let count=0;
function test(name,run){run();count++;console.log('ok '+name);}
test('qualified A at current price uses shared risk budget',()=>{const d=evaluate();assert.equal(d.shares,200);assert.equal(d.planned_loss,10400);assert.equal(d.required_cash,200400);});
test('risk versus cash boundaries and explicit zero cost',()=>{
 for(const cash of [100199,100200]){const f=fixture();change(f,13,cash);if(cash===100199)assert.throws(()=>evaluate(f));else assert.equal(evaluate(f).shares,100);}
 const f=fixture();change(f,14,0);assert.equal(evaluate(f).shares,300);
});
test('every required entry must be fresh and bound to its code',()=>{
 for(const key of Object.keys(fixture().meta)){
  for(const patch of [{at:now-300001},{at:now+1},{symbol:'285A'},{value:'changed'}]){
   const f=fixture();Object.assign(f.meta[key],patch);assert.throws(()=>evaluate(f));
  }
  const f=fixture();delete f.meta[key];assert.throws(()=>evaluate(f));
 }
});
test('missing, numeric strings, booleans, negatives and stop above price block',()=>{
 for(const ix of [10,11,12,13,14])for(const val of ['',true,'100',-1,NaN,Infinity]){
  const f=fixture();change(f,ix,val);assert.throws(()=>evaluate(f));
 }
 const f=fixture();change(f,10,1000);assert.throws(()=>evaluate(f));
});
test('rank, sizing and changed SBI inputs invalidate evidence',()=>{
 for(const patch of [[1,'B'],[2,99],[3,1001],[6,false],[10,false],[12,new Date(now)]]){
  const f=fixture();f.state.sbi[patch[0]]=patch[1];assert.throws(()=>evaluate(f));
 }
 const f=fixture();f.state.top[3]='B';assert.throws(()=>evaluate(f));
});
test('session lunch, late afternoon, weekends and calendar coverage fail closed',()=>{
 for(const t of ['2026-11-11T09:04:00+09:00','2026-11-11T11:25:00+09:00','2026-11-11T14:55:00+09:00','2026-11-14T10:00:00+09:00','2028-01-04T10:00:00+09:00'])assert.throws(()=>evaluate(fixture(),Date.parse(t)));
 assert.equal(ctx.astraThirdSession_('2026-11-20'),'2026-11-26');
 assert.equal(ctx.astraExpectedDay_(Date.parse('2026-11-24T10:00:00+09:00')),'2026-11-20');
});
test('same-day global and third-session events block irrespective of finished label',()=>{
 for(const code of [codes[0],'0000'])for(const day of ['2026-11-11','2026-11-16']){
  const f=fixture();f.state.events=[[code,'',day,'','','','','終了','']];assert.throws(()=>evaluate(f));
 }
 const f=fixture();f.state.events=[[codes[0],'','2026-11-17','','','','','','']];assert.equal(evaluate(f).shares,200);
});
test('event absence requires explicit through-date, URL and memo',()=>{
 for(const [ix,val] of [[15,'2026-11-13'],[15,'2026-02-30'],[16,''],[16,'javascript:alert(1)'],[17,'']]){
  const f=fixture();change(f,ix,val);assert.throws(()=>evaluate(f));
 }
 const f=fixture();f.state.events=[[codes[0],'','','','','','','','']];assert.throws(()=>evaluate(f));
});
test('timestamps expire with conservative display buffer',()=>{
 const f=fixture();const d=evaluate(f);assert.equal(d.expires,now-1000+240000);
 assert.throws(()=>evaluate(f,now+240001));
});
test('formula binds quotes, single confirmation and timestamps, no derived M',()=>{
 const f=fixture();change(f,17,'根拠 "引用"'); const formula=ctx.astraGuardFormula_(f.state,evaluate(f));
 assert.ok(formula.startsWith('=IFERROR(IF(AND('));assert.ok(formula.includes('"根拠 ""引用"""'));
 assert.ok(!formula.includes("'SBI連動_v77'!M4"));assert.ok(formula.includes('NOW()<'));
 assert.ok(formula.includes('B34=TRUE'));assert.ok(formula.includes("'イベント管理'!A2:I100"));
 assert.ok(formula.includes('TEXTJOIN("␞",FALSE'));assert.ok(!formula.includes('CHAR(29)'));
});
function dataFixture(){
 let day='2026-11-10',days=[];while(days.length<50){if(ctx.astraTradingDay_(day))days.push(day);day=ctx.astraMoveDay_(day,-1);}
 const raw=codes.flatMap(code=>days.map(d=>[d,code,'1000','1020','980','1000','1000','1000','','','100000','100000','100000','1','40','40','4']));
 const imported=codes.flatMap(code=>raw.filter(r=>r[1]===code).slice(0,45).map(r=>[new Date(r[0]+'T00:00:00+09:00'),'',/^\d+$/.test(code)?Number(code):code,...r.slice(2).map(x=>x===''?'':Number(x))]));
 return {status:{stocks_total:100,stocks_success:100,stocks_failed:0,rows:5000,failed:[],updated_at_jst:'2026-11-10T21:00:00+09:00',latest_market_date:'2026-11-10'},raw,imported};
}
test('100 by 45 exact import passes regardless of row order',()=>{const d=dataFixture();assert.equal(ctx.astraAuditRows_(d.status,d.raw,d.imported.reverse(),now).rows,4500);});
test('stale, failed, unknown stock, duplicate, bad prices, altered optional cell block',()=>{
 const mutations=[d=>d.status.latest_market_date='2026-11-09',d=>d.status.stocks_failed=1,d=>d.status.updated_at_jst='2026-11-12T00:00:00+09:00',d=>d.raw[0][1]='9999',d=>d.raw[0]=d.raw[1],d=>d.raw[0][2]='-1',d=>d.raw[0][10]='0.5',d=>d.imported[0][17]=7,d=>d.imported[0][1]=0,d=>d.imported[0]=d.imported[1],d=>d.imported.pop()];
 for(const change of mutations){const d=dataFixture();change(d);assert.throws(()=>ctx.astraAuditRows_(d.status,d.raw,d.imported,now));}
});
test('every imported column has a content mismatch check',()=>{
 for(let c=3;c<18;c++){const d=dataFixture();d.imported[0][c]=123456;assert.throws(()=>ctx.astraAuditRows_(d.status,d.raw,d.imported,now));}
});
let store,grids,docLock=true,scriptLock=true,fetchHook=()=>{},httpCode=200;
const props={getProperty:k=>store[k]||null,setProperty:(k,v)=>{store[k]=v;},deleteProperty:k=>{delete store[k];},getProperties:()=>({...store})};
const book={getId:()=> '1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc',getSpreadsheetTimeZone:()=> 'Asia/Tokyo',getSheetByName:name=>grids[name]?sheet(name):null};
function sheet(name){return {getName:()=>name,getParent:()=>book,getRange:(r,c,nr=1,nc=1)=>{
 if(typeof r==='string'){
  const m=/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/.exec(r);
  const col=s=>[...s].reduce((v,c)=>v*26+c.charCodeAt(0)-64,0);
  c=col(m[1]);r=Number(m[2]);nr=m[4]?Number(m[4])-r+1:1;nc=m[3]?col(m[3])-c+1:1;
 }
 const read=()=>Array.from({length:nr},(_,i)=>Array.from({length:nc},(_,j)=>grids[name][r-1+i]?.[c-1+j]??''));
 const write=rows=>{for(let i=0;i<nr;i++)for(let j=0;j<nc;j++){grids[name][r-1+i]??=[];grids[name][r-1+i][c-1+j]=rows[i][j];}};
 return {getValues:read,getValue:()=>read()[0][0],getDisplayValues:()=>read().map(row=>row.map(x=>String(x))),
  setValue:v=>write(Array.from({length:nr},()=>Array(nc).fill(v))),setValues:write,clearContent:()=>write(Array.from({length:nr},()=>Array(nc).fill(''))),
  setFormula:v=>write([[v]])};
 }};}
ctx.PropertiesService={getDocumentProperties:()=>props};
ctx.LockService={getDocumentLock:()=>({tryLock:()=>docLock,releaseLock:()=>{}}),getScriptLock:()=>({tryLock:()=>scriptLock,hasLock:()=>scriptLock,releaseLock:()=>{}})};
ctx.SpreadsheetApp={flush:()=>{},getActiveSpreadsheet:()=>book};
const nativeFixture=dataFixture();
ctx.UrlFetchApp={fetch:()=>({getResponseCode:()=>httpCode,getContentText:()=>JSON.stringify({sha:'a'.repeat(40)})}),fetchAll:()=>{fetchHook();return [nativeFixture.status,[JSON.parse(fs.readFileSync('astra/watchlist.json')).length]].map((_,i)=>({getResponseCode:()=>httpCode,getContentText:()=>i===0?JSON.stringify(nativeFixture.status):[JSON.parse(vm.runInContext('JSON.stringify(ASTRA_GATE_HEADER)',ctx)),...nativeFixture.raw].map(r=>r.join(',')).join('\n')}));}};
ctx.Utilities.parseCsv=s=>s.split('\n').map(r=>r.split(','));
function setupNative(){
 const f=fixture();docLock=scriptLock=true;httpCode=200;fetchHook=()=>{};store={};
 grids={'発注前資金確認':Array.from({length:40},()=>Array(3).fill('')),'SBI連動_v77':Array.from({length:13},()=>Array(15).fill('')),
  '実戦候補TOP10':Array.from({length:11},()=>Array(17).fill('')),'イベント管理':Array.from({length:100},()=>Array(9).fill('')),
  'JPX休場日':Array.from({length:43},()=>['']),'株価データ取込':[Array(18).fill(''),Array(18).fill(''),...nativeFixture.imported.map(r=>[...r])]};
 f.state.form.forEach((v,i)=>grids['発注前資金確認'][i][1]=v);grids['発注前資金確認'][33][1]=true;
 for(let i=0;i<10;i++){
  const s=[...f.state.sbi];s[0]=codes[i];grids['SBI連動_v77'][i+3]=['',...s,''];
  const t=[...f.state.top];t[1]=codes[i];grids['実戦候補TOP10'][i+1]=t;
 }
 JSON.parse(fs.readFileSync('astra/jpx_calendar.json')).closed_dates.forEach((v,i)=>grids['JPX休場日'][i+2][0]=new Date(v+'T00:00:00+09:00'));
 store['astra.sbi.v1.receipt.4']=JSON.stringify(f.receipt);
 store['astra.final.v1.inputs']=JSON.stringify(f.meta);
}
function nativeEdit(row=34,col=2,nr=1,nc=1){
 const old=Date.now;Date.now=()=>now;
 try{ctx.astraHandleFinalEdit_({range:{getSheet:()=>sheet('発注前資金確認'),getRow:()=>row,getLastRow:()=>row+nr-1,getColumn:()=>col,getLastColumn:()=>col+nc-1,getNumRows:()=>nr,getNumColumns:()=>nc}});}
 finally{Date.now=old;}
}
test('controller audits raw source and creates copy-only GO snapshot',()=>{
 setupNative();nativeEdit();assert.equal(grids['発注前資金確認'][33][1],true);
 assert.equal(grids['発注前資金確認'][36][1],200);
 assert.ok(grids['発注前資金確認'][25][1].includes('検証GO（実注文禁止）'));
 assert.ok(store['astra.final.v1.receipt']);
});
test('bulk checkbox, unknown inputs, HTTP and lock failures never leave GO',()=>{
 const changes=[()=>{store['astra.final.v1.inputs']='{}';},()=>{httpCode=500;},()=>{docLock=false;},()=>{scriptLock=false;}];
 for(const change of changes){setupNative();change();nativeEdit();assert.equal(grids['発注前資金確認'][33][1],false);assert.ok(!store['astra.final.v1.receipt']);}
 setupNative();nativeEdit(34,2,2,1);assert.equal(grids['発注前資金確認'][33][1],false);
});
test('changed inputs and concurrent cancellation during audit fail closed',()=>{
 for(const hook of [()=>grids['発注前資金確認'][13][1]=99999,()=>props.setProperty('astra.final.v1.generation','new-edit')]){
  setupNative();fetchHook=hook;nativeEdit();assert.equal(grids['発注前資金確認'][33][1],false);assert.ok(!store['astra.final.v1.receipt']);
 }
});
test('symbol selection clears former risk data and input edits invalidate',()=>{
 setupNative();nativeEdit(3,2);assert.ok(grids['発注前資金確認'].slice(9,18).every(r=>r[1]===''));
 setupNative();nativeEdit(14,2);assert.equal(grids['発注前資金確認'][33][1],false);
 assert.equal(JSON.parse(store['astra.final.v1.inputs'])['14'].at,now);
});
test('holiday-sheet alteration blocks even if all other evidence passes',()=>{
 setupNative();grids['JPX休場日'][2][0]=new Date('2026-01-02T00:00:00+09:00');nativeEdit();
 assert.equal(grids['発注前資金確認'][33][1],false);
 setupNative();grids['実戦候補TOP10'][3][1]=grids['実戦候補TOP10'][2][1];nativeEdit();
 assert.equal(grids['発注前資金確認'][33][1],false);
 setupNative();grids['イベント管理'][1][8]='区切り␞混在';nativeEdit();
 assert.equal(grids['発注前資金確認'][33][1],false);
});
console.log(count+' final gate scenarios passed');
