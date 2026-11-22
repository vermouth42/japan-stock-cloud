# -*- coding: utf-8 -*-
"""
競馬予想アプリ - Step17: 2015年以降の出走馬の血統をまとめて補完する

【なぜ必要か】
血統 (父・母父) が「その後も現役を続けた馬」にしか入っていないと、血統の有無が
未来の情報になってしまう (2022年に偽の回収率130%が出た原因)。
2015年以降に走った全ての馬 (引退馬を含む) の血統をそろえれば、血統ファクターを
10年分きちんと検証できる。

【16_scrape_pedigree.py との違い】
  * 対象を「2015年以降 (変更可) に出走し、血統が空の馬」に絞る
  * netkeiba_id が無い馬 (主に Kaggle 期間の引退馬) は、その馬が走ったレースの
    結果ページ (db.netkeiba.com/race/<race_id>/) から馬名→ID を拾って horses に保存する
  * 新しいレースの馬から順に処理する。途中で止めても、その時点で血統がそろった年から
    keiba パッケージが自動で使い始める (--pedigree-since auto)
  * 何度でも再実行できる (済んだ馬・何度も失敗した馬は飛ばす)

【実行方法】 keiba_app フォルダで。長時間かかるので app.py は止めておく
   python 17_backfill_pedigree.py status            対象件数と年別の充足率を表示 (通信なし)
   python 17_backfill_pedigree.py ids 50            レース結果ページ 50 件から netkeiba_id を集める
   python 17_backfill_pedigree.py ped 100           血統ページ 100 頭分を取得
   python 17_backfill_pedigree.py all               ids → ped を対象が無くなるまで続ける
   オプション: --since 2015-01-01   対象とする出走の開始日
"""

import argparse
import re
import sqlite3
import sys
import time

DB_PATH = "keiba.db"
REQUEST_DELAY = 2.5            # 1アクセスごとの間隔 (秒)
PROACTIVE_BREAK_EVERY = 200    # これだけアクセスするごとに休む
PROACTIVE_BREAK_SECONDS = 180
COOLDOWN_THRESHOLD = 5         # 連続失敗でこれだけ続いたら休む
COOLDOWN_SECONDS = 90
DRIVER_RESTART_EVERY = 250
MAX_FAILS = 2                  # この回数失敗した馬・レースは以後飛ばす
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


# ------------------------------------------------------------------ DB
def connect(path=DB_PATH):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS pedigree_backfill_fail (
                        kind TEXT, key TEXT, fails INTEGER, last_error TEXT, tried_at TEXT,
                        PRIMARY KEY (kind, key))""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(horses)")}
    for c in ("netkeiba_id", "sire", "dam", "broodmare_sire"):
        if c not in cols:
            conn.execute(f"ALTER TABLE horses ADD COLUMN {c} TEXT")
    conn.commit()
    return conn


def norm(name):
    return re.sub(r"\s+", "", str(name or ""))


def record_fail(conn, kind, key, err):
    conn.execute("""INSERT INTO pedigree_backfill_fail VALUES (?, ?, 1, ?, datetime('now','localtime'))
                    ON CONFLICT(kind, key) DO UPDATE SET fails = fails + 1, last_error = excluded.last_error,
                    tried_at = excluded.tried_at""", (kind, str(key), str(err)[:200]))
    conn.commit()


# 対象: 期間内に出走し、血統 (父) が空の馬。最後に走った日が新しい順
TARGET_SQL = """
    SELECT e.horse_id, MAX(r.date) AS last_date
    FROM entries e JOIN races r ON e.race_id = r.race_id
    LEFT JOIN horses h ON h.horse_id = e.horse_id
    WHERE r.date >= ? AND (h.horse_id IS NULL OR h.sire IS NULL OR h.sire = '')
    GROUP BY e.horse_id
"""


def targets(conn, since, need_id):
    """need_id=True: netkeiba_id も無い馬 / False: netkeiba_id はある馬"""
    rows = conn.execute(f"""
        SELECT t.horse_id, t.last_date, h.netkeiba_id FROM ({TARGET_SQL}) t
        LEFT JOIN horses h ON h.horse_id = t.horse_id
        LEFT JOIN pedigree_backfill_fail f ON f.kind = 'ped' AND f.key = t.horse_id
        WHERE COALESCE(f.fails, 0) < ?
        ORDER BY t.last_date DESC""", (since, MAX_FAILS)).fetchall()
    if need_id:
        return [(h, d) for h, d, nid in rows if not nid]
    return [(h, nid) for h, d, nid in rows if nid]


def status(conn, since):
    print("年別の血統充足率 (結果のある出走ベース):")
    q = """SELECT substr(r.date,1,4) y, COUNT(*) n,
                  SUM(CASE WHEN h.sire IS NOT NULL AND h.sire != '' THEN 1 ELSE 0 END) s,
                  SUM(CASE WHEN h.netkeiba_id IS NOT NULL AND h.netkeiba_id != '' THEN 1 ELSE 0 END) i
           FROM entries e JOIN races r ON e.race_id = r.race_id
           LEFT JOIN horses h ON h.horse_id = e.horse_id
           WHERE r.date >= ? GROUP BY y ORDER BY y"""
    print("  年     出走数   血統あり   netkeiba_idあり")
    for y, n, s, i in conn.execute(q, (since,)):
        print(f"  {y}  {n:7d}   {s / n:7.1%}   {i / n:7.1%}")
    no_id = targets(conn, since, need_id=True)
    with_id = targets(conn, since, need_id=False)
    races = len(pending_races(conn, since))
    print(f"\n血統が空の馬: {len(no_id) + len(with_id)} 頭")
    print(f"  netkeiba_id あり (すぐ血統を取得できる): {len(with_id)} 頭")
    print(f"  netkeiba_id なし (先にレース結果ページから ID を拾う): {len(no_id)} 頭 "
          f"/ 拾うのに必要なレースページ 最大 {races} 件")
    per = REQUEST_DELAY + PROACTIVE_BREAK_SECONDS / PROACTIVE_BREAK_EVERY + 1.0
    hours = (races + len(no_id) + len(with_id)) * per / 3600
    print(f"  所要時間のめやす: 最大 約 {hours:.0f} 時間 (1件 約 {per:.1f} 秒。ID は1ページで十数頭分拾えるので実際はもっと短い)")


def to_text(html):
    """bytes -> str。netkeiba は時期・ページで UTF-8 / EUC-JP が混在するので順に試す。"""
    if isinstance(html, str):
        return html
    for enc in ("utf-8", "euc_jp", "cp932"):
        try:
            return html.decode(enc)
        except UnicodeDecodeError:
            pass
    return html.decode("euc_jp", errors="ignore")


# ------------------------------------------------------------------ HTTP
_session = None
_driver = None


def http_get(url):
    global _session
    import requests
    if _session is None:
        _session = requests.Session()
        _session.headers.update(UA)
    r = _session.get(url, timeout=20)
    r.raise_for_status()
    return r.content


def make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    d = webdriver.Chrome(options=options)
    d.set_page_load_timeout(30)
    return d


def selenium_get(url, wait_for):
    global _driver
    from selenium.webdriver.support.ui import WebDriverWait
    if _driver is None:
        _driver = make_driver()
    _driver.get(url)
    try:
        WebDriverWait(_driver, 15).until(lambda d: wait_for in d.page_source)
    except Exception:
        pass
    return _driver.page_source


def restart_driver():
    global _driver
    if _driver is not None:
        _driver.quit()
        time.sleep(5)
        _driver = None


def fetch(url, must_contain):
    """まず軽い requests で取得し、目的の要素が無ければ Selenium で描画して取得。"""
    try:
        text = to_text(http_get(url))
        if must_contain in text:
            return text
    except Exception:
        pass
    return selenium_get(url, must_contain)


# ------------------------------------------------------------------ parsing
def parse_race_horses(html):
    """レース結果ページ -> {馬名: netkeiba_id}"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(to_text(html), "lxml")
    out = {}
    for a in soup.find_all("a", href=re.compile(r"/horse/(\d{10})/?$")):
        m = re.search(r"/horse/(\d{10})", a["href"])
        name = norm(a.get_text(strip=True) or a.get("title"))
        if m and name:
            out.setdefault(name, m.group(1))
    return out


def extract_sire_dam(html):
    """5代血統表: rowspan=16 の1つ目=父, 2つ目=母 / rowspan=8 の3つ目=母父 (16番と同じ)"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(to_text(html), "lxml")
    table = soup.find("table", class_="blood_table")
    if not table:
        return None, None, None

    def clean(cell):
        a = cell.find("a")
        return a.get_text(strip=True) if a else None

    c16 = table.find_all("td", attrs={"rowspan": "16"})
    if len(c16) < 2:
        return None, None, None
    c8 = table.find_all("td", attrs={"rowspan": "8"})
    return clean(c16[0]), clean(c16[1]), (clean(c8[2]) if len(c8) >= 3 else None)


# ------------------------------------------------------------------ steps
def pending_races(conn, since):
    """ID の無い対象馬が走ったレースを、そういう馬が多い順 (同数なら新しい順) に返す。"""
    return [r[0] for r in conn.execute(f"""
        SELECT e.race_id, COUNT(*) c, MAX(r.date) d
        FROM entries e JOIN races r ON e.race_id = r.race_id
        JOIN ({TARGET_SQL}) t ON t.horse_id = e.horse_id
        LEFT JOIN horses h ON h.horse_id = e.horse_id
        LEFT JOIN pedigree_backfill_fail f ON f.kind = 'race' AND f.key = e.race_id
        WHERE (h.netkeiba_id IS NULL OR h.netkeiba_id = '') AND COALESCE(f.fails, 0) < ?
          AND r.date >= ?
        GROUP BY e.race_id ORDER BY c DESC, d DESC""", (since, MAX_FAILS, since))]


def save_ids(conn, race_id, found):
    """そのレースの出走馬 (entries の馬名) と結果ページの馬名を突き合わせて netkeiba_id を保存。"""
    names = [r[0] for r in conn.execute("SELECT horse_id FROM entries WHERE race_id = ?", (race_id,))]
    hcols = {r[1] for r in conn.execute("PRAGMA table_info(horses)")}
    n = 0
    for horse in names:
        nid = found.get(norm(horse))
        if not nid:
            continue
        exists = conn.execute("SELECT netkeiba_id FROM horses WHERE horse_id = ?", (horse,)).fetchone()
        if exists is None:
            cols = ["horse_id", "netkeiba_id"] + (["name"] if "name" in hcols else [])
            vals = [horse, nid] + ([horse] if "name" in hcols else [])
            conn.execute(f"INSERT INTO horses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
            n += 1
        elif not exists[0]:
            conn.execute("UPDATE horses SET netkeiba_id = ? WHERE horse_id = ?", (nid, horse))
            n += 1
    conn.commit()
    return n


class Pacer:
    """アクセス間隔・先回り休止・連続失敗時の休止・ブラウザ再起動をまとめて管理。"""

    def __init__(self):
        self.count = 0
        self.fails = 0

    def after(self, ok):
        self.count += 1
        self.fails = 0 if ok else self.fails + 1
        if self.fails >= COOLDOWN_THRESHOLD:
            print(f"  ⏸ {self.fails}回連続で失敗したため {COOLDOWN_SECONDS} 秒休止します...")
            time.sleep(COOLDOWN_SECONDS)
            self.fails = 0
        if self.count % DRIVER_RESTART_EVERY == 0:
            restart_driver()
        if self.count % PROACTIVE_BREAK_EVERY == 0:
            print(f"  ⏸ {self.count}件アクセスしたので {PROACTIVE_BREAK_SECONDS} 秒休止します (先回り休止)...")
            time.sleep(PROACTIVE_BREAK_SECONDS)
        time.sleep(REQUEST_DELAY)


def step_ids(conn, since, limit, pacer):
    races = pending_races(conn, since)[:limit]
    print(f"レース結果ページ {len(races)} 件から netkeiba_id を集めます...")
    got = 0
    for k, rid in enumerate(races, 1):
        # 直前の処理で、このレースの対象馬がもう全部 ID を得ていれば飛ばす
        left = conn.execute(f"""SELECT COUNT(*) FROM entries e JOIN ({TARGET_SQL}) t ON t.horse_id = e.horse_id
                                LEFT JOIN horses h ON h.horse_id = e.horse_id
                                WHERE e.race_id = ? AND (h.netkeiba_id IS NULL OR h.netkeiba_id = '')""",
                            (since, rid)).fetchone()[0]
        if left == 0:
            continue
        ok = False
        try:
            html = fetch(f"https://db.netkeiba.com/race/{rid}/", "/horse/")
            found = parse_race_horses(html)
            n = save_ids(conn, rid, found)
            got += n
            ok = n > 0
            print(f"  {'✓' if ok else '✗'} {rid}: ID 無しの対象馬 {left} 頭 / ID を保存 {n} 頭")
            if not ok:
                record_fail(conn, "race", rid, "no match")
        except Exception as e:
            print(f"  ✗ {rid}: 失敗 ({e})")
            record_fail(conn, "race", rid, e)
        pacer.after(ok)
        if k % 20 == 0:
            print(f"{k}/{len(races)} 件処理済み (ID 取得 {got} 頭)")
    return got


def step_ped(conn, since, limit, pacer):
    rows = targets(conn, since, need_id=False)[:limit]
    print(f"{len(rows)} 頭の血統を取得します...")
    success = 0
    for k, (horse, nid) in enumerate(rows, 1):
        ok = False
        try:
            html = fetch(f"https://db.netkeiba.com/horse/ped/{nid}/", "blood_table")
            sire, dam, bms = extract_sire_dam(html)
            if sire and dam:
                conn.execute("UPDATE horses SET sire = ?, dam = ?, broodmare_sire = ? WHERE horse_id = ?",
                             (sire, dam, bms, horse))
                conn.commit()
                print(f"  ✓ {horse}: 父={sire} / 母={dam} / 母父={bms}")
                success += 1
                ok = True
            else:
                print(f"  ✗ {horse}: 血統を抽出できませんでした")
                record_fail(conn, "ped", horse, "no blood_table")
        except Exception as e:
            print(f"  ✗ {horse}: 失敗 ({e})")
            record_fail(conn, "ped", horse, e)
        pacer.after(ok)
        if k % 20 == 0:
            print(f"{k}/{len(rows)} 件処理済み (成功 {success})")
    return success


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "ids", "ped", "all"])
    ap.add_argument("limit", nargs="?", type=int, default=100000)
    ap.add_argument("--since", default="2015-01-01")
    ap.add_argument("--db", default=DB_PATH)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
    conn = connect(a.db)
    pacer = Pacer()
    try:
        if a.cmd == "status":
            status(conn, a.since)
        elif a.cmd == "ids":
            step_ids(conn, a.since, a.limit, pacer)
        elif a.cmd == "ped":
            step_ped(conn, a.since, a.limit, pacer)
        else:
            # ID の取得と血統の取得を交互に、小分けで進める (途中で止めても両方が少しずつ進む)
            while True:
                n1 = step_ids(conn, a.since, 50, pacer)
                n2 = step_ped(conn, a.since, 200, pacer)
                if n1 == 0 and n2 == 0:
                    break
            status(conn, a.since)
    except KeyboardInterrupt:
        print("\n中断しました。もう一度実行すると続きから再開します。")
    finally:
        restart_driver()
        conn.close()


if __name__ == "__main__":
    main()
