"""GitHub Actions / cron-job.org から呼び出されるバッチ起動スクリプト。

JST 8時/12時/15時に定期実行され、以下を行う：
  1. Pythonスクリーニング（少数候補に絞る）
  2. 市場概況スキャン（Sonnet + Web検索、前回から4時間以上経過時のみ更新）
  3. Haikuバッチ評価で Tier 分類
  4. Sonnet詳細分析（Tier A 上位3件を対象）
  5. 結果を Supabase（PostgreSQL）に保存
     ※ Phase 2 で SQLite から Supabase に移行済み。trade.db の commit/push は廃止。

コスト目安：1回あたり $0.15〜0.30（月 $15〜27）
J-Quants Light プラン（60/分）使用。Free 時代の対応で候補数は少なめに制限。
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError 回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import db
from src.ai_analyzer import AIAnalyzer
from src.config import SCALE_CATEGORY_FILTER, SCREENING_MARKET
from src.jquants_client import JQuantsClient
from src.screening import Screener

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 設定（レートリミットとコスト抑制のため慎重に）
# ─────────────────────────────────────────

# J-Quants プラン名（"Free" / "Light" / "Standard" / "Premium"）。
# Light 以上では直近のデータが取れるので過去日指定の workaround は不要。
JQUANTS_PLAN = "Light"

# Tier A から詳細分析する上限（仕様書設計⑤の運用範囲）
DETAILED_ANALYSIS_LIMIT = 3
# 市場概況の最低更新間隔（時間）。GitHub Actions の遅延を吸収するため、
# 「朝のみ実行」ではなく「前回から N時間以上経過していれば更新」とする。
MARKET_OVERVIEW_REFRESH_HOURS = 4


def _jst_now() -> datetime:
    """現在時刻（JST）。"""
    return datetime.now(timezone(timedelta(hours=9)))


def _should_refresh_market_overview(now: datetime) -> bool:
    """市場概況を再取得すべきかを判定。

    GitHub Actions の遅延で「朝のみ実行」が機能しない事象を回避するため、
    時刻ベースではなく「前回更新からの経過時間」で判定する。
    """
    latest = db.get_latest_market_overview()
    if not latest:
        return True  # 初回は必ず取得

    last_dt_str = latest.get("batch_datetime") or latest.get("created_at", "")
    if not last_dt_str:
        return True

    # YYYY-MM-DD HH:MM 形式 or YYYY-MM-DDTHH:MM:SS形式に対応
    try:
        # 簡易パース：先頭16文字（YYYY-MM-DD HH:MM）を見る
        last_dt = datetime.strptime(last_dt_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            last_dt = datetime.fromisoformat(last_dt_str.replace("Z", "+00:00"))
            last_dt = last_dt.replace(tzinfo=None)
        except Exception:
            return True  # パース失敗時は念のため更新

    # JST naive 同士で比較
    now_naive = now.replace(tzinfo=None)
    elapsed = (now_naive - last_dt).total_seconds() / 3600
    return elapsed >= MARKET_OVERVIEW_REFRESH_HOURS


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    now = _jst_now()
    batch_dt = now.strftime("%Y-%m-%d %H:%M")
    batch_date = now.strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"🤖 AIトレードサポート バッチ実行")
    print(f"   JST: {batch_dt}")
    print("=" * 60)

    # ─── DB 初期化（ファイルが無ければ作成） ───
    db.init_db()

    # ─── 1. スクリーニング（API呼び出しあり、Light プランで約9分） ───
    # 母集団: プライム × ScaleCat ホワイトリスト（TOPIX 500 = 約493社）。
    # 株価は /equities/bars/daily?date=... で全銘柄を一括取得し、
    # 各銘柄では fins/summary 1コールのみ。
    print(f"\n📋 Step 1: スクリーニング実行中（J-Quants {JQUANTS_PLAN} プラン）...")
    print(f"   対象: {SCREENING_MARKET} × {SCALE_CATEGORY_FILTER}")
    try:
        screener = Screener(client=JQuantsClient(plan=JQUANTS_PLAN))
        candidates = screener.run(
            market=SCREENING_MARKET,
            scale_categories=SCALE_CATEGORY_FILTER,
        )
        print(f"✅ 候補銘柄: {len(candidates)}件")
        db.save_candidates(batch_date, candidates)
    except Exception as e:
        print(f"❌ スクリーニング失敗: {e}")
        traceback.print_exc()
        candidates = []

    # 候補ゼロ時も処理は続行（市場概況・保有銘柄の評価は意味あり）
    if not candidates:
        print("⚠️  候補ゼロのため、Tier分類・詳細分析はスキップ")

    # ─── 2. 市場概況（時間ベースで判定：前回更新から N時間経過） ───
    analyzer = AIAnalyzer()
    overview = None
    should_refresh = _should_refresh_market_overview(now)
    if should_refresh:
        print(f"\n🌏 Step 0: 市場概況スキャン（前回から {MARKET_OVERVIEW_REFRESH_HOURS}時間以上経過 → 更新）...")
        try:
            ov = analyzer.run_market_overview()
            db.save_market_overview({
                "batch_datetime": batch_dt,
                "summary": ov.summary,
                "search_count": len(ov.search_queries),
                "citation_count": len(ov.citations),
            })
            overview = ov
            print(f"✅ 市場概況取得（検索{len(ov.search_queries)}回・引用{len(ov.citations)}件）")
        except Exception as e:
            print(f"⚠️ 市場概況取得失敗（続行）: {e}")
            traceback.print_exc()
    else:
        print(f"\n🌏 Step 0: 市場概況は最新（前回更新から {MARKET_OVERVIEW_REFRESH_HOURS}時間未満）→ スキップ")
        # 既存の最新市場概況を DB から読み込む
        latest = db.get_latest_market_overview()
        if latest:
            from src.ai_analyzer import MarketOverview as MO
            overview = MO(summary=latest["summary"], citations=[], search_queries=[])

    # ─── 3. Tier分類（Haikuバッチ） ───
    tiers = []
    if candidates:
        print("\n🏷️  Step 2: Tier分類（Haiku）...")
        try:
            tiers = analyzer.classify_tiers(candidates, market_overview=overview)
            for t in tiers:
                print(f"  [{t.tier}] {t.code} {t.name}: {t.reason[:40]}")
        except Exception as e:
            print(f"❌ Tier分類失敗: {e}")
            traceback.print_exc()

    # ─── 4. 詳細分析（Sonnet + Web検索、Tier A 上位） ───
    # Anthropic Sonnet 4.6 の組織レート制限（30,000 input tokens/分）を回避するため、
    # 各銘柄の API 呼び出し後に 60秒スリープを挟む（最後の1件の後はスリープしない）。
    SONNET_SLEEP_BETWEEN_SEC = 60.0
    tier_a = [t for t in tiers if t.tier == "A"][:DETAILED_ANALYSIS_LIMIT]
    if tier_a:
        print(f"\n🔍 Step 3: 詳細分析（Tier A 上位{len(tier_a)}件）...")
        # Tier A 銘柄の dict を candidates から抽出
        tier_a_stocks: list[dict[str, Any]] = []
        for target in tier_a:
            stock = next((c for c in candidates if c["code"] == target.code), None)
            if stock is not None:
                tier_a_stocks.append(stock)

        for stock, analysis, error in analyzer.analyze_stocks_throttled(
            tier_a_stocks,
            market_overview=overview,
            sleep_between_seconds=SONNET_SLEEP_BETWEEN_SEC,
        ):
            code = stock.get("code", "?")
            if error is not None or analysis is None:
                print(f"  ❌ {code}: 詳細分析失敗 ({error})")
                traceback.print_exc()
                continue
            db.save_recommendation({
                "batch_datetime": batch_dt,
                "code": analysis.code,
                "name": analysis.name,
                "recommendation": analysis.recommendation,
                "tier": "A",
                "reasoning": _split_lines(analysis.reasoning),
                "risks": analysis.risks,
                "citation_count": len(analysis.citations),
                "latest_close": stock.get("latest_close"),
                "market_cap": stock.get("market_cap"),
            })
            print(f"  ✅ {analysis.code} {analysis.name}: {analysis.recommendation.upper()}")

    # Tier B 以下も HOLD として保存（一覧表示用）
    if tiers:
        for t in tiers:
            if t.tier in ("B", "C"):
                stock = next((c for c in candidates if c["code"] == t.code), None)
                if stock is None:
                    continue
                try:
                    db.save_recommendation({
                        "batch_datetime": batch_dt,
                        "code": t.code,
                        "name": t.name,
                        "recommendation": "hold",
                        "tier": t.tier,
                        "reasoning": [t.reason],
                        "risks": [],
                        "citation_count": 0,
                        "latest_close": stock.get("latest_close"),
                        "market_cap": stock.get("market_cap"),
                    })
                except Exception as e:
                    logger.warning(f"{t.code} 保存失敗: {e}")

    print("\n🎉 バッチ完了")


def _split_lines(text: str) -> list[str]:
    """複数行の箇条書きを list[str] に変換。"""
    return [line.strip().lstrip("-*・ ").strip()
            for line in text.splitlines()
            if line.strip() and line.strip() != "-"]


if __name__ == "__main__":
    main()
