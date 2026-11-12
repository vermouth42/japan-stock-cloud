import copy
import unittest
import threading
from datetime import date, datetime, timedelta
from astra.market_data import Calendar,JST,parse_chart,validate_dataset,calculate,build


class MarketDataTest(unittest.TestCase):
    def test_delayed_morning_run_uses_previous_day(self):
        self.assertEqual(Calendar().latest_complete(datetime(2026,11,9,12,6,tzinfo=JST)),date(2026,11,6))
        self.assertEqual(Calendar().latest_complete(datetime(2026,11,10,8,10,tzinfo=JST)),date(2026,11,9))

    def test_after_buffer_includes_today(self):
        self.assertEqual(Calendar().latest_complete(datetime(2026,11,9,22,47,tzinfo=JST)),date(2026,11,9))

    def test_golden_week_and_new_year(self):
        self.assertEqual(Calendar().latest_complete(datetime(2026,5,7,8,10,tzinfo=JST)),date(2026,5,1))
        self.assertEqual(Calendar().latest_complete(datetime(2027,1,4,8,10,tzinfo=JST)),date(2026,12,30))

    def test_event_business_days(self):
        self.assertEqual(Calendar().sessions_until(date(2026,11,20),date(2026,11,26)),3)

    def test_unknown_calendar_fails_closed(self):
        with self.assertRaises(ValueError):Calendar().latest_complete(datetime(2028,11,1,20,tzinfo=JST))

    def fixture(self):
        return {'chart':{'result':[{'timestamp':[datetime(2026,11,d,9,tzinfo=JST).timestamp() for d in (6,9)],'indicators':{'quote':[{'open':[100,101],'high':[110,111],'low':[90,91],'close':[105,106],'volume':[1000,2000]}]}}]}}

    def test_intraday_bar_excluded_before_checks(self):
        rows=parse_chart(self.fixture(),date(2026,11,6))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['date'],'2026-11-06')

    def test_missing_latest_blocks(self):
        with self.assertRaises(ValueError):parse_chart(self.fixture(),date(2026,11,10))

    def test_duplicate_and_invalid_ohlcv_block(self):
        p=self.fixture();p['chart']['result'][0]['timestamp'][1]=p['chart']['result'][0]['timestamp'][0]
        with self.assertRaises(ValueError):parse_chart(p,date(2026,11,6))
        for value in [None,float('nan'),-1]:
            p=self.fixture();p['chart']['result'][0]['indicators']['quote'][0]['close'][0]=value
            with self.subTest(value=value),self.assertRaises(ValueError):parse_chart(p,date(2026,11,6))

    def test_dataset_needs_all_100(self):
        codes=[str(1000+i) for i in range(100)]
        rows=[]
        # Synthetic unique history; this test concerns coverage, not sessions.
        for c in codes:
            for i in range(45): rows.append([f'2026-{9+i//28:02}-{1+i%28:02}',c]+['1']*15)
            rows[-1][0]='2026-11-06'
        validate_dataset(rows,codes,date(2026,11,6))
        with self.assertRaises(ValueError):validate_dataset(rows[:-1],codes,date(2026,11,6))
        with self.assertRaises(ValueError):validate_dataset(rows+[rows[0]],codes,date(2026,11,6))

    def test_schema_and_legacy_atr(self):
        bars=[dict(date=f'2026-10-{i+1:02}',open=100,high=110,low=90,close=105,volume=1000) for i in range(25)]
        r=calculate('1000',bars)[-1]
        self.assertEqual((len(r),r[9],r[15]),(17,'','20'))

    def test_failed_fetch_cancels_waiting_stocks_and_redacts_error(self):
        calls = []
        lock = threading.Lock()
        release = threading.Event()
        def getter(code, now, cutoff):
            with lock:
                calls.append(code)
            if code == '1000':
                raise OSError('sensitive upstream response must not appear')
            release.wait(0.2)
            raise OSError('network failed')
        with self.assertRaisesRegex(RuntimeError, '^data acquisition failed for 1000: OSError$'):
            build([str(1000+i) for i in range(100)], datetime(2026,11,10,10,tzinfo=JST), getter)
        self.assertLess(len(calls), 100)

    def test_success_reports_all_100_and_preserves_schema(self):
        codes = [str(1000+i) for i in range(100)]
        completed = []
        def getter(code, now, cutoff):
            return [dict(date=(cutoff-timedelta(days=i)).isoformat(), open=100,
                         high=110, low=95, close=105, volume=1000) for i in range(44,-1,-1)]
        rows, status = build(codes, datetime(2026,11,10,10,tzinfo=JST), getter,
                             lambda n,total,code: completed.append((n,total,code)))
        self.assertEqual((len(rows), status['stocks_success']), (4500,100))
        self.assertEqual([n for n,_,_ in completed], list(range(1,101)))
        self.assertEqual({code for _,_,code in completed}, set(codes))

    def test_invalid_calendar_dates_and_boolean_prices_rejected(self):
        codes = [str(1000+i) for i in range(100)]
        with self.assertRaises(ValueError):
            validate_dataset([['2026-02-30','1000']+['1']*15],codes,date(2026,11,9))
        p = self.fixture()
        p['chart']['result'][0]['indicators']['quote'][0]['volume'][0] = True
        with self.assertRaises(ValueError):
            parse_chart(p,date(2026,11,6))


if __name__=='__main__':unittest.main()
