"""Native mobile sizing worksheet; calculation only, never live authorization.

No account balance is inferred. Inputs belong to an explicitly typed code.
All calculated sizing is zero for a rank/lot/price/input failure. Event inputs
are preparation notes until the native final confirmation gate is connected.
"""
import json

TITLE = '発注前資金確認'
SHEET_ID = 202611114


def lookup(column):
    return ('=IF($B$3="","",IFERROR(INDEX(FILTER(\'SBI連動_v77\'!$'+column+'$4:$'+column+
            '$13,ARRAYFORMULA(TO_TEXT(\'SBI連動_v77\'!$B$4:$B$13))=TO_TEXT($B$3)),1),""))')


FORMULAS = {4: lookup('C'), 5: lookup('D'), 6: lookup('E'), 7: lookup('F'), 8: lookup('N')}
FORMULAS[20] = '=IFERROR(IFS(B3="","銘柄を選択",TO_TEXT(B10)<>TO_TEXT(B3),"入力対象コードが不一致",NOT(OR(B4="A",B4="A+")),"A/A+以外",OR(NOT(ISNUMBER(B5)),B5<100,MOD(B5,100)<>0),"推奨100株未満・単位不正",OR(COUNT(B6:B7)<>2,MIN(B6:B7)<=0,B6<B7),"現在値・VWAP条件外",OR(COUNT(B11:B15)<>5,MIN(B11:B14)<=0,B15<0),"資金入力不足・数値不正",B11>=B6,"損切り価格が現在値以上",TRUE,"計算可能"),"計算エラー")'
FORMULAS[21] = '=IFERROR(IF(B20="計算可能",FLOOR(MIN(B12,B13)/((B6-B11+B15)*100),1)*100,0),0)'
FORMULAS[22] = '=IFERROR(IF(B20="計算可能",FLOOR(B14/((B6+B15)*100),1)*100,0),0)'
FORMULAS[23] = '=IFERROR(IF(B20="計算可能",MIN(B5,B21,B22),0),0)'
FORMULAS[24] = '=IFERROR(IF(B23>=100,B23*(B6-B11+B15),0),0)'
FORMULAS[25] = '=IFERROR(IF(B23>=100,B23*(B6+B15),0),0)'
FORMULAS[26] = '=IFERROR(IF(B23<100,"100株未満・GO禁止","資金計算のみ・GO禁止"),"判定異常・GO禁止")'
FORMULAS[28] = lookup('M')


def requests(sheet_id=SHEET_ID):
    labels = {
        1: ['発注前の資金確認','',''],
        2: ['計算用・GO禁止','',''],
        3: ['候補コード','','一覧から選択'],
        4: ['BUYランク','','SBI連動から自動表示'],
        5: ['推奨株数','','この株数も超えない'],
        6: ['SBI現在値（円）','','SBI連動に入力した値'],
        7: ['SBI当日VWAP（円）','','SBI連動に入力した値'],
        8: ['SBI確認の記録日時','','日時だけではGOになりません'],
        9: ['入力欄','','コードを変えたら全項目を再入力'],
        10: ['入力対象コード','','選択したコードを入力'],
        11: ['損切り価格（円）','','現在値より低い価格'],
        12: ['今回の許容損失（円）','','1取引で許容する予定損失'],
        13: ['残りの損失予算（円）','','保有中・未約定注文の予定損失を差し引く'],
        14: ['買付余力（円）','','SBIの現在の現物買付余力'],
        15: ['1株あたり費用（円）','','往復手数料＋スリッページ。0も明示入力'],
        16: ['イベント調査期限','','何日まで調査したか。日付で入力'],
        17: ['イベント根拠URL','','公式IR・公表日程など'],
        18: ['イベント調査メモ','','全体イベントも確認。登録なし＝安全ではありません'],
        19: ['計算結果','','注文・GOの許可ではありません'],
        20: ['入力チェック','','欠損・異常時は株数0'],
        21: ['損失予算による上限（株）','','今回と残余予算の小さい方'],
        22: ['買付余力による上限（株）','','費用を含め100株単位'],
        23: ['計算上限株数（株）','','推奨・損失予算・余力の最小値'],
        24: ['予定損失＋費用（円）','','急落・ギャップで超過する場合があります'],
        25: ['必要資金＋費用（円）','','費用を全額含めた保守的な計算'],
        26: ['この画面の判定','','100株未満なら買わない'],
        27: ['連携状態','最終確認処理の接続待ち','入力の確認日時・銘柄との結合は未接続'],
        28: ['SBI連動の停止理由','','既存の最終判定を参照'],
        29: ['使い方','',''],
        30: ['1. SBI連動で価格と4項目を入力し、最後に確認をタップ。','',''],
        31: ['2. この画面のコードと資金項目を入力。初期資金は余力の代わりにしません。','',''],
        32: ['3. 現在は計算用です。数値が出ても新規買いGOではありません。','',''],
    }
    rows = []
    for r in range(1, 33):
        values = [{'userEnteredValue': {'stringValue':v}} if v else {} for v in labels[r]]
        if r in FORMULAS:
            values[1] = {'userEnteredValue': {'formulaValue':FORMULAS[r]}}
        rows.append({'values':values})
    def area(r1,r2,c1=0,c2=3):
        return {'sheetId':sheet_id,'startRowIndex':r1-1,'endRowIndex':r2,'startColumnIndex':c1,'endColumnIndex':c2}
    req = [
        {'addSheet':{'properties':{'sheetId':sheet_id,'title':TITLE,'gridProperties':{'rowCount':40,'columnCount':3,'frozenRowCount':2}}}},
        {'updateCells':{'range':area(1,32),'rows':rows,'fields':'userEnteredValue'}},
        {'repeatCell':{'range':area(1,32),'cell':{'userEnteredFormat':{'wrapStrategy':'WRAP','verticalAlignment':'MIDDLE','textFormat':{'fontSize':11}}},'fields':'userEnteredFormat'}},
        {'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'COLUMNS','startIndex':0,'endIndex':1},'properties':{'pixelSize':180},'fields':'pixelSize'}},
        {'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'COLUMNS','startIndex':1,'endIndex':2},'properties':{'pixelSize':180},'fields':'pixelSize'}},
        {'updateDimensionProperties':{'range':{'sheetId':sheet_id,'dimension':'COLUMNS','startIndex':2,'endIndex':3},'properties':{'pixelSize':260},'fields':'pixelSize'}},
        {'setDataValidation':{'range':area(3,3,1,2),'rule':{'condition':{'type':'ONE_OF_RANGE','values':[{'userEnteredValue':"='実戦候補TOP10'!$B$2:$B$11"}]},'strict':True,'showCustomUi':True}}},
        {'setDataValidation':{'range':area(11,14,1,2),'rule':{'condition':{'type':'NUMBER_GREATER','values':[{'userEnteredValue':'0'}]},'strict':True}}},
        {'setDataValidation':{'range':area(15,15,1,2),'rule':{'condition':{'type':'NUMBER_GREATER_THAN_EQ','values':[{'userEnteredValue':'0'}]},'strict':True}}},
        {'setDataValidation':{'range':area(16,16,1,2),'rule':{'condition':{'type':'DATE_IS_VALID'},'strict':True}}},
        {'repeatCell':{'range':area(8,8,1,2),'cell':{'userEnteredFormat':{'numberFormat':{'type':'DATE_TIME','pattern':'yyyy/mm/dd hh:mm:ss'}}},'fields':'userEnteredFormat.numberFormat'}},
        {'repeatCell':{'range':area(16,16,1,2),'cell':{'userEnteredFormat':{'numberFormat':{'type':'DATE','pattern':'yyyy/mm/dd'}}},'fields':'userEnteredFormat.numberFormat'}},
    ]
    for row in (1,2,9,19,29):
        req.append({'repeatCell':{'range':area(row,row),'cell':{'userEnteredFormat':{'backgroundColor':{'red':.9,'green':.93,'blue':.97},'textFormat':{'bold':True}}},'fields':'userEnteredFormat(backgroundColor,textFormat.bold)'}})
    for start,end in ((1,2),(30,30),(31,31),(32,32)):
        req.append({'mergeCells':{'range':area(start,end),'mergeType':'MERGE_ROWS'}})
    for start,end in ((3,3),(10,18)):
        req.append({'repeatCell':{'range':area(start,end,1,2),'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':.98,'blue':.88}}},'fields':'userEnteredFormat.backgroundColor'}})
    req.append({'autoResizeDimensions':{'dimensions':{'sheetId':sheet_id,'dimension':'ROWS','startIndex':0,'endIndex':32}}})
    return req


if __name__ == '__main__':
    print(json.dumps(requests(), ensure_ascii=False))
