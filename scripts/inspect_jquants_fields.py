"""J-Quants V2 のレスポンスフィールドを探索するスクリプト。

スクリーニング実装に必要な以下を調べる：
  - プライム市場の MarketCode の値
  - 黒字判定に使える純利益フィールド名
  - 時価総額計算に使える発行済株式数フィールド名

実行:
    python scripts/inspect_jquants_fields.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.jquants_client import JQuantsClient


def pretty(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def main() -> None:
    client = JQuantsClient()

    # ─── 1. 銘柄情報（MarketCode と発行済株式数を確認） ───
    print("=" * 60)
    print("📋 /equities/master（上場銘柄情報：7203 トヨタ）")
    print("=" * 60)
    info_list = client.get_listed_info(code="7203")
    if info_list:
        stock = info_list[0]
        print(pretty(stock))
        print()
        print("🔍 確認ポイント:")
        print(f"  - MarketCode: {stock.get('MarketCode', 'NOT FOUND')}")
        print(f"  - MarketCodeName: {stock.get('MarketCodeName', 'NOT FOUND')}")
        # 発行済株式数候補
        for key in ("IssuedShares", "ListedShares", "SharesOutstanding", "NumberOfShares"):
            if key in stock:
                print(f"  - {key}: {stock[key]}")
    else:
        print("データなし")

    # ─── 2. 財務サマリー（黒字判定に使うフィールドを確認） ───
    print("\n" + "=" * 60)
    print("📊 /fins/summary（財務サマリー：7203 トヨタ、最新1件）")
    print("=" * 60)
    summary_list = client.get_financial_summary(code="7203")
    if summary_list:
        latest = summary_list[0]
        # 値があるフィールドだけを表示（空フィールドで画面が埋まるのを避ける）
        non_empty = {k: v for k, v in latest.items() if v not in (None, "", [])}
        print("(値が入っているフィールドのみ表示)")
        print(pretty(non_empty))
        print()
        print("🔍 確認ポイント（黒字判定の候補）:")
        profit_keys = [k for k in latest.keys() if "Profit" in k or "NP" in k or "NetIncome" in k]
        for k in profit_keys:
            print(f"  - {k}: {latest.get(k)}")
    else:
        print("データなし")

    # ─── 3. 他のプライム銘柄も試す（MarketCodeの確認） ───
    print("\n" + "=" * 60)
    print("📋 他の著名プライム銘柄のMarketCodeを確認")
    print("=" * 60)
    other_stocks = {
        "6758": "ソニーグループ",
        "9984": "ソフトバンクグループ",
        "8306": "三菱UFJフィナンシャル",
        "7974": "任天堂",
    }
    for code, name in other_stocks.items():
        try:
            info = client.get_listed_info(code=code)
            if info:
                print(f"  {code} {name}: MarketCode={info[0].get('MarketCode')} "
                      f"MarketCodeName={info[0].get('MarketCodeName')}")
        except Exception as e:
            print(f"  {code} {name}: エラー {e}")

    print("\n🎉 探索完了。上の結果から実際のフィールド名・MarketCode値を教えてください。")


if __name__ == "__main__":
    main()
