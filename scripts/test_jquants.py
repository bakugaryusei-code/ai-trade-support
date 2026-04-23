"""J-Quants API V2 への接続確認スクリプト。

実行:
    python scripts/test_jquants.py

確認内容:
  1. APIキーでの認証（x-api-key ヘッダー）
  2. 銘柄情報の取得（7203 トヨタ自動車）
  3. 日次株価の取得
  4. 財務サマリーの取得
"""
from __future__ import annotations

import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加（src パッケージを import 可能にする）
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.jquants_client import JQuantsClient


def main() -> None:
    print("🔐 J-Quants API V2 に接続中...")
    client = JQuantsClient()

    # 1. 銘柄情報（7203 トヨタ自動車）
    print("\n📋 銘柄情報の取得テスト（7203 トヨタ自動車）")
    try:
        info_list = client.get_listed_info(code="7203")
        if info_list:
            stock = info_list[0]
            print(f"✅ {stock.get('CompanyName', 'N/A')} ({stock.get('Code', 'N/A')})")
            for k, v in stock.items():
                print(f"    {k}: {v}")
        else:
            print("⚠️  データなし")
    except Exception as e:
        print(f"❌ 取得失敗: {e}")
        print("   → APIキーが正しく設定されているか確認してください")
        sys.exit(1)

    # 2. 日次株価（直近30日）
    print("\n📈 日次株価の取得テスト（直近30日）")
    try:
        quotes = client.get_daily_quotes(code="7203")
        if quotes:
            print(f"✅ {len(quotes)}件取得")
            latest = quotes[-1]
            print(f"   最新日付: {latest.get('Date', 'N/A')}")
            print(f"   終値(C):  {latest.get('C', 'N/A')}")
            print(f"   出来高(Vo): {latest.get('Vo', 'N/A')}")
            print(f"   調整後終値(AdjC): {latest.get('AdjC', 'N/A')}")
        else:
            print("⚠️  データなし（Free プランは12週間遅延。過去日を指定すればデータ出ます）")
    except Exception as e:
        print(f"❌ 取得失敗: {e}")

    # 3. 財務サマリー
    print("\n📊 財務サマリーの取得テスト")
    try:
        summary = client.get_financial_summary(code="7203")
        if summary:
            print(f"✅ {len(summary)}件取得")
            latest = summary[0]
            print(f"   開示日: {latest.get('DisclosedDate', 'N/A')}")
            # V2のカラム名が分からないので候補を全部表示
            for k, v in latest.items():
                print(f"    {k}: {v}")
        else:
            print("⚠️  データなし")
    except Exception as e:
        print(f"❌ 取得失敗: {e}")

    print("\n🎉 接続テスト完了")


if __name__ == "__main__":
    main()
