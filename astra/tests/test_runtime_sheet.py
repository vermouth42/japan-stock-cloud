from copy import deepcopy
import unittest
from astra.runtime_sheet import GoogleReadOnly,RANGES,SHEET_ID,context,capture
from astra.tests.test_webhook_pipeline import fixture


class RuntimeSheetTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture();self.calls=[]
        self.meta={'spreadsheetId':SHEET_ID,'properties':{'timeZone':'Asia/Tokyo'}}
        self.data={'spreadsheetId':SHEET_ID,'valueRanges':[
            {'range':f"'{s}'!{r}",'majorDimension':'ROWS','values':self.f.bundle[k]} for k,(s,r) in RANGES.items()]}

    def get(self,url,token):
        self.calls.append((url,token))
        return deepcopy(self.data if 'batchGet' in url else self.meta)

    def test_fixed_workbook_bounded_get_and_context(self):
        r=GoogleReadOnly(lambda:'fixture-token',get_json=self.get).snapshot(lambda:self.f.now)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all('fixture-token' not in url for url,_ in self.calls))
        self.assertIn('valueRenderOption=UNFORMATTED_VALUE',self.calls[1][0])
        c=context(r,self.f.codes,self.f.codes[0],self.f.now)
        self.assertEqual(c.stop,'990.00');self.assertEqual(c.budget.cash,'1000000')

    def test_wrong_workbook_or_reordered_ranges_rejected(self):
        for mode in ('workbook','range'):
            self.setUp()
            if mode=='workbook':self.meta['spreadsheetId']='different'
            else:self.data['valueRanges'].reverse()
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'SHEET_READ_FAILED'):
                GoogleReadOnly(lambda:'fixture-token',get_json=self.get).snapshot(lambda:self.f.now)

    def test_credential_error_is_redacted_and_metadata_required(self):
        def fail():raise RuntimeError('SENSITIVE_ACCOUNT_VALUE')
        with self.assertRaisesRegex(ValueError,'^AUTHENTICATED_SHEET_READ_FAILED$'):
            GoogleReadOnly(fail,get_json=self.get).snapshot(lambda:self.f.now)
        with self.assertRaisesRegex(ValueError,'WORKBOOK_METADATA_INVALID'):
            capture(lambda *a:self.fail('must not read'),lambda:self.f.now,metadata={})
