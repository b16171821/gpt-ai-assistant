"""Reproducible technical-signal research, never executable short performance.

python -m scripts.short.backtest --roundtrip-bps 50 --slippage-bps 15 --borrow-rate .10
"""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from statistics import mean

from .config import CONFIG,ENTRY_SIGNALS
from .tw_v2 import TW_CONFIG
import shutil
from .engine import analyze_history,market_environment
from .sources import CACHE,SHORT_DATA
from .tracking import create_record,evaluate_record,tracking_statistics
from .validation import atomic_json,read_json,validate_bars


def research_period(day):
    if '2015-01-01'<=day<='2021-12-31':return 'TRAIN'
    if '2022-01-01'<=day<='2023-12-31':return 'VALIDATION'
    if day>='2024-01-01':return 'OUT_OF_SAMPLE'
    return None


def market_timeline(series):
    """Build once per market. Each classification sees only that day's prefix."""
    dates=sorted(set.intersection(*[{b['date'] for b in rows} for rows in series.values()])) if len(series)>=2 else []
    pointers={k:0 for k in series};out={}
    for day in dates:
        slices={}
        for k,rows in series.items():
            while pointers[k]<len(rows) and rows[pointers[k]]['date']<=day:pointers[k]+=1
            slices[k]=rows[max(0,pointers[k]-70):pointers[k]]
        out[day]=market_environment(slices,day)
    return out


def summarize(records):
    stats=tracking_statistics(records)
    done=[r for r in records if r.get('entryStatus')=='TRIGGERED' and r['status'] in {'TARGET_HIT','STOP_HIT','TIME_EXIT'} and r.get('netPnlPerOriginalShare') is not None]
    r_values=[r['netPnlPerOriginalShare']/(r['originalStrategy']['stopLoss']-r['fillPrice']) for r in done]
    wins=[x for x in r_values if x>0];losses=[x for x in r_values if x<0]
    streak=max_streak=0
    for r in sorted(done,key=lambda r:(r['endDate'],r['trackingId'])):
        streak=streak+1 if r['netPnlPerOriginalShare']<0 else 0
        max_streak=max(max_streak,streak)
    return {**stats,'sampleCount':len(records),'resolvedTradeCount':len(done),
            'averageWinLossRatio':mean(wins)/abs(mean(losses)) if wins and losses else None,
            'expectancyR':mean(r_values) if r_values else None,'maximumConsecutiveLosses':max_streak,
            'uncertaintyPolicy':'同日待確認樣本保留，達標率顯示上下界；已判定交易期望值另列，不混成可執行績效'}


def research_portfolio(records,market,*,start_equity=None,config=CONFIG):
    """Unlevered notional research ledger; no borrowed proceeds as buying power.

    A maximum of 5 simultaneous positions, 20% nominal exposure each, 100%
    aggregate nominal exposure, 1% risk, and 1% ADV. All are research settings,
    not broker margin rules. Ambiguous exits use a disclosed stop-first bound.
    """
    initial=start_equity or (1_000_000 if market=='TW' else 100_000)
    lot=1000 if market=='TW' else 1
    eligible=[r for r in records if r.get('entryStatus')=='TRIGGERED' and r.get('endDate')]
    eligible.sort(key=lambda r:(r['fillDate'],-(r['originalStrategy'].get('qualityScore') or 0),r['code']))
    entries=defaultdict(list);dates=set()
    for r in eligible:
        entries[r['fillDate']].append(r)
        dates.add(r['fillDate']);dates.add(r['endDate'])
        dates.update(x['date'] for x in r.get('marks',[]))
    active=[];cash=initial;equity=initial;peak=initial;max_dd=0;curve=[];admitted=[];capacity_skips=0;ambiguous=0
    for day in sorted(dates):
        # Positions closed intraday today still occupy capital at today's open.
        exposure=sum(p['shares']*p['record']['fillPrice'] for p in active)
        for r in entries.get(day,[]):
            costs=r.get('researchCostScenario',{'roundtripBps':50,'slippageBps':15,'annualBorrowRate':.10,'borrowCalendarDayBudget':35})
            entry,stop=r['fillPrice'],r['originalStrategy']['stopLoss']
            risk_per_share=stop-entry+entry*costs['roundtripBps']/10000+(entry+stop)*costs['slippageBps']/10000+entry*costs['annualBorrowRate']*costs['borrowCalendarDayBudget']/365
            if risk_per_share<=0:capacity_skips+=1;continue
            adv=r.get('averageVolume20') or 0
            quantity=min(equity*.01/risk_per_share,equity*.2/r['fillPrice'],adv*config.maximum_adv_participation)
            shares=max(0,int(quantity//lot)*lot)
            if len(active)>=5 or not shares or exposure+shares*r['fillPrice']>equity:
                capacity_skips+=1;continue
            active.append({'record':r,'shares':shares});exposure+=shares*r['fillPrice'];admitted.append(r['trackingId'])
        remaining=[];unrealized=0
        for position in active:
            r=position['record'];quantity=position['shares']
            if r['endDate']==day:
                net=r.get('netPnlPerOriginalShare')
                if net is None:
                    # Keep NEEDS_REVIEW in the report. This is the conservative
                    # curve bound, not a relabelled stop event or verified P&L.
                    net=-(r['originalStrategy']['stopLoss']-r['fillPrice'])-r['fillPrice']*.02
                    ambiguous+=1
                cash+=quantity*net
            else:
                mark=next((x for x in reversed(r.get('marks',[])) if x['date']<=day),None)
                if mark:
                    elapsed=max(1,(datetime.fromisoformat(day)-datetime.fromisoformat(r['fillDate'])).days)
                    borrow=r['fillPrice']*r.get('assumedAnnualBorrowRate',.10)*elapsed/365
                    unrealized+=quantity*(r['fillPrice']-mark['close']*mark.get('splitFactor',1)-mark.get('dividendLiability',0)-borrow)
                remaining.append(position)
        active=remaining;equity=cash+unrealized;peak=max(peak,equity)
        drawdown=(peak-equity)/peak*100 if peak else 0;max_dd=max(max_dd,drawdown)
        curve.append({'date':day,'equity':round(equity,2),'drawdownPct':round(drawdown,4)})
    # Downsample for delivery; max drawdown always uses the full daily series.
    step=max(1,len(curve)//300)
    return {'currency':'TWD' if market=='TW' else 'USD','initialResearchEquity':initial,
            'endingResearchEquityConservativeBound':equity,'maximumDrawdownPctConservativeBound':max_dd,
            'admittedTrades':len(admitted),'capacityOrUnitExclusions':capacity_skips,'ambiguousExitsInBound':ambiguous,
            'ledgerType':'UNLEVERED_NOTIONAL_RESEARCH_NOT_BROKER_ACCOUNT',
            'assumptions':{'riskPercent':1,'positionNotionalPercent':20,'aggregateNotionalPercent':100,'maximumPositions':5,'maximumAdvPercent':1,'borrowCalendarDayBudget':35},
            'curve':curve[::step]+([curve[-1]] if curve and curve[-1] not in curve[::step] else [])}


def simulate_symbol(stock,bars,environments,*,config=CONFIG,roundtrip_bps=50,slippage_bps=15,borrow_rate=.10):
    signals=analyze_history(bars,stock,include_charts=False)
    positions={b['date']:i for i,b in enumerate(bars)}
    records=[];busy_until='';counts=Counter();phase_sessions=defaultdict(list)
    for b in bars:
        phase=research_period(b['date'])
        if phase:phase_sessions[phase].append(b['date'])
    for signal in signals:
        day=signal['dataDate'];phase=research_period(day)
        if not phase:continue
        env=environments.get(day,{'code':'NO_DATA','allowEntry':False,'label':'資料不足'})
        if signal['originalSignal'] not in ENTRY_SIGNALS:continue
        counts['rawConfirmedSignals']+=1;counts['regime_'+env['code']]+=1
        if not signal['technicalCandidate']:counts['technicalRejected']+=1;continue
        if not env['allowEntry']:counts['marketFiltered']+=1;continue
        if day<=busy_until:counts['overlappingSignalsRetained']+=1;continue
        days=phase_sessions[phase];idx=days.index(day)
        if phase!='TRAIN' and idx<30:counts['embargoedAtAdmission']+=1;continue
        if idx+config.max_holding_days+1>=len(days):counts['boundaryPurgedAtAdmission']+=1;continue
        signal['marketEnvironment']=env['code']
        record=create_record(signal,origin='HISTORICAL_RESEARCH',config=config)
        record['assumedAnnualBorrowRate']=borrow_rate
        record['researchCostScenario']={'roundtripBps':roundtrip_bps,'slippageBps':slippage_bps,'annualBorrowRate':borrow_rate,'borrowCalendarDayBudget':35}
        start=positions[day]+1
        future=bars[start:start+config.max_holding_days+config.observation_days+2]
        record=evaluate_record(record,future,config=config,roundtrip_cost_bps=roundtrip_bps,slippage_bps=slippage_bps,annual_borrow_rate=borrow_rate,
                               expected_sessions=sorted(environments))
        record['researchPeriod']=phase;record['regimeAtSignal']=env['code'];record['averageVolume20']=signal['averageVolume20']
        if record.get('fillDate'):
            marks=[];factor=1;liability=0
            for b in future:
                if b['date']<record['fillDate']:continue
                if record['endDate'] and b['date']>record['endDate']:break
                factor*=b.get('split',0) or 1
                liability+=b.get('dividend',0)*factor
                marks.append({'date':b['date'],'close':b['close'],'splitFactor':factor,'dividendLiability':liability})
            record['marks']=marks
        if record.get('endDate'):busy_until=record['endDate']
        records.append(record)
    return records,dict(counts)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roundtrip-bps',type=float,default=50)
    parser.add_argument('--slippage-bps',type=float,default=15)
    parser.add_argument('--borrow-rate',type=float,default=.10)
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args(argv)
    for value in (args.roundtrip_bps,args.slippage_bps,args.borrow_rate):
        if value<0:raise ValueError('Costs cannot be negative')
    master=read_json(SHORT_DATA/'universe.json',{})
    tw_v2_active=str((read_json(SHORT_DATA/'tw_latest.json',{}) or {}).get('meta',{}).get('strategyVersion','')).startswith('short-tw-v2')
    previous=read_json(SHORT_DATA/'backtest_report.json',{})
    if tw_v2_active and previous.get('version')==CONFIG.version:
        archive=SHORT_DATA/'research_versions'/CONFIG.version;archive.mkdir(parents=True,exist_ok=True)
        for name in ('backtest_report.json','backtest_trades.json'):
            target=archive/name
            if not target.exists() and (SHORT_DATA/name).exists():shutil.copy2(SHORT_DATA/name,target)
    benchmarks=read_json(SHORT_DATA/'historical_benchmarks.json') or read_json(SHORT_DATA/'benchmarks.json',{})
    dates=benchmarks.get('meta',{}).get('dates',{})
    timelines={m:market_timeline(benchmarks.get(m,{})) for m in ('TW','US')}
    stocks=master.get('stocks',[])
    if args.limit:stocks=stocks[:args.limit]
    records=[];coverage=[];counts=Counter()
    for i,stock in enumerate(stocks):
        if tw_v2_active and stock['market']=='TW':
            coverage.append({'market':'TW','code':stock['code'],'usable':False,'evaluationStatus':'V2_NOT_RUN','issues':['新台股規格未完成回測，不沿用v1績效']});continue
        cached=read_json(CACHE/(stock['ticker']+'.json'))
        bars=[b for b in (cached or {}).get('bars',[]) if b['date']<=dates.get(stock['market'],'')]
        issues=validate_bars(bars)
        if issues or len(bars)<CONFIG.min_history:
            coverage.append({'market':stock['market'],'code':stock['code'],'usable':False,'issues':issues[:3] or ['INSUFFICIENT_HISTORY']});continue
        coverage.append({'market':stock['market'],'code':stock['code'],'usable':True,'firstBar':bars[0]['date'],'lastBar':bars[-1]['date'],
                         'historicalDataIssues':(cached or {}).get('historyIssues',[]),
                         'completeRequestedPeriod':bars[0]['date']<='2014-07-01' and not cached.get('historyIssues')})
        result,c=simulate_symbol(stock,bars,timelines[stock['market']],roundtrip_bps=args.roundtrip_bps,slippage_bps=args.slippage_bps,borrow_rate=args.borrow_rate)
        records.extend(result);counts.update(c)
        if (i+1)%50==0:print(f'Research {i+1}/{len(stocks)}; observations={len(records)}',flush=True)
    markets={}
    for market in ('TW','US'):
        subset=[r for r in records if r['market']==market]
        markets[market]={'summary':summarize(subset),'periods':{p:summarize([r for r in subset if r['researchPeriod']==p]) for p in ('TRAIN','VALIDATION','OUT_OF_SAMPLE')},
                         'regimes':{p:summarize([r for r in subset if r['regimeAtSignal']==p]) for p in ('FAVORABLE','WAIT','BULL_STRONG','NO_DATA')},
                         'portfolio':research_portfolio(subset,market)}
    if tw_v2_active:
        markets['TW'].update(status='NOT_RUN',version=TW_CONFIG.version,reason='新台股條件尚未完成回測，不能沿用v1績效或宣稱高勝率')
    markets['US']['version']=CONFIG.version
    report={'version':'short-mixed-tw-v2-us-v1' if tw_v2_active else CONFIG.version,'createdAt':datetime.now(timezone.utc).isoformat(),
            'status':'TECHNICAL_RESEARCH_WITH_UNRESOLVED_DATA_LIMITS','disclosure':('台股v2尚未完成回測；舊版研究已封存，不套用新條件。美股仍為v1技術研究。' if tw_v2_active else '')+'技術訊號回測，非可執行放空績效。現行存續名單含存活者偏誤，缺乏歷史券源與完整歷史限制；歷史資料不完整者已揭露，不能宣稱全市場或已驗證樣本外獲利。',
            'requestedPeriod':{'start':'2015-01-01','end':dates},'pointInTimeUniverse':False,'historicalBorrowInventory':'UNAVAILABLE',
            'historicalRestrictions':'UNAVAILABLE','earningsHistory':'UNAVAILABLE','parameters':CONFIG.manifest(),
            'parameterSelection':'使用使用者事前確認規格；未最佳化參數。歷史保留期間不是已驗證盲測績效。',
            'costScenario':{'roundtripBps':args.roundtrip_bps,'slippageBpsPerSide':args.slippage_bps,'annualBorrowRate':args.borrow_rate,
                            'type':'RESEARCH_ASSUMPTIONS_NOT_OFFICIAL_OR_BROKER_RATES'},
            'periods':{'TRAIN':['2015-01-01','2021-12-31'],'VALIDATION':['2022-01-01','2023-12-31'],'OUT_OF_SAMPLE':['2024-01-01',dates]},
            'embargoSessions':30,'boundaryPolicy':'排除跨界持有；條件在進場前按日期決定，不依輸贏排除',
            'coverage':{'requestedSymbols':len(stocks),'usableSymbols':sum(x['usable'] for x in coverage),
                        'completeRequestedHistorySymbols':sum(x.get('completeRequestedPeriod',False) for x in coverage),
                        'symbols':coverage},'signalCounts':dict(counts),'markets':markets,
            'limitations':['無歷史全市場／下市名單，存活者偏誤未排除','歷史券源、召回、融券與借券限制未取得，不能模擬真實可執行績效',
                           '財報公告時點歷史不完整，事件排除未驗證','Yahoo歷史異常區段已隔離，未以猜測補價',
                           '日K同日觸價順序未知；保留人工確認，達標率給上下界','研究組合非券商保證金帳戶，不模擬追繳與券商強制回補',
                           '成本為情境假設；股息與拆股僅使用生效日已知事件，不倒用未來調整因子']}
    atomic_json(SHORT_DATA/'backtest_report.json',report)
    # Full event records remain available for audit; charts/marks are omitted
    # from the downloadable journal, not losing outcomes or ambiguous events.
    atomic_json(SHORT_DATA/'backtest_trades.json',{'meta':{'scope':'TECHNICAL_ONLY','counts':dict(counts)},
                'records':[{k:v for k,v in r.items() if k!='marks'} for r in records]})
    from .public_data import publish_auxiliary
    publish_auxiliary()
    print({'symbols':len(stocks),'researchObservations':len(records),'markets':{m:markets[m]['summary'] for m in markets}},flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
