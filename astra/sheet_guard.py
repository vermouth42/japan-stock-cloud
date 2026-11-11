"""Generate a reversible, fail-closed Sheets patch. No network or write calls.

This temporary gate never grants GO. It closes legacy BUY/GO routes until the
confirmation handler, cash/risk inputs and common live decision are deployed.
Apply on the verified copy first; back up target CellData before production.
"""

SBI_ID = 1550759862
IPAD_ID = 359333604
SIGNAL_ID = 916006725


def vwap_formula(row):
    return f'=IFERROR(AND(ISNUMBER(E{row}),ISNUMBER(F{row}),E{row}>0,F{row}>0,E{row}>=F{row}),FALSE)'


def event_formula(row):
    return (f'=IF(B{row}="","",IFERROR(LET(codes,ARRAYFORMULA(TO_TEXT(\'イベント管理\'!$A$2:$A$100)),'
        'raw,\'イベント管理\'!$C$2:$C$100,'
        'dates,MAP(raw,LAMBDA(d,IF(d="",0,IF(ISNUMBER(d),INT(d),IFERROR(DATEVALUE(d),-1))))),'
        f'rel,ARRAYFORMULA((codes=TO_TEXT(B{row}))+(codes="0000")),'
        'limitday,WORKDAY(TODAY(),3,\'JPX休場日\'!$A$3:$A$43),'
        'near,SUMPRODUCT(rel,--(dates>=TODAY()),--(dates<=limitday)),'
        'bad,SUMPRODUCT(rel,--(dates<1)),'
        'unknownyear,SUMPRODUCT(rel,--(dates>0),--(dates>=DATE(2028,1,1))),'
        'IF(OR(YEAR(TODAY())<2026,YEAR(limitday)>2027,unknownyear>0),"休日範囲外・GO禁止",'
        'IF(near>0,"重要イベント3営業日以内",IF(bad>0,"イベント日不明・GO禁止",'
        '"イベント調査範囲未確認・GO禁止")))),"イベント判定異常・GO禁止"))')


def gate_formula(row):
    return (f'=IFERROR(IF(B{row}="","",IF(NOT(OR(C{row}="A",C{row}="A+")),"ランク条件外・GO禁止",'
        f'IF(OR(NOT(ISNUMBER(D{row})),D{row}<100,MOD(D{row},100)<>0),"株数条件外・GO禁止",'
        f'IF(O{row}="重要イベント3営業日以内","イベント接近・GO禁止",'
        f'IF(OR(E{row}="",F{row}=""),"SBI価格未入力・GO禁止",'
        f'IF(NOT(G{row}),"VWAP条件外・GO禁止",'
        '"安全連携未完了・GO禁止")))))),"判定異常・GO禁止")')


def linked_gate(symbol_cell):
    # Text normalization supports numeric codes and alphanumeric stock codes.
    return (f'=IF({symbol_cell}="","",IFERROR(IF(COUNTIF(\'SBI連動_v77\'!$B$4:$B$13,{symbol_cell})<>1,'
        '"銘柄対応不明・GO禁止",INDEX(\'SBI連動_v77\'!$M$4:$M$13,'
        f'MATCH(TO_TEXT({symbol_cell}),ARRAYFORMULA(TO_TEXT(\'SBI連動_v77\'!$B$4:$B$13)),0))),'
        '"安全判定未確認・GO禁止"))')


def cell(sheet, row, column, formula):
    return {'updateCells':{'range':{'sheetId':sheet,'startRowIndex':row-1,'endRowIndex':row,
        'startColumnIndex':column-1,'endColumnIndex':column},
        'rows':[{'values':[{'userEnteredValue':{'formulaValue':formula}}]}],
        'fields':'userEnteredValue'}}


def patch_requests():
    requests=[]
    for row in range(4,14):
        requests.extend([cell(SBI_ID,row,7,vwap_formula(row)),
                         cell(SBI_ID,row,15,event_formula(row)),
                         cell(SBI_ID,row,13,gate_formula(row)),
                         cell(SIGNAL_ID,row,16,linked_gate(f'B{row}'))])
    for row in (7,10,13,16,19):
        requests.append(cell(IPAD_ID,row,7,linked_gate(f'B{row-2}')))
    return requests


if __name__=='__main__':
    import json
    print(json.dumps(patch_requests(),ensure_ascii=False,indent=2))
