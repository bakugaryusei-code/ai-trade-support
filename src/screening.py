"""スクリーニング処理。

東証プライム市場の全銘柄から、以下の条件で候補を絞り込む：
  - 時価総額 ≥ 500億円（config.MIN_MARKET_CAP_YEN）
  - 直近決算が黒字（NP > 0、空なら FNP > 0）

レートリミットは JQuantsClient 側で自動的に守られるので、ここでは意識しない。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.config import (
    MIN_MARKET_CAP_YEN,
    REQUIRE_PROFIT,
    SCREENING_MARKET,
)
from src.jquants_client import JQuantsClient

logger = logging.getLogger(__name__)

# V2 の Mkt フィールドの値（プライム以外は実データで確認必要）
_MARKET_CODE_MAP = {
    "PRIME": "0111",
    "STANDARD": "0112",
    "GROWTH": "0113",
}


def normalize_code(code: str) -> str:
    """J-Quants V2の銘柄コードを4桁に正規化。

    `/equities/master` は5桁（末尾0付き、例: `"72030"`）を返すが、
    他のエンドポイント（/fins/summary・/equities/bars/daily）は
    4桁（例: `"7203"`）を期待するため、末尾の `0` を除去する。
    """
    if len(code) == 5 and code.endswith("0"):
        return code[:-1]
    return code


def filter_by_market(
    stocks: list[dict[str, Any]],
    market: str = "PRIME",
) -> list[dict[str, Any]]:
    """指定市場の銘柄だけに絞り込む。"""
    if market == "ALL":
        return stocks
    target_code = _MARKET_CODE_MAP.get(market)
    if not target_code:
        raise ValueError(f"未知のmarket指定: {market}")
    return [s for s in stocks if s.get("Mkt") == target_code]


def is_profitable(financial_summary: list[dict[str, Any]]) -> bool:
    """直近の財務サマリーから黒字かどうかを判定。

    NP（実績純利益）を優先、空なら FNP（予想純利益）を見る。
    どちらも取得できなければ False。
    """
    if not financial_summary:
        return False
    latest = financial_summary[0]
    for field in ("NP", "FNP"):
        value = latest.get(field)
        if value in (None, "", 0):
            continue
        try:
            return float(value) > 0
        except (ValueError, TypeError):
            continue
    return False


def calculate_market_cap(
    financial_summary: list[dict[str, Any]],
    latest_close_price: float | None,
) -> float | None:
    """時価総額 = 期末発行済株式数（ShOutFY）× 最新終値。

    どちらかが取れない時は None。
    """
    if not financial_summary or latest_close_price is None:
        return None
    shares = financial_summary[0].get("ShOutFY")
    if not shares:
        return None
    try:
        return float(shares) * latest_close_price
    except (ValueError, TypeError):
        return None


def _extract_latest_close(quotes: list[dict[str, Any]]) -> float | None:
    """日次株価レスポンスから直近の調整後終値（AdjC）を取り出す。"""
    if not quotes:
        return None
    latest = quotes[-1]
    for field in ("AdjC", "C"):
        value = latest.get(field)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (ValueError, TypeError):
            continue
    return None


class Screener:
    """スクリーニング全体のオーケストレーター。"""

    def __init__(self, client: JQuantsClient | None = None) -> None:
        self._client = client or JQuantsClient()

    def get_candidates_by_market(
        self,
        market: str = SCREENING_MARKET,
    ) -> list[dict[str, Any]]:
        """全上場銘柄から指定市場のものを抽出（API 1コール）。"""
        all_stocks = self._client.get_listed_info()
        return filter_by_market(all_stocks, market=market)

    def evaluate_stock(
        self,
        code: str,
        quote_from_date: "date | None" = None,
        quote_to_date: "date | None" = None,
    ) -> dict[str, Any]:
        """1銘柄の黒字フラグ・時価総額・直近終値を算出（API 2コール）。

        Args:
            quote_from_date / quote_to_date:
                株価取得の日付範囲。省略時はクライアントデフォルト（直近30日）。
                Free プランはデータ 12週間遅延のため、古めの日付を指定すると良い。
        """
        normalized = normalize_code(code)
        summary = self._client.get_financial_summary(normalized)
        quotes = self._client.get_daily_quotes(
            normalized,
            from_date=quote_from_date,
            to_date=quote_to_date,
        )
        latest_close = _extract_latest_close(quotes)

        return {
            "code": normalized,
            "profitable": is_profitable(summary),
            "latest_close": latest_close,
            "market_cap": calculate_market_cap(summary, latest_close),
            "profit_value": summary[0].get("NP") if summary else None,
            "disclosed_date": summary[0].get("DiscDate") if summary else None,
        }

    def run(
        self,
        market: str = SCREENING_MARKET,
        min_market_cap_yen: int = MIN_MARKET_CAP_YEN,
        require_profit: bool = REQUIRE_PROFIT,
        limit: int | None = None,
        quote_from_date: date | None = None,
        quote_to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """スクリーニングを実行して候補銘柄リストを返す。

        Args:
            market: 対象市場（"PRIME" / "STANDARD" / "GROWTH" / "ALL"）。
            min_market_cap_yen: 時価総額の下限（円）。
            require_profit: True なら黒字必須。
            limit: テスト用の件数制限。Free プランでは必ず指定推奨。
            quote_from_date / quote_to_date: 株価取得の日付範囲。
                Free プランは最新12週間のデータが取れないので、古い日付を指定推奨。

        Returns:
            条件を満たした銘柄の情報（名称・時価総額・純利益など）。
        """
        target_stocks = self.get_candidates_by_market(market)
        total = len(target_stocks)
        logger.info(f"{market}市場の銘柄数: {total}")

        if limit is not None:
            target_stocks = target_stocks[:limit]
            logger.info(f"テスト制限: 先頭{limit}件のみ処理")

        candidates: list[dict[str, Any]] = []
        for i, stock in enumerate(target_stocks, 1):
            code = stock["Code"]
            name = stock.get("CoName", "")
            try:
                result = self.evaluate_stock(
                    code,
                    quote_from_date=quote_from_date,
                    quote_to_date=quote_to_date,
                )
            except Exception as e:
                logger.warning(f"[{i}/{len(target_stocks)}] {code} {name}: 評価失敗 ({e})")
                continue

            if require_profit and not result["profitable"]:
                logger.debug(f"[{i}/{len(target_stocks)}] {code} {name}: 赤字でスキップ")
                continue

            if result["market_cap"] is None:
                logger.debug(f"[{i}/{len(target_stocks)}] {code} {name}: 時価総額計算不可でスキップ")
                continue

            if result["market_cap"] < min_market_cap_yen:
                continue

            result["name"] = name
            candidates.append(result)
            logger.info(
                f"[{i}/{len(target_stocks)}] {code} {name}: "
                f"時価総額={result['market_cap']/1e8:.0f}億円 / 候補"
            )

        return candidates
