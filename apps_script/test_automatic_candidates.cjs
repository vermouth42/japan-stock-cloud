'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx=vm.createContext({Date,Number,String,Object,Array,JSON,Math,Set,Map,Error});
for(const f of ['sbi_receipt.gs','final_gate_config.gs','final_gate.gs','automatic_candidates.gs'])
 vm.runInContext(fs.readFileSync('apps_script/'+f,'utf8'),ctx);
const codes=JSON.parse(fs.readFileSync('astra/watchlist.json'));
const now=Date.parse('2026-11-12T10:00:00+09:00');
const header=n=>vm.runInContext(n,ctx);
const copy='1QO4FhzBW3fivWNgO7he3AwE9cGNEKtL0-Kp8WYEE1yc';
function fixture(){
 const ranking=[header('ASTRA_AUTO_RANK_HEADER')],top10=[header('ASTRA_AUTO_TOP_HEADER')];
 codes.forEach((code,i)=>{
  const common={'順位':i+1,'コード':code,'最新日':new Date('2026-11-11T00:00:00+09:00'),
   '最終Score':100-i,'BUY':i<2?'A':'B','終値':1000,'信用':'C0','推奨株数':100};
  ranking.push(ranking[0].map(k=>common[k]??''));
  if(i<10)top10.push(top10[0].map(k=>common[k]??''));
 });
 return {book:copy,timezone:'Asia/Tokyo',begin:now-1000,end:now,ranking,top10,events:[header('ASTRA_AUTO_EVENT_HEADER')]};
}
let count=0;
function test(name,run){run();count++;console.log('ok '+name);}
function project(b=fixture(),clock=now){return ctx.astraAutoProject_(b,clock);}
test('qualified observation is never GO and absent calendar not confirmed',()=>{
 const r=project();assert.equal(r.screening_count,2);assert.equal(r.go_allowed,false);
 assert.ok(r.candidates.every(c=>c.go_allowed===false));
});
test('100-stock omitted tail regression is detected',()=>{
 const b=fixture();b.ranking[100][15]=999;assert.throws(()=>project(b),/TOP10/);
});
test('duplicate, missing and unknown stock fail closed',()=>{
 for(const mutate of [b=>b.ranking.pop(),b=>b.ranking[100][1]=codes[0],b=>b.ranking[100][1]='9999',b=>b.top10.pop()]){
  const b=fixture();mutate(b);assert.throws(()=>project(b));
 }
});
test('B and insufficient shares stay out',()=>{
 const b=fixture();b.top10[1][12]=0;assert.equal(project(b).screening_count,1);
 assert.ok(project().candidates.slice(2).every(c=>!c.screening));
});
test('booleans strings nonlots negative and missing quantity are rejected',()=>{
 for(const qty of [true,'100',99,150,-100,null,NaN]){const b=fixture();b.top10[1][12]=qty;assert.throws(()=>project(b));}
});
test('stale, conflicting grades and changed headers are rejected',()=>{
 for(const mutate of [b=>b.ranking[100][2]='2026-11-10',b=>b.top10[1][3]='B',b=>b.ranking[0]=['changed']]){
  const b=fixture();mutate(b);assert.throws(()=>project(b));
 }
});
test('capture time cannot bless old, future or slow reads',()=>{
 for(const change of [{begin:now-300000,end:now-300000},{end:now+1},{begin:now-30001},
                     {book:'other'},{timezone:'UTC'}])assert.throws(()=>project({...fixture(),...change}));
});
test('event dates override ended labels and stale counters',()=>{
 const b=fixture();b.events.push([codes[0],'test','2026/11/13','','','',-3,'終了','']);
 assert.equal(project(b).screening_count,1);
 b.events[1][0]='0000';assert.equal(project(b).screening_count,0);
 b.events[1][2]='2026/11/18';assert.equal(project(b).screening_count,2);
});
test('event missing date and unknown calendar year are rejected',()=>{
 for(const value of ['', '2026/02/30', '2030/01/01',46337.5]){
  const b=fixture();b.events.push([codes[0],'',value]);assert.throws(()=>project(b));
 }
});
test('serial dates and alphanumeric codes are handled',()=>{
 const b=fixture();b.ranking.forEach((r,i)=>{if(i)r[2]=46337;});
 assert.equal(project(b).stocks,100);assert.ok(project(b).candidates.some(c=>c.symbol==='285A'));
});
test('three-session holiday boundary matches shared JPX calendar',()=>{
 assert.equal(ctx.astraThirdSession_('2026-11-20'),'2026-11-26');
});
// Mock only external Apps Script services. The actual source functions run.
let store={},triggers=[],created=0,released=0,lockAvailable=true,grid=null,readBundle=fixture();
const props={getProperty:k=>store[k]||null,setProperty:(k,v)=>{store[k]=v;},deleteProperty:k=>{delete store[k];}};
const range=address=>({getValue:()=>grid?.[0][0],getValues:()=>grid,
 setValues:rows=>{assert.equal(address,'A1:F16');assert.equal(rows.length,16);assert.ok(rows.every(r=>r.length===6));grid=rows;},
 setFontSize(){return this;},setVerticalAlignment(){return this;},setWrap(){return this;},
 setBackground(){return this;},setFontColor(){return this;},setFontWeight(){return this;},setNumberFormat(){return this;}});
const sheet={getRange:range,setFrozenRows(){},setColumnWidths(){},setColumnWidth(){}};
const book={getId:()=>copy,getSpreadsheetTimeZone:()=> 'Asia/Tokyo',
 getSheetByName:name=>{assert.equal(name,'自動候補照合');return grid?sheet:null;},insertSheet:name=>{assert.equal(name,'自動候補照合');return sheet;}};
ctx.PropertiesService={getScriptProperties:()=>props};
ctx.SpreadsheetApp={getActiveSpreadsheet:()=>book,openById:id=>{assert.equal(id,copy);return book;},flush(){}};
ctx.LockService={getScriptLock:()=>({tryLock:()=>lockAvailable,releaseLock:()=>released++})};
ctx.ScriptApp={getProjectTriggers:()=>triggers,newTrigger:handler=>({timeBased(){return this;},everyMinutes(n){assert.equal(n,5);return this;},
 create(){created++;const t={getHandlerFunction:()=>handler};triggers.push(t);return t;}})};
const realRead=ctx.astraAutoRead_;
ctx.astraAutoRead_=()=>readBundle;
const realNow=Date.now;
Date.now=()=>now;
try{
 test('successful refresh only writes dedicated output and verifies readback',()=>{
  const r=ctx.astraRefreshCandidates();assert.equal(r.ok,true);assert.equal(grid[0][0],header('ASTRA_AUTO_MARKER'));
  assert.ok(grid.slice(6).every(r=>r[5]==='禁止'));assert.equal(grid[2][5],100);assert.equal(released,1);
 });
 test('installer is idempotent and preserves existing sync handler',()=>{
  const sync={getHandlerFunction:()=> 'syncJapaneseStocksV76'};triggers=[sync];
  ctx.installAstraCandidateTrigger();ctx.installAstraCandidateTrigger();
  assert.equal(created,1);assert.equal(triggers[0],sync);assert.equal(triggers.length,2);
 });
 test('failed refresh clears previous candidates and invalidates installer',()=>{
  readBundle=fixture();readBundle.ranking.pop();assert.throws(()=>ctx.astraRefreshCandidates());
  assert.ok(grid.slice(6).every(r=>r.every(v=>v==='')));assert.equal(grid[2][3],'無効');
  assert.throws(()=>ctx.installAstraCandidateTrigger());readBundle=fixture();
 });
 test('lock conflict never writes or installs another trigger',()=>{
  const before=JSON.stringify(grid);lockAvailable=false;
  assert.equal(ctx.astraRefreshCandidates().go_allowed,false);assert.equal(JSON.stringify(grid),before);
  assert.throws(()=>ctx.installAstraCandidateTrigger());lockAvailable=true;
 });
 test('output name collision preserves unrelated sheet contents',()=>{
  const saved=grid;grid=[['unrelated user data']];assert.throws(()=>ctx.astraRefreshCandidates());
  assert.equal(grid[0][0],'unrelated user data');grid=saved;
 });
 test('copied script cannot follow a production book binding',()=>{
  store['astra.candidates.v1.book']='1AQ0CcqOnyLHWSOnHR_m0FsvcvNFZIbJ8dRYzKuKU6Zk';
  assert.throws(()=>ctx.astraAutoBook_());store['astra.candidates.v1.book']=copy;
 });
 test('duplicate candidate triggers stop installation without deleting any',()=>{
  ctx.astraRefreshCandidates();triggers.push({getHandlerFunction:()=> 'astraCandidateTick'});
  assert.throws(()=>ctx.installAstraCandidateTrigger());assert.equal(triggers.length,3);
 });
 test('reader only requests intended source ranges',()=>{
  const requested=[];const source={getId:()=>copy,getSpreadsheetTimeZone:()=> 'Asia/Tokyo',getSheetByName:n=>({getRange:r=>({getValues:()=>{requested.push(n+'!'+r);return [];}})})};
  realRead(source);assert.deepEqual(requested,['監視ランキング!A3:R103','実戦候補TOP10!A1:Q11','イベント管理!A1:I100']);
 });
 test('night and weekend ticks leave output untouched',()=>{
  const previous=JSON.stringify(grid);
  for(const time of ['2026-11-12T06:59:00+09:00','2026-11-12T22:00:00+09:00','2026-11-14T10:00:00+09:00']){
   Date.now=()=>Date.parse(time);assert.equal(ctx.astraCandidateTick(),undefined);
  }
  assert.equal(JSON.stringify(grid),previous);
 });
}finally{Date.now=realNow;}
console.log(count+' automatic candidate scenarios passed');
