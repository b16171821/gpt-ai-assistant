from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import math,unittest
from scripts.short.tw_v2 import TW_CONFIG,indicators,analyze_latest,structures,pivots,candle_flags,adjusted_asof
from scripts.short.tw_v2_sources import parse_breadth,parse_yahoo_quote,cross_verify,taiwan_environment
from scripts.short.tw_v2_selection import select_lists,finalize_row,FIELD_NAMES
from scripts.short.tracking import create_record,evaluate_record,original_hash
from scripts.short.validation import ShortDataError
from tests.short.test_short_core import bar,day,plan


def fixture():
    out=[bar(day(i),200-i*.4,high=201-i*.4,low=199-i*.4) for i in range(300)]
    out.extend(bar(day(i),64+(i%2)*.3,high=66,low=61.5) for i in range(300,330))
    out.append(bar(day(330),59.7,open_=62,high=62.2,low=59.5,volume=3_000_000))
    return out


def verified_quote(date_='2026-11-06'):
    stamp=datetime.fromisoformat(date_+'T13:30:00').replace(tzinfo=ZoneInfo('Asia/Taipei')).timestamp()
    return {'symbol':'TEST.TW','regularMarketPrice':100,'regularMarketVolume':1000000,'regularMarketTime':stamp,'marketState':'CLOSED'}


class IndicatorsTests(unittest.TestCase):
    def test_wilder_seed_and_monotonic_down_direction(self):
        rows=[bar(day(i),200-i) for i in range(50)];values=indicators(rows)
        self.assertIsNone(values[26]['adx14']);self.assertAlmostEqual(values[27]['adx14'],100)
        self.assertEqual(values[27]['plusDI14'],0);self.assertGreater(values[27]['minusDI14'],0)
        self.assertAlmostEqual(values[27]['atr14'],2)

    def test_volume_average_excludes_signal_day(self):
        rows=[bar(day(i),100,volume=100) for i in range(21)];rows[-1]['volume']=200
        self.assertEqual(indicators(rows)[-1]['averageVolume20'],100)

    def test_decline_uses_sixty_sessions_not_sixty_calendar_days(self):
        rows=[bar(day(i),100) for i in range(61)];rows[-1]=bar(day(60),70)
        self.assertAlmostEqual(indicators(rows)[-1]['return60'],-.30)

    def test_flat_adx_is_zero_not_nan(self):
        rows=[bar(day(i),100,high=100,low=100) for i in range(40)]
        self.assertEqual(indicators(rows)[-1]['adx14'],0)

    def test_yahoo_split_adjusted_history_is_not_split_twice(self):
        rows=[bar(day(0),50),bar(day(1),50,split=2)]
        self.assertEqual(adjusted_asof(rows,split_adjusted=True)[0]['close'],50)
        self.assertEqual(adjusted_asof(rows)[0]['close'],25)

    def test_long_wick_and_explosive_red_bull_are_excluded(self):
        self.assertTrue(candle_flags(bar(day(1),99,open_=100,high=101,low=90),1000000)['longLowerWick'])
        self.assertTrue(candle_flags(bar(day(1),110,open_=100,high=111,low=99,volume=3000000),1000000)['explosiveBull'])


class PatternV2Tests(unittest.TestCase):
    def test_first_break_is_conditional_then_next_close_confirms(self):
        rows=fixture();r=analyze_latest(rows)
        self.assertEqual(r['patternCompletion'],'CONDITIONAL_A');self.assertFalse(r['technicalCandidate'])
        rows.append(bar(day(331),59.6,open_=60,high=60.4,low=59.5,volume=3000000))
        r=analyze_latest(rows);self.assertEqual(r['patternCompletion'],'CONFIRMED_A');self.assertLess(r['breakDate'],r['dataDate'])

    def test_next_day_reclaim_is_false_break_not_short(self):
        rows=fixture()+[bar(day(331),63,open_=60,high=64,low=59.5,volume=3000000)]
        r=analyze_latest(rows);self.assertTrue(r['falseBreak']);self.assertFalse(r['technicalCandidate'])

    def test_rebound_volume_contracts_before_later_b_rejection(self):
        rows=fixture()+[bar(day(331),61.1,open_=60.4,high=61.7,low=60.2,volume=300000)]
        first=analyze_latest(rows);self.assertNotEqual(first['signalType'],'B')
        rows.append(bar(day(332),59.6,open_=61,high=61.1,low=59.4,volume=3500000))
        r=analyze_latest(rows);self.assertEqual(r['signalType'],'B')
        w=r['signalWitnesses']['retest'];self.assertLess(w['breakDate'],w['reboundDate']);self.assertLess(w['reboundDate'],w['confirmedDate'])
        rows[-1]['volume']=500000;r=analyze_latest(rows);self.assertFalse(r['technicalChecks']['volume2x']);self.assertFalse(r['technicalCandidate'])

    def test_support_distance_over_five_percent_is_no_chase(self):
        r=analyze_latest(fixture()+[bar(day(331),55,open_=59,high=59.5,low=54.9,volume=3500000)])
        self.assertTrue(r['tooExtended']);self.assertFalse(r['technicalCandidate'])

    def test_future_bars_do_not_mutate_previous_asof(self):
        rows=fixture();before=deepcopy(rows);r=analyze_latest(rows)
        future=rows+[bar(day(331),30,split=2)]
        analyze_latest(future)
        self.assertEqual(rows,before);self.assertEqual(analyze_latest(future[:-1]),r)

    def test_head_shoulders_and_m_top_require_known_vertices(self):
        def shaped(points):
            prices=[]
            for (a,pa),(b,pb) in zip(points,points[1:]):
                prices.extend(pa+(pb-pa)*(i-a)/(b-a) for i in range(a,b))
            prices.append(points[-1][1]);return [bar(day(i),c,high=c+.1,low=c-.1) for i,c in enumerate(prices)]
        m=shaped([(0,90),(5,110),(12,90),(20,111),(27,94),(35,98)])
        self.assertIn('DOUBLE_TOP',[x['kind'] for x in structures(m,1)])
        hs=shaped([(0,90),(5,105),(12,90),(20,115),(28,91),(36,104),(43,94),(49,97)])
        patterns=structures(hs,1);self.assertIn('HEAD_SHOULDERS',[x['kind'] for x in patterns])
        for p in patterns:
            for v in p['vertices']:self.assertLessEqual(v['knownAt'],hs[-1]['date'])

    def test_triangle_and_flag_geometry(self):
        prices=[]
        points=[(0,90),(4,110),(9,91),(14,107),(19,93),(24,104),(29,95),(34,101),(39,97)]
        for (a,pa),(b,pb) in zip(points,points[1:]):prices.extend(pa+(pb-pa)*(i-a)/(b-a) for i in range(a,b))
        prices.append(97)
        tri=[bar(day(i),c,high=c+.1,low=c-.1) for i,c in enumerate(prices)]
        self.assertIn('TRIANGLE',[p['kind'] for p in structures(tri,1)])
        flag=[bar(day(i),150-i*.5,volume=3000000) for i in range(20)]
        flag +=[bar(day(20+i),140-i*2,volume=3000000) for i in range(11)]
        flag +=[bar(day(31+i),120+i*.3,high=121+i*.3,low=119+i*.3,volume=300000) for i in range(10)]
        self.assertIn('BEAR_FLAG',[p['kind'] for p in structures(flag,1)])


class DataAndSelectionV2Tests(unittest.TestCase):
    def test_breadth_uses_stock_column_and_matching_date(self):
        p={'stat':'OK','date':'20261106','tables':[{'fields':['類型','整體市場','股票'],'data':[['上漲(漲停)','9999(9)','105(4)'],['下跌(跌停)','12345(200)','952(24)']]}]}
        r=parse_breadth(p,'2026-11-06');self.assertEqual((r['advancing'],r['declining']),(105,952))
        with self.assertRaises(ShortDataError):parse_breadth(p,'2026-11-05')

    def test_yahoo_intraday_or_stale_cannot_be_full_close(self):
        p=verified_quote();r=parse_yahoo_quote({'nested':[p]},'TEST.TW','2026-11-06');self.assertEqual(r['volume'],1000000)
        for changed in [dict(p,marketState='REGULAR'),dict(p,regularMarketTime=p['regularMarketTime']-3600),dict(p,regularMarketVolume=None)]:
            with self.assertRaises(ShortDataError):parse_yahoo_quote(changed,'TEST.TW','2026-11-06')
        self.assertEqual(cross_verify(r,{'date':'2026-11-06','close':101,'volume':1000000},'2026-11-06')['status'],'REJECTED')

    def test_unknown_source_never_becomes_watch_or_confirmed(self):
        r=analyze_latest(fixture(),{'name':'測試','code':'TEST','market':'TW','currency':'TWD'});r['dataFresh']=True;r['sourceCrossCheck']={'status':'PENDING'}
        c,w=select_lists([r]);self.assertEqual(c,[]);self.assertEqual(w,[])

    def test_twenty_output_fields_and_technical_quality_not_rewritten(self):
        r=analyze_latest(fixture(),{'name':'測試','code':'TEST','market':'TW','currency':'TWD'})
        before=r['qualityScore'];r['tradeQualification']={'label':'待確認','reasons':[],'allowEntry':True,'marginShort':{'status':'PENDING'},'borrowShort':{'status':'PENDING'}}
        r['sourceCrossCheck']={'status':'PENDING'}
        r=finalize_row(r,{}, {'operation':'積極放空'})
        self.assertEqual(r['qualityScore'],before);self.assertFalse(r['confirmedTrade']);self.assertEqual(len(r['fixedOutput']),20)
        self.assertEqual([x['label'] for x in r['fixedOutput']],FIELD_NAMES)
        self.assertEqual(r['fixedOutput'][7]['value']['status'],'UNVERIFIED')

    def test_confirmed_candidate_requires_actual_costs_and_net_two_r(self):
        r=analyze_latest(fixture(),{'name':'測試','code':'TEST','market':'TW','currency':'TWD'})
        r.update(technicalCandidate=True,qualityScore=100,entry=100,stopLoss=103,target=85,validPrices=True,signalType='B',maBearOrder=True,riskPct=3)
        r['sourceCrossCheck']={'status':'VERIFIED'}
        r['tradeQualification']={'allowEntry':True,'reasons':[],'marginShort':{'status':'VERIFIED'},'borrowShort':{'status':'PENDING'},'brokerEvidence':{'route':'MARGIN_SHORT','sourceType':'BROKER_API','verified':True,'symbol':'TEST','market':'TW','availableShares':1000,'validUntil':'2099-01-01T00:00:00+00:00'}}
        ev={'officialStatus':'VERIFIED','earningsStatus':'VERIFIED_CLEAR','newsStatus':'VERIFIED_CLEAR','squeezeStatus':'VERIFIED_CLEAR'};env={'operation':'積極放空'}
        missing=finalize_row(r,ev,env);self.assertFalse(missing['confirmedTrade']);self.assertIsNone(missing['netRiskRewardRatio'])
        r['tradeQualification']['brokerEvidence'].update(transactionCostVerified=True,borrowRateAnnual=.1,roundtripFeeRate=.005,slippageBufferRate=.0015,dividendBufferPerShare=0)
        known=finalize_row(r,ev,env);self.assertTrue(known['confirmedTrade']);self.assertGreaterEqual(known['netRiskRewardRatio'],2)
        r['tradeQualification']['brokerEvidence']['borrowRateAnnual']=2
        costly=finalize_row(r,ev,env);self.assertFalse(costly['confirmedTrade']);self.assertEqual(costly['qualityScore'],100)

    def test_unknown_policy_or_news_blocks_aggressive_environment(self):
        rows=[bar(day(i),200-i) for i in range(80)]
        context={'breadth':{'dataDate':rows[-1]['date'],'advancing':10,'declining':100},'volume':{'dataDate':rows[-1]['date'],'ratio':2,'downDay':True},'newsStatus':'UNKNOWN','policySupportStatus':'UNKNOWN'}
        e=taiwan_environment({'加權指數':rows},rows[-1]['date'],context)
        self.assertFalse(e['allowEntry']);self.assertEqual(e['operation'],'等待')


class PartialTrackingV2Tests(unittest.TestCase):
    def record(self):
        p=plan();p.update(taiwanStrictV2=True,strategyVersion=TW_CONFIG.version,target2=80,tradeTier='EXECUTABLE_CANDIDATE',partialExitPolicy={'firstTargetFraction':.5},adx14=40)
        return create_record(p,origin='HISTORICAL_RESEARCH')

    def test_target1_covers_only_half_and_original_levels_remain(self):
        r=self.record();o=deepcopy(r['originalStrategy']);r=evaluate_record(r,[bar('2026-10-02'),bar('2026-10-03',91,open_=99,low=89,high=100)])
        self.assertEqual(r['status'],'ACTIVE');self.assertEqual(r['remainingFraction'],.5);self.assertEqual(r['partialCovers'][0]['price'],90)
        self.assertEqual(r['originalStrategy'],o);self.assertIsNone(r['netPnlPerOriginalShare'])

    def test_first_then_second_target_weighted_result(self):
        r=evaluate_record(self.record(),[bar('2026-10-02'),bar('2026-10-03',91,open_=99,low=89,high=100),bar('2026-10-04',81,open_=88,low=79,high=89)],annual_borrow_rate=0)
        self.assertEqual(r['status'],'TARGET_HIT');self.assertEqual(r['grossPnlPerOriginalShare'],15)

    def test_gap_stop_after_partial_uses_open_and_preserves_known_half(self):
        r=evaluate_record(self.record(),[bar('2026-10-02'),bar('2026-10-03',91,open_=99,low=89,high=100),bar('2026-10-04',107,open_=108,high=110,low=102)],annual_borrow_rate=0)
        self.assertEqual(r['status'],'STOP_HIT');self.assertEqual(r['exitPrice'],108);self.assertEqual(r['grossPnlPerOriginalShare'],1)

    def test_same_bar_first_target_and_stop_is_manual(self):
        r=evaluate_record(self.record(),[bar('2026-10-02',100,high=106,low=89)])
        self.assertEqual(r['status'],'NEEDS_REVIEW');self.assertIsNone(r['netPnlPerOriginalShare'])

    def test_gap_target1_then_same_bar_stop_and_target2_is_manual(self):
        r=evaluate_record(self.record(),[bar('2026-10-02'),bar('2026-10-03',90,open_=89,high=106,low=79)],annual_borrow_rate=0)
        self.assertEqual(r['status'],'NEEDS_REVIEW');self.assertEqual(r['remainingFraction'],.5)
        self.assertEqual(len(r['partialCovers']),1);self.assertIsNone(r['netPnlPerOriginalShare'])

    def test_gap_below_second_target_completes_before_later_stop(self):
        r=evaluate_record(self.record(),[bar('2026-10-02'),bar('2026-10-03',90,open_=79,high=106,low=78)],annual_borrow_rate=0)
        self.assertEqual(r['status'],'TARGET_HIT');self.assertEqual(r['grossPnlPerOriginalShare'],21)

    def test_trailing_stop_applies_next_session_and_never_widens(self):
        r=evaluate_record(self.record(),[bar('2026-10-02'),bar('2026-10-03',91,open_=99,low=89,high=100)])
        self.assertEqual(r['trailEffectiveFrom'],'NEXT_SESSION');self.assertLessEqual(r['currentStop'],105)
        stop=r['currentStop'];r=evaluate_record(r,[bar('2026-10-04',92,open_=93,high=94,low=91)])
        self.assertLessEqual(r['currentStop'],stop)

    def test_unknown_live_qualification_cannot_simulate_fill(self):
        r=self.record();r['originalStrategy']['tradeTier']='OBSERVATION_ONLY';r['originalHash']=original_hash(r['originalStrategy'])
        r=evaluate_record(r,[bar('2026-10-02')]);self.assertEqual(r['entryStatus'],'NOT_TRIGGERED')

if __name__=='__main__':unittest.main()
