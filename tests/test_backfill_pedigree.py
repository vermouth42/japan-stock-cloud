"""17_backfill_pedigree.py のオフラインテスト (通信はモック)."""
import importlib.util
import pathlib
import sqlite3

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bf", pathlib.Path(__file__).parents[1] / "tools" / "17_backfill_pedigree.py")
bf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bf)

RACE_HTML = """<html><body><table class="race_table_01">
<tr><td>1</td><td><a href="/horse/2017100001/" title="アルファ">アルファ</a></td><td><a href="/jockey/result/recent/01234/">騎手</a></td></tr>
<tr><td>2</td><td><a href="/horse/2017100002/" title="ベータ">ベータ</a></td></tr>
<tr><td>3</td><td><a href="/horse/2017100003/">ガンマ </a></td><td><a href="/horse/ped/2017100003/">血統</a></td></tr>
</table></body></html>""".encode("euc_jp")

PED_HTML = """<table class="blood_table">
<tr><td rowspan="16"><a href="/horse/sire/">ディープインパクト</a></td>
<td rowspan="8"><a>サンデーサイレンス</a></td></tr>
<tr><td rowspan="8"><a>ウインドインハーヘア</a></td></tr>
<tr><td rowspan="16"><a>母ウマ</a></td><td rowspan="8"><a>キングカメハメハ</a></td></tr>
<tr><td rowspan="8"><a>母母</a></td></tr></table>"""


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "REQUEST_DELAY", 0)
    db = tmp_path / "keiba.db"
    c = sqlite3.connect(db)
    c.executescript("""
        CREATE TABLE races (race_id TEXT PRIMARY KEY, date TEXT, class TEXT);
        CREATE TABLE entries (entry_id INTEGER PRIMARY KEY, race_id TEXT, horse_id TEXT);
        CREATE TABLE horses (horse_id TEXT PRIMARY KEY, name TEXT, sire TEXT, dam TEXT,
                             broodmare_sire TEXT, netkeiba_id TEXT);
        INSERT INTO races VALUES ('201801010101', '2018-01-06', '未勝利'), ('201001010101', '2010-01-06', '未勝利');
        INSERT INTO entries (race_id, horse_id) VALUES
            ('201801010101', 'アルファ'), ('201801010101', 'ベータ'), ('201801010101', 'ガンマ'),
            ('201001010101', '昔の馬');
        INSERT INTO horses VALUES ('アルファ', 'アルファ', NULL, NULL, NULL, NULL),
                                  ('ベータ', 'ベータ', '既知の父', '既知の母', NULL, NULL);
    """)
    c.commit()
    c.close()
    return bf.connect(str(db))


def test_parsers():
    assert bf.parse_race_horses(RACE_HTML) == {"アルファ": "2017100001", "ベータ": "2017100002",
                                               "ガンマ": "2017100003"}
    assert bf.extract_sire_dam(PED_HTML) == ("ディープインパクト", "母ウマ", "キングカメハメハ")


def test_targets_and_flow(conn, monkeypatch):
    # 2015年以降に走った血統なしの馬だけ (昔の馬・既知のベータは対象外)
    assert [h for h, _ in bf.targets(conn, "2015-01-01", need_id=True)] == ["アルファ", "ガンマ"]
    assert bf.pending_races(conn, "2015-01-01") == ["201801010101"]
    pages = {"https://db.netkeiba.com/race/201801010101/": RACE_HTML}
    monkeypatch.setattr(bf, "fetch", lambda url, must: pages.get(url, PED_HTML))
    pacer = bf.Pacer()
    assert bf.step_ids(conn, "2015-01-01", 10, pacer) == 3          # ID はそのレースの全馬分保存
    got = dict(conn.execute("SELECT horse_id, netkeiba_id FROM horses"))
    assert got["アルファ"] == "2017100001" and got["ガンマ"] == "2017100003"
    assert dict(conn.execute("SELECT horse_id, sire FROM horses"))["ベータ"] == "既知の父"  # 血統は上書きしない
    assert bf.step_ped(conn, "2015-01-01", 10, pacer) == 2
    rows = dict(conn.execute("SELECT horse_id, sire FROM horses WHERE horse_id IN ('アルファ','ガンマ')"))
    assert set(rows.values()) == {"ディープインパクト"}
    assert bf.targets(conn, "2015-01-01", need_id=False) == []        # 再実行しても取り直さない


def test_failures_are_skipped(conn, monkeypatch):
    conn.execute("UPDATE horses SET netkeiba_id = '2017100001' WHERE horse_id = 'アルファ'")
    conn.commit()
    monkeypatch.setattr(bf, "fetch", lambda url, must: "<html>no table</html>")
    pacer = bf.Pacer()
    for _ in range(bf.MAX_FAILS):
        assert bf.step_ped(conn, "2015-01-01", 10, pacer) == 0
    assert bf.targets(conn, "2015-01-01", need_id=False) == []        # 2回失敗したら以後飛ばす
