"""Claude API への接続確認スクリプト。

Haiku と Sonnet を1回ずつ呼び、返答が取れるかを確認する。

実行:
    python scripts/test_claude.py

注意:
    1回の実行で約 $0.001〜0.01 程度のコストがかかる（無視できるレベル）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows + cmd で `>` リダイレクト時の UnicodeEncodeError を回避
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.claude_client import ClaudeClient


def main() -> None:
    print("🤖 Claude API 接続テスト開始\n")

    client = ClaudeClient()

    # ─── 1. Haiku（軽量モデル）──
    print("🪶 Haiku 4.5 に「日本の株式スイングトレードで重要な指標を3つ、簡潔に」と質問中...")
    try:
        reply = client.ask(
            "日本の株式スイングトレードで特に重要なテクニカル指標を3つ、"
            "各50字以内で簡潔に教えて。",
            heavy=False,
            max_tokens=512,
        )
        print("✅ Haiku 応答:")
        print("-" * 60)
        print(reply)
        print("-" * 60)
    except Exception as e:
        print(f"❌ Haiku 呼び出し失敗: {e}")
        sys.exit(1)

    # ─── 2. Sonnet（詳細分析モデル）──
    print("\n🎼 Sonnet 4.6 に「ファンダメンタル分析の流れ」を質問中...")
    try:
        reply = client.ask(
            "個別株のファンダメンタル分析の基本的な流れを、200字程度でまとめて。",
            heavy=True,
            max_tokens=512,
        )
        print("✅ Sonnet 応答:")
        print("-" * 60)
        print(reply)
        print("-" * 60)
    except Exception as e:
        print(f"❌ Sonnet 呼び出し失敗: {e}")
        sys.exit(1)

    print("\n🎉 Claude API 接続テスト完了")


if __name__ == "__main__":
    main()
