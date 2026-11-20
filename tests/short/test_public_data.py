from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.short.public_data import publish_market_index


class PublicDataTests(unittest.TestCase):
    def test_detail_batches_are_at_most_fifteen_and_leave_inputs_unchanged(self):
        payload={'meta':{'market':'TW','direction':'SHORT','dataDate':'2026-11-06'},
                 'stocks':[{'code':str(i),'qualityScore':80,'chart':[{'close':100}],
                            'tradeQualification':{'officialRows':[{'source':'test'}],'reasons':['券源未知']}} for i in range(37)]}
        before=deepcopy(payload)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);index=publish_market_index(payload,root)
            pages=list((root/'pages').rglob('*.json'));self.assertEqual(len(pages),3)
            self.assertEqual(sorted(len(json.loads(p.read_text())['stocks']) for p in pages),[7,15,15])
            self.assertEqual(index['stocks'][0]['qualityScore'],80)
            self.assertNotIn('chart',index['stocks'][0]);self.assertEqual(payload,before)

    def test_pruning_view_pages_never_removes_frozen_tracking(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tracking.json').write_text('immutable-test-record')
            for i in range(4):publish_market_index({'meta':{'market':'TW','dataDate':'2026-11-06'},'stocks':[{'code':str(i)}]},root)
            self.assertEqual((root/'tracking.json').read_text(),'immutable-test-record')
            self.assertLessEqual(len(list((root/'pages').glob('TW-*'))),2)


if __name__=='__main__':unittest.main()
