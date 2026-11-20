"""Run the full suite; report the precisely recorded pre-existing data failure.

No test result, strategy fixture, long data or expected output is rewritten.
"""
import sys
import unittest


def main():
    suite=unittest.defaultTestLoader.discover('tests',pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    unexpected=[]
    for test,trace in result.failures:
        known=test.id().endswith('test_disposition_data.DispositionDataTests.test_all_current_tw_stocks_have_additive_risk_records')
        known=known and "'台股:2035'" in trace and "'台股:2755'" in trace
        # The recorded baseline consists of exactly these missing symbols.
        lines=[x.strip().strip("'\"") for x in trace.splitlines() if x.strip().startswith(('\'台股:', '"台股:'))]
        known=known and set(lines)=={'台股:2035','台股:2755'}
        if known:print('::warning title=Pre-existing long data coverage failure::2035 and 2755 are absent from the unchanged disposition snapshot. The test remains failed; no baseline was rewritten.')
        else:unexpected.append(test.id())
    # Preserve the real failing exit status, including the documented baseline.
    # CI must not silently turn a pre-existing failure into an all-green suite.
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
