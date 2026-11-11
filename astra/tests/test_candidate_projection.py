import unittest
from copy import deepcopy
from datetime import datetime, timedelta

from astra.candidate_projection import project, SHEET_ID, RANK_HEADERS
from astra.market_data import JST
from astra.sheet_bridge import TOP_HEADERS, EVENT_HEADERS


class CandidateProjectionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,12,10,tzinfo=JST)
        self.codes = [str(1000+i) for i in range(99)] + ['285A']
        ranks, tops = [], []
        for i, code in enumerate(self.codes):
            common = {'コード':code,'順位':i+1,'最終Score':100-i,'BUY':'A' if i<2 else 'B',
                      '終値':1000,'信用':'C0'}
            ranks.append([{**common,'最新日':'2026/11/11'}.get(k) for k in RANK_HEADERS])
            if i < 10:
                tops.append([{**common,'推奨株数':100}.get(k) for k in TOP_HEADERS])
        self.bundle = dict(spreadsheet_id=SHEET_ID,spreadsheet_timezone='Asia/Tokyo',
            capture_started_at=(self.now-timedelta(seconds=2)).isoformat(),captured_at=self.now.isoformat(),
            ranking=[RANK_HEADERS]+ranks,top10=[TOP_HEADERS]+tops,events=[EVENT_HEADERS])

    def run_case(self):
        return project(self.bundle,self.codes,self.now)

    def test_capture_is_not_confirmation_and_empty_events_are_not_clear(self):
        r=self.run_case()
        self.assertEqual(r['screening_candidate_count'],2)
        self.assertIsNone(r['rank_observed_at'])
        self.assertFalse(r['event_coverage_verified'])
        self.assertFalse(r['go_allowed'])
        self.assertTrue(all(not c['go_allowed'] and c['live_blockers'] for c in r['candidates']))

    def test_known_event_uses_date_not_stale_countdown_or_ended_label(self):
        self.bundle['events'].append(['1000','test','2026/11/13','決算','','',-3,'終了','fixture'])
        r=self.run_case()['candidates'][0]
        self.assertFalse(r['screening_conditions_met'])
        self.assertEqual(r['known_near_events'][0]['business_days'],1)

    def test_macro_event_blocks_every_candidate(self):
        self.bundle['events'].append(['0000','macro','2026/11/17','macro','','',99,'','fixture'])
        self.assertEqual(self.run_case()['screening_candidate_count'],0)

    def test_stale_missing_duplicate_or_extra_ranking_rejected(self):
        original=deepcopy(self.bundle)
        for mode in ('stale','missing','duplicate','extra'):
            self.bundle=deepcopy(original)
            rows=self.bundle['ranking']
            if mode=='stale': rows[100][2]='2026/11/10'
            if mode=='missing': rows.pop()
            if mode=='duplicate': rows[100][1]='1000'
            if mode=='extra': rows.append(list(rows[1]))
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.run_case()

    def test_excluded_100th_stock_with_higher_score_rejected(self):
        self.bundle['ranking'][100][15]=999
        with self.assertRaisesRegex(ValueError,'all 100'): self.run_case()

    def test_conflicting_candidate_or_unknown_grade_rejected(self):
        self.bundle['top10'][1][3]='B'
        with self.assertRaises(ValueError): self.run_case()
        self.bundle['ranking'][1][16]='B'
        self.assertFalse(self.run_case()['candidates'][0]['screening_conditions_met'])
        self.bundle['ranking'][100][16]='?'
        with self.assertRaises(ValueError): self.run_case()

    def test_share_values_are_strict(self):
        original=deepcopy(self.bundle)
        for qty in (None,True,'100',99,150,-100,float('nan')):
            self.bundle=deepcopy(original);self.bundle['top10'][1][12]=qty
            with self.subTest(qty=qty), self.assertRaises(ValueError): self.run_case()
        self.bundle=deepcopy(original);self.bundle['top10'][1][12]=0
        self.assertFalse(self.run_case()['candidates'][0]['screening_conditions_met'])

    def test_future_stale_slow_and_wrong_workbook_rejected(self):
        original=deepcopy(self.bundle)
        for changes in ({'captured_at':(self.now+timedelta(seconds=1)).isoformat()},
                        {'capture_started_at':(self.now-timedelta(seconds=31)).isoformat()},
                        {'capture_started_at':(self.now-timedelta(minutes=5)).isoformat(),
                         'captured_at':(self.now-timedelta(minutes=5)).isoformat()},
                        {'spreadsheet_id':'other'}, {'spreadsheet_timezone':'UTC'}):
            self.bundle={**deepcopy(original),**changes}
            with self.subTest(changes=changes),self.assertRaises(ValueError): self.run_case()

    def test_invalid_event_does_not_become_clear(self):
        for row in (['1000','test','bad'], ['9999','typo','2026/11/13'],['1000','test','2030/01/01']):
            self.bundle['events']=[EVENT_HEADERS,row]
            with self.subTest(row=row),self.assertRaises(ValueError):self.run_case()


if __name__ == '__main__':
    unittest.main()
