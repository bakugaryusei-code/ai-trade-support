"""Claude Web検索ツールの動作確認スクリプト。

「最新情報が必要な質問」を投げて、Claude が自動で Web検索を実行し、
結果を踏まえた回答を返すかを確認する。

実行:
    python scripts/test_claude_websearch.py

注意:
    1回の実行で約 $0.02〜0.05 程度のコストがかかる（検索数による）。
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.claude_client import ClaudeClient


def main() -> None:
    print("🌐 Claude Web検索ツール テスト開始\n")

    client = ClaudeClient()

    # 最新情報が必須な質問 → Claude が自動で検索するはず
    prompt = (
        "今週（直近7日以内）の日本の株式市場について、"
        "日経平均・TOPIXの値動きと、注目されたセクターや材料を3点にまとめて。"
        "数値や情報は最新の実データに基づくこと。"
    )

    print("📝 プロンプト:")
    print(f"   {prompt}\n")
    print("🔍 検索中（通常 10〜30秒かかります）...\n")

    try:
        result = client.ask_with_web_search(
            prompt,
            heavy=True,
            max_searches=3,
            max_tokens=2048,
        )
    except Exception as e:
        print(f"❌ 失敗: {e}")
        sys.exit(1)

    # ─── 結果の表示 ───
    print("=" * 70)
    print("📄 回答:")
    print("=" * 70)
    print(result["text"])
    print("=" * 70)

    print(f"\n🔎 実行された検索: {len(result['search_queries'])}件")
    for i, q in enumerate(result["search_queries"], 1):
        print(f"   [{i}] {q}")

    print(f"\n🔗 引用URL: {len(result['citations'])}件")
    for i, c in enumerate(result["citations"][:5], 1):  # 先頭5件だけ
        print(f"   [{i}] {c.get('title', 'N/A')}")
        print(f"       {c.get('url', 'N/A')}")
    if len(result["citations"]) > 5:
        print(f"   ...ほか {len(result['citations']) - 5} 件")

    print("\n🎉 Web検索ツール テスト完了")


if __name__ == "__main__":
    main()
