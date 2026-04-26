"""GitHub Actions から呼び出されるバッチ起動スクリプト。

JST 8時/12時/15時に定期実行され、以下を行う：
  1. Pythonスクリーニング（少数候補に絞る）
  2. 市場概況スキャン（Sonnet + Web検索、朝のみ）
  3. Haikuバッチ評価で Tier 分類
  4. Sonnet詳細分析（Tier A 上位を対象）
  5. 結果を SQLite に保存
  6. （workflow 側で）data/trade.db を commit して GitHub に push

コスト目安：1回あたり $0.15〜0.30（月 $15〜27）
Free プランの J-Quants レートリミット（5/分）に対応するため、候補数は少なめに制限。
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError 回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import db
from src.ai_analyzer import AIAnalyzer
from src.screening import Screener

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 設定（レートリミットとコスト抑制のため慎重に）
# ─────────────────────────────────────────

# Free プラン対応：スクリーニングで処理する銘柄上限
SCREENING_LIMIT = 5
# Tier A から詳細分析する上限（コスト抑制）
DETAILED_ANALYSIS_LIMIT = 3


def _jst_now() -> datetime:
    """現在時刻（JST）。"""
    return datetime.now(timezone(timedelta(hours=9)))


def _is_morning_batch(now: datetime) -> bool:
    """朝のバッチかどうか（6時〜10時の範囲）。市場概況はここでのみ実行。"""
    return 6 <= now.hour <= 10


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    now = _jst_now()
    batch_dt = now.strftime("%Y-%m-%d %H:%M")
    batch_date = now.strftime("%Y-%m-%d")
    is_morning = _is_morning_batch(now)

    print("=" * 60)
    print(f"🤖 AIトレードサポート バッチ実行")
    print(f"   JST: {batch_dt}")
    print(f"   朝のバッチ: {is_morning}")
    print("=" * 60)

    # ─── DB 初期化（ファイルが無ければ作成） ───
    db.init_db()

    # ─── 1. スクリーニング（API呼び出しあり、時間がかかる） ───
    print("\n📋 Step 1: スクリーニング実行中...")
    try:
        screener = Screener()
        candidates = screener.run(limit=SCREENING_LIMIT)
        print(f"✅ 候補銘柄: {len(candidates)}件")
        db.save_candidates(batch_date, candidates)
    except Exception as e:
        print(f"❌ スクリーニング失敗: {e}")
        traceback.print_exc()
        candidates = []

    # 候補ゼロ時も処理は続行（市場概況・保有銘柄の評価は意味あり）
    if not candidates:
        print("⚠️  候補ゼロのため、Tier分類・詳細分析はスキップ")

    # ─── 2. 市場概況（朝のみ） ───
    analyzer = AIAnalyzer()
    overview = None
    if is_morning:
        print("\n🌏 Step 0: 市場概況スキャン（Sonnet + Web検索）...")
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
        # 朝以外は最新の市場概況を DB から読み込む
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
    tier_a = [t for t in tiers if t.tier == "A"][:DETAILED_ANALYSIS_LIMIT]
    if tier_a:
        print(f"\n🔍 Step 3: 詳細分析（Tier A 上位{len(tier_a)}件）...")
        for target in tier_a:
            stock = next((c for c in candidates if c["code"] == target.code), None)
            if stock is None:
                continue
            try:
                analysis = analyzer.analyze_stock(stock, market_overview=overview)
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
            except Exception as e:
                print(f"  ❌ {target.code}: 詳細分析失敗 ({e})")
                traceback.print_exc()

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
