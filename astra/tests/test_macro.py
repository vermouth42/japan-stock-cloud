import unittest
from datetime import datetime, timedelta, timezone
from astra.macro import Observation, asof, context


class MacroTest(unittest.TestCase):
    def t(self, day, hour=0):
        return datetime(2026, 11, day, hour, tzinfo=timezone.utc)

    def obs(self, observed, available, value):
        return Observation("SOX", observed, available, value, "fixture", str(value))

    def test_us_close_after_japan_decision_is_unavailable(self):
        rows = [self.obs(self.t(9,21), self.t(9,21), 100),
                self.obs(self.t(10,21), self.t(10,21), 999)]
        result = asof(rows, "SOX", self.t(10,0), timedelta(days=4))
        self.assertEqual(result.value, 100)

    def test_revision_not_backfilled_into_past(self):
        rows = [self.obs(self.t(1), self.t(2), 100),
                self.obs(self.t(1), self.t(9), 110)]
        self.assertEqual(asof(rows,"SOX",self.t(8),timedelta(days=30)).value,100)
        self.assertEqual(asof(rows,"SOX",self.t(10),timedelta(days=30)).value,110)

    def test_old_revision_does_not_replace_new_observation(self):
        rows = [self.obs(self.t(1),self.t(10),110),
                self.obs(self.t(8),self.t(9),120)]
        self.assertEqual(asof(rows,"SOX",self.t(10),timedelta(days=30)).value,120)

    def test_stale_missing_conflicting_and_naive(self):
        row=self.obs(self.t(1),self.t(2),100)
        self.assertIsNone(asof([row],"SOX",self.t(10),timedelta(days=4)))
        self.assertEqual(context([],self.t(10),{"SOX":timedelta(days=4)})["missing"],("SOX",))
        with self.assertRaises(ValueError):
            asof([row,self.obs(self.t(1),self.t(2),101)],"SOX",self.t(10),timedelta(days=30))
        with self.assertRaises(ValueError):
            asof([],"SOX",datetime(2026,11,10),timedelta(days=4))


if __name__ == "__main__": unittest.main()
