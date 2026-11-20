"""Small mobile indexes plus revisioned detail pages of at most 15 symbols."""
from copy import deepcopy
import hashlib
import json
import shutil

from .sources import SHORT_DATA
from .validation import atomic_json,read_json


def publish_market_index(payload,root=SHORT_DATA):
    market=payload['meta']['market']
    if market not in {'TW','US'}:raise ValueError('Unsupported market namespace')
    revision=hashlib.sha256(json.dumps(payload['stocks'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    stocks=[]
    for start in range(0,len(payload['stocks']),15):
        page=f'pages/{market}-{revision}/page-{start//15:03d}.json'
        rows=payload['stocks'][start:start+15]
        atomic_json(root/page,{'meta':{'direction':'SHORT','market':market,'dataDate':payload['meta']['dataDate'],
                                     'revision':revision,'maximumBatchSize':15},'stocks':rows})
        for original in rows:
            row=deepcopy(original)
            row.pop('chart',None)
            row.pop('historyDataIssues',None)
            row.pop('fixedOutput',None)
            qualification=row.get('tradeQualification',{})
            for key in ('officialRows','ruleSources','sourceErrors','brokerEvidence'):
                qualification.pop(key,None)
            row['detailPage']=page
            stocks.append(row)
    index={**payload,'meta':{**payload['meta'],'revision':revision,'detailPageSize':15},'stocks':stocks}
    atomic_json(root/('tw_index.json' if market=='TW' else 'us_index.json'),index)
    # Only disposable rendered detail pages are pruned. Frozen plans, failures,
    # tracking history and research trade records are never removed here.
    versions=sorted((root/'pages').glob(market+'-*'),key=lambda p:p.stat().st_mtime,reverse=True)
    keep={market+'-'+revision}
    for folder in versions:
        if len(keep)<2:keep.add(folder.name)
    for folder in versions:
        if folder.name not in keep and folder.is_dir():shutil.rmtree(folder)
    return index


def publish_auxiliary(root=SHORT_DATA):
    benchmarks=read_json(root/'benchmarks.json',{})
    preview={'meta':benchmarks.get('meta',{})}
    for market in ('TW','US'):
        preview[market]={k:[{'date':b['date'],'close':b['close']} for b in rows[-60:]] for k,rows in benchmarks.get(market,{}).items()}
    atomic_json(root/'benchmark_preview.json',preview)
    sources=read_json(root/'eligibility_sources.json',{})
    manifest={k:v for k,v in sources.items() if k!='rows'}
    from .tw_v2_sources import SOURCE_LINKS
    manifest['sources']=SOURCE_LINKS+manifest.get('sources',[])
    tw=read_json(root/'tw_latest.json',{})
    manifest['taiwanMandatoryCrossCheck']=tw.get('meta',{}).get('mandatorySourceStatus',{})
    atomic_json(root/'source_manifest.json',manifest)
    report=read_json(root/'backtest_report.json')
    if report:
        summary=deepcopy(report)
        summary.get('coverage',{}).pop('symbols',None)
        atomic_json(root/'research_summary.json',summary)


def main():
    for filename in ('tw_latest.json','us_latest.json'):
        payload=read_json(SHORT_DATA/filename)
        if payload:publish_market_index(payload)
    publish_auxiliary()


if __name__=='__main__':main()
