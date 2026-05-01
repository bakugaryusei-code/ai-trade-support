"""スクリーニング処理。

東証プライム市場の全銘柄から、以下の条件で候補を絞り込む：
  - 時価総額 ≥ 500億円（config.MIN_MARKET_CAP_YEN）
  - 直近決算が黒字（NP > 0、空なら FNP > 0）

レートリミットは JQuantsClient 側で自動的に守られるので、ここでは意識しない。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
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


def filter_by_scale_category(
    stocks: list[dict[str, Any]],
    categories: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    """ScaleCat ホワイトリストで銘柄を絞り込む。

    /equities/master のレスポンスに含まれる ScaleCat フィールドの値を
    ホワイトリスト方式で照合し、一致する銘柄のみを返す。

    主な用途：プライム市場約1,575社から TOPIX 500（Core30+Large70+Mid400）の
    493社に絞ることで、時価総額・業種分散の点で「絞り込み」が機能する母集団
    にする。

    Args:
        stocks: get_listed_info() の返値、または filter_by_market 後のリスト。
        categories: 通すべき ScaleCat 値のタプル/リスト。
                   表記揺れに注意（"TOPIX Mid400" 等の固定文字列で完全一致）。

    Returns:
        ScaleCat がホワイトリストに含まれる銘柄のみ。
    """
    allowed = set(categories)
    return [s for s in stocks if s.get("ScaleCat") in allowed]


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


def _build_close_map(quotes: list[dict[str, Any]]) -> dict[str, float]:
    """全銘柄単日株価リストから {正規化4桁コード: 終値} を構築。

    終値は AdjC（調整後終値）優先、無ければ C（終値）。
    レスポンスの Code が 5桁（末尾0付）の場合は normalize_code で4桁に揃える。
    """
    result: dict[str, float] = {}
    for q in quotes:
        code = q.get("Code", "")
        if not code:
            continue
        normalized = normalize_code(code)
        for field in ("AdjC", "C"):
            v = q.get(field)
            if v in (None, ""):
                continue
            try:
                result[normalized] = float(v)
                break
            except (ValueError, TypeError):
                continue
    return result


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
        latest_close: float | None = None,
        quote_from_date: "date | None" = None,
        quote_to_date: "date | None" = None,
    ) -> dict[str, Any]:
        """1銘柄の黒字フラグ・時価総額・直近終値を算出。

        Args:
            latest_close: 事前取得済みの終値。指定された場合は株価APIを呼ばず
                これを使う（一括取得 → per-banking 集計の高速パス）。
                None の場合は従来通り get_daily_quotes で取得（テスト・Freeプラン用）。
            quote_from_date / quote_to_date:
                latest_close=None の時のみ有効。株価取得の日付範囲。
                Free プランはデータ 12週間遅延のため、古めの日付を指定すると良い。

        API コール数：
            - latest_close 指定あり: 1コール（fins/summary のみ）
            - latest_close 指定なし: 2コール（fins/summary + bars/daily）
        """
        normalized = normalize_code(code)
        summary = self._client.get_financial_summary(normalized)

        if latest_close is None:
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

    def fetch_latest_close_map(
        self,
        max_attempts: int = 7,
    ) -> dict[str, float]:
        """直近営業日まで遡り、全上場銘柄の終値マップを一括取得。

        /equities/bars/daily?date=YYYY-MM-DD は date 単独指定で全銘柄分を返す
        （pagination 込みでも数コール）。指定日が休場・未配信なら前日に遡る。

        Args:
            max_attempts: 最大何日前まで遡るか（既定7日 ≒ 連休跨ぎを許容）。

        Returns:
            {正規化4桁コード: 終値} の辞書。終値は AdjC 優先、無ければ C。
            データが見つからない場合は空辞書。
        """
        today = date.today()
        for delta in range(max_attempts):
            target = today - timedelta(days=delta)
            try:
                quotes = self._client.get_daily_quotes_by_date(target)
            except Exception as e:
                logger.warning(f"全銘柄株価取得失敗 ({target}): {e}")
                continue
            if not quotes:
                continue
            logger.info(
                f"終値マップ: {target} のデータ {len(quotes)}件を採用"
            )
            return _build_close_map(quotes)
        logger.warning(
            f"過去{max_attempts}日に営業日データが見つかりませんでした"
        )
        return {}

    def run(
        self,
        market: str = SCREENING_MARKET,
        min_market_cap_yen: int = MIN_MARKET_CAP_YEN,
        require_profit: bool = REQUIRE_PROFIT,
        scale_categories: tuple[str, ...] | list[str] | None = None,
        limit: int | None = None,
        quote_from_date: date | None = None,
        quote_to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """スクリーニングを実行して候補銘柄リストを返す。

        Args:
            market: 対象市場（"PRIME" / "STANDARD" / "GROWTH" / "ALL"）。
            min_market_cap_yen: 時価総額の下限（円）。
            require_profit: True なら黒字必須。
            scale_categories: ScaleCat ホワイトリスト（例: ("TOPIX Core30", ...)）。
                None なら ScaleCat フィルタなし（市場フィルタのみ）。
                指定すると、母集団を TOPIX 100/500 などのインデックス構成銘柄に
                絞った上でスクリーニングする。
            limit: 件数制限（テスト用）。先頭N件にスライス。
            quote_from_date / quote_to_date: 株価取得の日付範囲。
                **指定すると per-code モードで動作**（テスト・Free プラン互換）。
                両方とも None なら **bulk モード**（全銘柄株価を1コールで一括取得）。

        Returns:
            条件を満たした銘柄の情報（名称・時価総額・純利益など）。

        高速パスについて:
            quote_from_date と quote_to_date がどちらも None の場合、株価は
            fetch_latest_close_map() で全銘柄分を一括取得し、各銘柄の評価では
            fins/summary 1コールのみ行う。これにより TOPIX 500（493社）でも
            約500コール = 約9分（Light プラン60/分）で完了する。

            日付範囲が指定された場合は従来の per-code モードとなり、各銘柄で
            fins/summary + bars/daily の2コールを発行する（テスト互換用）。
        """
        target_stocks = self.get_candidates_by_market(market)
        total_market = len(target_stocks)
        logger.info(f"{market}市場の銘柄数: {total_market}")

        if scale_categories:
            target_stocks = filter_by_scale_category(target_stocks, scale_categories)
            logger.info(
                f"ScaleCat絞り込み後: {len(target_stocks)}社"
                f"（フィルタ: {tuple(scale_categories)}）"
            )

        if limit is not None:
            target_stocks = target_stocks[:limit]
            logger.info(f"件数制限: 先頭{limit}件のみ処理")

        # bulk モード判定：日付範囲が両方とも未指定なら一括取得を試みる。
        use_bulk_quotes = quote_from_date is None and quote_to_date is None
        close_map: dict[str, float] = {}
        if use_bulk_quotes:
            logger.info("株価を bulk モードで一括取得中（/equities/bars/daily?date=...）")
            close_map = self.fetch_latest_close_map()
            if not close_map:
                # 7日遡って空＝API障害かデータ未配信。明示的に失敗させる。
                raise RuntimeError(
                    "全銘柄株価マップが空でした。bulk モードでスクリーニング不能です。"
                    "（休場連続・API障害・date 仕様変更の可能性）"
                )

        candidates: list[dict[str, Any]] = []
        for i, stock in enumerate(target_stocks, 1):
            code = stock["Code"]
            name = stock.get("CoName", "")
            normalized = normalize_code(code)
            pre_close = close_map.get(normalized) if use_bulk_quotes else None

            try:
                result = self.evaluate_stock(
                    code,
                    latest_close=pre_close,
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
