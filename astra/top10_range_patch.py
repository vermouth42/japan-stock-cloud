"""Generate only the audited TOP10 97-to-100 stock reference repair.

Accepts current CellData; no network. Unexpected formulas stop generation.
Backup the input, validate on a copy, and compare again before applying.
"""
import re

from .sheet_guard import cell


def patch(cell_data):
    sheet = cell_data['sheets'][0]
    if len(cell_data['sheets']) != 1 or sheet['properties']['title'] != '実戦候補TOP10':
        raise ValueError('unexpected target sheet')
    blocks = sheet['data']
    if len(blocks) != 1 or blocks[0].get('startRow', 0) or blocks[0].get('startColumn', 0):
        raise ValueError('expected A1:Q11 cell data')
    rows = blocks[0]['rowData']
    if len(rows) != 11:
        raise ValueError('expected header plus 10 candidates')
    requests = []
    pattern = r"('監視ランキング'!\$([A-R])\$4:\$\2\$)100(?!\d)"
    for i, row in enumerate(rows[1:], 2):
        if len(row['values']) != 17:
            raise ValueError('unexpected row width')
        for j in (2,3,4,5,6,7,15,16,17):
            c = row['values'][j-1]
            formula = c.get('userEnteredValue', {}).get('formulaValue', '')
            updated, count = re.subn(pattern, r'\g<1>103', formula)
            if count != (3 if j == 2 else 2) or c.get('dataValidation'):
                raise ValueError('target formula or validation changed')
            requests.append(cell(sheet['properties']['sheetId'], i, j, updated))
    return requests
