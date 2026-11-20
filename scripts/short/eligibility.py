"""Market restrictions are separate from broker inventory and technical quality."""
from copy import deepcopy
from datetime import datetime, timezone

from .config import CONFIG

OFFICIAL_SOURCES = [
    {'id':'twseCap','name':'TWSE 當日可借券賣出股數','url':'https://openapi.twse.com.tw/v1/SBL/TWT96U'},
    {'id':'tpexSuspend','name':'TPEx 暫停融券賣出預告','url':'https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_term'},
    {'id':'tpexRules','name':'TPEx 融／借券賣出價格限制','url':'https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_margin_mark'},
    {'id':'twseHalt','name':'TWSE 暫停交易證券','url':'https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU'},
    {'id':'twseNotice','name':'TWSE 注意股票','url':'https://openapi.twse.com.tw/v1/announcement/notice'},
    {'id':'twseDisposition','name':'TWSE 處置股票','url':'https://openapi.twse.com.tw/v1/announcement/punish'},
    {'id':'tpexNotice','name':'TPEx 注意股票','url':'https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information'},
    {'id':'tpexDisposition','name':'TPEx 處置股票','url':'https://www.tpex.org.tw/openapi/v1/tpex_disposal_information'},
]
RULE_SOURCES = [
    {'name':'SEC Regulation SHO Rule 201','url':'https://www.ecfr.gov/current/title-17/section-242.201'},
    {'name':'SEC Regulation SHO Rule 203','url':'https://www.ecfr.gov/current/title-17/section-242.203'},
    {'name':'FINRA 4210 Margin Requirements','url':'https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210'},
    {'name':'TWSE 法規共享知識庫','url':'https://twse-regulation.twse.com.tw/TW/Index.aspx'},
]


def official_date(value):
    digits=''.join(c for c in str(value or '') if c.isdigit())
    if len(digits)==7:
        return f'{int(digits[:3])+1911:04d}-{digits[3:5]}-{digits[5:]}'
    if len(digits)==8:
        return f'{digits[:4]}-{digits[4:6]}-{digits[6:]}'
    return None


def code_of(row):
    return str(row.get('SecuritiesCompanyCode') or row.get('Code') or row.get('stockCode') or '').strip()


def official_evidence(stock, snapshots, errors, as_of):
    market,code=stock['market'],stock['code']
    evidence={'officialStatus':'PENDING','brokerInventory':'UNAVAILABLE','borrowRateAnnual':None,
              'ruleSources':RULE_SOURCES,'marketCapacity':None,'restrictions':[],'officialRows':[],
              'marginShort':{'status':'PENDING'},'borrowShort':{'status':'PENDING'},
              'earningsStatus':'UNKNOWN','sourceErrors':errors}
    if market=='US':
        evidence['restrictions']=['美股券商 locate／券源、SSR及借券成本待核實','財報事件時間待核實']
        return evidence
    for row in snapshots.get('twseCap',[]):
        key='GRETAICode' if stock.get('exchange')=='TPEX' else 'TWSECode'
        if str(row.get(key,'')).strip()==code:
            volume_key='GRETAIAvailableVolume' if key=='GRETAICode' else 'TWSEAvailableVolume'
            try: volume=int(str(row.get(volume_key,'')).replace(',',''))
            except ValueError: volume=None
            evidence['marketCapacity']={'shares':volume,'dataDate':None,'source':'twseCap',
                                        'note':'市場額度，非券商庫存；原介面未附資料日期，不推定為策略日'}
            break
    for kind, rows in snapshots.items():
        if kind=='twseCap':continue
        for row in rows:
            if code_of(row)!=code:continue
            evidence['officialRows'].append({'source':kind,'raw':row,'dataDate':official_date(row.get('Date'))})
            start=official_date(row.get('ShortSaleSuspensionStartDate'))
            end=official_date(row.get('ShortSaleSuspensionEndDate'))
            if kind=='tpexSuspend' and start and end and start<=as_of<=end:
                evidence['marginShort']={'status':'BLOCKED','startDate':start,'endDate':end,'reason':row.get('Reason') or '官方暫停融券賣出'}
                evidence['restrictions'].append('官方暫停融券賣出')
            if kind=='tpexRules' and official_date(row.get('Date'))==as_of:
                for field,route in [('BannedFromMarginShortSelling','marginShort'),('BannedFromSBLShortSelling','borrowShort')]:
                    if str(row.get(field,'')).strip().lower() in {'是','有','y','yes','1','true'}:
                        evidence[route]={'status':'BLOCKED','reason':field}
                        evidence['restrictions'].append('官方'+('禁止融券賣出' if route=='marginShort' else '禁止借券賣出'))
                if str(row.get('SubjectToUptickRule','')).strip():
                    evidence['restrictions'].append('官方價格限制欄位：'+str(row['SubjectToUptickRule']))
            if 'Notice' in kind:
                evidence['restrictions'].append('官方注意公告，需核對適用日期')
            if 'Disposition' in kind:
                evidence['restrictions'].append('官方處置公告，依原文期間核對，不推算解除日')
            if kind=='twseHalt':
                evidence['restrictions'].append('官方暫停交易名單，須核對恢復日')
    evidence['restrictions']=list(dict.fromkeys(evidence['restrictions']))
    return evidence


def attach_qualification(stock, environment, evidence, *, data_fresh=True, broker=None, now=None, config=CONFIG):
    """Return a new row, leaving all technical fields untouched."""
    out=deepcopy(stock)
    evidence=deepcopy(evidence)
    reasons=[]
    minimum=config.min_turnover_tw if stock['market']=='TW' else config.min_turnover_us
    liquidity=stock.get('averageTurnover20',0)>=minimum and stock.get('volume',0)>0
    if not data_fresh:reasons.append('收盤資料過期或不完整')
    if not environment.get('allowEntry'):reasons.append(environment.get('label','大盤資料不足'))
    if not stock.get('technicalCandidate'):reasons.append(stock.get('statusReason','技術條件未確認'))
    if not liquidity:reasons.append('流動性未達研究門檻')
    if evidence.get('restrictions'):reasons.extend(evidence['restrictions'])
    current=(now or datetime.now(timezone.utc)).isoformat()
    # Only a dedicated broker adapter may supply this schema. UI inputs and
    # public market capacity are never promoted to broker confirmation.
    broker_ok=bool(broker and broker.get('sourceType')=='BROKER_API' and broker.get('verified') is True
                   and broker.get('symbol')==stock['code'] and broker.get('market')==stock['market']
                   and broker.get('verifiedAt') and broker.get('validUntil','')>current
                   and broker.get('availableShares',0)>0 and broker.get('route') in {'MARGIN_SHORT','SBL','US_SHORT'})
    if not broker_ok:reasons.append('交易資格／券源待確認')
    if evidence.get('officialStatus')!='VERIFIED':reasons.append('完整交易限制待官方資料核對')
    if evidence.get('earningsStatus')!='VERIFIED_CLEAR':reasons.append('財報／公司事件資料待確認')
    allow=not reasons
    evidence.update({'status':'VERIFIED' if allow else 'PENDING','label':'資格已核實' if allow else '交易資格／券源待確認',
                     'allowEntry':allow,'liquidityOk':liquidity,'minimumAverageTurnover':minimum,
                     'reasons':list(dict.fromkeys(reasons)),'brokerEvidence':broker if broker_ok else None})
    out['tradeQualification']=evidence
    out['marketEnvironment']=environment['code']
    out['tradeTier']='EXECUTABLE_CANDIDATE' if allow else 'OBSERVATION_ONLY'
    out['adjustedSignal']='可執行候選' if allow else '禁止追空' if stock.get('tooExtended') else '疑似假跌破' if stock.get('falseBreak') else '僅供觀察｜交易資格待確認'
    return out
