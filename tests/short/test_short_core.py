from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from scripts.short.config import CONFIG
from scripts.short.eligibility import attach_qualification, official_evidence
from scripts.short.engine import analyze_history, market_environment
from scripts.short.tracking import create_record as create_live_record, evaluate_record, reconcile_tracking, tracking_statistics
from scripts.short.validation import ShortDataError, atomic_json, price_metrics, refresh_last_good, validate_bars


def bar(day, close=100, *, open_=None, high=None, low=None, volume=1_000_000, **extra):
    open_=close if open_ is None else open_
    return {'date':day,'open':open_,'high':max(open_,close)+1 if high is None else high,
            'low':min(open_,close)-1 if low is None else low,'close':close,'volume':volume,'dividend':0,'split':0,**extra}


def day(i):
    return (date(2026,1,1)+timedelta(days=i)).isoformat()


def create_record(row, **kwargs):
    return create_live_record(row,origin='HISTORICAL_RESEARCH',**kwargs)


def platform_fixture():
    rows=[]
    for i in range(100):
        c=145-i*.42+math.sin(i*math.pi/4)*.8
        rows.append(bar(day(i),c))
    for i in range(100,120):
        c=102 if i%2 else 101
        rows.append(bar(day(i),c,high=104,low=99))
    rows.append(bar(day(120),97.8,open_=100,high=100,low=97,volume=3_000_000))
    return rows


def plan(day_='2026-10-01',**overrides):
    r={'market':'TW','currency':'TWD','exchange':'TWSE','code':'TEST','name':'測試股票','dataDate':day_,
       'originalSignal':'PLATFORM_BREAK','rawSignals':['PLATFORM_BREAK'],'pressure':102,'referenceKnownAt':'2026-09-30',
       'observationLow':99,'observationHigh':101,'entry':100,'entryBasis':'OBSERVATION_MIDPOINT',
       'stopLoss':105,'target':90,'atr14':2,'qualityScore':90,'riskRewardRatio':2,'strategyVersion':CONFIG.version,
       'signalWitnesses':{},'marketEnvironment':'FAVORABLE','technicalCandidate':True,'close':100,
       'tradeTier':'OBSERVATION_ONLY','averageTurnover20':100_000_000,'volume':1_000_000,
       'statusReason':'技術條件成立','tooExtended':False,'falseBreak':False}
    r.update(overrides)
    return r


class PatternTests(unittest.TestCase):
    def test_platform_break_uses_prior_bars(self):
        data=platform_fixture();r=analyze_history(data)[-1]
        self.assertIn('PLATFORM_BREAK',r['rawSignals'])
        self.assertEqual(r['pressure'],99)
        self.assertEqual(r['referenceKnownAt'],data[-2]['date'])
        self.assertLess(r['referenceKnownAt'],r['dataDate'])

    def test_break_is_not_same_day_retest(self):
        r=analyze_history(platform_fixture())[-1]
        self.assertNotIn('FAILED_RETEST',r['rawSignals'])

    def test_retest_needs_later_rebound_and_later_rejection(self):
        rows=platform_fixture()
        rows.append(bar(day(121),98.7,open_=98,high=99,low=98))
        first=analyze_history(rows)[-1]
        self.assertNotIn('FAILED_RETEST',first['rawSignals'])
        rows.append(bar(day(122),97,open_=98.5,high=98.8,low=96.5))
        r=analyze_history(rows)[-1]
        self.assertIn('FAILED_RETEST',r['rawSignals'])
        w=r['signalWitnesses']['retest']
        self.assertLess(w['breakDate'],w['reboundDate']);self.assertLess(w['reboundDate'],w['confirmedDate'])

    def test_false_break_after_reclaim(self):
        rows=platform_fixture()+[bar(day(121),101,open_=98,high=102,low=97.5)]
        r=analyze_history(rows)[-1]
        self.assertIn('FALSE_BREAK',r['rawSignals']);self.assertFalse(r['technicalCandidate'])

    def test_intraday_reclaim_without_close_is_not_false_break(self):
        rows=platform_fixture()+[bar(day(121),98,open_=98,high=102,low=97.5)]
        self.assertNotIn('FALSE_BREAK',analyze_history(rows)[-1]['rawSignals'])

    def test_deep_decline_is_not_erased(self):
        rows=platform_fixture()+[bar(day(121),80,open_=95,high=96,low=79,volume=3_000_000)]
        r=analyze_history(rows)[-1]
        self.assertTrue(r['tooExtended']);self.assertIn('TOO_EXTENDED',r['rawSignals']);self.assertFalse(r['technicalCandidate'])

    def test_ma_rejection_uses_prior_ma_and_later_confirmation(self):
        rows=[bar(day(i),200-i*.6+math.sin(i*math.pi/6)*2) for i in range(130)]
        prior=analyze_history(rows)[-1];self.assertEqual(prior['trendState'],'CONFIRMED')
        c=rows[-1]['close']+1
        rows.append(bar(day(130),c,open_=c-.3,high=prior['ma20'],low=c-1))
        self.assertNotIn('MA_REJECTION',analyze_history(rows)[-1]['rawSignals'])
        rows.append(bar(day(131),c-2,open_=c-.5,high=c,low=c-3))
        r=analyze_history(rows)[-1]
        self.assertIn('MA_REJECTION',r['rawSignals'])
        w=r['signalWitnesses']['maRejection']
        self.assertLess(w['maKnownAt'],w['reboundDate']);self.assertLess(w['reboundDate'],w['confirmedDate'])

    def test_prefix_is_unchanged_by_future_bars_and_actions(self):
        rows=platform_fixture();before=analyze_history(rows)[-1]
        future=rows+[bar(day(121),50,open_=50,split=2),bar(day(122),52)]
        after=next(r for r in analyze_history(future) if r['dataDate']==before['dataDate'])
        before.pop('chart');after.pop('chart');self.assertEqual(before,after)

    def test_input_bars_are_not_mutated(self):
        rows=platform_fixture()+[bar(day(121),50,split=2)];original=deepcopy(rows)
        analyze_history(rows);self.assertEqual(rows,original)

    def test_duplicate_and_nan_rejected_before_signal(self):
        rows=platform_fixture();rows[-1]['close']=float('nan')
        with self.assertRaises(ShortDataError):analyze_history(rows)
        rows=platform_fixture();rows.append(deepcopy(rows[-1]))
        with self.assertRaises(ShortDataError):analyze_history(rows)


class PriceAndDataTests(unittest.TestCase):
    def test_short_risk_reward(self):
        r=price_metrics(100,105,90)
        self.assertEqual(r['rewardPct'],10);self.assertEqual(r['riskPct'],5);self.assertEqual(r['riskRewardRatio'],2)

    def test_missing_zero_nonfinite_and_wrong_direction_never_valid(self):
        for values in [(0,105,90),(100,None,90),(100,100,90),(100,95,90),(100,105,110),(100,float('inf'),90),(100,105,0)]:
            with self.subTest(values=values):
                r=price_metrics(*values);self.assertFalse(r['validPrices']);self.assertIsNone(r['riskRewardRatio'])

    def test_ohlcv_calendar_and_stale_checks(self):
        rows=[bar('2026-10-01'),bar('2026-10-05')]
        issues=validate_bars(rows,as_of='2026-10-06',expected_sessions=['2026-10-01','2026-10-02','2026-10-05'])
        self.assertIn('MISSING_SESSION:2026-10-02',issues)
        self.assertTrue(any(x.startswith('STALE_BARS') for x in issues))

    def test_invalid_volume_and_ohlc(self):
        self.assertTrue(validate_bars([bar('2026-10-01',volume=-1)]))
        self.assertTrue(validate_bars([bar('2026-10-01',high=90)]))

    def test_api_failure_keeps_last_good_bytes_and_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tw_latest.json';atomic_json(path,{'meta':{'dataDate':'2026-10-01'},'stocks':[{'code':'TEST'}]})
            before=path.read_bytes()
            def fail():raise TimeoutError('test outage')
            result,ok=refresh_last_good(path,fail,attempted_at='2026-10-06T00:00:00Z')
            self.assertFalse(ok);self.assertEqual(path.read_bytes(),before);self.assertEqual(result['meta']['dataDate'],'2026-10-01')
            status=json.loads(path.with_name('tw_latest_status.json').read_text());self.assertTrue(status['stale'])

    def test_older_successful_api_response_cannot_replace_newer_close(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'us_latest.json'
            atomic_json(path,{'meta':{'dataDate':'2026-10-05'}});before=path.read_bytes()
            result,ok=refresh_last_good(path,lambda:{'meta':{'dataDate':'2026-10-02'}})
            self.assertFalse(ok);self.assertEqual(path.read_bytes(),before)

    def test_market_dates_must_align(self):
        one=[bar(day(i),100-i*.1) for i in range(80)]
        two=one[:-1]
        e=market_environment({'A':one,'B':two},one[-1]['date'])
        self.assertEqual(e['code'],'NO_DATA');self.assertFalse(e['allowEntry'])


class QualificationTests(unittest.TestCase):
    def test_unknown_inventory_does_not_rewrite_technical_score(self):
        row=plan();before=deepcopy(row)
        out=attach_qualification(row,{'code':'FAVORABLE','allowEntry':True,'label':'空方環境有利'},
                                 {'officialStatus':'PENDING','restrictions':[],'earningsStatus':'UNKNOWN'})
        self.assertEqual(row,before);self.assertEqual(out['qualityScore'],90)
        self.assertEqual(out['tradeTier'],'OBSERVATION_ONLY');self.assertFalse(out['tradeQualification']['allowEntry'])

    def test_public_market_capacity_never_confirms_broker_inventory(self):
        ev=official_evidence(plan(),{'twseCap':[{'TWSECode':'TEST','TWSEAvailableVolume':'999,999'}]}, {},'2026-10-01')
        self.assertEqual(ev['marketCapacity']['shares'],999999);self.assertIsNone(ev['marketCapacity']['dataDate'])
        self.assertEqual(ev['brokerInventory'],'UNAVAILABLE')

    def test_route_restriction_is_separate(self):
        ev=official_evidence(plan(exchange='TPEX'),{'tpexSuspend':[{'SecuritiesCompanyCode':'TEST','ShortSaleSuspensionStartDate':'1151001','ShortSaleSuspensionEndDate':'1151005'}]}, {},'2026-10-02')
        self.assertEqual(ev['marginShort']['status'],'BLOCKED');self.assertEqual(ev['borrowShort']['status'],'PENDING')

    def test_favorable_market_does_not_override_inventory(self):
        out=attach_qualification(plan(),{'code':'FAVORABLE','allowEntry':True,'label':'空方環境有利'},
                                 {'officialStatus':'VERIFIED','restrictions':[],'earningsStatus':'VERIFIED_CLEAR'})
        self.assertFalse(out['tradeQualification']['allowEntry'])


class LifecycleTests(unittest.TestCase):
    def test_late_live_record_cannot_backfill_past_open(self):
        row=plan(earliestEntryAt='2026-10-02T01:00:00+00:00')
        r=create_live_record(row,created_at='2026-10-02T03:00:00+00:00')
        out=evaluate_record(r,[bar('2026-10-02',90,open_=100,high=101,low=89)])
        self.assertEqual(out['entryStatus'],'NOT_TRIGGERED')
        self.assertIsNone(out['netPnlPerOriginalShare'])

    def test_live_record_before_open_can_only_technically_trigger(self):
        row=plan(earliestEntryAt='2026-10-02T01:00:00+00:00')
        r=create_live_record(row,created_at='2026-10-01T09:00:00+00:00')
        out=evaluate_record(r,[bar('2026-10-02')])
        self.assertEqual(out['entryStatus'],'TRIGGERED')
        self.assertFalse(out['executionConfirmed'])

    def test_missing_live_open_time_does_not_fabricate_fill(self):
        r=create_live_record(plan(),created_at='2026-10-01T09:00:00+00:00')
        self.assertEqual(evaluate_record(r,[bar('2026-10-02')])['entryStatus'],'NOT_TRIGGERED')

    def test_signal_day_low_does_not_count(self):
        r=create_record(plan())
        result=evaluate_record(r,[bar('2026-10-01',100,low=80,high=110),bar('2026-10-02',100)])
        self.assertEqual(result['status'],'ACTIVE');self.assertEqual(result['fillDate'],'2026-10-02')

    def test_target_first_remains_terminal_after_later_stop(self):
        r=create_record(plan());rows=[bar('2026-10-02'),bar('2026-10-03',92,open_=100,low=89,high=101),bar('2026-10-04',107,open_=106,high=110)]
        result=evaluate_record(r,rows);self.assertEqual(result['status'],'TARGET_HIT');self.assertEqual(result['endDate'],'2026-10-03')
        again=evaluate_record(result,[bar('2026-10-05',120)])
        self.assertEqual(again,result)

    def test_stop_first_does_not_become_later_target(self):
        rows=[bar('2026-10-02'),bar('2026-10-03',103,open_=100,high=106,low=99),bar('2026-10-04',85)]
        result=evaluate_record(create_record(plan()),rows)
        self.assertEqual(result['status'],'STOP_HIT');self.assertEqual(result['endDate'],'2026-10-03')

    def test_same_day_ambiguous_requires_review(self):
        result=evaluate_record(create_record(plan()),[bar('2026-10-02',100,high=106,low=89)])
        self.assertEqual(result['status'],'NEEDS_REVIEW');self.assertIsNone(result['netPnlPerOriginalShare'])
        stats=tracking_statistics([result]);self.assertEqual(stats['needsReview'],1);self.assertIsNone(stats['targetRate'])

    def test_gap_stop_uses_open_and_known_chronology(self):
        rows=[bar('2026-10-02'),bar('2026-10-03',100,open_=110,high=112,low=85)]
        r=evaluate_record(create_record(plan()),rows)
        self.assertEqual(r['status'],'STOP_HIT');self.assertEqual(r['exitPrice'],110);self.assertTrue(r['events'][-1]['gapStop'])

    def test_next_open_outside_zone_never_fabricates_fill(self):
        rows=[bar('2026-10-02',95,open_=95,high=101,low=89),bar('2026-10-03',100)]
        r=evaluate_record(create_record(plan()),rows)
        self.assertEqual(r['entryStatus'],'NOT_TRIGGERED');self.assertIsNone(r['fillPrice']);self.assertEqual(tracking_statistics([r])['triggered'],0)

    def test_waiting_record_not_in_trade_performance(self):
        r=evaluate_record(create_record(plan(technicalCandidate=False)),[bar('2026-10-02',85,open_=100,high=101,low=80)])
        self.assertEqual(r['entryStatus'],'NOT_TRIGGERED');self.assertIsNone(r['netPnlPerOriginalShare'])
        self.assertEqual(tracking_statistics([r])['completed'],0)

    def test_original_plan_not_overwritten_by_update_or_new_signal(self):
        old=create_record(plan());before=deepcopy(old['originalStrategy'])
        new=plan('2026-10-02',stopLoss=115,target=80)
        result=reconcile_tracking({'records':[old]},[new],{'TW:TEST':[bar('2026-10-02')]})
        preserved=next(r for r in result['records'] if r['trackingId']==old['trackingId'])
        self.assertEqual(preserved['originalStrategy'],before);self.assertEqual(preserved['firstSignalDate'],'2026-10-01')

    def test_tampering_with_original_is_rejected(self):
        r=create_record(plan());r['originalStrategy']['stopLoss']=120
        with self.assertRaisesRegex(ValueError,'Immutable'):evaluate_record(r,[bar('2026-10-02')])

    def test_absent_next_day_selection_preserves_tracking(self):
        old=create_record(plan())
        result=reconcile_tracking({'records':[old]},[],{})
        self.assertEqual(len(result['records']),1);self.assertEqual(result['records'][0]['originalHash'],old['originalHash'])

    def test_split_changes_derived_levels_only(self):
        old=create_record(plan());rows=[bar('2026-10-02'),bar('2026-10-03',50,open_=50,high=51,low=49,split=2)]
        result=evaluate_record(old,rows)
        self.assertEqual(result['originalStrategy']['stopLoss'],105);self.assertEqual(result['executionLevels']['stopLoss'],52.5)

    def test_dividend_is_liability_not_free_profit(self):
        r=evaluate_record(create_record(plan()),[bar('2026-10-02'),bar('2026-10-03',90,open_=99,low=89,high=100,dividend=1)],annual_borrow_rate=0)
        self.assertEqual(r['status'],'TARGET_HIT');self.assertEqual(r['grossPnlPerOriginalShare'],9)

    def test_duplicate_update_is_idempotent(self):
        rows=[bar('2026-10-02')];r=evaluate_record(create_record(plan()),rows)
        self.assertEqual(evaluate_record(r,rows),r)

    def test_missing_intermediate_session_cannot_be_skipped_to_target(self):
        rows=[bar('2026-10-02'),bar('2026-10-04',90,open_=100,high=101,low=89)]
        r=evaluate_record(create_record(plan()),rows,expected_sessions=['2026-10-02','2026-10-03','2026-10-04'])
        self.assertEqual(r['status'],'ACTIVE');self.assertEqual(r['lastEvaluatedDate'],'2026-10-02')
        self.assertEqual(r['coverageStatus'],'GAP_REQUIRES_REVIEW');self.assertIsNone(r['netPnlPerOriginalShare'])


if __name__=='__main__':unittest.main()
