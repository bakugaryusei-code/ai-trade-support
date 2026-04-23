"""AI分析のオーケストレーション（Step 4cの本体）。

3段階のClaude分析をまとめる：
  1. run_market_overview()  - Sonnet + Web検索、朝のみ（コスト最適化）
  2. classify_tiers()        - Haikuで候補銘柄をTier A/B/Cに分類
  3. analyze_stock()         - Sonnet + Web検索で個別銘柄の詳細分析
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.claude_client import ClaudeClient

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 返却用データクラス
# ─────────────────────────────────────────


@dataclass
class MarketOverview:
    """Step 0 の結果。"""

    summary: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)


@dataclass
class TierClassification:
    """Step 2 の各銘柄に対する結果。"""

    code: str
    name: str
    tier: str  # "A" / "B" / "C"
    reason: str


@dataclass
class StockAnalysis:
    """Step 3 の結果。"""

    code: str
    name: str
    recommendation: str  # "buy" / "sell" / "hold"
    reasoning: str
    risks: list[str] = field(default_factory=list)
    raw_text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────
# メインクラス
# ─────────────────────────────────────────


class AIAnalyzer:
    """Claude を使った3段階分析のオーケストレーター。"""

    def __init__(self, claude_client: ClaudeClient | None = None) -> None:
        self._client = claude_client or ClaudeClient()

    # ─── Step 0: 市場概況 ───────────────

    def run_market_overview(self) -> MarketOverview:
        """日経平均・TOPIX・セクター動向・マクロイベントを Web検索で要約。

        朝のルーティンで1回だけ実行する想定（1日3バッチのうち朝のみ）。
        コスト: 約 $0.05。
        """
        prompt = (
            "あなたは日本株のスイングトレード向け市場アナリストです。\n"
            "今日の日本株式市場について、Web検索で最新情報を調べ、以下の観点で要約してください：\n\n"
            "1. **主要指数**: 日経平均・TOPIX・NT倍率の値と前日比\n"
            "2. **上昇セクター / 下落セクター**: 具体的に業種名と背景\n"
            "3. **注目材料**: 企業決算・政策・地政学リスクなど、翌日以降の相場に影響しそうな3点\n"
            "4. **明日の注目ポイント**: スイングトレーダーが留意すべき1〜2点\n\n"
            "400〜600字程度。数値と事実は最新の実データに基づくこと。"
        )
        result = self._client.ask_with_web_search(
            prompt,
            heavy=True,
            max_searches=3,
            max_tokens=2048,
        )
        return MarketOverview(
            summary=result["text"],
            citations=result["citations"],
            search_queries=result["search_queries"],
        )

    # ─── Step 2: Tier分類（Haikuバッチ） ───────────────

    def classify_tiers(
        self,
        candidates: list[dict[str, Any]],
        market_overview: MarketOverview | None = None,
    ) -> list[TierClassification]:
        """候補銘柄を Tier A/B/C に一括分類（Haiku、Web検索なし）。

        Args:
            candidates: screening.Screener.run() が返すリスト。
                      必要キー: code / name / market_cap / profit_value
            market_overview: あれば Step 0 の結果をコンテキストに含める。

        Returns:
            各銘柄の分類結果。
        """
        if not candidates:
            return []

        # 候補銘柄を compact に整形
        candidate_lines = []
        for c in candidates:
            mc_oku = (c.get("market_cap") or 0) / 1e8
            candidate_lines.append(
                f"- {c.get('code')} {c.get('name','')} "
                f"（時価総額 {mc_oku:.0f}億円 / 純利益 {c.get('profit_value', 'N/A')}）"
            )
        candidates_text = "\n".join(candidate_lines)

        overview_text = market_overview.summary if market_overview else "（市場概況情報なし）"

        prompt = (
            "あなたは日本株スイングトレードのスクリーナーです。\n"
            "市場概況を踏まえて、候補銘柄を以下の3段階に分類してください：\n\n"
            "- **Tier A**: 今日、詳細分析する価値が高い銘柄（最大5件、ゼロでも可）\n"
            "- **Tier B**: 中立、保留\n"
            "- **Tier C**: 現時点では候補から外すべき\n\n"
            "--- 市場概況 ---\n"
            f"{overview_text}\n\n"
            "--- 候補銘柄 ---\n"
            f"{candidates_text}\n\n"
            "返答は **JSON配列のみ**（前後の説明不要）で：\n"
            '[{"code": "1301", "tier": "A", "reason": "30字以内の理由"}, ...]\n'
            "すべての候補銘柄に対して必ず1要素返すこと。"
        )

        reply = self._client.ask(
            prompt,
            heavy=False,  # Haiku
            max_tokens=2048,
        )

        tier_data = self._extract_json_array(reply)
        if tier_data is None:
            logger.warning("Tier分類の返答からJSONが抽出できず。空リストを返す。")
            return []

        # code → name の辞書（返答に name が無い場合補う）
        code_to_name = {c.get("code"): c.get("name", "") for c in candidates}

        results: list[TierClassification] = []
        for item in tier_data:
            code = item.get("code", "")
            results.append(
                TierClassification(
                    code=code,
                    name=code_to_name.get(code, ""),
                    tier=item.get("tier", "C"),
                    reason=item.get("reason", ""),
                )
            )
        return results

    # ─── Step 3: 個別銘柄の詳細分析 ───────────────

    def analyze_stock(
        self,
        stock: dict[str, Any],
        market_overview: MarketOverview | None = None,
    ) -> StockAnalysis:
        """Sonnet + Web検索で1銘柄を詳細分析し、買い/売り/様子見の推奨を出す。"""
        mc_oku = (stock.get("market_cap") or 0) / 1e8
        overview_text = market_overview.summary if market_overview else "（市場概況情報なし）"

        prompt = (
            "あなたは日本株のスイングトレード向けアナリストです。\n"
            "以下の銘柄について、Web検索で直近のニュース・IR・決算情報を収集し、"
            "投資判断を**明確に**推奨してください。\n\n"
            "--- 分析対象 ---\n"
            f"銘柄コード: {stock.get('code')}\n"
            f"会社名:     {stock.get('name', '')}\n"
            f"直近終値:   {stock.get('latest_close', 'N/A')}\n"
            f"時価総額:   {mc_oku:.0f}億円\n"
            f"純利益:     {stock.get('profit_value', 'N/A')}\n"
            f"開示日:     {stock.get('disclosed_date', 'N/A')}\n\n"
            "--- 市場概況 ---\n"
            f"{overview_text}\n\n"
            "--- 出力形式（必ずこの構造で返答） ---\n"
            "## 推奨\n"
            "[buy / sell / hold のいずれか1語]\n\n"
            "## 根拠\n"
            "- 箇条書きで3〜5点、具体的な事実・数値・ニュースを引用\n\n"
            "## リスク要因\n"
            "- 箇条書きで2〜3点\n"
        )

        result = self._client.ask_with_web_search(
            prompt,
            heavy=True,
            max_searches=3,
            max_tokens=2048,
        )
        raw = result["text"]

        return StockAnalysis(
            code=stock.get("code", ""),
            name=stock.get("name", ""),
            recommendation=self._extract_recommendation(raw),
            reasoning=self._extract_section(raw, "根拠"),
            risks=self._extract_bullets(self._extract_section(raw, "リスク要因")),
            raw_text=raw,
            citations=result["citations"],
        )

    # ─── 内部ヘルパー ──────────────────

    @staticmethod
    def _extract_json_array(text: str) -> list[dict[str, Any]] | None:
        """テキスト中から最初のJSON配列を抽出してパース。"""
        # コードブロック内を優先
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        candidate = m.group(1) if m else None
        if not candidate:
            # 素のJSON配列を探す
            m = re.search(r"\[.*\]", text, re.DOTALL)
            candidate = m.group(0) if m else None
        if not candidate:
            return None
        try:
            data = json.loads(candidate)
            return data if isinstance(data, list) else None
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失敗: {e}")
            return None

    @staticmethod
    def _extract_recommendation(text: str) -> str:
        """`## 推奨` 以下から buy/sell/hold を抽出。"""
        m = re.search(r"##\s*推奨\s*\n+\s*(\w+)", text)
        if m:
            token = m.group(1).lower()
            for key in ("buy", "sell", "hold"):
                if key in token:
                    return key
        # フォールバック：テキスト全体から最初に出る語を採用
        for key in ("buy", "sell", "hold"):
            if re.search(rf"\b{key}\b", text, re.IGNORECASE):
                return key
        return "hold"

    @staticmethod
    def _extract_section(text: str, heading: str) -> str:
        """`## {heading}` 以降、次の `##` 手前までを抽出。"""
        pattern = rf"##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_bullets(text: str) -> list[str]:
        """先頭が `-` または `*` の行を抽出して配列で返す。"""
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("-", "*", "・")):
                lines.append(stripped.lstrip("-*・ ").strip())
        return lines
