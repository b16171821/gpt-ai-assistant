"""TW-only user specification, 2026-11-08. Pure, as-of causal analysis.

US v1 and all long-side modules remain untouched. No broker inference here.
"""
from copy import deepcopy
from dataclasses import dataclass
from statistics import mean
from .config import ShortConfig
from .validation import positive,price_metrics,validate_bars,ShortDataError

@dataclass(frozen=True)
class TaiwanConfig(ShortConfig):
    version: str='short-tw-v2-user-2026-11-08'
    min_history: int=265
    ma_year: int=240
    decline_sessions: int=60
    minimum_decline: float=.30
    break_volume: float=2.0
    adx_period: int=14
    minimum_adx: float=25.0
    support_break_pct: float=.02
    maximum_support_distance: float=.05
    priority_stop_pct: float=3.0
    maximum_planned_risk_pct: float=1.0
    max_long_lower_wick: float=.40
    max_bull_body: float=.60
    rebound_volume_ratio: float=.70
    pattern_lookback: int=90
    double_top_tolerance: float=.03
    shoulder_tolerance: float=.05
    head_minimum_height: float=.03
    flag_pole_decline: float=.08
    order_zone_pct: float=.002
    trail_atr: float=2.0
    partial_cover_fraction: float=.50

TW_CONFIG=TaiwanConfig()
PATTERNS={'HEAD_SHOULDERS':'頭肩頂','DOUBLE_TOP':'M頭','BOX':'箱型跌破','TRIANGLE':'三角收斂跌破','BEAR_FLAG':'空頭旗型'}


def adjusted_asof(bars,*,split_adjusted=False):
    out=deepcopy(bars);factor=1.;cash=0.
    # Only events effective in the supplied prefix are known at this as-of.
    for i in range(len(out)-1,-1,-1):
        raw=bars[i]
        for k in ('open','high','low','close'):out[i][k]=raw[k]/factor-cash
        out[i]['volume']=raw['volume']*factor
        split=raw.get('split',0) or 1
        cash+=(raw.get('dividend',0) or 0)/factor
        factor*=1 if split_adjusted else split
    if any(not positive(b[k]) for b in out for k in ('open','high','low','close')):
        raise ShortDataError('NON_POSITIVE_ASOF_ADJUSTED_HISTORY')
    return out


def indicators(bars,n=14):
    """Wilder sum smoothing; first DI at index n, first ADX at 2n-1.

    Volume MA excludes current bar. ADX starts at the first trusted history
    bar, never uses future adjusted-close factors.
    """
    out=[];trs=[];plus=[];minus=[];dxs=[];st=sp=sm=adx=None
    closes=[];volumes=[];close_sums=[0.];volume_sums=[0.]
    for i,b in enumerate(bars):
        closes.append(b['close']);close_sums.append(close_sums[-1]+b['close'])
        if i:
            p=bars[i-1];up=b['high']-p['high'];down=p['low']-b['low']
            trs.append(max(b['high']-b['low'],abs(b['high']-p['close']),abs(b['low']-p['close'])))
            plus.append(up if up>down and up>0 else 0.)
            minus.append(down if down>up and down>0 else 0.)
            if i==n:st,sp,sm=sum(trs),sum(plus),sum(minus)
            elif i>n:st=st-st/n+trs[-1];sp=sp-sp/n+plus[-1];sm=sm-sm/n+minus[-1]
        pdi=100*sp/st if st and st>0 else (0. if st==0 else None)
        mdi=100*sm/st if st and st>0 else (0. if st==0 else None)
        dx=100*abs(pdi-mdi)/(pdi+mdi) if pdi is not None and pdi+mdi else (0. if pdi is not None else None)
        if dx is not None:
            dxs.append(dx)
            if len(dxs)==n:adx=mean(dxs)
            elif len(dxs)>n:adx=(adx*(n-1)+dx)/n
        out.append({'ma20':(close_sums[-1]-close_sums[-21])/20 if i>=19 else None,'ma60':(close_sums[-1]-close_sums[-61])/60 if i>=59 else None,
                    'ma240':(close_sums[-1]-close_sums[-241])/240 if i>=239 else None,'atr14':st/n if st is not None else None,
                    'adx14':adx,'plusDI14':pdi,'minusDI14':mdi,
                    'averageVolume20':(volume_sums[-1]-volume_sums[-21])/20 if len(volumes)>=20 else None,
                    'return60':closes[-1]/closes[-61]-1 if len(closes)>=61 else None})
        volumes.append(b['volume']);volume_sums.append(volume_sums[-1]+b['volume'])
    return out


def pivots(bars,side=2):
    highs=[];lows=[]
    for i in range(side,len(bars)-side):
        w=bars[i-side:i+side+1]
        if all(bars[i]['high']>b['high'] for j,b in enumerate(w) if j!=side):highs.append((i,bars[i]['high']))
        if all(bars[i]['low']<b['low'] for j,b in enumerate(w) if j!=side):lows.append((i,bars[i]['low']))
    return highs,lows


def line(points,x):
    (a,pa),(b,pb)=points
    return pa+(pb-pa)*(x-a)/(b-a)


def structures(prior,atr,config=TW_CONFIG):
    """All vertices have two completed right bars in `prior`; no signal bar."""
    if len(prior)<30 or not positive(atr):return []
    offset=max(0,len(prior)-config.pattern_lookback);bars=prior[offset:];end=len(bars)
    hs,ls=pivots(bars,config.pivot_side);out=[]
    def add(kind,level,height,vertices):
        if positive(level) and height>atr and level-height>0:
            out.append({'kind':kind,'name':PATTERNS[kind],'level':level,'height':height,'measuredTarget':level-height,
                        'measuredTarget2':level-1.5*height if level-1.5*height>0 else None,
                        'referenceKnownAt':prior[-1]['date'],
                        'vertices':[{'date':bars[i]['date'],'price':p,'knownAt':bars[min(i+2,end-1)]['date']} for i,p in vertices]})
    box=bars[-20:];bottom=min(b['low'] for b in box);top=max(b['high'] for b in box)
    touches=[i for i,b in enumerate(box) if b['low']<=bottom+.35*atr]
    if (top-bottom)/bottom<=config.platform_max_width and any(b-a>=3 for a in touches for b in touches):
        add('BOX',bottom,top-bottom,[])
    recent=hs[-7:]
    for a,b in zip(recent,recent[1:]):
        if not 5<=b[0]-a[0]<=40 or abs(a[1]-b[1])/max(a[1],b[1])>config.double_top_tolerance:continue
        valleys=[p for p in ls if a[0]<p[0]<b[0]]
        if valleys:
            v=min(valleys,key=lambda p:p[1]);add('DOUBLE_TOP',v[1],min(a[1],b[1])-v[1],[a,v,b])
    for a,h,b in zip(recent,recent[1:],recent[2:]):
        if h[1]<max(a[1],b[1])*(1+config.head_minimum_height) or abs(a[1]-b[1])/max(a[1],b[1])>config.shoulder_tolerance:continue
        left=[p for p in ls if a[0]<p[0]<h[0]];right=[p for p in ls if h[0]<p[0]<b[0]]
        if left and right:
            p,q=min(left,key=lambda x:x[1]),min(right,key=lambda x:x[1]);neck=line((p,q),end)
            add('HEAD_SHOULDERS',neck,h[1]-line((p,q),h[0]),[a,p,h,q,b])
    if len(hs)>=2 and len(ls)>=2:
        a,b=hs[-2:];p,q=ls[-2:]
        if b[1]<a[1] and q[1]>p[1] and min(a[0],p[0])>=end-50:
            upper,lower=line((a,b),end),line((p,q),end)
            if lower<upper and upper-lower<max(a[1],b[1])-min(p[1],q[1]):
                add('TRIANGLE',lower,max(a[1],b[1])-min(p[1],q[1]),[a,b,p,q])
    flag=bars[-10:];pole=bars[-21:-10]
    if len(pole)>=10 and pole[-1]['close']/pole[0]['close']-1<=-config.flag_pole_decline:
        lower_a=min(flag[:5],key=lambda b:b['low']);lower_b=min(flag[5:],key=lambda b:b['low'])
        if lower_b['low']>lower_a['low'] and flag[-1]['close']>flag[0]['close'] and mean(b['volume'] for b in flag)<=mean(b['volume'] for b in pole)*config.rebound_volume_ratio:
            a=flag.index(lower_a);b=flag.index(lower_b)
            level=line(((a,lower_a['low']),(b,lower_b['low'])),10)
            if (max(x['high'] for x in flag)-min(x['low'] for x in flag))/level<=.10:
                add('BEAR_FLAG',level,pole[0]['high']-pole[-1]['low'],[])
    return out


def candle_flags(bar,average_volume,config=TW_CONFIG):
    spread=bar['high']-bar['low'];body=abs(bar['close']-bar['open'])
    lower=min(bar['open'],bar['close'])-bar['low']
    ratio=bar['volume']/average_volume if positive(average_volume) else None
    return {'longLowerWick':spread>0 and lower/spread>=config.max_long_lower_wick,
            'explosiveBull':bar['close']>bar['open'] and spread>0 and body/spread>=config.max_bull_body and ratio is not None and ratio>=2,
            'closeNearLow':spread>0 and (bar['close']-bar['low'])/spread<=config.close_location_max,
            'volumeRatio':ratio}


def analyze_latest(raw,meta=None,config=TW_CONFIG):
    issues=validate_bars(raw)
    if issues:raise ShortDataError(';'.join(issues[:3]))
    if len(raw)<config.min_history:raise ShortDataError('TW_V2_REQUIRES_265_COMPLETED_BARS')
    bars=adjusted_asof(raw,split_adjusted=(meta or {}).get('historyConvention')=='YAHOO_SPLIT_ADJUSTED');inds=indicators(bars);now=bars[-1];ind=inds[-1];atr=ind['atr14'];n=len(bars)-1;c=now['close']
    if not positive(atr):raise ShortDataError('ATR_MISSING')
    flags=candle_flags(now,ind['averageVolume20']);setups=[];fakes=[]
    for i in range(max(30,n-config.retest_days),n+1):
        pa=inds[i-1]['atr14']
        for p in structures(bars[:i],pa,config):
            level=p['level'];b=bars[i];cf=candle_flags(b,inds[i]['averageVolume20'])
            if not (b['close']<=level*(1-config.support_break_pct) and cf['volumeRatio'] is not None and cf['volumeRatio']>=2 and cf['closeNearLow'] and not cf['longLowerWick'] and not cf['explosiveBull']):continue
            reclaimed=any(x['close']>=level for x in bars[i+1:])
            if reclaimed:
                fakes.append(p);continue
            age=n-i
            if age<=3:
                setups.append({**p,'breakIndex':i,'breakDate':b['date'],'age':age,'signal':'PLATFORM_BREAK',
                               'confirmation':'CONDITIONAL_A' if age==0 else 'CONFIRMED_A','confirmedDate':now['date'],
                               'stop':level+.25*pa,'strategy':'A','reboundDate':None})
            # B needs t0 < t1 < t2, a contracted rebound, then a later bearish rejection.
            for j in range(i+1,min(n,i+config.retest_days)+1):
                rebound=bars[j]
                if abs(rebound['high']-level)>.5*pa or rebound['close']>=level or rebound['close']<=bars[j-1]['close']:continue
                if rebound['volume']>inds[j]['averageVolume20']*config.rebound_volume_ratio or rebound['volume']>b['volume']*config.rebound_volume_ratio:continue
                if 0<n-j<=config.rejection_days and c<now['open'] and c<rebound['low']-.1*pa:
                    setups.append({**p,'breakIndex':i,'breakDate':b['date'],'age':age,'signal':'FAILED_RETEST','confirmation':'CONFIRMED_B',
                                   'confirmedDate':now['date'],'stop':max(level,rebound['high'])+.25*pa,'strategy':'B','reboundDate':rebound['date']})
    setups.sort(key=lambda p:(p['strategy']!='B',p['age']>3,p['age'],p['kind']=='BOX'))
    setup=setups[0] if setups else None
    potential=structures(bars[:-1],inds[-2]['atr14'],config)
    if not setup and potential:
        p=min(potential,key=lambda p:abs(c-p['level']))
        setup={**p,'breakDate':None,'age':None,'confirmation':'FORMING','strategy':None,'signal':'NO_SETUP','stop':p['level']+.25*atr,'reboundDate':None}
    level=setup['level'] if setup else None
    distance=(level-c)/level if positive(level) else None
    ret5=c/bars[-6]['close']-1
    extended=bool(positive(level) and distance>config.maximum_support_distance or (ind['ma20']-c)/atr>config.deep_ma_atr or ret5<=config.deep_return)
    lower_wick=flags['longLowerWick'];bull=flags['explosiveBull']
    false=bool(fakes and not setups)
    supports=[p for i,p in pivots(bars[:-1])[1] if len(bars)-i<=240 and 0<p<c]
    measured=setup['measuredTarget'] if setup else None
    if positive(measured) and measured<c:supports.append(measured)
    supports=sorted(set(supports),reverse=True)
    target=supports[0] if supports else None
    target2=next((p for p in supports if target is not None and p<target),None)
    if target2 is None and setup and positive(setup['measuredTarget2']) and target and setup['measuredTarget2']<target:target2=setup['measuredTarget2']
    lo=hi=entry=stop=None
    if setup and setup['confirmation']!='FORMING' and positive(level):
        # Do not move the order toward a desirable RR. This is solely current-close location.
        width=min(.1*atr,config.order_zone_pct*level)
        lo=max(c-width,level*(1-config.maximum_support_distance));hi=min(c+width,level*(1-config.support_break_pct))
        if lo<=hi:entry=(lo+hi)/2;stop=setup['stop']
        else:lo=hi=None
    metrics=price_metrics(entry,stop,target)
    if metrics['validPrices'] and not (stop>hi>=entry>=lo>target):metrics=price_metrics(None,None,None)
    action_age=min((n-i for i,b in enumerate(raw) if b.get('split') or b.get('dividend')),default=999)
    checks={'decline60':ind['return60'] is not None and ind['return60']<=-config.minimum_decline,
            'volume2x':flags['volumeRatio'] is not None and flags['volumeRatio']>=2,
            'belowAllMA':all(positive(ind[k]) and c<ind[k] for k in ('ma20','ma60','ma240')),
            'adx25':ind['adx14'] is not None and ind['adx14']>=25,
            'minusDIAbovePlus':ind['minusDI14'] is not None and ind['minusDI14']>ind['plusDI14'],
            'noReversalCandle':not lower_wick and not bull,'notExtended':not extended,'notFalseBreak':not false,
            'patternConfirmed':bool(setup and setup['confirmation'] in {'CONFIRMED_A','CONFIRMED_B'}),
            'validStructureStop':metrics['validPrices'] and metrics['riskPct']<=config.maximum_stop_pct,
            'rr2':metrics['validPrices'] and metrics['riskRewardRatio']>=2,'actionClear':action_age>=config.action_cooldown_days}
    reasons_map={'decline60':'60交易日跌幅未達30%','volume2x':'當日量未達前20日均量2倍','belowAllMA':'未同時跌破20／60／240MA',
                 'adx25':'ADX14未達25','minusDIAbovePlus':'-DI未高於+DI','noReversalCandle':'長下影或爆量長紅反轉風險',
                 'notExtended':'距支撐超過5%、均線乖離過大或連續急跌，禁止追空','notFalseBreak':'跌破後重新站回原支撐',
                 'patternConfirmed':'型態尚未完成隔日／反彈轉弱確認','validStructureStop':'明確結構價位不足或停損距離超標','rr2':'最近下方支撐空間不足2R','actionClear':'公司行動後觀察期'}
    reasons=[reasons_map[k] for k,v in checks.items() if not v]
    quality=round(sum(checks.values())/len(checks)*100) if metrics['validPrices'] else None
    ma_order=ind['ma20']<ind['ma60']<ind['ma240']
    technical=all(checks.values())
    grade='高' if technical and setup['strategy']=='B' and ma_order and metrics['riskPct']<=3 else '中' if technical else '低'
    witness={'breakDate':setup.get('breakDate'),'reboundDate':setup.get('reboundDate'),'confirmedDate':now['date']} if setup else {}
    result={**(meta or {}),'direction':'SHORT','strategyVersion':config.version,'dataDate':now['date'],
            'dataTimeTaipei':now['date'].replace('-','/')+' 13:30','timeBasis':'完整日K交易時段截止，非擷取時間或逐筆成交時間','priceType':'COMPLETE_CLOSE',
            'close':raw[-1]['close'],'latestClose':raw[-1]['close'],'high':raw[-1]['high'],'low':raw[-1]['low'],'volume':raw[-1]['volume'],
            **ind,'volumeRatio':flags['volumeRatio'],'averageVolumeBasis':'前20完整交易日，不含訊號日，單位股',
            'adxMethod':{'period':14,'method':'Wilder TR/+DM/-DM 平滑；14個DX均值初始化ADX，其後Wilder遞推','calculationStart':bars[0]['date'],'cutoff':now['date'],'firstADXIndex':27},
            'stage':setup['name'] if setup else '尚無可核實頭部／整理型態','patternType':setup['kind'] if setup else None,
            'patternName':setup['name'] if setup else None,'patternCompletion':setup['confirmation'] if setup else 'NO_PATTERN',
            'originalSignal':'FALSE_BREAK' if false else setup['signal'] if setup else 'NO_SETUP',
            'rawSignals':(['FALSE_BREAK'] if false else [setup['signal']] if setup else ['NO_SETUP'])+(['TOO_EXTENDED'] if extended else []),
            'signalType':setup['strategy'] if setup else None,'breakDate':setup.get('breakDate') if setup else None,
            'breakAgeSessions':setup.get('age') if setup else None,'pressure':level,'neckline':level,'resistance':level,
            'referenceKnownAt':setup['referenceKnownAt'] if setup else None,'nextSupport':target,'measuredTarget':measured,
            'measuredTarget2':setup.get('measuredTarget2') if setup else None,'target2':target2,
            'observationLow':lo,'observationHigh':hi,'entry':entry,'entryBasis':'OBSERVATION_MIDPOINT','stopLoss':stop,'target':target,**metrics,
            'supportDistancePct':distance*100 if distance is not None else None,'tooExtended':extended,'falseBreak':false,'corporateActionPending':action_age<5,
            'technicalCandidate':technical,'technicalChecks':checks,'missingConditions':reasons,'qualityScore':quality,'qualityParts':{},
            'signalQualityRating':grade,'qualityRatingDisclosure':'高／中／低僅指訊號品質，非統計勝率','maBearOrder':ma_order,
            'statusReason':'；'.join(reasons) if reasons else '技術條件符合；交易資格與重大事件仍須獨立核實',
            'adjustedSignal':'尚待隔日確認' if setup and setup['confirmation']=='CONDITIONAL_A' else '禁止追空' if extended else '等待完整確認',
            'signalWitnesses':{'retest':witness,'patternVertices':setup['vertices'] if setup else []},
            'conditionalBreakPrice':level*(1-config.support_break_pct) if positive(level) else None,
            'conditionalReboundLow':level-.5*atr if positive(level) else None,'conditionalReboundHigh':level if positive(level) else None,
            'partialExitPolicy':{'firstTargetFraction':.5,'remainingFraction':.5,'trailATR':2,'maximumRiskPct':1,'noAveragingDown':True},
            'averageTurnover20':mean(b['close']*b['volume'] for b in bars[-21:-1]),
            'chart':[{'date':b['date'],'close':b['close']} for b in bars[-40:]],'dataIssues':[]}
    return result


def potential_plan(row):
    """Unconfirmed future break zone, derived from support; never fits an RR."""
    r=deepcopy(row)
    if r.get('patternCompletion')!='FORMING' or not positive(r.get('pressure')):return r
    level=r['pressure'];atr=r['atr14'];lo=level*.978;hi=level*.98;entry=(lo+hi)/2
    stop=level+.25*atr;target=r['nextSupport'];metrics=price_metrics(entry,stop,target)
    if metrics['validPrices'] and stop>hi>=entry>=lo>target:
        r.update(observationLow=lo,observationHigh=hi,entry=entry,stopLoss=stop,target=target,**metrics)
        # Current technical score/checks remain unchanged; this is a contingent plan.
        r['planCondition']='未完成型態；只有未來收盤跌破2%、量能／方向／位置全部確認後才重估，非目前掛單建議'
    return r
