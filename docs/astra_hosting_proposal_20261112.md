# 受信先の準備案（未契約・未配備）

本人回答：既存クラウド・独自ドメインなし。2026-11-12確認。

## 選定候補

AWS LightsailのLinux/Unix・パブリックIPv4・1GBメモリ（東京リージョン候補）。基本料金は月7米ドル、40GB SSD。受信・照合・通知待ちの小規模サービス用候補であり、100銘柄のバックテストをこの小型機で同時実行しない。負荷試験は未実施。

同梱範囲外の転送、スナップショット、税等は別料金になり得る。無料キャンペーンは継続費用の計算に入れない。停止だけでは課金停止にならないため、不要なインスタンス等の削除手順も運用書に含める。削除は本人確認後に行う。請求アラートは費用の強制上限ではない。

独自ドメインは現時点で購入しない。Let's EncryptはIPアドレス証明書を一般提供しており、有効期間160時間の短期証明書を利用できる。Certbot 5.4以上のwebroot方式と自動更新・反映の試験が必要。TradingViewからIPアドレスURLへの実送信は未確認なので、「ドメイン不要で動作確認済み」と扱わない。

## 契約前に確定する構成

| 項目 | 方針 | 未確認事項 |
|---|---|---|
| 通信 | 固定IPv4、HTTPS 443 | TradingViewの実接続と実証明書チェーン |
| 受信 | 永続保存後に即応答、照合は別処理 | 本番用HTTPサーバー・負荷制限・監視 |
| 認証 | 証明書チェーンと送信元識別を確認 | 本人のアラートとの結び付け |
| Sheet | 読み取り専用認証、秘密は安全な実行環境へ | 常時更新できる認証設定 |
| 永続化 | SQLite資金予約・通知待ち・受信記録 | バックアップ、復旧、容量上限 |
| 通知 | 既存の配信抽象へ本人向け配信を接続 | 実配信手段とiPhone到達 |

今回の `webhook_receiver.py` は127.0.0.1限定の試験用であり、そのまま公開しない。新サーバーを契約しても、板・イベント・保有履歴・正式バックテストの不足が自動的に解消するわけではない。

## 次の実施順序

1. 今回のコードを開発ブランチに反映し、GitHub CIを確認する。
2. 公開用受信構成、更新・復旧・認証設定をコード化して隔離試験する。
3. 実際に用意するプランと追加料金を提示し、本人に契約・課金を確認する。現時点では申込を求めない。
4. 本人によるAWSアカウント作成・必要な認証を完了後、承認された試験環境に配備する。
5. 本人のTradingViewアラートで実接続を確認する。GOは無効のまま、受信・重複・失効・停止・復旧を検証する。
6. 板・イベント・通知を接続して実機試験し、その後に本番移行の可否を判断する。

認証情報をチャットへ貼らない。新規アカウント作成、契約、公開アクセス設定、課金は未実施。

## 確認した公式資料

- AWS料金 https://aws.amazon.com/lightsail/pricing/
- バンドル https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html
- 課金 https://docs.aws.amazon.com/en_en/lightsail/latest/userguide/amazon-lightsail-frequently-asked-questions-faq-billing-and-account-management.html
- IP証明書 https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability
- Certbot https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html
- TradingView認証 https://www.tradingview.com/support/solutions/43000680459-webhook-authentication/
