"""python -m scripts.short.run_update --start 2014-01-01

All writes are confined to data/short. Long strategies and data are read-only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json

from .config import CONFIG, ENTRY_SIGNALS
from .eligibility import OFFICIAL_SOURCES, RULE_SOURCES, attach_qualification, official_evidence
from .engine import analyze_history, market_environment
from .sources import SHORT_DATA, CACHE, completed_sessions, download_batch, load_histories, restriction_snapshots, tw_official, universe
from .tracking import reconcile_tracking
from .validation import ShortDataError, atomic_json, read_json, refresh_last_good, validate_bars


def rank(row):
    return (not row.get('dataFresh',False),not row.get('technicalCandidate',False),row.get('tooExtended',False),
            row.get('falseBreak',False),-(row.get('qualityScore') if row.get('qualityScore') is not None else -1),
            -(row.get('riskRewardRatio') or 0),row.get('riskPct') or 999,row['code'])


def invalid_row(stock, issues, date=None):
    return {**stock,'direction':'SHORT','strategyVersion':CONFIG.version,'dataDate':date,'close':None,'latestClose':None,
            'stage':'資料不足','originalSignal':'NO_SETUP','rawSignals':[],'adjustedSignal':'資料不足，僅供觀察',
            'statusReason':'；'.join(issues[:3]),'pressure':None,'neckline':None,'observationLow':None,'observationHigh':None,
            'entry':None,'entryBasis':'OBSERVATION_MIDPOINT','stopLoss':None,'target':None,'validPrices':False,
            'rewardPct':None,'riskPct':None,'riskRewardRatio':None,'qualityScore':None,'technicalCandidate':False,
            'technicalGrade':'C','dataFresh':False,'dataIssues':issues,'chart':[]}


def build_market(stocks, histories, expected, sessions, environment, official, snapshots, source_errors, requested_start='2014-01-01'):
    rows,bar_map,analysis_errors=[],{},[]
    for stock in stocks:
        ticker=stock['ticker'];bars=deepcopy(histories.get(ticker,[]))
        bars=[b for b in bars if b['date']<=expected]
        if stock['market']=='TW' and official:
            quote=official['quotes'].get(stock['code'])
            if quote:
                bar={'date':quote['date'],**{k:quote[k] for k in ('open','high','low','close','volume')},'dividend':0,'split':0}
                previous=next((x for x in bars if x['date']==expected),None)
                if previous:
                    bar['dividend']=previous.get('dividend',0);bar['split']=previous.get('split',0)
                bars=[x for x in bars if x['date']!=expected]+[bar]
                bars.sort(key=lambda b:b['date'])
        issues=validate_bars(bars)
        if issues or len(bars)<CONFIG.min_history:
            row=invalid_row(stock,issues or ['INSUFFICIENT_HISTORY'],bars[-1]['date'] if bars else None)
        else:
            try:
                results=analyze_history(bars,stock)
                if not results:raise ShortDataError('No warmed-up analysis')
                row=results[-1]
                recent_start=bars[-CONFIG.min_history]['date']
                # Missing official exchange sessions are not filled. Suspended or
                # newly listed securities remain observations pending verification.
                date_set={b['date'] for b in bars}
                missing=[s for s in sessions if recent_start<=s<=expected and s not in date_set]
                issues=([f'STALE_BARS:{bars[-1]["date"]}'] if bars[-1]['date']!=expected else [])
                if missing:issues.append(f'MISSING_RECENT_SESSIONS:{len(missing)}')
                quote=(official or {}).get('quotes',{}).get(stock['code']) if stock['market']=='TW' else None
                if stock['market']=='TW' and (not quote or quote.get('date')!=expected):
                    issues.append('OFFICIAL_CLOSE_UNVERIFIED')
                row['dataIssues']=issues
                row['dataFresh']=not issues
                row['source']='TWSE／TPEx 官方收盤＋Yahoo Finance歷史' if stock['market']=='TW' else 'Yahoo Finance完整日K'
                row['calendarSource']='exchange_calendars XTAI／XNYS；台股最新日另以官方收盤核對'
                # Persist validated raw bars only under the short cache namespace.
                cached_meta=read_json(CACHE/(ticker+'.json'),{})
                row['historyDataIssues']=cached_meta.get('historyIssues',[])
                row['trustedHistoryStart']=bars[0]['date']
                row['historicalResearchUsable']=not row['historyDataIssues']
                atomic_json(CACHE/(ticker+'.json'),{**cached_meta,'requestedStart':requested_start,
                            'dataDate':bars[-1]['date'],'bars':bars})
                bar_map[f"{stock['market']}:{stock['code']}"]=bars
            except Exception as exc:
                row=invalid_row(stock,[f'ANALYSIS_ERROR:{type(exc).__name__}:{exc}'],bars[-1]['date'] if bars else None)
                analysis_errors.append(f'{ticker}:{exc}')
        evidence=official_evidence(stock,snapshots,source_errors,expected)
        row=attach_qualification(row,environment,evidence,data_fresh=row.get('dataFresh',False))
        rows.append(row)
    rows.sort(key=rank)
    fresh=sum(r.get('dataFresh',False) for r in rows)
    selected=[r for r in rows if r.get('dataFresh') and r.get('validPrices') and not r.get('tooExtended') and not r.get('falseBreak')
              and r.get('qualityScore',0) is not None and r.get('qualityScore',0)>=CONFIG.reserve_score
              and r.get('originalSignal') in ENTRY_SIGNALS|{'BEAR_TREND'}][:5]
    selected_ids={r['code'] for r in selected}
    for row in rows:row['priorityObservation']=row['code'] in selected_ids
    coverage=fresh/len(stocks)*100 if stocks else 0
    market=stocks[0]['market'] if stocks else None
    payload={'meta':{'direction':'SHORT','strategyVersion':CONFIG.version,'market':market,
                     'currency':'TWD' if market=='TW' else 'USD','dataDate':expected,'expectedDataDate':expected,
                     'updatedAt':datetime.now(timezone.utc).isoformat(),'freshnessStatus':'COMPLETE' if coverage>=95 else 'PARTIAL',
                     'universeCount':len(stocks),'analyzedCount':sum(r['close'] is not None for r in rows),
                     'freshCount':fresh,'staleCount':len(stocks)-fresh,'coveragePct':coverage,
                     'priorityCount':len(selected),'executableCount':sum(r['tradeQualification']['allowEntry'] for r in rows),
                     'scope':'現行名單，非全市場歷史名單','pointInTimeUniverse':False,
                     'pricePolicy':'完整收盤，觀察區中間值；次一交易日才可模擬進場',
                     'errors':analysis_errors,'rulesLastCheckedAt':None,
                     'rulesStatus':'券商庫存、完整限制及未來事件未核實，不允許標為實際可下單'},
             'marketEnvironment':environment,'parameters':CONFIG.manifest(),'stocks':rows}
    return payload,bar_map,selected


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',default='2014-01-01')
    parser.add_argument('--limit',type=int,default=0,help='Research-only subset, explicitly reported')
    parser.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    SHORT_DATA.mkdir(parents=True,exist_ok=True)
    master=universe()
    if args.limit:
        master['stocks']=[s for market in ('TW','US') for s in [x for x in master['stocks'] if x['market']==market][:args.limit]]
        master['meta']['subsetLimit']=args.limit
    atomic_json(SHORT_DATA/'universe.json',master)
    sessions,dates,calendars={},{},{}
    for market in ('TW','US'):
        sessions[market],calendars[market]=completed_sessions(market,start=args.start)
        dates[market]=sessions[market][-1]
    print('Completed sessions:',dates,flush=True)
    official,indexes_tw=None,{}
    errors=[]
    try:
        official,indexes_tw=tw_official()
        if official['officialDate']!=dates['TW']:
            raise ShortDataError(f'Official TW close {official["officialDate"]} differs from completed calendar {dates["TW"]}')
        # Taiwan's official published index sessions take precedence over a
        # library holiday calendar (including newly announced closure days).
        sessions['TW']=sorted({b['date'] for bars in indexes_tw.values() for b in bars})
    except Exception as exc:
        official=None;indexes_tw={};errors.append(f'TW_OFFICIAL_UNAVAILABLE:{exc}')
    indexes_us_raw=download_batch(['^GSPC','^IXIC'],start=args.start)
    indexes_us={name:[x for x in indexes_us_raw.get(ticker,[]) if x['date']<=dates['US']]
                for name,ticker in [('S&P 500','^GSPC'),('NASDAQ','^IXIC')]}
    environments={'TW':market_environment(indexes_tw,dates['TW']),'US':market_environment(indexes_us,dates['US'])}
    snapshots,source_errors=restriction_snapshots()
    atomic_json(SHORT_DATA/'eligibility_sources.json',{'meta':{'fetchedAt':datetime.now(timezone.utc).isoformat(),
                'sourceDatesAreNotFetchDates':True,'brokerInventory':'UNAVAILABLE'},'sources':OFFICIAL_SOURCES,
                'ruleSources':RULE_SOURCES,'errors':source_errors,'rows':snapshots})
    print('Restrictions fetched; downloading independent histories',flush=True)
    histories,download_errors=load_histories(master['stocks'],dates,start=args.start,force=args.force,official=official,
        progress=lambda i,n,ok,bad:print(f'History batch {i}/{n}: available={ok}, errors={bad}',flush=True))
    errors+=download_errors
    tracking=read_json(SHORT_DATA/'tracking.json',{'records':[]})
    stock_metadata={(s['market'],s['code']):s for s in master['stocks']}
    for record in tracking.get('records',[]):
        meta=stock_metadata.get((record['market'],record['code']))
        if meta:
            record.setdefault('exchange',meta['exchange'])
            record.setdefault('ticker',meta['ticker'])
        if not record.get('earliestEntryAt'):
            calendar=calendars[record['market']]
            next_day=calendar.date_to_session(record['firstSignalDate'],direction='next')
            if next_day.date().isoformat()<=record['firstSignalDate']:
                next_day=calendar.next_session(next_day)
            record['nextEntrySessionDate']=next_day.date().isoformat()
            record['earliestEntryAt']=calendar.session_open(next_day).isoformat()
    all_bars,all_selected={},[]
    results={}
    for market in ('TW','US'):
        stock_list=[s for s in master['stocks'] if s['market']==market]
        def produce():
            if market=='TW' and not official:raise ShortDataError('Official TW data unavailable; preserve last-good market snapshot')
            payload,bar_map,selected=build_market(stock_list,histories,dates[market],sessions[market],environments[market],official,
                                                 snapshots,source_errors,args.start)
            if not payload['meta']['analyzedCount']:raise ShortDataError('No valid closing data')
            for selected_row in selected:
                calendar=calendars[market]
                next_day=calendar.next_session(calendar.date_to_session(selected_row['dataDate']))
                selected_row['nextEntrySessionDate']=next_day.date().isoformat()
                selected_row['earliestEntryAt']=calendar.session_open(next_day).isoformat()
            all_bars.update(bar_map);all_selected.extend(selected)
            return payload
        payload,ok=refresh_last_good(SHORT_DATA/('tw_latest.json' if market=='TW' else 'us_latest.json'),produce)
        results[market]={'success':ok,'dataDate':(payload or {}).get('meta',{}).get('dataDate'),
                         'freshCount':(payload or {}).get('meta',{}).get('freshCount'),
                         'universeCount':len(stock_list),'environment':environments[market]}
    # Records absent from today's universe still refresh using their own cached
    # history; unavailable histories preserve the previous state, never erase it.
    for record in tracking.get('records',[]):
        key=f"{record['market']}:{record['code']}"
        if key not in all_bars:
            suffix=record['code'] if record['market']=='US' else record['code']+('.TWO' if record.get('exchange')=='TPEX' else '.TW')
            cached=read_json(CACHE/(suffix+'.json'))
            if cached:all_bars[key]=[r for r in cached['bars'] if r['date']<=dates[record['market']]]
    updated=reconcile_tracking(tracking,all_selected,all_bars,expected_sessions_by_market=sessions)
    atomic_json(SHORT_DATA/'tracking.json',updated)
    locks=read_json(SHORT_DATA/'locks.json',{})
    for record in updated['records']:
        value={'firstSignalDate':record['firstSignalDate'],'originalStrategy':record['originalStrategy'],'hash':record['originalHash']}
        if record['trackingId'] in locks and locks[record['trackingId']]!=value:raise ShortDataError('Original short lock changed')
        locks.setdefault(record['trackingId'],value)
    atomic_json(SHORT_DATA/'locks.json',locks)
    saved=read_json(SHORT_DATA/'snapshots.json',{'records':[]})
    keys={(r['market'],r['code'],r['dataDate'],r['originalSignal']) for r in saved['records']}
    for row in all_selected:
        key=(row['market'],row['code'],row['dataDate'],row['originalSignal'])
        if key not in keys:saved['records'].append(row);keys.add(key)
    atomic_json(SHORT_DATA/'snapshots.json',saved)
    atomic_json(SHORT_DATA/'benchmarks.json',{'meta':{'dates':dates,'pointInTimePriceCutoff':True},'TW':indexes_tw,'US':indexes_us})
    health={'meta':{'updatedAt':datetime.now(timezone.utc).isoformat(),'direction':'SHORT',
                    'status':'WARNING' if errors or source_errors else 'HEALTHY'},
            'markets':results,'errors':errors,'officialSourceErrors':source_errors,
            'limits':['券商庫存未接入','歷史券源與歷史全市場名單不足','未核實的制度與事件均不視為可交易']}
    atomic_json(SHORT_DATA/'health.json',health)
    from .public_data import main as publish_public_data
    publish_public_data()
    print(json.dumps({'markets':{m:{k:v for k,v in r.items() if k!='environment'} for m,r in results.items()},
                      'tracking':len(updated['records']),'errors':len(errors)},ensure_ascii=False),flush=True)
    return 0 if all(r['success'] for r in results.values()) else 1


if __name__=='__main__':raise SystemExit(main())
