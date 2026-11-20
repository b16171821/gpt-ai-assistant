import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT=Path(__file__).resolve().parents[2]


class LegacyIsolationTests(unittest.TestCase):
    def test_all_original_source_and_configuration_files_are_unchanged(self):
        baseline=json.loads((Path(__file__).parent/'legacy_source_hashes.json').read_text())
        differences=[p for p,h in baseline['sha256'].items() if not (ROOT/p).exists() or hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
        self.assertEqual(differences,[],f'Legacy source changed: {differences}; do not rewrite this baseline to pass tests')

    def test_frozen_legacy_us_outputs_for_up_down_and_range(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        import update_us_stock_scanner as scanner
        fixture=json.loads((Path(__file__).parent/'legacy_us_fixture.json').read_text())
        for case in fixture['cases']:
            rows=[]
            for i in range(260):
                c=case['closeStart']+i*case['closeStep']+math.sin(i*.5)
                rows.append({'Open':c-.3,'High':c+1,'Low':c-1,'Close':c,'Volume':case['volume']*(3 if i==259 else 1)})
            f=pd.DataFrame(rows,index=pd.bdate_range('2025-01-01',periods=260))
            with self.subTest(case=case['name']):
                self.assertEqual(scanner.analyze_one(f.copy(),case['name'],case['name'],True),case['expected'])


if __name__=='__main__':unittest.main()
