"""スクリーニング処理の動作確認スクリプト（少数銘柄）。

Free プランのレートリミット（5/分）とデータ12週間遅延を考慮し：
  - 先頭3銘柄だけ処理
  - 株価は3ヶ月前〜4ヶ月前の期間で取得

全プライム銘柄（約1600）でスクリーニングするには、運用時に Light プラン以上に
切り替える必要がある。

実行:
    python scripts/test_screening.py
"""
from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.screening import Screener


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("🔍 スクリーニングテスト開始\n")

    screener = Screener()

    # ─── 1. プライム銘柄の一覧を取得（API 1コール） ───
    print("📋 プライム銘柄一覧を取得中（API 1コール）...")
    prime_stocks = screener.get_candidates_by_market("PRIME")
    print(f"✅ プライム銘柄数: {len(prime_stocks)}")
    print("   先頭5件:")
    for s in prime_stocks[:5]:
        print(f"     {s.get('Code')} {s.get('CoName')}")
    print()

    # ─── 2. 少数銘柄でスクリーニング実行 ───
    # Free プランは株価データが12週間遅延するので、3〜4ヶ月前の期間を指定
    today = date.today()
    quote_from = today - timedelta(days=120)  # 4ヶ月前
    quote_to = today - timedelta(days=90)     # 3ヶ月前

    limit = 3
    print(f"🎯 先頭{limit}銘柄で条件チェック（API {limit}銘柄×2コール={limit*2}コール+銘柄一覧1コール）")
    print(f"   株価取得期間: {quote_from} 〜 {quote_to}（Freeプラン12週間遅延を考慮）")
    print(f"   Free プランのスロットリング → 約{(limit * 2 + 1) * 13}秒かかります\n")

    candidates = screener.run(
        limit=limit,
        quote_from_date=quote_from,
        quote_to_date=quote_to,
    )

    # ─── 3. 結果表示 ───
    print(f"\n✅ 条件を満たした銘柄: {len(candidates)}件")
    for c in candidates:
        mc_oku = c["market_cap"] / 1e8 if c["market_cap"] else None
        print(f"  - {c['code']} {c['name']}")
        print(f"      直近終値: {c['latest_close']}")
        print(f"      純利益:   {c['profit_value']}")
        print(f"      時価総額: {mc_oku:.0f}億円" if mc_oku else "      時価総額: 計算不可")
        print(f"      開示日:   {c['disclosed_date']}")

    if not candidates:
        print("   （候補ゼロ：Freeプランで株価取れない or 条件に合う銘柄なし）")

    print("\n🎉 スクリーニングテスト完了")


if __name__ == "__main__":
    main()
