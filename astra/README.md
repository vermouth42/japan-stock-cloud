# Astra隔離開発版

開発版・本番未配備。mainのデータ取得、本番シート、既存自動化は変更していません。
2026-11-10の続行では、実データ取得と検証用シートへの4,500行連携まで確認しました。

- 監査：`docs/astra_audit_20261110.md`
- 実装計画：`docs/astra_implementation_plan.md`
- 候補通知：`tradingview/CloudLite_Candidate_v6.pine`
- 技術研究：`tradingview/CloudLite_Research_v6.pine`
- 最終安全条件の検証関数：`astra/preflight.py`
- 確定日の日足取得：`astra/market_data.py`（実取得100銘柄成功）
- 既存シート用45日変換：`astra/sheet_rows.py`
- 読取専用の同期監査：`astra/sync_audit.py`（一致していても古い日付なら停止）
- 統合判定のリハーサル：`astra/readiness.py`（生データを再監査し、時間帯・売買条件を照合。本番GOは常に禁止）
- マクロの公表時点結合：`astra/macro.py`（接続元は未実装）
- 手動実行の隔離検証：`.github/workflows/astra-validate.yml`（GitHubでは未実行）
- 続行結果と運用手順：`docs/astra_progress_20261110.md`、`docs/astra_runbook.md`

ローカル検証（Python標準ライブラリのみ）：

```sh
python -m unittest discover -s astra/tests -v
```

実取得は明示実行時のみ：`python -m astra.market_data --out astra-stage`。
100銘柄のいずれかが欠落、最新確定日なし、45日未満なら出力しません。
JPXカレンダーは2026・2027年のみ対応し、範囲外は停止します。
Yahoo日足は現行互換データであり、TSEリアルタイム・分足・板ではありません。
株式分割等の調整方針を未検証のため、このCSVを正式バックテスト用と見なしません。

Pine 2本は2026-11-10にTradingViewで保存・コンパイルを確認しました。候補版は1分/5分足の表示と初期停止、研究版は日足で模擬取引の生成を確認。実通知は未確認です。研究版の結果はA/A+等を再現していないので実戦成績ではありません。候補通知の設定を変えたら既存アラートの再作成が必要です。

同期監査は次のコマンドで、GitHubのCSV・statusと、既存シートA3:R4502をUNFORMATTED_VALUEで取得したJSON配列を照合します。行順は不問、100銘柄各45行と全18列の内容一致を確認します。正常時も `go_allowed` は常にfalseです。本番へ書き込みません。終了コード2は不一致・鮮度不足等による停止です。

```sh
python -m astra.sync_audit --status status.json --prices prices.csv --sheet-rows /path/to/sheet_rows.json
```

続行結果は `docs/astra_progress_20261111.md` を参照してください。

`preflight.evaluate()` の戻り値は入力データに基づくオフライン判定です。入力の真偽や最新性を取得する接続はありません。イベント営業日・JPX営業日・SBI確認・ランク等を信頼できる情報源で検証する呼出し側が未完成のため、この関数をそのまま本番GO通知に使用しないでください。予定損失はギャップ等で超過し得ます。

統合リハーサル（2026-11-11追加）：

```sh
python -m astra.readiness --status status.json --prices prices.csv --sheet-rows /path/to/sheet_rows.json --manual astra/examples/manual_unverified.json
```

手動入力は `Snapshot` の銘柄・ランク・SBI確認・イベント・価格・資金等だけです。時刻はタイムゾーン付きISO8601。監視件数・データ確認済みフラグ・市場日は入力できず、生データから再判定します。入力例は全項目未確認として停止します。パスワード・APIキーは入力しません。

`offline_checks_passed` は提供された入力に対する検証結果で、実情報の証明ではありません。`go_allowed` は常にfalse、CLI終了コードは常に2です。実運用のGOゲートや注文処理へ直接接続しないでください。仮の新規候補時間帯は候補Pineと同じ09:05以上11:25未満・12:30以上14:55未満（JPX営業日、JST）です。

取得CLIは進捗を標準エラーに表示し、1銘柄が失敗した時点で未着手の取得を取消します。開始済みの最大4リクエストは既存のタイムアウト／再試行まで待つため即時終了ではありません。成功前に出力を書きません。これ自体はGitHub定期実行や本番同期の復旧を意味しません。

最新の続行結果：`docs/astra_resume_20261111.md`。
