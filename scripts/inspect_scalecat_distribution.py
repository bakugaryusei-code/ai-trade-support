"""プライム市場の ScaleCat 分布を実測する調査スクリプト。

目的:
  TOPIX Core30 / Large70 / Mid400 / Small1 / Small2 / 空値 がそれぞれ
  何社含まれるかを把握し、「TOPIX 500 で絞ると何社になるか」を確定させる。
  この結果を元に screening.py の改修方針（A-2案）を最終決定する。

実行:
    python scripts/inspect_scalecat_distribution.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.jquants_client import JQuantsClient


def main() -> None:
    print("🔍 J-Quants /equities/master を1コールで取得（全上場銘柄）")
    client = JQuantsClient(plan="Light")
    all_stocks = client.get_listed_info()
    print(f"   取得: {len(all_stocks)}件\n")

    # プライムだけに絞る（Mkt == "0111"）
    prime = [s for s in all_stocks if s.get("Mkt") == "0111"]
    print(f"📊 プライム市場: {len(prime)}社\n")

    # ScaleCat 分布
    counter = Counter(s.get("ScaleCat") or "（空値）" for s in prime)
    print("📋 プライム市場の ScaleCat 分布:")
    print("-" * 60)
    total = 0
    for cat, n in sorted(counter.items(), key=lambda x: (-x[1], x[0])):
        print(f"   {cat:<30s}: {n:>5d}社")
        total += n
    print("-" * 60)
    print(f"   {'合計':<30s}: {total:>5d}社\n")

    # TOPIX 500（Core30 + Large70 + Mid400）の合計
    topix500_cats = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")
    topix500 = sum(counter.get(c, 0) for c in topix500_cats)
    print(f"🎯 TOPIX 500（Core30 + Large70 + Mid400）: {topix500}社")
    topix100 = counter.get("TOPIX Core30", 0) + counter.get("TOPIX Large70", 0)
    print(f"🎯 TOPIX 100（Core30 + Large70）         : {topix100}社\n")

    # 業種分布も参考に表示（S33Nm = 33業種）
    print("📊 プライム市場の業種分布（上位15業種）:")
    sector_counter = Counter(s.get("S33Nm") or "（空値）" for s in prime)
    for sector, n in sector_counter.most_common(15):
        print(f"   {sector:<30s}: {n:>5d}社")


if __name__ == "__main__":
    main()
