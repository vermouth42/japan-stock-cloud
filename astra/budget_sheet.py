"""Isolated, mobile native-sheet preview of the user-input budget.

Uses completed daily closes, NOT current quotes. Shared preview allocations
are not the durable notification ledger and never authorize an order.
"""
import json
TITLE='入力予算判定'
SHEET_ID=202611120


def requests():
    rows=[['入力予算に基づく判定','',''],
          ['参照：設定 B2/B3。SBI実残高の自動取得ではありません。','',''],
          ['判定用予算',"='設定'!B2",'変更は従来の設定 B2'],
          ['1取引リスク率',"='設定'!B3",'変更は従来の設定 B3'],
          ['費用・滑りの仮定率',.002,'往復分の仮定。実際の手数料ではない／変更可'],
          ['許容損失', '=IFERROR(IF(AND(ISNUMBER(B3),B3>=0,ISNUMBER(B4),B4>0,B4<=1),B3*B4,0),0)','1取引の上限。ギャップ超過はあり得る'],
          ['終値による試算。TV価格・板・イベントとの最終照合は未接続。GO禁止。','',''],
          ['上位から予算を配分。通知の予約台帳とは別の試算です。','','']]
    for i in range(10):
        t=i+2;r=len(rows)+1
        prev=0 if i==0 else r-2
        remaining='MAX(0,$B$3)' if i==0 else f'MAX(0,$B$3-SUM('+','.join(f'B{9+j*8+6}' for j in range(i))+'))'
        ok=f'AND(ISNUMBER($B$3),$B$3>=0,ISNUMBER($B$4),$B$4>0,$B$4<=1,ISNUMBER($B$5),$B$5>=0,OR(C{r}="A",C{r}="A+"),ISNUMBER(B{r+1}),ISNUMBER(B{r+2}),B{r+1}>B{r+2},B{r+2}>0,ISNUMBER(\'実戦候補TOP10\'!M{t}),\'実戦候補TOP10\'!M{t}>=100,MOD(\'実戦候補TOP10\'!M{t},100)=0)'
        rows += [
            [f'候補 {i+1}',f'=TO_TEXT(\'実戦候補TOP10\'!B{t})',f"='実戦候補TOP10'!D{t}"],
            ['日足終値',f"='実戦候補TOP10'!F{t}",'当日現在値ではありません'],
            ['損切り基準',f'=IFERROR(B{r+1}*(1-\'実戦候補TOP10\'!H{t}/100),0)','元の損切り率を維持'],
            ['1株の費用仮定',f'=IFERROR(ROUNDUP(B{r+1}*$B$5,2),0)','予算・損失の両方に加算'],
            ['100株の予定損失',f'=IFERROR(ROUNDUP((B{r+1}-B{r+2}+B{r+3})*100,0),0)','許容損失と比較'],
            ['試算株数',f'=IFERROR(IF({ok},MAX(0,MIN(\'実戦候補TOP10\'!M{t},FLOOR($B$6/B{r+4},1)*100,FLOOR(({remaining})/(ROUNDUP((B{r+1}+B{r+3})*100,0)),1)*100)),0),0)','A/A+のみ。100株単位'],
            ['配分予定資金',f'=IFERROR(ROUNDUP(B{r+5}*(B{r+1}+B{r+3}),0),0)','上位候補から差し引いた試算'],
            ['判定',f'=IFERROR(IF(NOT(OR(C{r}="A",C{r}="A+")),"見送り：A/A+以外",IF(B{r+5}<100,"見送り：100株の予算・損失条件外","予算内：市場条件の照合待ち")),"見送り：入力異常")','注文判断：未完了・GO禁止']]
    n=len(rows)
    def area(a,b,c=0,d=3):return dict(sheetId=SHEET_ID,startRowIndex=a-1,endRowIndex=b,startColumnIndex=c,endColumnIndex=d)
    def cell(v):
        return {'userEnteredValue':{('numberValue' if isinstance(v,(int,float)) else 'formulaValue' if v.startswith('=') else 'stringValue'):v}}
    req=[{'addSheet':{'properties':{'sheetId':SHEET_ID,'title':TITLE,'gridProperties':{'rowCount':n+10,'columnCount':3,'frozenRowCount':1}}}},
         {'updateCells':{'range':area(1,n),'rows':[{'values':[cell(v) for v in row]} for row in rows],'fields':'userEnteredValue'}},
         {'repeatCell':{'range':area(1,n),'cell':{'userEnteredFormat':{'wrapStrategy':'WRAP','verticalAlignment':'MIDDLE','textFormat':{'fontSize':11}}},'fields':'userEnteredFormat'}},
         {'repeatCell':{'range':area(3,n,1,2),'cell':{'userEnteredFormat':{'numberFormat':{'type':'NUMBER','pattern':'#,##0.00'}}},'fields':'userEnteredFormat.numberFormat'}},
         {'repeatCell':{'range':area(4,5,1,2),'cell':{'userEnteredFormat':{'numberFormat':{'type':'PERCENT','pattern':'0.0%'}}},'fields':'userEnteredFormat.numberFormat'}},
         {'setDataValidation':{'range':area(5,5,1,2),'rule':{'condition':{'type':'NUMBER_BETWEEN','values':[{'userEnteredValue':'0'},{'userEnteredValue':'1'}]},'strict':True}}},
         {'repeatCell':{'range':area(5,5,1,2),'cell':{'userEnteredFormat':{'backgroundColor':{'red':1,'green':.98,'blue':.88}}},'fields':'userEnteredFormat.backgroundColor'}}]
    for a in (1,2,7,8):req.append({'mergeCells':{'range':area(a,a),'mergeType':'MERGE_ROWS'}})
    for a in (1,*range(9,n,8)):
        req.append({'repeatCell':{'range':area(a,a),'cell':{'userEnteredFormat':{'backgroundColor':{'red':.94,'green':.94,'blue':.94},'textFormat':{'bold':True}}},'fields':'userEnteredFormat(backgroundColor,textFormat.bold)'}})
    for i,width in enumerate((145,215,205)):
        req.append({'updateDimensionProperties':{'range':{'sheetId':SHEET_ID,'dimension':'COLUMNS','startIndex':i,'endIndex':i+1},'properties':{'pixelSize':width},'fields':'pixelSize'}})
    req.append({'autoResizeDimensions':{'dimensions':{'sheetId':SHEET_ID,'dimension':'ROWS','startIndex':0,'endIndex':n}}})
    return req


if __name__=='__main__':print(json.dumps(requests(),ensure_ascii=False))
