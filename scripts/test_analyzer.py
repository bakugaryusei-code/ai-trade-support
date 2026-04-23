"""AI分析3段階パイプラインの動作確認スクリプト。

実行の流れ：
  Step 0: 市場概況（Sonnet + Web検索）
  Step 2: Tier分類（Haiku、モックの候補銘柄リスト）
  Step 3: Tier A の銘柄を1件だけ詳細分析（Sonnet + Web検索）

コストは約 $0.15〜0.25 程度。

実行:
    python scripts/test_analyzer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ai_analyzer import AIAnalyzer


# 知名度が高く、ニュース・IRが豊富にある銘柄で動作確認
MOCK_CANDIDATES = [
    {
        "code": "7203",
        "name": "トヨタ自動車",
        "market_cap": 35_000_000_000_000,  # 35兆円
        "profit_value": "3947242000000",   # 約3.9兆円
        "latest_close": 3200.0,
        "disclosed_date": "2024-02-06",
    },
    {
        "code": "6758",
        "name": "ソニーグループ",
        "market_cap": 15_000_000_000_000,  # 15兆円
        "profit_value": "1000000000000",
        "latest_close": 13000.0,
        "disclosed_date": "2024-02-14",
    },
    {
        "code": "9984",
        "name": "ソフトバンクグループ",
        "market_cap": 12_000_000_000_000,
        "profit_value": "500000000000",
        "latest_close": 9000.0,
        "disclosed_date": "2024-02-07",
    },
    {
        "code": "8306",
        "name": "三菱UFJフィナンシャル",
        "market_cap": 20_000_000_000_000,
        "profit_value": "1400000000000",
        "latest_close": 1800.0,
        "disclosed_date": "2024-02-07",
    },
]


def main() -> None:
    print("🤖 AI分析3段階パイプライン テスト開始\n")

    analyzer = AIAnalyzer()

    # ─── Step 0: 市場概況 ───
    print("=" * 70)
    print("🌏 Step 0: 市場概況スキャン（Sonnet + Web検索）")
    print("=" * 70)
    print("実行中（10〜30秒）...\n")
    overview = analyzer.run_market_overview()
    print(overview.summary)
    print(f"\n🔎 検索 {len(overview.search_queries)}件 / 引用 {len(overview.citations)}件\n")

    # ─── Step 2: Tier分類 ───
    print("=" * 70)
    print("🏷️  Step 2: Tier分類（Haiku、Web検索なし）")
    print("=" * 70)
    print(f"候補銘柄 {len(MOCK_CANDIDATES)}件を分類中（5〜10秒）...\n")
    tiers = analyzer.classify_tiers(MOCK_CANDIDATES, market_overview=overview)
    for t in tiers:
        print(f"  [{t.tier}] {t.code} {t.name}: {t.reason}")

    tier_a = [t for t in tiers if t.tier == "A"]
    print(f"\n→ Tier A: {len(tier_a)}件")

    # ─── Step 3: 詳細分析（Tier A の先頭1件のみ、コスト抑制） ───
    if not tier_a:
        print("\n⚠️  Tier A が空のため、詳細分析はスキップ")
    else:
        target = tier_a[0]
        stock_data = next(c for c in MOCK_CANDIDATES if c["code"] == target.code)

        print("\n" + "=" * 70)
        print(f"🔍 Step 3: {target.code} {target.name} の詳細分析（Sonnet + Web検索）")
        print("=" * 70)
        print("実行中（15〜40秒）...\n")
        analysis = analyzer.analyze_stock(stock_data, market_overview=overview)

        print(f"📊 推奨: {analysis.recommendation.upper()}")
        print(f"\n📝 根拠:\n{analysis.reasoning}")
        print(f"\n⚠️  リスク要因:")
        for r in analysis.risks:
            print(f"  - {r}")
        print(f"\n🔗 引用 {len(analysis.citations)}件")

    print("\n🎉 AI分析パイプライン テスト完了")


if __name__ == "__main__":
    main()
