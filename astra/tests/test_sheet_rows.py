import unittest
from datetime import date, timedelta
from astra.sheet_rows import prepare


class SheetRowsTest(unittest.TestCase):
    def test_45_days_per_symbol_and_existing_layout(self):
        codes=[str(1000+i) for i in range(99)]+['278A']
        cutoff=date(2026,11,9)
        rows=[]
        for code in codes:
            for i in range(50):
                r=[(cutoff-timedelta(days=i)).isoformat(),code]+['1']*15
                r[9]=''
                rows.append(r)
        actual=prepare(rows,codes,cutoff)
        self.assertEqual(len(actual),4500)
        self.assertTrue(all(len(r)==18 and r[1]=='' and r[10]=='' for r in actual))
        self.assertEqual(actual[0][0],46335)
        self.assertIn('278A',{r[2] for r in actual})
        self.assertEqual(min(r[0] for r in actual),46335-44)


if __name__=='__main__':unittest.main()
