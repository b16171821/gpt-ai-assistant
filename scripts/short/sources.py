"""Closing-bar adapters. The legacy long scanners are never imported or run."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import gzip
import sys
import urllib.request

import pandas as pd
import yfinance as yf

from .eligibility import OFFICIAL_SOURCES
from .validation import ShortDataError, atomic_json, finite, read_json, validate_bars, trailing_valid_segment

ROOT=Path(__file__).resolve().parents[2]
SHORT_DATA=ROOT/'data'/'short'
CACHE=SHORT_DATA/'cache'


def request_json(url, *, timeout=25):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 short-observation/1.0','Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8-sig'))


def completed_sessions(market, *, start='2014-01-01', now=None):
    import exchange_calendars as xcals
    now=pd.Timestamp(now or datetime.now(timezone.utc))
    calendar=xcals.get_calendar('XTAI' if market=='TW' else 'XNYS',start=start,end=(now+pd.Timedelta(days=15)).date().isoformat())
    sessions=calendar.sessions_in_range(max(pd.Timestamp(start),calendar.first_session),pd.Timestamp(now.date()))
    days=[s.date().isoformat() for s in sessions if calendar.session_close(s)+pd.Timedelta(minutes=15)<=now]
    if not days:raise ShortDataError('No completed exchange session')
    return days,calendar


def tw_official():
    sys.path.insert(0,str(ROOT/'scripts'))
    from tw_official_data import load_latest_official_quotes, fetch_twse_index_history, fetch_tpex_index_history
    quotes=load_latest_official_quotes()
    day=quotes['officialDate']
    indexes={'加權指數':frame_rows(fetch_twse_index_history(day,months=5)),
             '櫃買指數':frame_rows(fetch_tpex_index_history(day,months=5))}
    return quotes,indexes


def frame_rows(frame):
    if frame is None or frame.empty:return []
    if getattr(frame.columns,'nlevels',1)>1:
        if len(set(frame.columns.get_level_values(-1)))==1:frame=frame.droplevel(-1,axis=1)
        else:raise ShortDataError('Ambiguous price frame')
    rows=[]
    for index,row in frame.iterrows():
        # Rows with entirely absent OHLC are API alignment padding. Partial bars
        # remain invalid and are reported instead of guessed or forward filled.
        if all(pd.isna(row.get(k)) for k in ('Open','High','Low','Close')):continue
        def number(key,default=None):
            value=row.get(key,default)
            if value is None or pd.isna(value):return default
            return float(value)
        rows.append({'date':pd.Timestamp(index).date().isoformat(),'open':number('Open'),'high':number('High'),
                     'low':number('Low'),'close':number('Close'),'volume':number('Volume'),
                     'dividend':number('Dividends',0),'split':number('Stock Splits',0)})
    return rows


def universe():
    import twstock
    tw=[]
    for code,item in twstock.codes.items():
        if not str(code).isdigit() or len(str(code))!=4 or '股票' not in (getattr(item,'type','') or ''):continue
        exchange='TWSE' if '上市' in (getattr(item,'market','') or '') else 'TPEX'
        tw.append({'market':'TW','currency':'TWD','code':str(code),'name':item.name,'exchange':exchange,
                   'ticker':str(code)+('.TW' if exchange=='TWSE' else '.TWO')})
    # Preserve the existing scope without importing its strategy constants.
    tw=tw[:1200]
    current=read_json(ROOT/'data'/'us_latest.json',{})
    names={r.get('ticker'):r.get('name') for r in current.get('stocks',[])}
    watch=read_json(ROOT/'data'/'us_watchlist.json',{}).get('tickers',[])
    us=[{'market':'US','currency':'USD','code':t,'ticker':t,'name':names.get(t) or t,'exchange':'US'}
        for t in watch if t not in {'SPY','QQQ','DIA','IWM'}]
    return {'meta':{'asOfDate':datetime.now(timezone.utc).date().isoformat(),'pointInTime':False,
                    'scope':'現行台股前1200檔／美股觀察清單；非歷史全市場名單','survivorshipBias':'UNRESOLVED'},'stocks':tw+us}


def download_batch(tickers, *, start='2014-01-01', end=None):
    end=end or (datetime.now(timezone.utc).date()+timedelta(days=1)).isoformat()
    raw=yf.download(tickers,start=start,end=end,interval='1d',group_by='ticker',auto_adjust=False,actions=True,
                    progress=False,threads=6,timeout=25)
    result={}
    for ticker in tickers:
        try:
            if getattr(raw.columns,'nlevels',1)>1:
                if ticker in raw.columns.get_level_values(0):frame=raw[ticker]
                elif ticker in raw.columns.get_level_values(1):frame=raw.xs(ticker,axis=1,level=1)
                else:continue
            elif len(tickers)==1:frame=raw
            else:continue
            rows=frame_rows(frame)
            if rows:result[ticker]=rows
        except (ValueError,KeyError,ShortDataError):continue
    return result


def load_histories(stocks, dates, *, start='2014-01-01', force=False, progress=None, official=None):
    CACHE.mkdir(parents=True,exist_ok=True)
    output,errors,needed={},[],[]
    for stock in stocks:
        ticker=stock['ticker'];old=read_json(CACHE/(ticker+'.json'))
        if old and old.get('requestedStart')==start and old.get('dataDate')==dates[stock['market']] and not force:
            output[ticker]=old['bars']
        else:needed.append(ticker)
    batches=[needed[i:i+35] for i in range(0,len(needed),35)]
    # yfinance's shared batch state is not safe to run multiple download() calls
    # concurrently; its internal per-symbol downloads already run in parallel.
    by_ticker={s['ticker']:s for s in stocks}
    for i,batch in enumerate(batches):
        try:downloaded=download_batch(batch,start=start)
        except Exception as exc:
            downloaded={};errors.append(f'BATCH_{i}:{type(exc).__name__}')
        for ticker in batch:
            stock=by_ticker[ticker];day=dates[stock['market']]
            rows=[r for r in downloaded.get(ticker,[]) if r['date']<=day]
            # Yahoo can publish a partial latest TW bar. Replace that exact
            # session using the independently verified official OHLCV before
            # validating the history; do not guess or discard the whole series.
            quote=(official or {}).get('quotes',{}).get(stock['code']) if stock['market']=='TW' else None
            if quote and quote['date']==day and rows:
                old=next((r for r in rows if r['date']==day),{})
                bar={k:quote[k] for k in ('date','open','high','low','close','volume')}
                bar.update(dividend=old.get('dividend',0),split=old.get('split',0))
                rows=[r for r in rows if r['date']!=day]+[bar]
                rows.sort(key=lambda r:r['date'])
            segment,history_issues=trailing_valid_segment(rows)
            if rows:
                archive=CACHE/'raw';archive.mkdir(exist_ok=True)
                with gzip.open(archive/(ticker+'.json.gz'),'wt',encoding='utf-8') as file:
                    json.dump({'requestedStart':start,'bars':rows,'historyIssues':history_issues},file,allow_nan=False)
            if segment and not validate_bars(segment):
                output[ticker]=segment
                atomic_json(CACHE/(ticker+'.json'),{'requestedStart':start,'dataDate':segment[-1]['date'],'bars':segment,
                            'historyIssues':history_issues,'trustedHistoryStart':segment[0]['date'],
                            'fullHistoryUsable':not history_issues})
            else:
                old=read_json(CACHE/(ticker+'.json'))
                if old:output[ticker]=old['bars']
                errors.append(f'{ticker}:API_FAILED_OR_INVALID;last_good_preserved')
        if progress:progress(i+1,len(batches),len(output),len(errors))
    return output,errors


def restriction_snapshots():
    snapshots,errors={},{}
    def get(source):
        try:
            result=request_json(source['url'])
            if not isinstance(result,list):raise ShortDataError('Expected official rows')
            return source['id'],result,None
        except Exception as exc:return source['id'],[],str(exc)[:200]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name,rows,error in pool.map(get,OFFICIAL_SOURCES):
            snapshots[name]=rows
            if error:errors[name]=error
    return snapshots,errors
