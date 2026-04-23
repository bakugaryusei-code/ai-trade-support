"""テクニカル指標の計算。

Step 3で実装予定。MA5 / MA25 / RSI(14) を pandas / numpy で算出。
結果は SQLite にキャッシュして日次で再計算を避ける。
"""
from __future__ import annotations
