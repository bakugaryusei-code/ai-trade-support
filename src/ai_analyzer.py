"""AI分析・推奨生成の中核ロジック。

Step 4で実装予定。2段構え：
  Step 0 (朝のみ): 市場概況スキャン（Sonnet + Web検索）
  Step 1: Pythonスクリーニング（screening.pyを呼ぶ）
  Step 2: Haikuバッチ評価（Tier A/B/C分類）
  Step 3: Sonnet詳細分析（Tier A銘柄 + 保有銘柄 + Web検索）
"""
from __future__ import annotations
