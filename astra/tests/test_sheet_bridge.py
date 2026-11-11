import copy
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from astra.market_data import JST
from astra.sheet_bridge import (TOP_HEADERS, SBI_HEADERS, EVENT_HEADERS, table,
    sheet_time, event_date, fingerprint, build_manual, near_events, review)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 10, tzinfo=JST)
        self.top = dict.fromkeys(TOP_HEADERS)
        self.top.update({'コード':'1000','BUY':'A','推奨株数':100})
        self.sbi = dict.fromkeys(SBI_HEADERS)
        self.sbi.update({'コード':'1000','BUY':'A','推奨株数':100,
            'SBI現在値':1000,'SBI当日VWAP':1000,'VWAP上':False,
            '出来高継続':True,'上ヒゲ小':True,'ギャップOK':True,
            '板OK':True,'SBI確認済（タップ）':True,'確認記録日時（自動）':self.now.isoformat()})
        self.receipt = dict(symbol='1000',fingerprint=fingerprint(self.top,self.sbi),
            rank_checked_at=self.now.isoformat(),sbi_checked_at=self.now.isoformat(),
            event_checked_at=self.now.isoformat(),events_clear_through='2026-11-16',
            event_evidence='synthetic fixture only',stop=950,allowed_loss=10000,
            remaining_risk_budget=10000,available_cash=1000000,cost_per_share=2)

    def event(self, day, code='1000', **changes):
        row = dict.fromkeys(EVENT_HEADERS)
        row.update({'コード':code,'イベント日':day,'種別':'fixture','残日数':999,'警戒':'終了'})
        row.update(changes)
        return row

    def test_same_price_vwap_is_passed_without_legacy_greater_than_flag(self):
        manual, reasons, _ = build_manual(self.top,self.sbi,[],self.receipt,self.now)
        self.assertEqual(reasons,[])
        self.assertEqual(manual['current_price'],manual['vwap'])

    def test_reading_sheet_does_not_create_confirmation(self):
        manual, reasons, _ = build_manual(self.top,self.sbi,[],None,self.now)
        self.assertEqual(manual['symbol'],'1000')
        self.assertNotIn('rank_checked_at',manual)
        self.assertNotIn('sbi_checked_at',manual)
        self.assertIn('CONFIRMATION_RECEIPT_MISSING_OR_SCHEMA',reasons)

    def test_new_symbol_in_same_row_invalidates_receipt(self):
        top,sbi=copy.deepcopy(self.top),copy.deepcopy(self.sbi)
        top['コード']=sbi['コード']='1001'
        _,reasons,_=build_manual(top,sbi,[],self.receipt,self.now)
        self.assertIn('CONFIRMATION_BINDING_MISMATCH',reasons)

    def test_price_rank_size_or_check_change_invalidates_receipt(self):
        for key,value in [('SBI現在値',1100),('BUY','B'),('推奨株数',200),('板OK',False)]:
            with self.subTest(key=key):
                sbi={**self.sbi,key:value}
                _,reasons,_=build_manual(self.top,sbi,[],self.receipt,self.now)
                self.assertIn('CONFIRMATION_BINDING_MISMATCH',reasons)

    def test_unchecked_confirmation_and_changed_timestamp(self):
        for key,value in [('SBI確認済（タップ）',False),('確認記録日時（自動）',(self.now-timedelta(minutes=1)).isoformat())]:
            sbi={**self.sbi,key:value}
            receipt={**self.receipt,'fingerprint':fingerprint(self.top,sbi)}
            _,reasons,_=build_manual(self.top,sbi,[],receipt,self.now)
            self.assertTrue(any(x in reasons for x in ['SBI_CONFIRMATION_UNCHECKED','SBI_TIMESTAMP_MISMATCH']))

    def test_event_countdown_and_finished_label_cannot_override_date(self):
        _,reasons,near=build_manual(self.top,self.sbi,[self.event('2026/11/13')],self.receipt,self.now)
        self.assertIn('KNOWN_EVENT_WITHIN_3_JPX_SESSIONS',reasons)
        self.assertEqual(near[0]['business_days'],2)

    def test_weekend_and_jpx_holiday_count(self):
        near=near_events([self.event('2026-11-25')],'1000',date(2026,11,20))
        self.assertEqual(near[0]['business_days'],2)  # Nov 23 holiday
        self.assertEqual(near_events([self.event('2026-11-10')],'1000',self.now.date()),[])
        self.assertEqual(len(near_events([self.event('2026-11-12','0000')],'1000',self.now.date())),1)

    def test_coverage_is_explicit_not_inferred_from_empty_calendar(self):
        r={**self.receipt,'events_clear_through':'2026-11-13'}
        _,reasons,_=build_manual(self.top,self.sbi,[],r,self.now)
        self.assertIn('EVENT_COVERAGE_INCOMPLETE',reasons)

    def test_schema_dates_and_numeric_codes(self):
        self.assertEqual(table([['コード'],[2851]],['コード'])[0]['コード'],'2851')
        self.assertEqual(table([['コード'],['285A']],['コード'])[0]['コード'],'285A')
        self.assertEqual(event_date(46336),date(2026,11,10))
        self.assertEqual(sheet_time(46337.5),datetime(2026,11,11,12,tzinfo=JST))
        for value in (True,None,'2026-11-11T10:00:00',float('nan')):
            with self.subTest(value=value),self.assertRaises((ValueError,TypeError)):
                sheet_time(value)
        with self.assertRaises(ValueError):
            table([['wrong'],['1000']],['コード'])

    def bundle(self):
        tops,sbis=[TOP_HEADERS],[SBI_HEADERS]
        for i in range(10):
            top={**self.top,'コード':str(1000+i)}
            sbi={**self.sbi,'コード':str(1000+i)}
            tops.append([top[k] for k in TOP_HEADERS])
            sbis.append([sbi[k] for k in SBI_HEADERS])
        return dict(captured_at=self.now.isoformat(),spreadsheet_timezone='Asia/Tokyo',
                    top10=tops,sbi=sbis,events=[EVENT_HEADERS],receipts={})

    @patch('astra.sheet_bridge.assess')
    def test_all_candidates_stop_without_receipts_even_if_core_passed(self, assess):
        assess.return_value={'reasons':[],'offline_checks_passed':True,'proposed_shares':100}
        result=review(self.bundle(),{},[],[],[str(1000+i) for i in range(100)],self.now)
        self.assertEqual(len(result['candidates']),10)
        self.assertFalse(result['go_allowed'])
        self.assertTrue(all(not r['offline_checks_passed'] and r['proposed_shares']==0 for r in result['candidates']))

    def test_stale_export_duplicate_and_missing_rows_rejected(self):
        for change in ('stale','future','duplicate','missing','timezone'):
            b=self.bundle()
            if change=='stale':b['captured_at']=(self.now-timedelta(minutes=6)).isoformat()
            if change=='future':b['captured_at']=(self.now+timedelta(seconds=1)).isoformat()
            if change=='duplicate':b['sbi'][2]=b['sbi'][1][:]
            if change=='missing':b['sbi'].pop()
            if change=='timezone':b['spreadsheet_timezone']='UTC'
            with self.subTest(change=change),self.assertRaises(ValueError):
                review(b,{},[],[],[str(1000+i) for i in range(100)],self.now)

    @patch('astra.sheet_bridge.assess')
    def test_missing_event_date_blocks_matching_symbol(self, assess):
        b=self.bundle();b['events'].append([self.event(None)[k] for k in EVENT_HEADERS])
        assess.return_value={'reasons':[],'offline_checks_passed':True,'proposed_shares':100}
        result=review(b,{},[],[],[str(1000+i) for i in range(100)],self.now)
        self.assertEqual(result['candidates'][0]['reasons'],['SOURCE_VALUES_INVALID'])


if __name__=='__main__':
    unittest.main()
