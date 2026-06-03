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
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from src.claude_client import ClaudeClient

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 日付ヘルパー（プロンプトへの注入用）
# ─────────────────────────────────────────
_JST = timezone(timedelta(hours=9))
_WEEKDAY_JP = ("月", "火", "水", "木", "金", "土", "日")


def _today_jst_str() -> str:
    """JST の今日を「YYYY-MM-DD（曜日）」形式で返す。

    Claude の training cutoff から推測した日付が実日付とズレるのを防ぐため、
    すべての分析プロンプトに今日の日付を明示する。
    """
    now = datetime.now(_JST)
    return f"{now.year}-{now.month:02d}-{now.day:02d}（{_WEEKDAY_JP[now.weekday()]}曜日）"


# ─────────────────────────────────────────
# 自己矛盾検出（## 推奨 と ## 根拠 の不整合を補正）
# ─────────────────────────────────────────
# Claude が「## 推奨: buy」と出しつつ ## 根拠 で「強制HOLD降格」と明記する
# 自己矛盾ケースを検出するキーワード（小文字化テキストに対してマッチ）。
_HOLD_OVERRIDE_KEYWORDS = (
    # 降格・強制系
    "強制hold",
    "強制ホールド",
    "強制 hold",
    "hold に降格",
    "holdに降格",
    "hold へ降格",
    "holdへ降格",
    "holdへ強制",
    "buy → hold",
    "buy->hold",
    # 制約抵触系
    "制約1に抵触",
    "制約1抵触",
    "制約1違反",
    "制約2に抵触",
    "制約2抵触",
    "制約2違反",
    "制約3に抵触",
    "制約3抵触",
    "制約3違反",
    "制約4に抵触",
    "制約4抵触",
    "制約4違反",
    "決算ギャンブル",
    # ─── 2026-06-03 追加: 「結論は HOLD」を別の言い回しで書くケース ───
    # （大和ハウス誤BUY の根拠に実際に現れた表現を網羅）
    "holdとする",
    "holdと判断",
    "holdが妥当",
    "holdが適切",
    "holdを推奨",
    "様子見とする",
    "様子見が妥当",
    "buy根拠を形成しない",
    "買い根拠を形成しない",
    "buyの根拠を形成しない",
    "buyに満たない",
    "buyには満たない",
    "buyを見送",
    "買いを見送",
    "buyではなく",
    "買いではなく",
    "buyせず",
    "buyしない",
    "両方肯定的な場合のみbuy",  # 「…のみBUYに満たない」文脈の前半を拾う
)


def _detect_self_contradiction(reasoning: str, risks: list[str]) -> str | None:
    """根拠/リスク本文から HOLD 降格を示唆するキーワードを検出し、最初のマッチを返す。

    スペース・全半角矢印のゆらぎを吸収するため、テキストとキーワードを
    両方とも空白除去 + 小文字化してから照合する。

    Returns:
        マッチしたキーワード（matched signal）、なければ None。
    """
    parts: list[str] = [reasoning] if isinstance(reasoning, str) else list(reasoning or [])
    if risks:
        parts.extend(str(r) for r in risks)
    combined = " ".join(parts).lower().replace(" ", "")
    for kw in _HOLD_OVERRIDE_KEYWORDS:
        if kw.lower().replace(" ", "") in combined:
            return kw
    return None


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

        朝のバッチで1回だけ実行する想定（1日1回運用）。
        コスト: 約 $0.05。
        """
        today = _today_jst_str()
        prompt = (
            "あなたは日本株のスイングトレード向け市場アナリストです。\n\n"
            "--- 現在日時（必ずこの日付を起点に判断すること） ---\n"
            f"本日: **{today}**（日本標準時 JST）\n"
            "※ この日付を冒頭で必ず明記し、Web検索結果が古い日付の場合は\n"
            "  「最新の」「直近の」と再検索して鮮度を担保すること。\n\n"
            "--- タスク ---\n"
            "上記の本日時点における日本株式市場について、Web検索で最新情報を調べ、\n"
            "以下の観点で要約してください：\n\n"
            "1. **主要指数**: 日経平均・TOPIX・NT倍率の値と前日比\n"
            "2. **上昇セクター / 下落セクター**: 具体的に業種名と背景\n"
            "3. **注目材料**: 企業決算・政策・地政学リスクなど、翌日以降の相場に影響しそうな3点\n"
            "4. **明日の注目ポイント**: スイングトレーダーが留意すべき1〜2点\n\n"
            f"400〜600字程度。日付は冒頭で「{today}」と明記すること。\n"
            "数値と事実は最新の実データに基づくこと。"
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

        # Haiku 4.5 の上限は 64,000 トークン（公式仕様）。
        # TOPIX 500 を全件絞り込んだ後の候補（最大493件）をすべて分類するため、
        # 1要素あたり ~70トークン × 500件 ≈ 35,000トークンの出力余地が必要。
        # 旧値 2,048 では 404件入力時に途中で切れて JSON 抽出失敗していた。
        reply = self._client.ask(
            prompt,
            heavy=False,  # Haiku
            max_tokens=32000,
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
        holdings_context: str | None = None,
    ) -> StockAnalysis:
        """Sonnet + Web検索で1銘柄を詳細分析し、買い/売り/様子見の推奨を出す。

        仕様書 v1.1 のユーザー前提（スイング・初心者・堅実）に基づく
        4つの制約条件をプロンプトに明示する。

        ※ 価格・予算による BUY/HOLD の振り分けは行わない（純粋な投資判断）。
          単価が予算を超えるかどうかは表示側（app.py）で別軸マーカーとして扱う。

        Args:
            stock: 候補銘柄の dict。少なくとも code / name / latest_close /
                market_cap / profit_value / disclosed_date を含むこと。
            market_overview: あれば各分析プロンプトに含める市場概況。
            holdings_context: 既保有銘柄の概要文字列（分散リスク判断用）。
                None または空文字列のときは「現在保有なし」として扱う。
        """
        mc_oku = (stock.get("market_cap") or 0) / 1e8
        overview_text = market_overview.summary if market_overview else "（市場概況情報なし）"
        holdings_text = holdings_context if (holdings_context or "").strip() else "（現在保有なし）"
        latest_close = stock.get("latest_close")

        today = _today_jst_str()
        prompt = (
            "あなたは日本株のスイングトレード向けアナリストです。\n"
            "以下の銘柄について、Web検索で直近のニュース・IR・決算情報を収集し、"
            "投資判断を**明確に**推奨してください。\n\n"

            "--- 現在日時（必ずこの日付を起点に判断すること） ---\n"
            f"本日: **{today}**（日本標準時 JST）\n"
            "※ Claude の事前学習日付ではなく、上記日付を真の「本日」として扱うこと。\n"
            "※ 「±3営業日」等の日付計算は必ず上記日付を起点に行うこと。\n\n"

            "--- ユーザー前提（必ず遵守） ---\n"
            "・初期種銭は **10万円**（堅実運用できれば段階的に増資する方針）\n"
            "・SBI証券のS株（単元未満株、1株から購入可）\n"
            "・スイングトレード（数日〜数週間保有）の **初心者**\n"
            "・短期で大きく稼ぐより **負けないこと** を優先する設計\n"
            "・※ 株価が現在予算で買えるか否かは投資判断に **影響させない**。\n"
            "  予算判定は表示側で別マーカーとして扱うため、純粋な投資メリットで\n"
            "  BUY / SELL / HOLD を出すこと。\n\n"

            "--- 投資判断の制約条件（必ず守ること、違反は無効判断扱い） ---\n"
            "**制約1: 決算前後の BUY 禁止【最重要】**\n"
            "   Web検索で **次回** 決算発表予定日を必ず確認し、本日から ±3営業日\n"
            "   以内に発表予定なら BUY を **絶対に出さず**、必ず **HOLD** とすること。\n"
            "   次回決算日が分からない場合も慎重側に倒し、HOLD とする。\n"
            "   ※ ## 根拠 に「強制HOLD」「制約1抵触」「決算前のため HOLD」等を\n"
            "     書いた場合、## 推奨 は必ず HOLD とすること（自己矛盾は絶対禁止）。\n\n"
            "**制約2: 短期イベントドリブン BUY の禁止**\n"
            "   以下を **主要根拠** とした BUY は出さない（補助的な言及はOK）:\n"
            "   - 「決算サプライズ期待」「ギャップアップ狙い」\n"
            "   - 「材料出尽くし反発期待」「催促相場期待」\n"
            "   テクニカル + ファンダの **両方** が肯定的な場合のみ BUY、\n"
            "   片方だけなら HOLD とする。\n\n"
            "**制約3: リスク要因の重み付け**\n"
            "   - リスク欄に「高バリュエーション」「PER過熱」「テクニカル調整懸念」の\n"
            "     **2つ以上** を主要根拠として挙げる場合は **原則 HOLD**\n"
            "     （1つだけの単発言及は許容、テクニカル/ファンダ全体の整合で BUY 可）\n"
            "   - リスク要因を **4件以上** 挙げる場合は **原則 HOLD**\n"
            "   - 「負けないこと優先」のため、強気と弱気が拮抗するなら HOLD に倒す\n\n"
            "**制約4: 既保有銘柄との分散**\n"
            f"   保有中銘柄: {holdings_text}\n"
            "   保有中の銘柄と同じ **33業種分類** に該当する場合は集中リスクを\n"
            "   リスク欄に明記し、BUY を慎重に判断する（同業種の重複は基本回避）。\n\n"

            "--- 分析対象 ---\n"
            f"銘柄コード: {stock.get('code')}\n"
            f"会社名:     {stock.get('name', '')}\n"
            f"直近終値:   {latest_close} 円\n"
            f"時価総額:   {mc_oku:.0f}億円\n"
            f"純利益:     {stock.get('profit_value', 'N/A')}\n"
            f"前回開示日: {stock.get('disclosed_date', 'N/A')} （※次回決算日は Web検索で確認）\n\n"

            "--- 市場概況 ---\n"
            f"{overview_text}\n\n"

            "--- 出力形式（必ずこの構造で返答） ---\n"
            "## 推奨\n"
            "[buy / hold のいずれか1語] ※ sell は出力しないこと（未保有銘柄のため意味がない）\n"
            "※ ## 根拠 や ## リスク要因 で「強制HOLD」「制約抵触」と書く場合は、\n"
            "  上の1語は必ず hold とすること（書きながら buy を出す自己矛盾は絶対禁止）。\n\n"
            "## 根拠\n"
            "- 箇条書きで3〜5点、具体的な事実・数値・ニュースを引用\n"
            "- 上記の制約1〜4を踏まえた判断であることが分かる記載にすること\n\n"
            "## リスク要因\n"
            "- 箇条書きで2〜3点を基本とする（4件以上挙げると HOLD 推奨に統一される）\n"
        )

        result = self._client.ask_with_web_search(
            prompt,
            heavy=True,
            max_searches=3,
            max_tokens=4096,
        )
        raw = result["text"]

        analysis = StockAnalysis(
            code=stock.get("code", ""),
            name=stock.get("name", ""),
            recommendation=self._extract_recommendation(raw, allowed=("buy", "hold")),
            reasoning=self._extract_section(raw, "根拠"),
            risks=self._extract_bullets(self._extract_section(raw, "リスク要因")),
            raw_text=raw,
            citations=result["citations"],
        )

        # ─── 自己矛盾の安全網: ## 推奨 = buy だが根拠で「強制HOLD」等が明記された
        #     場合、HOLD に降格する。Claude のラベル/根拠不整合を検出した最終チェック。
        if analysis.recommendation == "buy":
            matched_kw = _detect_self_contradiction(analysis.reasoning, analysis.risks)
            if matched_kw:
                logger.warning(
                    f"自己矛盾検出: {analysis.code} の ## 推奨=buy だが根拠に "
                    f"'{matched_kw}' を検出 → HOLD に降格"
                )
                analysis.recommendation = "hold"
                analysis.risks.insert(
                    0,
                    f"AIの根拠内で「{matched_kw}」相当の記述が検出されたため、"
                    "推奨ラベルを BUY → HOLD に統一（自己矛盾自動補正）"
                )

        return analysis

    # ─── Step 4: 保有銘柄の継続/売却/買い増し判断 ───────────

    def analyze_held_position(
        self,
        holding: dict[str, Any],
        market_overview: MarketOverview | None = None,
        holdings_context: str | None = None,
    ) -> StockAnalysis:
        """保有ポジションについて HOLD/SELL/ADD のいずれかを Sonnet+Web検索で判断。

        未保有銘柄向けの analyze_stock とは出力ラベルが異なる:
          - hold: 継続保有（特に変化なし、もしくは様子見）
          - sell: 売却推奨（リスク顕在化 or 利確タイミング）
          - add:  買い増し推奨（追加買付が魅力的な水準・タイミング）

        Args:
            holding: 保有銘柄の dict。code / name / shares / avg_cost / latest_close を含む。
            market_overview: 市場概況。
            holdings_context: 全保有銘柄の概要（同業種ペナルティ用）。
        """
        code = holding.get("code", "")
        name = holding.get("name", "")
        shares = int(holding.get("shares") or 0)
        try:
            avg_cost = float(holding.get("avg_cost") or 0)
        except (ValueError, TypeError):
            avg_cost = 0.0
        latest_close = holding.get("latest_close")
        try:
            close_val = float(latest_close) if latest_close is not None else None
        except (ValueError, TypeError):
            close_val = None

        # 含み損益の事前計算（プロンプトに添える）
        if close_val is not None and avg_cost > 0 and shares > 0:
            pnl_pct = (close_val - avg_cost) / avg_cost * 100
            pnl_yen = (close_val - avg_cost) * shares
            pnl_summary = (
                f"含み損益: {pnl_pct:+.2f}% / 概算 {pnl_yen:+,.0f}円"
            )
        else:
            pnl_summary = "含み損益: 計算不能（株価または平均取得単価が未取得）"

        holdings_text = (
            holdings_context if (holdings_context or "").strip()
            else "（このポジションのみ）"
        )
        overview_text = (
            market_overview.summary if market_overview
            else "（市場概況情報なし）"
        )

        today = _today_jst_str()
        prompt = (
            "あなたは日本株のスイングトレード向けアナリストです。\n"
            "以下は **ユーザーが既に保有している銘柄** です。\n"
            "Web検索で直近のニュース・IR・決算情報を収集し、保有継続 / 売却 / 買い増しを\n"
            "**明確に**判断してください。\n\n"

            "--- 現在日時（必ずこの日付を起点に判断すること） ---\n"
            f"本日: **{today}**（日本標準時 JST）\n"
            "※ Claude の事前学習日付ではなく、上記日付を真の「本日」として扱うこと。\n"
            "※ 「±3営業日」等の日付計算は必ず上記日付を起点に行うこと。\n\n"

            "--- ユーザー前提（必ず遵守） ---\n"
            "・初期種銭は10万円（堅実運用で段階的に増資する方針）\n"
            "・SBI証券のS株（単元未満株）、スイングトレード（数日〜数週間保有）の初心者\n"
            "・短期で大きく稼ぐより **負けないこと** を優先する設計\n"
            "・※ 株価が現在予算で買い増しできるか否かは判断に **影響させない**\n\n"

            "--- 投資判断の制約条件（必ず守ること） ---\n"
            "**制約1: 決算前後の判断**\n"
            "   Web検索で次回決算発表予定日を確認。±3営業日以内なら ADD は出さず、\n"
            "   保有継続（HOLD）を基本とする。決算前リスク回避としての SELL は許可。\n\n"
            "**制約2: 短期イベントドリブン判断の禁止**\n"
            "   「決算サプライズ期待」「ギャップアップ狙い」「材料出尽くし反発期待」を\n"
            "   主要根拠とする ADD / SELL は出さない。\n\n"
            "**制約3: リスク要因の重み付け**\n"
            "   - 「高バリュエーション」「PER過熱」「テクニカル調整懸念」が明確な場合は\n"
            "     SELL を優先検討（リスク顕在化の前に手仕舞う発想）\n"
            "   - 下値硬直 + 好材料が出ていれば ADD 候補\n"
            "   - 迷ったら HOLD（負けないこと優先）\n\n"
            "**制約4: 保有銘柄全体の分散**\n"
            f"   現在の保有銘柄全体: {holdings_text}\n"
            "   同業種への ADD は集中リスクになるため、ADD 推奨時はリスク欄に明記する。\n\n"

            "--- 保有ポジション情報 ---\n"
            f"銘柄コード:    {code}\n"
            f"会社名:        {name}\n"
            f"保有株数:      {shares} 株\n"
            f"平均取得単価:  {avg_cost:,.0f} 円\n"
            f"現在値:        {close_val if close_val is not None else 'N/A'} 円\n"
            f"{pnl_summary}\n\n"

            "--- 市場概況 ---\n"
            f"{overview_text}\n\n"

            "--- 出力形式（必ずこの構造で返答） ---\n"
            "## 推奨\n"
            "[hold / sell / add のいずれか1語] ※ buy は出力しないこと（既に保有しているため）\n\n"
            "## 根拠\n"
            "- 箇条書きで3〜5点。含み損益・テクニカル・ファンダ・市場環境を踏まえる。\n"
            "- 上記の制約1〜4を踏まえた判断であることが分かる記載にすること。\n\n"
            "## リスク要因\n"
            "- 箇条書きで2〜4点。\n"
        )

        result = self._client.ask_with_web_search(
            prompt,
            heavy=True,
            max_searches=3,
            max_tokens=4096,
        )
        raw = result["text"]

        analysis = StockAnalysis(
            code=code,
            name=name,
            recommendation=self._extract_recommendation(raw, allowed=("hold", "sell", "add")),
            reasoning=self._extract_section(raw, "根拠"),
            risks=self._extract_bullets(self._extract_section(raw, "リスク要因")),
            raw_text=raw,
            citations=result["citations"],
        )

        # ─── 保有判断の自己矛盾安全網: ## 推奨 = add だが根拠で「制約抵触/強制HOLD」
        #     等が明記されたら HOLD に降格。SELL はリスク回避として有効なので対象外。
        if analysis.recommendation == "add":
            matched_kw = _detect_self_contradiction(analysis.reasoning, analysis.risks)
            if matched_kw:
                logger.warning(
                    f"自己矛盾検出: {analysis.code} の ## 推奨=add だが根拠に "
                    f"'{matched_kw}' を検出 → HOLD に降格"
                )
                analysis.recommendation = "hold"
                analysis.risks.insert(
                    0,
                    f"AIの根拠内で「{matched_kw}」相当の記述が検出されたため、"
                    "推奨ラベルを ADD → HOLD に統一（自己矛盾自動補正）"
                )

        return analysis

    def analyze_held_positions_throttled(
        self,
        holdings: list[dict[str, Any]],
        market_overview: MarketOverview | None = None,
        sleep_between_seconds: float = 60.0,
        holdings_context: str | None = None,
    ) -> Iterator[tuple[dict[str, Any], "StockAnalysis | None", "Exception | None"]]:
        """保有ポジションを順次判断するスロットリング付きジェネレータ。

        analyze_stocks_throttled の保有銘柄版。analyze_held_position を呼ぶ点だけ違う。
        各呼び出しの **後** に sleep_between_seconds 秒スリープ（最後の1件後はスキップ）。

        Yields:
            (holding, analysis, error) の tuple。失敗時は (holding, None, Exception)。
        """
        n = len(holdings)
        for i, h in enumerate(holdings):
            try:
                analysis = self.analyze_held_position(
                    h,
                    market_overview=market_overview,
                    holdings_context=holdings_context,
                )
                yield (h, analysis, None)
            except Exception as e:
                yield (h, None, e)

            is_last = (i == n - 1)
            if not is_last and sleep_between_seconds > 0:
                logger.info(
                    f"レート制限回避: 次の保有判断まで {sleep_between_seconds:.0f}秒待機中…"
                )
                time.sleep(sleep_between_seconds)

    def analyze_stocks_throttled(
        self,
        stocks: list[dict[str, Any]],
        market_overview: MarketOverview | None = None,
        sleep_between_seconds: float = 60.0,
        holdings_context: str | None = None,
    ) -> Iterator[tuple[dict[str, Any], "StockAnalysis | None", "Exception | None"]]:
        """複数銘柄を順次詳細分析するスロットリング付きジェネレータ。

        Anthropic Sonnet 4.6 の組織レート制限（30,000 input tokens/分）を
        回避するため、各 analyze_stock 呼び出しの **後** に
        sleep_between_seconds 秒のスリープを挟む。最後の銘柄の処理後は
        スリープしない（バッチ時間の無駄を最小化）。

        ジェネレータなので、消費側の保存・ロギング処理時間はスリープと
        重なる（ウォールクロックは sleep のみ加算）。

        Args:
            stocks: 詳細分析対象の銘柄リスト（screening の result dict 形式）。
            market_overview: 市場概況（あれば各分析プロンプトに含める）。
            sleep_between_seconds: 各銘柄の API 呼び出し **後** の待機秒数。
                0 を渡すとスリープしない（テスト用）。

        Yields:
            (stock, analysis, error) の tuple。
              - 成功時: (stock, StockAnalysis, None)
              - 失敗時: (stock, None, Exception)
            呼び出し側で per-stock の保存・エラーログ処理を行う。
        """
        n = len(stocks)
        for i, stock in enumerate(stocks):
            try:
                analysis = self.analyze_stock(
                    stock,
                    market_overview=market_overview,
                    holdings_context=holdings_context,
                )
                yield (stock, analysis, None)
            except Exception as e:
                yield (stock, None, e)

            is_last = (i == n - 1)
            if not is_last and sleep_between_seconds > 0:
                logger.info(
                    f"レート制限回避: 次の詳細分析まで {sleep_between_seconds:.0f}秒待機中…"
                )
                time.sleep(sleep_between_seconds)

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
    def _extract_recommendation(
        text: str,
        allowed: tuple[str, ...] = ("buy", "sell", "hold"),
    ) -> str:
        """`## 推奨` **セクション本文** から allowed のラベルを抽出。

        【重要な設計判断（2026-06-03 不具合修正）】
        旧実装は「## 推奨 ヘッダ直後の1単語」を取り、失敗時は **本文全体から
        最初の buy/hold を拾う** フォールバックを持っていた。これが
        「## 推奨：HOLD」「**HOLD（様子見）**」のような記号・コロン付き出力で
        ヘッダ抽出に失敗し、根拠中の否定文脈（"BUY 根拠を形成しない" 等）の
        BUY を誤って拾って buy を返す不具合を起こしていた。

        新実装の方針:
          1. `## 推奨` セクション本文（次の `##` 見出しまで）のみを判定対象にする
             → 根拠・リスク欄の否定文脈 BUY を構造的に見ない
          2. セクション本文に hold があれば最優先で hold（負けないこと優先）
          3. 本文全体スキャンのフォールバックは **廃止**
          4. 判定不能なら hold（allowed に hold が無ければ先頭）

        Args:
            text: モデル応答全文。
            allowed: 抽出を許可するラベルのタプル。
                未保有候補 → ("buy", "hold")
                保有判断   → ("hold", "sell", "add")
        """
        # ## 推奨 セクション本文を抽出（コロン「：/:」・改行・記号「**」のゆらぎに強い）。
        # 次の `##` 見出し or 文末まで。これにより根拠/リスク欄は判定対象に含まれない。
        m = re.search(r"##\s*推奨[\s:：]*(.*?)(?=\n##|\Z)", text, re.DOTALL)
        section = (m.group(1) if m else "").lower()

        if section.strip():
            # 保守優先: 推奨欄に hold があれば hold（"** HOLD **" 等の記号付きも \b で拾う）
            if "hold" in allowed and re.search(r"\bhold\b", section):
                return "hold"
            # それ以外は allowed の優先順位（先頭優先）で最初にマッチした語
            for key in allowed:
                if re.search(rf"\b{re.escape(key)}\b", section):
                    return key

        # 推奨セクションが取れない / 判定語なし → hold（負けないこと優先）。
        # ※ 旧実装の「本文全体から最初の buy を拾う」フォールバックは、根拠中の
        #   否定文脈 BUY を誤検出する事故の元だったため廃止した。
        return "hold" if "hold" in allowed else allowed[0]

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
