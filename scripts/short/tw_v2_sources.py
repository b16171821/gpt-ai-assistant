"""Strict Yahoo Taiwan + appropriate exchange cross-checks; unknown stays unknown."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,date
from zoneinfo import ZoneInfo
from statistics import mean
from urllib.request import Request,urlopen
import json,re
from .sources import request_json
from .eligibility import official_date
from .validation import finite,positive,ShortDataError
from .tw_v2 import indicators,TW_CONFIG

SOURCE_LINKS=[
 {'name':'Yahoo奇摩股市','url':'https://tw.stock.yahoo.com/'},
 {'name':'TWSE最新收盤／漲跌家數','url':'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'},
 {'name':'TWSE市場成交資訊','url':'https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK'},
 {'name':'TPEx上櫃每日收盤','url':'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'}]


def number(value):
    try:
        x=float(str(value).replace(',',''));return x if finite(x) else None
    except (TypeError,ValueError):return None


def parse_breadth(payload,expected):
    if payload.get('stat')!='OK' or official_date(payload.get('date'))!=expected:raise ShortDataError('BREADTH_DATE_OR_STATUS')
    for table in payload.get('tables',[]):
        if table.get('fields')!=['類型','整體市場','股票']:continue
        found={}
        for row in table.get('data',[]):
            if len(row)<3:continue
            m=re.match(r'^([\d,]+)',str(row[2]))
            if not m:continue
            if str(row[0]).startswith('上漲'):found['advancing']=int(m[1].replace(',',''))
            elif str(row[0]).startswith('下跌'):found['declining']=int(m[1].replace(',',''))
        if set(found)=={'advancing','declining'}:return {**found,'dataDate':expected,'scope':'TWSE上市普通股票欄，非全體證券數','source':SOURCE_LINKS[1]['url']}
    raise ShortDataError('STOCK_BREADTH_MISSING')


def parse_market_volumes(payload):
    if payload.get('stat')!='OK':raise ShortDataError('INDEX_VOLUME_STATUS')
    fields=payload.get('fields',[])
    if not all(k in fields for k in ('日期','成交股數','發行量加權股價指數')):raise ShortDataError('INDEX_VOLUME_FIELDS')
    out=[]
    for row in payload.get('data',[]):
        r=dict(zip(fields,row));day=official_date(r.get('日期'));vol=number(r.get('成交股數'));close=number(r.get('發行量加權股價指數'))
        if day and positive(vol) and positive(close):out.append({'date':day,'volume':vol,'close':close})
    return out


def fetch_market_context(expected):
    y,m,_=map(int,expected.split('-'));months=[]
    for ago in range(5):
        yy,mm=divmod(y*12+m-1-ago,12);months.append(f'{yy:04d}{mm+1:02d}01')
    urls=[SOURCE_LINKS[1]['url']+'?date='+expected.replace('-','')+'&type=ALLBUT0999&response=json']
    urls += [SOURCE_LINKS[2]['url']+'?date='+d+'&response=json' for d in months]
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(request_json,u) for u in urls]
        for u,f in zip(urls,futures):
            try:results.append((u,f.result(),None))
            except Exception as e:results.append((u,None,str(e)[:200]))
    context={'asOfDate':expected,'breadth':None,'volume':None,'newsStatus':'UNKNOWN','policySupportStatus':'UNKNOWN',
             'eventReason':'未接入具公告時點的重大利多／併購／回補與政策護盤資料，不推定無事件','errors':[]}
    if results[0][1]:
        try:context['breadth']=parse_breadth(results[0][1],expected)
        except Exception as e:context['errors'].append(str(e))
    else:context['errors'].append('BREADTH:'+str(results[0][2]))
    volumes=[]
    for u,p,e in results[1:]:
        if p:
            try:volumes.extend(parse_market_volumes(p))
            except Exception as ex:context['errors'].append(str(ex))
        else:context['errors'].append(u+':'+str(e))
    volumes=sorted({b['date']:b for b in volumes if b['date']<=expected}.values(),key=lambda b:b['date'])
    context['indexCloses']=volumes
    context['indexPriceSource']='TWSE FMTQIK 官方歷史收盤與市場成交資訊；未補造OHLC'
    if len(volumes)>=21 and volumes[-1]['date']==expected:
        avg=mean(b['volume'] for b in volumes[-21:-1]);now=volumes[-1]
        context['volume']={'dataDate':expected,'shares':now['volume'],'average20':avg,'ratio':now['volume']/avg,
                           'downDay':now['close']<volumes[-2]['close'],'scope':'TWSE市場成交股數；含各類證券，非個股成交量',
                           'source':SOURCE_LINKS[2]['url']}
    else:context['errors'].append('INDEX_VOLUME_21_SESSIONS_UNAVAILABLE')
    return context


def taiwan_environment(series,asof,context):
    bars=series.get('加權指數',[]) or context.get('indexCloses',[])
    base={'code':'NO_DATA','label':'資料不足，僅供觀察','asOfDate':asof,'allowEntry':False,'operation':'等待','trend':'資料不足',
          'dataTimeTaipei':asof.replace('-','/')+' 13:30','indexes':[],'context':context,'reason':'必要的官方指數、量能或漲跌家數未核實'}
    if len(bars)<65 or bars[-1]['date']!=asof:return base
    has_ohlc=all(all(positive(b.get(k)) for k in ('open','high','low','close')) for b in bars)
    ind=indicators(bars)[-1] if has_ohlc else {'ma20':mean(b['close'] for b in bars[-20:]),'ma60':mean(b['close'] for b in bars[-60:]),'atr14':None}
    prev_ma=mean(b['close'] for b in bars[-25:-5]);c=bars[-1]['close'];fast=ind['ma20'];slow=ind['ma60']
    bear=c<fast<slow and fast<prev_ma;bull=c>fast>slow and fast>prev_ma
    oversold=((fast-c)/ind['atr14']>2.5 if positive(ind['atr14']) else (fast-c)/fast>=.08) or c/bars[-6]['close']-1<=-.08
    index={'name':'加權指數','date':asof,'close':c,'ma20':fast,'ma60':slow,'ma20Falling':fast<prev_ma,'belowMA20':c<fast,'bear':bear,'bull':bull}
    base.update(indexPriceSource=context.get('indexPriceSource','TWSE官方OHLC'),indexOHLCVerified=has_ohlc,oversoldMethod='MA20下方8%或5日跌幅8%；OHLC齊全時另核對2.5ATR',indexes=[index],trend='空頭' if bear else '多頭' if bull else '震盪',oversold=oversold)
    breadth=context.get('breadth');vol=context.get('volume');news_ok=context.get('newsStatus')=='VERIFIED_CLEAR' and context.get('policySupportStatus')=='VERIFIED_CLEAR'
    known=bool(breadth and vol and breadth.get('dataDate')==asof and vol.get('dataDate')==asof)
    candle=bars[-1];spread=candle['high']-candle['low'] if has_ohlc else 0
    rebound=has_ohlc and c>candle['open'] and spread>0 and (c-candle['open'])/spread>=.6 and vol and vol['ratio']>=1.5
    if bull and known and breadth['advancing']>breadth['declining']*1.5 or rebound:
        base.update(code='BULL_STRONG',label='多方強勢，避免逆勢放空',operation='停止新空單',reason='站回月線且上漲家數占優，或出現帶量強力反彈')
    elif known and news_ok:
        if bear and vol['downDay'] and vol['ratio']>=1.2 and breadth['declining']>breadth['advancing'] and not oversold:
            base.update(code='FAVORABLE',label='空方環境有利',allowEntry=True,operation='積極放空',reason='月線下彎、跌時量增且下跌家數占優，事件資料已核實')
        elif not oversold:
            base.update(code='WAIT',label='等待確認',allowEntry=True,operation='保守放空',reason='大盤震盪或量價未一致，僅考慮最清楚的相對弱勢型態')
        else:base.update(code='WAIT',label='等待確認',reason='大盤急跌超賣，降低積極度並等待反彈')
    else:base.update(code='WAIT',label='等待確認',reason='重大消息／政策護盤尚未核實，或官方量能／家數不足，暫不建立新空單')
    return base


def parse_yahoo_quote(payload,symbol,expected):
    """Only known Yahoo raw quote fields are accepted; volume units are shares."""
    def raw(v):return v.get('raw') if isinstance(v,dict) else v
    def find(obj):
        if isinstance(obj,dict):
            if obj.get('symbol')==symbol and all(k in obj for k in ('regularMarketPrice','regularMarketVolume','regularMarketTime')):return obj
            for v in obj.values():
                q=find(v)
                if q:return q
        if isinstance(obj,list):
            for v in obj:
                q=find(v)
                if q:return q
        return None
    quote=find(payload)
    if not quote:raise ShortDataError('YAHOO_TW_KNOWN_QUOTE_SCHEMA_MISSING')
    close,volume,stamp=(raw(quote[k]) for k in ('regularMarketPrice','regularMarketVolume','regularMarketTime'))
    if not positive(close) or not positive(volume) or not positive(stamp):raise ShortDataError('YAHOO_TW_QUOTE_MISSING_VALUES')
    dt=datetime.fromtimestamp(stamp,timezone.utc).astimezone(ZoneInfo('Asia/Taipei'))
    if dt.date().isoformat()!=expected or (dt.hour,dt.minute)<(13,30):raise ShortDataError('YAHOO_TW_INTRADAY_OR_STALE')
    if quote.get('marketState') in {'REGULAR','PRE','POST'}:raise ShortDataError('YAHOO_TW_INTRADAY_STATE')
    return {'symbol':symbol,'close':close,'volume':volume,'dataDate':expected,'dataTimeTaipei':dt.strftime('%Y/%m/%d %H:%M'),
            'volumeUnit':'shares','priceType':'COMPLETE_CLOSE','source':'Yahoo奇摩股市'}


def yahoo_payloads(html):
    out=[]
    for body in re.findall(r'<script[^>]*type=["\']application/(?:ld\+)?json["\'][^>]*>(.*?)</script>',html,re.S|re.I):
        try:out.append(json.loads(body))
        except ValueError:pass
    marker='root.App.main = '
    i=html.find(marker)
    if i>=0:
        try:out.append(json.JSONDecoder().raw_decode(html[i+len(marker):])[0])
        except ValueError:pass
    return out


def fetch_yahoo_tw(stock,expected):
    url='https://tw.stock.yahoo.com/quote/'+stock['ticker']
    req=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html'})
    with urlopen(req,timeout=18) as r:html=r.read().decode('utf-8')
    for payload in yahoo_payloads(html):
        try:return {**parse_yahoo_quote(payload,stock['ticker'],expected),'url':url}
        except ShortDataError:continue
    raise ShortDataError('YAHOO_TW_PAGE_NO_VERIFIABLE_CLOSE')


def cross_verify(yahoo,official,expected):
    if not yahoo or not official:return {'status':'PENDING','reason':'Yahoo奇摩與對應交易所完整收盤尚未同時核實'}
    if yahoo.get('dataDate')!=expected or official.get('date')!=expected:return {'status':'REJECTED','reason':'來源日期不一致'}
    if not all(positive(x) for x in (yahoo.get('close'),official.get('close'),yahoo.get('volume'),official.get('volume'))):return {'status':'REJECTED','reason':'來源價格／成交股數缺漏'}
    price_error=abs(yahoo['close']-official['close'])/official['close'];volume_error=abs(yahoo['volume']-official['volume'])/official['volume']
    if price_error>.0001 or volume_error>.01:return {'status':'REJECTED','reason':'來源收盤價或全日量不一致','priceDifferencePct':price_error*100,'volumeDifferencePct':volume_error*100}
    return {'status':'VERIFIED','dataDate':expected,'yahoo':yahoo,'official':{'date':expected,'close':official['close'],'volume':official['volume'],'market':official.get('market')},
            'reason':'Yahoo奇摩與對應官方交易所收盤／成交股數一致（價差≤0.01%、量差≤1%）'}
