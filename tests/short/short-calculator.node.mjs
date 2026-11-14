import assert from 'node:assert/strict';
import test from 'node:test';
import {calculateShortPosition,defaults} from '../../short/short-position-calculator.mjs';
const base={...defaults,entry:100,stop:105,target:90};
test('costs, slippage, borrowing and dividends constrain shares before rounding',()=>{
 const r=calculateShortPosition(base);assert.equal(r.shares,1000);assert.ok(r.estimatedLoss<=r.riskBudget);assert.ok(r.notional<=r.notionalBudget);assert.ok(r.totalRiskPerShare>5);assert.ok(r.borrowFeePerShare>0);assert.equal(r.actualOrderShares,null);assert.equal(r.estimatedMargin,null);
});
test('zero budget and insufficient unit result in zero, never fractional order',()=>{assert.equal(calculateShortPosition({...base,exposure:0}).shares,0);assert.equal(calculateShortPosition({...base,capital:1000}).shares,0);});
test('short stop must be above entry and target below it',()=>{for(const p of [{stop:99},{target:101},{stop:100},{target:0},{entry:''}])assert.throws(()=>calculateShortPosition({...base,...p}));});
test('unknown costs are not guessed as zero',()=>{for(const key of ['cost','slippage','borrow'])assert.throws(()=>calculateShortPosition({...base,[key]:''}));});
test('currencies and trading route cannot be mixed',()=>{assert.throws(()=>calculateShortPosition({...base,currency:'USD'}));assert.doesNotThrow(()=>calculateShortPosition({...base,currency:'USD',route:'US_SHORT',lot:1}));});
test('manual margin assumption never becomes executable shares',()=>{const r=calculateShortPosition({...base,margin:90});assert.equal(r.estimatedMargin,r.notional*.9);assert.equal(r.actualOrderShares,null);assert.equal(r.executionStatus,'BROKER_CONFIRMATION_REQUIRED');});
test('larger borrowing cost reduces risk shares, does not improve risk reward',()=>{const a=calculateShortPosition({...base,lot:1,borrow:2}),b=calculateShortPosition({...base,lot:1,borrow:30});assert.ok(b.shares<=a.shares);assert.ok(b.netRR<a.netRR);});
test('200 scenarios respect risk, exposure, currency and lot constraints',()=>{for(let i=1;i<=200;i++){const r=calculateShortPosition({...base,capital:i*1237,lot:i%2?1:1000,cost:i/100,borrow:i/10});assert.ok(r.estimatedLoss<=r.riskBudget+1e-8);assert.ok(r.notional<=r.notionalBudget+1e-8);assert.equal(r.shares%r.lot,0);}});
test('Taiwan planned risk cannot exceed one percent including costs',()=>{assert.throws(()=>calculateShortPosition({...base,risk:1.01}),/1%/);const r=calculateShortPosition({...base,risk:1,lot:1});assert.ok(r.estimatedLoss<=r.capital*.01);});
