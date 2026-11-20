"""TW v2 partial exits. Only previous completed bars may tighten the stop."""
from copy import deepcopy
from datetime import datetime
from .validation import validate_bars,positive
from .tw_v2 import TW_CONFIG,indicators,adjusted_asof


def evaluate_v2(record,bars,*,config=TW_CONFIG,slippage_bps=0,roundtrip_cost_bps=0,annual_borrow_rate=None,expected_sessions=None):
    # Import lazily to keep v1 lifecycle and its original hashes unchanged.
    from .tracking import original_hash,entry_timing_verified,TERMINAL
    r=deepcopy(record);o=r['originalStrategy'];frozen=deepcopy(o)
    if r['originalHash']!=original_hash(o):raise ValueError('Immutable original short strategy was changed')
    if r['status'] in TERMINAL:return r
    issues=validate_bars(bars)
    if issues:r['dataIssues']=issues;return r
    r.setdefault('remainingFraction',1.);r.setdefault('partialRealizedGross',0.);r.setdefault('partialCovers',[])
    r.setdefault('currentStop',o['stopLoss']);r.setdefault('priorCompletedBars',[])
    available={b['date'] for b in bars};missing=sorted(d for d in (expected_sessions or []) if bars and r['lastEvaluatedDate']<d<=bars[-1]['date'] and d not in available)
    def end(status,day,price=None):
        r.update(status=status,endDate=day,exitPrice=price,result={'TARGET_HIT':'兩階段目標完成','STOP_HIT':'剩餘部位停損','TIME_EXIT':'持有到期','CANCELLED':'未觸發取消','EXPIRED':'未觸發到期','NEEDS_REVIEW':'同日順序未知，人工確認'}.get(status,status))
        r['events'].append({'date':day,'type':status,'price':price,'remainingFraction':r['remainingFraction']})
        if r['entryStatus']=='TRIGGERED' and price is not None:
            factor=r['splitFactor'];gross=r['partialRealizedGross']+r['remainingFraction']*(r['fillPrice']-price*factor)-r['dividendLiability']
            elapsed=max(1,(datetime.fromisoformat(day)-datetime.fromisoformat(r['fillDate'])).days)
            # Borrow cost deliberately budgets the full position until final exit;
            # no unverified early fee reduction is presented as an actual charge.
            rate=.10 if annual_borrow_rate is None else annual_borrow_rate
            weighted_cover=r['fillPrice']-(gross+r['dividendLiability'])
            cost=r['fillPrice']*roundtrip_cost_bps/10000+(r['fillPrice']+weighted_cover)*slippage_bps/10000+r['fillPrice']*rate*elapsed/365
            r.update(grossPnlPerOriginalShare=gross,netPnlPerOriginalShare=gross-cost,remainingFraction=0.,
                     costAssumptions={'roundtripCostBps':roundtrip_cost_bps,'slippageBpsPerSide':slippage_bps,'annualBorrowRate':rate,'borrowDays':elapsed,'borrowQuantityBasis':'保守以全原始部位計至最終出場，非實際券商費率'})
    for b in bars:
        day=b['date']
        if day<=r['lastEvaluatedDate'] or day<=r['firstSignalDate']:continue
        if missing and day>=missing[0]:
            r['coverageStatus']='GAP_REQUIRES_REVIEW';r.setdefault('dataIssues',[]).append('MISSING_LIFECYCLE_SESSION:'+missing[0]);break
        if b['volume']<=0:r.setdefault('dataIssues',[]).append('NO_EXECUTABLE_VOLUME:'+day);break
        r['observedSessions']+=1;r['lastEvaluatedDate']=day;r['latestStatus']={'dataDate':day,'close':b['close'],'high':b['high'],'low':b['low']}
        if r['entryStatus']!='TRIGGERED':
            if b.get('split') or b.get('dividend'):end('CANCELLED',day);break
            if not r['entryWindowEvaluated']:
                r['entryWindowEvaluated']=True
                allowed=entry_timing_verified(r) and o.get('technicalCandidate') and o.get('tradeTier')=='EXECUTABLE_CANDIDATE'
                if allowed and o['observationLow']<=b['open']<=o['observationHigh'] and o['stopLoss']>b['open']>o['target']:
                    r.update(entryStatus='TRIGGERED',status='ACTIVE',fillPrice=b['open'],fillDate=day)
                    r['events'].append({'date':day,'type':'ENTRY_AT_NEXT_OPEN','price':b['open'],'basis':'TECHNICAL_SIMULATION_NOT_BROKER_FILL'})
                else:r['events'].append({'date':day,'type':'ENTRY_NOT_FILLED','reason':'次一開盤時間／區間／完整交易資格未通過，禁止回填成交'})
            if r['entryStatus']!='TRIGGERED':
                if b['close']>=o['pressure'] or b['high']>=o['stopLoss']:end('CANCELLED',day)
                elif r['observedSessions']>=config.observation_days:end('EXPIRED',day)
                if r['status'] in TERMINAL:break
                continue
        r['holdingSessions']+=1
        if b.get('split'):r['splitFactor']*=b['split'];r['currentStop']/=b['split']
        factor=r['splitFactor']
        if b.get('dividend'):r['dividendLiability']+=b['dividend']*factor*r['remainingFraction']
        # At today's open the trail was known from yesterday's completed bar.
        stop=r['currentStop'];t1=o['target']/factor;t2=o.get('target2');t2=t2/factor if positive(t2) else None
        after_partial=bool(r['partialCovers']);target=t2 if after_partial else t1
        gap_stop=day!=r['fillDate'] and b['open']>=stop
        gap_target=positive(target) and day!=r['fillDate'] and b['open']<=target
        hit_stop=b['high']>=stop;hit_target=positive(target) and b['low']<=target
        if gap_stop:end('STOP_HIT',day,b['open']);r['events'][-1]['gapStop']=True;break
        if hit_stop and hit_target and not gap_target:end('NEEDS_REVIEW',day);break
        if gap_target or hit_target:
            fill=b['open'] if gap_target else target
            if not after_partial:
                fraction=.5;r['partialRealizedGross']+=fraction*(r['fillPrice']-fill*factor);r['remainingFraction']=.5
                r['partialCovers'].append({'date':day,'fraction':fraction,'price':fill,'type':'TARGET1_COVER_50_PERCENT'})
                r['events'].append({'date':day,'type':'TARGET1_COVER_50_PERCENT','price':fill,'remainingFraction':.5})
                r['target1Date']=day;r['firstTargetAchieved']=True;r['status']='ACTIVE'
                # If open is below target 1, later high can hit the original stop;
                # otherwise a same-bar original stop/target was caught above.
                if gap_target and hit_stop and positive(t2) and b['low']<=t2 and b['open']>t2:end('NEEDS_REVIEW',day);break
                if gap_target and hit_stop and (not positive(t2) or b['open']>t2):end('STOP_HIT',day,stop);break
                if positive(t2) and b['low']<=t2:
                    end('TARGET_HIT',day,b['open'] if b['open']<=t2 else t2);break
            else:end('TARGET_HIT',day,fill);break
        elif hit_stop:end('STOP_HIT',day,stop);break
        # Update only for next session; no current bar low is treated as an entry.
        prior=r['priorCompletedBars'];prior.append(deepcopy(b));r['priorCompletedBars']=prior[-40:]
        if r['partialCovers']:
            atr=o.get('atr14')/factor if positive(o.get('atr14')) else None
            if len(prior)>=15:
                # Execution-side prices are already on the current split scale.
                atr=indicators(adjusted_asof(prior))[-1]['atr14']
            if positive(atr):
                trail=b['close']+2*atr
                r['currentStop']=min(r['currentStop'],trail)
                r['trailKnownAt']=day;r['trailEffectiveFrom']='NEXT_SESSION'
        if r['holdingSessions']>=config.max_holding_days:end('TIME_EXIT',day,b['close']);break
    assert o==frozen and r['originalHash']==original_hash(frozen)
    return r
