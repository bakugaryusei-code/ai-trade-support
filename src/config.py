"""プロジェクト全体の設定値。

仕様書v1.1と設計判断に基づく初期値。
運用しながら `config.py` だけ変更すれば挙動を調整できる設計。
"""
from __future__ import annotations

# ─────────────────────────────────────────
# スクリーニング条件
# ─────────────────────────────────────────
SCREENING_MARKET: str = "PRIME"
MIN_MARKET_CAP_YEN: int = 50_000_000_000
REQUIRE_PROFIT: bool = True

PRIMARY_CANDIDATES_LIMIT: int = 40
TIER_A_MAX_DETAILED_ANALYSIS: int = 5

# ─────────────────────────────────────────
# Claude APIモデル設定
# ─────────────────────────────────────────
MODEL_LIGHT: str = "claude-haiku-4-5-20251001"
MODEL_HEAVY: str = "claude-sonnet-4-6"

RUN_MARKET_OVERVIEW_HOURS: tuple[int, ...] = (8,)

# ─────────────────────────────────────────
# バッチ実行スケジュール（JST）
# ─────────────────────────────────────────
BATCH_HOURS_JST: tuple[int, ...] = (8, 12, 15)

# ─────────────────────────────────────────
# テクニカル指標のパラメータ
# ─────────────────────────────────────────
MA_SHORT_PERIOD: int = 5
MA_LONG_PERIOD: int = 25
RSI_PERIOD: int = 14

# ─────────────────────────────────────────
# データベース
# ─────────────────────────────────────────
DB_PATH: str = "data/trade.db"

# ─────────────────────────────────────────
# J-Quants APIエンドポイント
# ─────────────────────────────────────────
JQUANTS_BASE_URL: str = "https://api.jquants.com/v1"
