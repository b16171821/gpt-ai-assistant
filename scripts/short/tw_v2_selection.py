"""Final trade eligibility and fixed 20-field output, separate from technical score."""
from copy import deepcopy
from datetime import datetime,timezone
from .tw_v2 import TW_CONFIG
from .validation import positive,finite,price_metrics

FIELD_NAMES=['股票名稱＋代號','現價＋資料日期時間','型態名稱','關鍵頸線／支撐','轉換後壓力位','第一滿足點','第二滿足點',
             '建議放空價格','合理掛單範圍','停損價格','第一停利價格','風險報酬比','ADX（14）及 -DI／+DI','量能與均線是否達標',
             '放空資格及券源狀態','訊號類型','勝率評級','進場確認條件','失效條件','推薦原因']


def fixed_output(row):
    r=row
    out=[{'number':i+1,'label':label,'value':value} for i,(label,value) in enumerate(zip(FIELD_NAMES,[
        {'name':r['name'],'code':r['code']},{'price':r['close'],'dateTimeTaipei':r['dataTimeTaipei'],'type':'完整收盤'},
        {'name':r['patternName'],'completion':r['patternCompletion']},r['pressure'],r['resistance'],r['measuredTarget'],r['measuredTarget2'],
        {'price':r['entry'],'basis':'觀察區中間值','conditional':not r.get('confirmedTrade')},
        {'low':r['observationLow'],'high':r['observationHigh']},r['stopLoss'],r['target'],r['riskRewardRatio'],
        {'adx14':r['adx14'],'minusDI14':r['minusDI14'],'plusDI14':r['plusDI14'],'method':r['adxMethod']},
        {'volume':r['volume'],'average20':r['averageVolume20'],'volumeRatio':r['volumeRatio'],'ma20':r['ma20'],'ma60':r['ma60'],'ma240':r['ma240'],'checks':r['technicalChecks']},
        {'status':r['tradeQualification']['label'],'reasons':r['tradeQualification']['reasons'],'margin':r['tradeQualification']['marginShort'],'sbl':r['tradeQualification']['borrowShort']},
        '反彈不過' if r['signalType']=='B' else '首根跌破' if r['signalType']=='A' else '尚未完成',
        {'rating':r['signalQualityRating'],'disclosure':r['qualityRatingDisclosure']},
        r['entryConfirmation'],r['invalidationConditions'],r['statusReason']]))]
    if not r.get('criticalDataVerified'):
        for i in (7,8,9,10,11):out[i]['value']={'status':'UNVERIFIED','note':'關鍵資料未核實，不提供進場建議；內部技術價位不代表可交易'}
    return out


def finalize_row(row,evidence,environment):
    r=deepcopy(row);q=r['tradeQualification'];extra=[]
    verified=r.get('sourceCrossCheck',{}).get('status')=='VERIFIED'
    if not verified:extra.append('Yahoo奇摩與對應交易所關鍵收盤資料未交叉核實')
    broker=q.get('brokerEvidence') or {}
    try:
        expiry=datetime.fromisoformat(str(broker.get('validUntil','')).replace('Z','+00:00'))
        inventory_ok=broker.get('sourceType')=='BROKER_API' and broker.get('verified') is True and broker.get('symbol')==r['code'] and broker.get('market')=='TW' and positive(broker.get('availableShares')) and expiry.tzinfo is not None and expiry>datetime.now(timezone.utc)
    except (TypeError,ValueError):inventory_ok=False
    if not inventory_ok:extra.append('券商API實際庫存／有效期限未核實')
    route=broker.get('route');route_evidence=q.get('marginShort' if route=='MARGIN_SHORT' else 'borrowShort',{})
    if route not in {'MARGIN_SHORT','SBL'} or route_evidence.get('status') not in {'VERIFIED','VERIFIED_AVAILABLE'}:
        extra.append('實際融券／借券賣出合法路徑及可用狀態待確認')
    cost_keys=('borrowRateAnnual','roundtripFeeRate','slippageBufferRate','dividendBufferPerShare')
    cost_ok=broker.get('transactionCostVerified') is True and all(finite(broker.get(k)) and broker[k]>=0 for k in cost_keys)
    r['netRiskRewardRatio']=None
    if not cost_ok:extra.append('借券／融券年費率、稅費與滑價／股息緩衝未完整核實')
    elif r.get('validPrices'):
        e,s,t=r['entry'],r['stopLoss'],r['target']
        shared=e*broker['roundtripFeeRate']+e*broker['borrowRateAnnual']*TW_CONFIG.max_holding_days/365+broker['dividendBufferPerShare']
        risk=s-e+shared+(e+s)*broker['slippageBufferRate']
        reward=e-t-shared-(e+t)*broker['slippageBufferRate']
        r['netRiskRewardRatio']=reward/risk if risk>0 else None
        if r['netRiskRewardRatio'] is None or r['netRiskRewardRatio']<2:extra.append('納入已核實費率與成本緩衝後風報比未達2R')

    for key,label in [('newsStatus','重大利多／併購／回補消息'),('squeezeStatus','融券餘額／券資比與集中回補風險')]:
        if evidence.get(key)!='VERIFIED_CLEAR':extra.append(label+'待確認')
    if environment.get('operation') not in {'積極放空','保守放空'}:extra.append('大盤不允許新空單')
    if environment.get('operation')=='保守放空' and not (r.get('maBearOrder') and r.get('signalType')=='B' and (r.get('riskPct') or 999)<=3):
        extra.append('保守環境只接受均線空排、停損≤3%的B型態')
    if evidence.get('squeezeStatus')=='HIGH_RISK' or evidence.get('newsStatus')=='HIGH_RISK':extra.append('已知重大消息／集中回補風險，排除')
    q['reasons']=list(dict.fromkeys(q['reasons']+extra))
    r['criticalDataVerified']=bool(verified and inventory_ok and cost_ok and route_evidence.get('status') in {'VERIFIED','VERIFIED_AVAILABLE'} and evidence.get('officialStatus')=='VERIFIED' and evidence.get('earningsStatus')=='VERIFIED_CLEAR' and evidence.get('newsStatus')=='VERIFIED_CLEAR' and evidence.get('squeezeStatus')=='VERIFIED_CLEAR' and not evidence.get('restrictions'))
    q['tradeDataVerified']=r['criticalDataVerified']
    r['confirmedTrade']=bool(q.get('allowEntry') and not extra and r.get('technicalCandidate') and r['criticalDataVerified'])
    q['allowEntry']=r['confirmedTrade'];q['status']='VERIFIED' if r['confirmedTrade'] else 'PENDING'
    q['label']='資格已核實' if r['confirmedTrade'] else '交易資格／券源待確認'
    r['tradeTier']='EXECUTABLE_CANDIDATE' if r['confirmedTrade'] else 'OBSERVATION_ONLY'
    r['adjustedSignal']='可執行候選' if r['confirmedTrade'] else '尚待隔日確認' if r['patternCompletion']=='CONDITIONAL_A' else '禁止追空' if r['tooExtended'] else '疑似假跌破' if r['falseBreak'] else '觀察名單，非確認交易訊號'
    r['entryConfirmation']='先有放量跌破，次一交易日仍未站回支撐，才於其後可成交時點在原核准區間模擬；券源、成本與事件亦須核實。' if r['signalType']=='A' else '先跌破、後續縮量反彈、再由不同交易日轉弱；完整收盤確認後最早下一交易日，且須完成券源／事件核實。'
    r['invalidationConditions']=['站回頸線／突破原結構停損','距頸線超過5%、急跌乖離過大或出現長下影／爆量長紅','最近下方支撐不足2R','券源召回、成本或交易限制變更','十交易日未觸發到期；不放寬掛單區間、不攤平、不延後原停損']
    r['fixedOutput']=fixed_output(r)
    return r


def select_lists(rows,maximum=3):
    def key(r):return (r.get('signalType')!='B',(r.get('riskPct') or 999)>3,not r.get('maBearOrder'),(r.get('breakAgeSessions') or 0)>3,-(r.get('qualityScore') or 0))
    confirmed=sorted([r for r in rows if r.get('confirmedTrade')],key=key)[:maximum]
    watches=[]
    for r in sorted(rows,key=key):
        if r.get('confirmedTrade') or not r.get('criticalDataVerified') or not r.get('dataFresh') or r.get('sourceCrossCheck',{}).get('status')!='VERIFIED':continue
        if r.get('tooExtended') or r.get('falseBreak') or not r.get('patternType'):continue
        checks=r.get('technicalChecks',{})
        if not checks.get('noReversalCandle') or not checks.get('actionClear'):continue
        if len(r.get('missingConditions',[]))>3 or not r.get('validPrices') or (r.get('riskRewardRatio') or 0)<2:continue
        if r.get('tradeQualification',{}).get('restrictions'):continue
        watches.append(r)
        if len(watches)==maximum:break
    if confirmed:watches=[]
    chosen={r['code'] for r in confirmed+watches}
    for r in rows:r['priorityObservation']=r['code'] in chosen;r['listRole']='CONFIRMED' if r in confirmed else 'OBSERVATION' if r in watches else 'DIAGNOSTIC'
    return confirmed,watches
