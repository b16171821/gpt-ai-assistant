"""Cached official monthly index history for point-in-time market filters."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sys

from .sources import ROOT,CACHE,frame_rows
from .validation import atomic_json,read_json,ShortDataError


def official_index_histories(as_of,start='2014-01-01',progress=None):
    sys.path.insert(0,str(ROOT/'scripts'))
    from tw_official_data import request_json,_index_rows_from_table,TWSE_INDEX_URL,TPEX_INDEX_URL
    first=date.fromisoformat(start).replace(day=1);last=date.fromisoformat(as_of).replace(day=1)
    months=[];cursor=first
    while cursor<=last:
        months.append(cursor)
        cursor=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
    tasks=[(name,m) for m in months for name in ('TWSE','TPEX')]
    destination=CACHE/'official_indexes';destination.mkdir(parents=True,exist_ok=True)
    def fetch(task):
        name,month=task;path=destination/f'{name}-{month:%Y-%m}.json'
        cached=read_json(path)
        if cached is not None and month<last:return name,cached,None
        try:
            if name=='TWSE':
                p=request_json(TWSE_INDEX_URL,params={'date':month.strftime('%Y%m%d'),'response':'json'},timeout=20)
                rows=_index_rows_from_table({'fields':p.get('fields'),'data':p.get('data')})
            else:
                p=request_json(TPEX_INDEX_URL,params={'date':month.strftime('%Y/%m/%d'),'response':'json'},timeout=20)
                tables=p.get('tables') or []
                rows=_index_rows_from_table(tables[0]) if tables else []
            if not rows:raise ShortDataError('Official index month has no usable bars')
            atomic_json(path,rows);return name,rows,None
        except Exception as e:return name,cached or [],f'{name}:{month}:{type(e).__name__}'
    all_rows={'TWSE':{},'TPEX':{}};errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,(name,rows,error) in enumerate(pool.map(fetch,tasks)):
            if error:errors.append(error)
            for r in rows:
                if r['Date']<=as_of:all_rows[name][r['Date']]=r
            if progress and (i+1)%40==0:progress(i+1,len(tasks),len(errors))
    result={}
    for key,label in [('TWSE','加權指數'),('TPEX','櫃買指數')]:
        result[label]=[{'date':d,'open':r['Open'],'high':r['High'],'low':r['Low'],'close':r['Close'],'volume':0,'dividend':0,'split':0}
                       for d,r in sorted(all_rows[key].items())]
    return result,errors


if __name__=='__main__':
    from .sources import SHORT_DATA
    previous=read_json(SHORT_DATA/'benchmarks.json',{})
    as_of=previous.get('meta',{}).get('dates',{}).get('TW')
    if not as_of:raise SystemExit('Run closing data update first')
    histories,errors=official_index_histories(as_of,progress=lambda i,n,e:print(f'Official index months {i}/{n}; errors={e}',flush=True))
    previous['TW']=histories
    previous.setdefault('meta',{})['officialHistoryErrors']=errors
    previous['meta']['twHistorySource']='TWSE／TPEx 官方月報，逐日截斷後計算濾網'
    atomic_json(SHORT_DATA/'historical_benchmarks.json',previous)
    print({k:len(v) for k,v in histories.items()},'errors',len(errors),flush=True)
