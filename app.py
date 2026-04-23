"""Streamlit ダッシュボードのエントリーポイント。

Step 5で本格実装。現時点は骨組みのみで、起動確認用の最小画面。
スマホ表示優先のレイアウト（縦スクロール・大きめフォント）。
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="AIトレードサポート",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.title("📈 AIトレードサポート")
st.caption("10万円元手 × SBI S株 × スイングトレード")

st.info(
    "🚧 このアプリはまだ骨組み段階です。\n\n"
    "Step 2〜7 の実装を経て、ダッシュボードが完成します。"
)

st.subheader("今日の予定機能")
st.markdown(
    """
    - 🌐 市場概況（朝の要約）
    - 🎯 AI売買推奨（根拠付き）
    - 📊 保有銘柄の状況
    - 📝 取引記録フォーム
    - 📈 AI成績トラッキング
    """
)
