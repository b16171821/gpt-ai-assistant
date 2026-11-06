import {JSDOM} from '../../short/node_modules/jsdom/lib/api.js';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {calculateShortPosition,defaults} from '../../short/short-position-calculator.mjs';

const root=new URL('../../short/',import.meta.url);
const date='2026-11-06';
const makeRow=(i,market='TW')=>({direction:'SHORT',market,currency:market==='TW'?'TWD':'USD',code:'TEST'+String(i).padStart(2,'0'),name:'測試公司'+i,
 dataDate:date,dataFresh:true,close:100,stage:'整理平台跌破',originalSignal:i===6?'FALSE_BREAK':'PLATFORM_BREAK',
 rawSignals:i===6?['FALSE_BREAK']:['PLATFORM_BREAK'],originalSignalDate:date,adjustedSignal:'僅供觀察｜交易資格待確認',
 referenceKnownAt:'2026-11-05',pressure:102,observationLow:99,observationHigh:101,entry:100,entryBasis:'OBSERVATION_MIDPOINT',stopLoss:105,target:90,
 qualityScore:90,technicalCandidate:i<6,priorityObservation:i<6,statusReason:'技術資料已確認，券源待核實',
 tradeQualification:{allowEntry:false,reasons:['交易資格／券源待確認'],marginShort:{status:'PENDING'},borrowShort:{status:'PENDING'}},
 tradeTier:'OBSERVATION_ONLY',signalWitnesses:{},chart:[{date:'2026-11-05',close:102},{date,close:100}]});
function payload(market){return {meta:{direction:'SHORT',market,dataDate:date,updatedAt:new Date().toISOString(),freshnessStatus:'COMPLETE',freshCount:40,universeCount:40,strategyVersion:'test'},marketEnvironment:{code:'FAVORABLE',label:'空方環境有利',asOfDate:date,indexes:[]},stocks:Array.from({length:40},(_,i)=>makeRow(i+1,market))};}

test('three-page DOM flows, immediate calculation, immutable originals, filters, storage isolation and last-good recovery',async()=>{
 const dom=new JSDOM(fs.readFileSync(new URL('index.html',root),'utf8'),{url:'https://qa.invalid/short/',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,errors=[];w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
 let offline=false;
 const data={'tw_index.json':payload('TW'),'us_index.json':payload('US'),'tracking.json':{meta:{direction:'SHORT'},records:[]},'health.json':{},'research_summary.json':{disclosure:'技術訊號回測，非可執行放空績效。'},'benchmark_preview.json':{},'source_manifest.json':{},'delivery.json':{summary:'測試'}};
 w.fetch=async url=>{if(offline)throw Error('offline test');const file=String(url).split('/').at(-1);if(!data[file])return {ok:false,status:404};return {ok:true,json:async()=>structuredClone(data[file])};};
 w.localStorage.setItem('positionCalculatorPreferences.v1','long-side-unchanged');w.localStorage.setItem('short-side-lab-v1','old-demo-unchanged');
 w.addEventListener('error',e=>errors.push(e.message));Object.assign(w,{calculateShortPosition,defaults});
 w.eval(fs.readFileSync(new URL('short-app.js',root),'utf8').replace(/^import[^\n]+\n/,'').replace(/\nexport \{[^}]+\};?\s*$/,''));
 const wait=()=>new Promise(r=>w.setTimeout(r,25)),q=s=>d.querySelector(s),qa=s=>[...d.querySelectorAll(s)],click=s=>{assert.ok(q(s),s);q(s).click();},input=(s,v)=>{q(s).value=v;q(s).dispatchEvent(new w.Event('input',{bubbles:true}));},change=(s,v)=>{q(s).value=v;q(s).dispatchEvent(new w.Event('change',{bubbles:true}));};
 await wait();assert.equal(qa('#priority-list .signal-card').length,5);assert.match(q('#priority-list').textContent,/交易資格／券源待確認/);
 q('#reserve-details').open=true;q('#reserve-details').dispatchEvent(new w.Event('toggle'));assert.equal(qa('#reserve-list .signal-card').length,15);click('#load-more');assert.equal(qa('#reserve-list .signal-card').length,30);
 input('#stock-search','TEST40');assert.equal(qa('#reserve-list .signal-card').length,1);click('#reset-filters');change('#signal-filter','FALSE_BREAK');assert.equal(qa('#reserve-list .signal-card').length,1);click('#reset-filters');
 click('[data-favorite="TW:TEST01"]');q('#only-favorites').checked=true;q('#only-favorites').dispatchEvent(new w.Event('change',{bubbles:true}));assert.equal(qa('#priority-list .signal-card').length,1);click('#reset-filters');
 const before=structuredClone(data['tw_index.json'].stocks[0]);click('[data-calc="TW:TEST01"]');await wait();assert.equal(q('#calc-entry').value,'100');assert.ok(!q('#page-calculator').hidden);assert.match(q('#calc-results').textContent,/待券商核實/);
 const previous=q('#calc-results').textContent;input('#calc-stop','106');assert.notEqual(q('#calc-results').textContent,previous);assert.match(q('#calc-results').textContent,/6/);
 input('#calc-stop','99');assert.ok(!q('#calc-error').hidden);assert.doesNotMatch(q('#calc-results').textContent,/名目試算股數/);
 input('#calc-stop','105');q('#risk-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));assert.match(q('#calc-confirm-status').textContent,/已確認/);
 input('#calc-entry','101');assert.equal(q('#calc-confirm-status').textContent,'');assert.deepEqual(data['tw_index.json'].stocks[0],before);
 change('#calc-currency','USD');assert.equal(q('#calc-route').value,'US_SHORT');assert.equal(q('#calc-capital').value,'100000');assert.equal(q('#calc-entry').value,'');assert.equal(q('#calc-lot').value,'1');
 change('#calc-route','MARGIN_SHORT');assert.match(q('#calc-error').textContent,/幣別不一致/);
 for(const page of ['observation','calculator','tracking']){w.location.hash=page;w.dispatchEvent(new w.HashChangeEvent('hashchange'));assert.ok(!q('#page-'+page).hidden);assert.equal(qa('.page:not([hidden])').length,1);}
 w.location.hash='observation';w.dispatchEvent(new w.HashChangeEvent('hashchange'));q('#observation-tab').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await wait();assert.ok(!q('#page-calculator').hidden);assert.equal(q('#calculator-tab').getAttribute('aria-selected'),'true');
 click('#menu-toggle');assert.equal(q('#menu-toggle').getAttribute('aria-expanded'),'true');click('#scrim');assert.equal(q('#menu-toggle').getAttribute('aria-expanded'),'false');
 click('#open-guide');assert.ok(q('#info-dialog').open);assert.match(q('#info-content').textContent,/反彈/);click('#close-info');
 offline=true;click('#refresh');await wait();assert.match(q('#load-status').textContent,/保留最後成功資料/);assert.match(q('#load-status').textContent,/2026-11-06/);
 assert.equal(w.localStorage.getItem('positionCalculatorPreferences.v1'),'long-side-unchanged');assert.equal(w.localStorage.getItem('short-side-lab-v1'),'old-demo-unchanged');
 assert.deepEqual(errors,[]);w.close();
});
