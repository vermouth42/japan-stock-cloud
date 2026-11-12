"""Patch the existing risk worksheet with the server-verified final action.

Apply only with the tested Apps Script bundle. No order API is used.
"""
import json
from .risk_form import SHEET_ID


def requests():
    def cell(row, col, value, formula=False):
        return {'updateCells': {'range': {'sheetId': SHEET_ID, 'startRowIndex': row-1,
            'endRowIndex': row, 'startColumnIndex': col-1, 'endColumnIndex': col},
            'rows': [{'values': [{'userEnteredValue': {('formulaValue' if formula else
                'boolValue' if isinstance(value, bool) else 'stringValue'): value}}]}],
            'fields': 'userEnteredValue'}}
    req = [cell(2,1,'本人の手動発注用・条件不足ならGO禁止'),
           cell(26,2,'最終確認なし・GO禁止'),
           cell(27,2,'最終確認ボタンで照合'),
           cell(27,3,'100銘柄の日足・SBI・資金・イベントを一括検査'),
           cell(32,1,'3. 個別・重要マクロイベントと資金を確認し、B34を単独タップ。'),
           cell(33,1,'最終確認'),cell(33,3,'GOは確認時点の記録。期限後は再確認。'),
           cell(34,1,'本人が確認（タップ）'),cell(34,2,False),
           cell(34,3,'損切り・余力・保有分の損失予算・個別/重要マクロイベントを確認しました'),
           cell(35,1,'照合完了日時'),cell(36,1,'表示有効期限'),cell(37,1,'照合済み株数'),
           cell(38,1,'日足データの版'),cell(39,1,'照合した市場日'),cell(40,1,'日足の照合結果')]
    region={'sheetId':SHEET_ID,'startRowIndex':32,'endRowIndex':40,'startColumnIndex':0,'endColumnIndex':3}
    req += [
        {'repeatCell':{'range':region,'cell':{'userEnteredFormat':{'wrapStrategy':'WRAP','verticalAlignment':'MIDDLE','textFormat':{'fontSize':11}}},'fields':'userEnteredFormat'}},
        {'setDataValidation':{'range':{'sheetId':SHEET_ID,'startRowIndex':33,'endRowIndex':34,'startColumnIndex':1,'endColumnIndex':2},'rule':{'condition':{'type':'BOOLEAN'},'strict':True}}},
        {'repeatCell':{'range':{'sheetId':SHEET_ID,'startRowIndex':34,'endRowIndex':36,'startColumnIndex':1,'endColumnIndex':2},'cell':{'userEnteredFormat':{'numberFormat':{'type':'DATE_TIME','pattern':'yyyy/mm/dd hh:mm:ss'}}},'fields':'userEnteredFormat.numberFormat'}},
        {'updateSpreadsheetProperties':{'properties':{'autoRecalc':'MINUTE'},'fields':'autoRecalc'}},
        {'autoResizeDimensions':{'dimensions':{'sheetId':SHEET_ID,'dimension':'ROWS','startIndex':25,'endIndex':40}}}
    ]
    for row in range(4,14):
        formula=(f'=IFERROR(IF(B{row}="","",IF(NOT(OR(C{row}="A",C{row}="A+")),"ランク条件外・GO禁止",'
                 f'IF(OR(NOT(ISNUMBER(D{row})),D{row}<100,MOD(D{row},100)<>0),"株数条件外・GO禁止",'
                 f'IF(AND(TO_TEXT(B{row})=TO_TEXT(\'発注前資金確認\'!B3),\'発注前資金確認\'!B34=TRUE,'
                 'ISNUMBER(\'発注前資金確認\'!B35),ISNUMBER(\'発注前資金確認\'!B36),'
                 'INT(\'発注前資金確認\'!B35)=TODAY(),NOW()>=\'発注前資金確認\'!B35,'
                 'NOW()<\'発注前資金確認\'!B36,\'発注前資金確認\'!B36-\'発注前資金確認\'!B35<=5/1440),'
                 '\'発注前資金確認\'!B26,"最終確認未実施・期限切れ・GO禁止")))),"判定異常・GO禁止")')
        req.append({'updateCells':{'range':{'sheetId':1550759862,'startRowIndex':row-1,'endRowIndex':row,'startColumnIndex':12,'endColumnIndex':13},
            'rows':[{'values':[{'userEnteredValue':{'formulaValue':formula}}]}],'fields':'userEnteredValue'}})
    return req


if __name__ == '__main__':
    print(json.dumps(requests(), ensure_ascii=False))
