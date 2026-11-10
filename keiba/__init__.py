"""keiba: 回収率100%超を目指す競馬予想フレームワーク.

パイプライン:
    data (schema) -> features (多ファクター, リーク防止) -> model (条件付きロジット)
    -> Benter型市場融合 -> betting (期待値 & ケリー) -> backtest (ウォークフォワード)
"""
from .features import build_features, FEATURE_GROUPS
from .model import ConditionalLogit, TwoStageModel
from .betting import Strategy, select_bets, optimize_strategy
from .backtest import walk_forward, summarize

__all__ = [
    "build_features", "FEATURE_GROUPS", "ConditionalLogit", "TwoStageModel",
    "Strategy", "select_bets", "optimize_strategy", "walk_forward", "summarize",
]
