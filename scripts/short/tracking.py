"""Chronological short lifecycle with immutable plans and explicit entry events."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from .config import CONFIG, ENTRY_SIGNALS
from .validation import price_metrics, positive, validate_bars

TERMINAL={'TARGET_HIT','STOP_HIT','TIME_EXIT','EXPIRED','CANCELLED','NEEDS_REVIEW','INVALID_AT_CREATION'}


def original_hash(original):
    return hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def create_record(row, *, origin='LIVE_SIGNAL', config=CONFIG, created_at=None):
    original={k:deepcopy(row.get(k)) for k in (
        'dataDate','originalSignal','pressure','referenceKnownAt','observationLow','observationHigh',
        'entry','entryBasis','stopLoss','target','qualityScore','riskRewardRatio','strategyVersion','signalWitnesses','atr14')}
    original['marketEnvironment']=row.get('marketEnvironment','NO_DATA')
    original['technicalCandidate']=bool(row.get('technicalCandidate'))
    original['tradeTier']=row.get('tradeTier','OBSERVATION_ONLY')
    record={'trackingId':f"SHORT:{row['market']}:{row['code']}:{row['dataDate']}:{row['originalSignal']}:{config.version}",
            'direction':'SHORT','market':row['market'],'currency':row['currency'],'exchange':row.get('exchange'),
            'ticker':row.get('ticker'),'code':row['code'],'name':row['name'],
            'firstSignalDate':row['dataDate'],'createdAt':created_at or datetime.now(timezone.utc).isoformat(),
            'earliestEntryAt':row.get('earliestEntryAt'),'nextEntrySessionDate':row.get('nextEntrySessionDate'),
            'origin':origin,'originalStrategy':original,'originalHash':original_hash(original),
            'entryStatus':'NOT_TRIGGERED','status':'WAIT_ENTRY','fillPrice':None,'fillDate':None,
            'entryWindowEvaluated':False,'lastEvaluatedDate':row['dataDate'],'observedSessions':0,'holdingSessions':0,
            'events':[],'latestStatus':{'dataDate':row['dataDate'],'close':row['close'],'signal':row['originalSignal']},
            'result':None,'endDate':None,'exitPrice':None,'grossPnlPerOriginalShare':None,'netPnlPerOriginalShare':None,
            'splitFactor':1.0,'dividendLiability':0.0,'assumedAnnualBorrowRate':0.10,
            'performanceScope':'TECHNICAL_ONLY','executionConfirmed':False,
            'notes':[], 'qualificationAtCreation':deepcopy(row.get('tradeQualification',{}))}
    valid=price_metrics(original['entry'],original['stopLoss'],original['target'])['validPrices']
    if not valid:
        record.update(status='INVALID_AT_CREATION',endDate=row['dataDate'],result='原始價位不足，不計績效')
    return record


def entry_timing_verified(record):
    if record.get('origin')=='HISTORICAL_RESEARCH':
        return True
    try:
        created=datetime.fromisoformat(record['createdAt'].replace('Z','+00:00'))
        opening=datetime.fromisoformat(record['earliestEntryAt'].replace('Z','+00:00'))
        return created.tzinfo is not None and opening.tzinfo is not None and created<opening
    except (KeyError,TypeError,ValueError,AttributeError):
        return False


def finish(record, status, day, exit_price=None, *, slippage_bps=0, roundtrip_cost_bps=0, annual_borrow_rate=None):
    record.update(status=status,endDate=day,exitPrice=exit_price)
    labels={'TARGET_HIT':'目標達成','STOP_HIT':'停損結束','TIME_EXIT':'持有到期結束','EXPIRED':'未觸發到期',
            'CANCELLED':'未進場取消','NEEDS_REVIEW':'同日觸價，需人工確認'}
    record['result']=labels.get(status,status)
    record['events'].append({'date':day,'type':status,'price':exit_price})
    if record['entryStatus']=='TRIGGERED' and exit_price is not None:
        factor=record['splitFactor']
        gross=record['fillPrice']-exit_price*factor-record['dividendLiability']
        days=(datetime.fromisoformat(day)-datetime.fromisoformat(record['fillDate'])).days
        rate=record['assumedAnnualBorrowRate'] if annual_borrow_rate is None else annual_borrow_rate
        borrow=record['fillPrice']*rate*max(1,days)/365
        costs=record['fillPrice']*roundtrip_cost_bps/10000 + (record['fillPrice']+exit_price*factor)*slippage_bps/10000
        record.update(grossPnlPerOriginalShare=gross,netPnlPerOriginalShare=gross-costs-borrow,
                      costAssumptions={'roundtripCostBps':roundtrip_cost_bps,'slippageBpsPerSide':slippage_bps,
                                       'annualBorrowRate':rate,'borrowDays':max(1,days),'borrowPerShare':borrow})
    return record


def evaluate_record(record, bars, *, config=CONFIG, slippage_bps=0, roundtrip_cost_bps=0, annual_borrow_rate=None, expected_sessions=None):
    r=deepcopy(record)
    immutable=deepcopy(r['originalStrategy'])
    if r.get('originalHash')!=original_hash(immutable):
        raise ValueError('Immutable original short strategy was changed')
    if r['status'] in TERMINAL:
        return r
    # Duplicate daily bars must not silently change event order or trigger twice.
    issues=validate_bars(bars)
    if issues:
        r['dataIssues']=issues
        return r
    args={'slippage_bps':slippage_bps,'roundtrip_cost_bps':roundtrip_cost_bps,'annual_borrow_rate':annual_borrow_rate}
    available={b['date'] for b in bars}
    missing=sorted(s for s in (expected_sessions or []) if r['lastEvaluatedDate']<s<=bars[-1]['date'] and s not in available) if bars else []
    first_gap=missing[0] if missing else None
    if first_gap:
        r['coverageStatus']='GAP_REQUIRES_REVIEW'
        r.setdefault('dataIssues',[]).append('MISSING_LIFECYCLE_SESSION:'+first_gap)
    for bar in bars:
        day=bar['date']
        if day<=r['firstSignalDate'] or day<=r['lastEvaluatedDate']:
            continue
        if first_gap and day>=first_gap:
            break
        if bar['volume']<=0:
            r.setdefault('dataIssues',[]).append(f'NO_EXECUTABLE_VOLUME:{day}')
            continue
        r['lastEvaluatedDate']=day
        r['observedSessions']+=1
        r['latestStatus']={'dataDate':day,'close':bar['close'],'high':bar['high'],'low':bar['low']}
        split=bar.get('split',0)
        dividend=bar.get('dividend',0)
        if r['entryStatus']!='TRIGGERED':
            if split or dividend:
                finish(r,'CANCELLED',day,**args)
                r['result']='公司行動改變原始價位，未進場計畫取消'
                break
            if not r['entryWindowEvaluated']:
                r['entryWindowEvaluated']=True
                timing_ok=entry_timing_verified(r)
                eligible=timing_ok and immutable['originalSignal'] in ENTRY_SIGNALS and immutable['technicalCandidate'] and immutable['marketEnvironment']=='FAVORABLE'
                if eligible and immutable['observationLow']<=bar['open']<=immutable['observationHigh'] and immutable['stopLoss']>bar['open']>immutable['target']:
                    r.update(entryStatus='TRIGGERED',fillPrice=bar['open'],fillDate=day,status='ACTIVE')
                    r['events'].append({'date':day,'type':'ENTRY_AT_NEXT_OPEN','price':bar['open'],
                                        'basis':'TECHNICAL_SIMULATION_NOT_BROKER_FILL'})
                else:
                    r['events'].append({'date':day,'type':'ENTRY_NOT_FILLED','reason':
                        '紀錄建立晚於預定開盤或時間資料不足，不回填過去成交' if not timing_ok else '次日開盤不在觀察區，或事前條件不允許'})
            if r['entryStatus']!='TRIGGERED':
                reclaimed=positive(immutable.get('pressure')) and positive(immutable.get('atr14')) and bar['close']>=immutable['pressure']+config.reclaim_atr*immutable['atr14']
                if bar['close']>=immutable['stopLoss'] or reclaimed:
                    finish(r,'CANCELLED',day,**args)
                elif r['observedSessions']>=config.observation_days:
                    finish(r,'EXPIRED',day,**args)
                if r['status'] in TERMINAL:break
                continue
        # A split updates derived execution levels/quantity, never the original plan.
        if split:
            r['splitFactor']*=split
            r['events'].append({'date':day,'type':'SPLIT','factor':split})
        if dividend:
            r['dividendLiability']+=dividend*r['splitFactor']
            r['events'].append({'date':day,'type':'SHORT_DIVIDEND_LIABILITY','amount':dividend*r['splitFactor']})
        stop=immutable['stopLoss']/r['splitFactor']
        target=immutable['target']/r['splitFactor']
        r['holdingSessions']+=1
        r['executionLevels']={'stopLoss':stop,'target':target,'splitFactor':r['splitFactor']}
        # For positions held overnight, the opening print gives known chronology.
        if day!=r['fillDate'] and bar['open']>=stop:
            finish(r,'STOP_HIT',day,bar['open'],**args)
            r['events'][-1]['gapStop']=True
            break
        if day!=r['fillDate'] and bar['open']<=target:
            finish(r,'TARGET_HIT',day,bar['open'],**args)
            break
        hit_target,hit_stop=bar['low']<=target,bar['high']>=stop
        if hit_target and hit_stop:
            finish(r,'NEEDS_REVIEW',day,**args)
            break
        if hit_stop:
            finish(r,'STOP_HIT',day,stop,**args)
            break
        if hit_target:
            finish(r,'TARGET_HIT',day,target,**args)
            break
        if r['holdingSessions']>=config.max_holding_days:
            finish(r,'TIME_EXIT',day,bar['close'],**args)
            break
    assert r['originalStrategy']==immutable
    assert r['originalHash']==original_hash(immutable)
    return r


def reconcile_tracking(previous, selected_rows, histories, *, config=CONFIG, expected_sessions_by_market=None):
    records=[deepcopy(r) for r in (previous or {}).get('records',[])]
    ids={r['trackingId'] for r in records}
    for row in selected_rows:
        new=create_record(row,config=config)
        if new['trackingId'] not in ids:
            records.append(new);ids.add(new['trackingId'])
    result=[]
    for record in records:
        key=f"{record['market']}:{record['code']}"
        bars=histories.get(key)
        if bars:
            record=evaluate_record(record,bars,config=config,slippage_bps=15,roundtrip_cost_bps=50,
                                   expected_sessions=(expected_sessions_by_market or {}).get(record['market']))
        elif record['status'] not in TERMINAL:
            record.setdefault('notes',[]).append({'type':'DATA_UNAVAILABLE','message':'本次未取得完整資料，保留原始策略與追蹤狀態'})
        result.append(record)
    result.sort(key=lambda r:(r['status'] in TERMINAL,r['firstSignalDate'],r['code']),reverse=True)
    return {'meta':{'direction':'SHORT','version':config.version,'updatedAt':datetime.now(timezone.utc).isoformat(),
                    'recordCount':len(result),'performanceScope':'TECHNICAL_ONLY','actualExecutedTrades':0,
                    'note':'已觸發僅指技術價格模擬，非券商成交紀錄；原始策略與首次訊號日期固定'},'records':result}


def tracking_statistics(records):
    filled=[r for r in records if r.get('entryStatus')=='TRIGGERED']
    completed=[r for r in filled if r['status'] in {'TARGET_HIT','STOP_HIT','TIME_EXIT'}]
    review=sum(r['status']=='NEEDS_REVIEW' for r in filled)
    hits=sum(r['status']=='TARGET_HIT' for r in completed)
    resolved=len(completed)
    # Report bounds whenever bar order is unresolved, rather than silently
    # dropping ambiguous samples and inflating a displayed success rate.
    return {'totalObservations':len(records),'triggered':len(filled),'untriggered':len(records)-len(filled),
            'completed':resolved,'targetHits':hits,'stops':sum(r['status']=='STOP_HIT' for r in completed),
            'needsReview':review,'targetRate':hits/resolved*100 if resolved and not review else None,
            'targetRateLower':hits/(resolved+review)*100 if resolved+review else None,
            'targetRateUpper':(hits+review)/(resolved+review)*100 if resolved+review else None,
            'actualExecutedTrades':0,'performanceScope':'TECHNICAL_ONLY'}
