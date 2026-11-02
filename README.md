# japan-stock-cloud

日本株100銘柄の日足を GitHub Actions で毎日取得し（`prices.csv`）、
デイトレ・スイング用のインジケーター、自動売買ストラテジー（EA）、スクリーニング、バックテストで使う。

| ファイル | 内容 |
|---|---|
| `tradingview/JP_SwingDay_Indicator.pine` | TradingView インジケーター（テクニカル＋ファンダメンタルズ） |
| `tradingview/JP_SwingDay_Strategy.pine` | TradingView ストラテジー（EA）。バックテスト・Webhook 発注アラート |
| `mt5/JP_SwingDay.mq5` | MT5 インジケーター（東証銘柄のCFDなど） |
| `python/screen.py` | 毎日の終値でシグナル・スコア一覧を作る → `signals.csv` / `signals.md` |
| `python/backtest.py` | スイング戦略を全銘柄でバックテスト |
| `docs/strategy_spec.md` | **ルールの仕様**（3つの実装で共通） |
| `prices.csv` / `latest.csv` / `status.json` | 平日の夜に Actions が更新する株価データ（cron は 20:30 JST） |

ルールの考え方: **上昇トレンドの銘柄だけを、出来高を伴う高値更新（BO）か、25日線までの押しからの反転（PB）で買う。
損切りは ATR で決め、1トレードの損失を資金の1%に抑える。** 詳細は [docs/strategy_spec.md](docs/strategy_spec.md)。

---

## 1. TradingView

### インジケーター

1. TradingView でチャートを開き、下の「Pine エディタ」に `JP_SwingDay_Indicator.pine` の中身を貼り付ける
2. 「チャートに追加」
3. 日足ならスイング、分足ならデイトレの表示に自動で切り替わる

| 表示 | 日足 | 分足 |
|---|---|---|
| 移動平均 | 5・25・75日線、トレンド帯（25〜75日線） | 同じ本数の分足MA |
| シグナル | BO・PB（買い）、BD・RS（売り） | ORB・VW（VWAP反発） |
| その他 | 直近シグナルの損切り・利確ライン | VWAP±1σ、OR高値・安値、前日高値・安値・終値 |
| 情報パネル | トレンド・RSI・ATR%・出来高倍率・25日線乖離・地合い | ＋VWAP乖離・寄りギャップ・日足トレンド |
| ファンダ | PER・PBR・ROE・営業利益率・自己資本比率・売上／EPS成長率・FCF・判定・次回決算 | 同左 |

アラート: 「アラート作成」→ 条件で `JP-SD` を選び、個別のシグナル（BO など）か
「alert() 関数呼び出しのみ」（損切り・利確の値つきの詳細メッセージ）を選ぶ。

### ストラテジー（EA）

1. `JP_SwingDay_Strategy.pine` を同じように貼り付けて「チャートに追加」
2. 「ストラテジーテスター」タブで成績を確認（日足ならスイング、5分足などならデイ）
3. 設定（歯車）で資金・許容損失・セットアップの ON/OFF・フィルターを調整

自動売買につなぐとき:

1. 「アラート作成」→ 条件で `JP-EA` を選ぶ
2. 「メッセージ」欄を `{{strategy.order.alert_message}}` だけにする
3. 「通知」で Webhook URL に発注ブリッジ（自前のサーバー）を指定する

JSON の形式と受け手の注意点は [仕様書の8章](docs/strategy_spec.md#8-アラートwebhookの-json)。

注意:

- Webhook は TradingView の有料プランが必要。東証のリアルタイムデータも有料（未契約だと遅延データ）。
- 国内証券で API 発注できるのは、au カブコム証券（kabuステーション API）、立花証券（e支店 API）など。
  SBI・楽天には一般向けの発注 API がないので、アラートを見て手動で発注する使い方になる。
- 決算予定日のフィルターはリアルタイムでしか効かない（過去の予定日は取れないため）。

## 2. MT5

1. MT5 の「ファイル」→「データフォルダを開く」→ `MQL5/Indicators/` に `mt5/JP_SwingDay.mq5` を置く
2. MetaEditor で開いて「コンパイル」（F7）
3. ナビゲーターからチャートにドラッグ

- 日足ならスイング、日足未満ならデイ（VWAP・OR・ORB／VWAP反発）。
- 分足の時間帯判定は、サーバー時刻と日本時間の差を自動で計算する（夏時間の切り替え前後で過去の足がずれる場合は手動で指定）。
- CFD ブローカーの多くは実出来高がなく、ティック出来高で代用する（出来高倍率の意味が少し変わる）。
- ファンダメンタルズと地合いフィルターは MT5 版にはない。
- バッファ 6（買い）・7（売り）・17（セットアップ番号）を `iCustom` で読めば、EA から使える。

## 3. Python（スクリーニング・バックテスト）

標準ライブラリだけで動く（Python 3.9 以上）。

```bash
python python/screen.py                  # signals.csv / signals.md を更新
python python/backtest.py                # スイング（買いのみ）
python python/backtest.py --short        # 信用売りも含める
python python/backtest.py --trades trades.csv --stop-atr 1.5 --tp-r 3
python -m unittest discover -s python/tests
```

GitHub Actions は株価の更新のあとに `screen.py` を実行し、`signals.csv` と `signals.md` も保存する。
Actions の実行結果のページ（Summary）にも同じ表が出る。スクリーニングが失敗しても、株価の保存は止まらない。

## 検証結果の要約

`prices.csv` の100銘柄（2026-03〜2026-10、実質4か月）で、買いのみ・既定パラメーター:
152回、勝率31.6%、平均 +0.08R、PF 1.18（決済済みのみだと PF 1.05）。
**期間が短く、まだ優位性があるとは言えない。** 実際のお金で使う前に、TradingView のストラテジーテスターで長期間を確認すること。

---

このリポジトリの内容は売買ルールの検証・学習用で、特定の銘柄の売買を勧めるものではない。投資判断はご自身で。
