"""Streamlit ダッシュボード本体（スマホ優先レイアウト）。

仕様書§3 / §11 準拠：
  - 縦スクロール・大きめフォント
  - センター寄せ単一カラム
  - タブで4セクション切り替え（推奨 / 保有 / 記録 / 成績）

Step 5 時点ではサンプルデータ（src.mock_data）で動作。
Step 6 完了時に SQLite 経由のデータアクセスに差し替える。
"""
from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from src import mock_data

# ─────────────────────────────────────────
# ページ設定
# ─────────────────────────────────────────

st.set_page_config(
    page_title="AIトレードサポート",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# スマホ表示最適化のCSS（フォント少し大きめ、タブを押しやすく）
st.markdown(
    """
    <style>
      html, body, [class*="css"]  { font-size: 16px; }
      h1 { font-size: 1.6rem !important; }
      h2 { font-size: 1.3rem !important; }
      h3 { font-size: 1.15rem !important; }
      .stTabs [data-baseweb="tab"] { font-size: 1.0rem; padding: 10px 14px; }
      .stButton > button { width: 100%; padding: 0.6rem; }
      [data-testid="stMetricValue"] { font-size: 1.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────
# session_state 初期化（Step 6 で SQLite に差し替え）
# ─────────────────────────────────────────

if "trades" not in st.session_state:
    st.session_state.trades = []      # 取引履歴
if "holdings" not in st.session_state:
    st.session_state.holdings = {}    # code -> {shares, avg_cost, name}


# ─────────────────────────────────────────
# 共通ヘルパー
# ─────────────────────────────────────────


def _reco_badge(recommendation: str) -> str:
    """buy/sell/hold を色付きバッジに変換。"""
    return {
        "buy":  "🟢 **BUY（買い推奨）**",
        "sell": "🔴 **SELL（売り推奨）**",
        "hold": "🟡 **HOLD（様子見）**",
    }.get(recommendation, "⚪ **UNKNOWN**")


def _format_yen(yen: float | int | None) -> str:
    if yen is None:
        return "—"
    if yen >= 1e8:
        return f"{yen/1e8:,.0f}億円"
    if yen >= 1e4:
        return f"{yen/1e4:,.0f}万円"
    return f"{yen:,.0f}円"


def _recompute_holdings_from_trades() -> None:
    """trades から holdings を再構築（買い増し・売却を反映）。"""
    holdings: dict[str, dict] = {}
    for t in st.session_state.trades:
        code = t["code"]
        if code not in holdings:
            holdings[code] = {"shares": 0, "total_cost": 0.0, "name": t.get("name", "")}
        if t["side"] == "buy":
            holdings[code]["shares"] += t["shares"]
            holdings[code]["total_cost"] += t["shares"] * t["price"]
        else:  # sell
            if holdings[code]["shares"] > 0:
                avg = holdings[code]["total_cost"] / holdings[code]["shares"]
                holdings[code]["shares"] -= t["shares"]
                holdings[code]["total_cost"] -= t["shares"] * avg
        if holdings[code]["shares"] <= 0:
            holdings.pop(code, None)
    # 平均取得単価を計算して保存
    result = {}
    for code, h in holdings.items():
        result[code] = {
            "shares": h["shares"],
            "avg_cost": h["total_cost"] / h["shares"] if h["shares"] > 0 else 0,
            "name": h["name"],
        }
    st.session_state.holdings = result


# ─────────────────────────────────────────
# ヘッダー
# ─────────────────────────────────────────

st.title("📈 AIトレードサポート")
st.caption(f"10万円元手 × SBI S株 × スイングトレード")

c1, c2 = st.columns(2)
c1.caption(f"最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
c2.caption("🪙 J-Quants: Free / ⚠️ データ12週間遅延")


# ─────────────────────────────────────────
# タブ
# ─────────────────────────────────────────

tab_reco, tab_hold, tab_log, tab_stat = st.tabs(
    ["🎯 推奨", "📊 保有", "📝 記録", "📈 成績"]
)


# ───── 🎯 推奨タブ ─────────────────────
with tab_reco:
    overview = mock_data.get_latest_market_overview()

    with st.expander("🌏 今日の市場概況", expanded=True):
        st.markdown(overview["summary"])
        st.caption(
            f"更新: {overview['updated_at']}｜"
            f"Web検索 {overview['search_count']}件 / 引用 {overview['citation_count']}件"
        )

    st.subheader("🎯 今日のAI推奨")

    recs = mock_data.get_todays_recommendations()
    if not recs:
        st.info("現在の推奨銘柄はありません。次のバッチ（12時）をお待ちください。")

    for rec in recs:
        with st.container(border=True):
            title_col, price_col = st.columns([2, 1])
            with title_col:
                st.markdown(f"### {rec['code']} {rec['name']}")
            with price_col:
                st.markdown(
                    f"**{rec['latest_close']:,.0f}円**<br>"
                    f"<small>時価総額 {rec['market_cap_oku']:,.0f}億円</small>",
                    unsafe_allow_html=True,
                )

            st.markdown(_reco_badge(rec["recommendation"]))

            st.markdown("**📝 根拠**")
            for r in rec["reasoning"]:
                st.markdown(f"- {r}")

            st.markdown("**⚠️ リスク要因**")
            for r in rec["risks"]:
                st.markdown(f"- {r}")

            st.caption(
                f"🔗 参照元 {rec['citation_count']}件｜更新 {rec['updated_at']}"
            )

            b1, b2 = st.columns(2)
            if b1.button(f"✓ 承認", key=f"approve_{rec['code']}"):
                st.success(
                    f"承認しました。SBIアプリで {rec['code']} を手動注文してください。"
                )
            if b2.button(f"✗ 却下", key=f"reject_{rec['code']}"):
                st.info(f"{rec['code']} を却下しました。")


# ───── 📊 保有タブ ─────────────────────
with tab_hold:
    _recompute_holdings_from_trades()

    st.subheader("📊 保有銘柄")

    if not st.session_state.holdings:
        st.info(
            "現在、保有銘柄はありません。\n\n"
            "買い注文したら「📝 記録」タブから取引を記録してください。"
        )
    else:
        for code, h in st.session_state.holdings.items():
            with st.container(border=True):
                st.markdown(f"### {code} {h['name']}")
                col1, col2 = st.columns(2)
                col1.metric("保有株数", f"{h['shares']}株")
                col2.metric("平均取得単価", f"{h['avg_cost']:,.0f}円")
                st.caption(
                    "含み損益は現在値取得のインフラ実装後に表示されます（Step 6以降）"
                )


# ───── 📝 記録タブ ─────────────────────
with tab_log:
    st.subheader("📝 取引を記録する")
    st.caption(
        "SBIアプリで注文が約定したら、ここに記録してください。"
        "履歴はAI分析と成績計算に使われます。"
    )

    # 直前の rerun で保存した成功メッセージがあれば表示
    if "_pending_trade_msg" in st.session_state:
        st.success(st.session_state.pop("_pending_trade_msg"))

    with st.form("trade_form", clear_on_submit=True):
        side = st.radio("種別", ["buy", "sell"], horizontal=True,
                        format_func=lambda x: "🟢 買い" if x == "buy" else "🔴 売り")
        col1, col2 = st.columns(2)
        with col1:
            code = st.text_input("銘柄コード *", placeholder="例: 7203")
        with col2:
            name = st.text_input("会社名 *", placeholder="例: トヨタ自動車")
        col3, col4 = st.columns(2)
        with col3:
            shares = st.number_input(
                "株数 *",
                min_value=1,
                value=None,
                step=1,
                placeholder="例: 5",
            )
        with col4:
            price = st.number_input(
                "単価（円）*",
                min_value=1.0,
                value=None,
                step=10.0,
                placeholder="例: 2580",
            )
        trade_date = st.date_input("約定日 *", value=date.today())

        st.caption(
            "※ 全項目（*）の入力後、必ず「記録する」ボタンをクリックしてください。"
            " Enterキーだけでは未入力項目のエラー表示になります。"
        )

        submitted = st.form_submit_button("記録する", type="primary")
        if submitted:
            # 未入力バリデーション（Enterでの誤送信を防ぐ）
            errors: list[str] = []
            if not code or not code.strip():
                errors.append("銘柄コードを入力してください")
            if not name or not name.strip():
                errors.append("会社名を入力してください")
            if shares is None:
                errors.append("株数を入力してください")
            if price is None:
                errors.append("単価を入力してください")

            if errors:
                for err in errors:
                    st.error(err)
            else:
                st.session_state.trades.append({
                    "side": side,
                    "code": code.strip(),
                    "name": name.strip(),
                    "shares": int(shares),
                    "price": float(price),
                    "date": trade_date.isoformat(),
                })
                side_jp = "🟢 買い" if side == "buy" else "🔴 売り"
                st.session_state["_pending_trade_msg"] = (
                    f"{side_jp} {code.strip()} {name.strip()} "
                    f"{int(shares)}株 × {float(price):,.0f}円 を記録しました。"
                    " 📊 保有タブで残高を確認できます。"
                )
                # 保有タブの再計算が反映されるよう再描画
                st.rerun()

    st.divider()
    st.subheader("📜 取引履歴")
    if not st.session_state.trades:
        st.caption("まだ取引履歴はありません。")
    else:
        for t in reversed(st.session_state.trades):
            emoji = "🟢" if t["side"] == "buy" else "🔴"
            st.markdown(
                f"- {emoji} **{t['date']}** {t['code']} {t['name']} "
                f"{t['shares']}株 × {t['price']:,.0f}円"
            )


# ───── 📈 成績タブ ─────────────────────
with tab_stat:
    stats = mock_data.get_performance_stats()

    st.subheader("📈 AI推奨の成績")

    if not stats["total_recommendations"]:
        st.info(
            "データ蓄積中です。1〜2週間運用後に勝率・平均リターン等が表示されます。"
        )
    else:
        col1, col2 = st.columns(2)
        col1.metric("累計推奨", f"{stats['total_recommendations']}件")
        col2.metric("勝率", f"{stats['win_rate_pct']:.0f}%" if stats['win_rate_pct'] is not None else "—")
        col3, col4 = st.columns(2)
        col3.metric("平均リターン", f"{stats['avg_return_pct']:+.2f}%" if stats['avg_return_pct'] is not None else "—")
        col4.metric("最大ドローダウン", f"{stats['max_drawdown_pct']:.2f}%" if stats['max_drawdown_pct'] is not None else "—")
        st.caption(f"集計期間: 直近{stats['period_days']}日")

    st.divider()
    st.caption(
        "※ 月次で Claude が過去の推奨を振り返り、"
        "reflections/YYYY-MM.md にレポートを残します（Step 6以降）"
    )
