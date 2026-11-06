from copy import deepcopy
import unittest

from scripts.short.backtest import market_timeline,research_period,research_portfolio,summarize
from scripts.short.tracking import evaluate_record
from tests.short.test_short_core import create_record
from scripts.short.validation import trailing_valid_segment
from tests.short.test_short_core import bar,day,plan


class ResearchTests(unittest.TestCase):
    def test_period_boundaries_are_chronological(self):
        self.assertEqual(research_period('2021-12-31'),'TRAIN')
        self.assertEqual(research_period('2022-01-01'),'VALIDATION')
        self.assertEqual(research_period('2024-01-01'),'OUT_OF_SAMPLE')
        self.assertIsNone(research_period('2014-12-31'))

    def test_market_filter_prefix_unchanged_by_future(self):
        rows=[bar(day(i),200-i*.3) for i in range(100)]
        short=market_timeline({'A':rows[:80],'B':rows[:80]})
        full=market_timeline({'A':rows,'B':rows})
        self.assertEqual(short[day(79)],full[day(79)])

    def test_bad_history_is_not_stitched_or_repaired(self):
        rows=[bar(day(i)) for i in range(10)]
        rows[5]['close']=500
        before=deepcopy(rows);tail,issues=trailing_valid_segment(rows)
        self.assertEqual([r['date'] for r in tail],[day(i) for i in range(6,10)])
        self.assertTrue(issues);self.assertEqual(rows,before)

    def test_untriggered_samples_never_raise_target_rate(self):
        waiting=create_record(plan(technicalCandidate=False))
        done=evaluate_record(create_record(plan()),[bar('2026-10-02'),bar('2026-10-03',90,open_=100,low=89,high=101)])
        stats=summarize([waiting,done]);self.assertEqual(stats['sampleCount'],2);self.assertEqual(stats['triggered'],1);self.assertEqual(stats['resolvedTradeCount'],1)

    def test_ambiguous_samples_report_bounds_and_no_single_rate(self):
        ambiguous=evaluate_record(create_record(plan()),[bar('2026-10-02',100,high=106,low=89)])
        stats=summarize([ambiguous]);self.assertEqual(stats['targetRateLower'],0);self.assertEqual(stats['targetRateUpper'],100);self.assertIsNone(stats['targetRate'])

    def test_nominal_portfolio_enforces_lots_and_preserves_records(self):
        r=evaluate_record(create_record(plan()),[bar('2026-10-02'),bar('2026-10-03',90,open_=100,low=89,high=101)])
        r['averageVolume20']=1_000_000;r['marks']=[{'date':'2026-10-02','close':100}]
        original=deepcopy(r)
        p=research_portfolio([r],'TW');self.assertEqual(p['admittedTrades'],1);self.assertEqual(p['currency'],'TWD');self.assertGreater(p['endingResearchEquityConservativeBound'],p['initialResearchEquity']);self.assertEqual(r,original)

    def test_insufficient_adv_capacity_is_excluded_before_outcome(self):
        r=evaluate_record(create_record(plan()),[bar('2026-10-02'),bar('2026-10-03',90,open_=100,low=89,high=101)])
        r['averageVolume20']=1
        p=research_portfolio([r],'TW');self.assertEqual(p['admittedTrades'],0);self.assertEqual(p['capacityOrUnitExclusions'],1)


if __name__=='__main__':unittest.main()
