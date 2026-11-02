"""日本株（デイ／スイング）向けテクニカル指標・シグナル・バックテスト。

標準ライブラリのみで動作する（GitHub Actions で追加インストール不要）。
指標の定義は TradingView の ta.* 関数と揃えてある（ATR・RSI は Wilder 平滑）。
"""

__all__ = ["indicators", "rules", "backtest"]
