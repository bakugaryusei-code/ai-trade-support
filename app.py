"""Streamlit ダッシュボード本体（スマホ優先レイアウト）。

仕様書§3 / §11 準拠：
  - 縦スクロール・大きめフォント
  - センター寄せ単一カラム
  - タブで4セクション切り替え（推奨 / 保有 / 記録 / 成績）

データは SQLite（src.db）で永続化。DB が空のときは src.mock_data から初期投入。
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from src import db

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
# DB 初期化（テーブル作成 + 初回はシード）
# ─────────────────────────────────────────

db.init_db()
db.seed_if_empty()


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


# ─────────────────────────────────────────
# ヘッダー
# ─────────────────────────────────────────

st.title("📈 AIトレードサポート")

# 「最終更新」は datetime.now()（ページを開いた時刻）ではなく、
# 実際にバッチが走ってデータが書き込まれた時刻（Supabase recommendations の
# 最新 batch_datetime）を表示する。リロードしても更新されないため
# データの新しさを正しく反映できる。
_last_batch = db.get_latest_batch_datetime()
c1, c2 = st.columns(2)
c1.caption(f"最終バッチ: {_last_batch or '—'}")
c2.caption("🪙 J-Quants: Light / 当日データ")


# ─────────────────────────────────────────
# タブ
# ─────────────────────────────────────────

tab_reco, tab_hold, tab_log, tab_stat = st.tabs(
    ["🎯 推奨", "📊 保有", "📝 記録", "📈 成績"]
)


# ───── 🎯 推奨タブ ─────────────────────
with tab_reco:
    overview = db.get_latest_market_overview()

    with st.expander("🌏 今日の市場概況", expanded=True):
        if overview:
            st.markdown(overview["summary"])
            st.caption(
                f"更新: {overview.get('updated_at', '')}｜"
                f"Web検索 {overview.get('search_count', 0)}件 / "
                f"引用 {overview.get('citation_count', 0)}件"
            )
        else:
            st.info("市場概況はまだ取得されていません（次の朝のバッチ 8時 で生成されます）。")

    st.subheader("🎯 今日のAI推奨")

    # 推奨枠は Tier A（Claude Sonnet が Web検索付きで詳細分析した銘柄）のみ表示。
    # Tier B/C も Supabase には保存されているが、根拠は Haiku の30字理由のみで
    # 詳細分析を経ていないため、推奨枠に並べるとミスリーディング。
    # Tier B/C の一覧表示が必要になったら別タブ・別画面で扱う方針（別件）。
    all_recs = db.get_todays_recommendations()
    recs = [r for r in all_recs if r.get("tier") == "A"]

    if not recs:
        if all_recs:
            st.info(
                f"今日のバッチでは Tier A（詳細分析対象）に該当する銘柄がありませんでした。"
                f"（候補のスクリーニング結果は {len(all_recs)} 件あります。）"
            )
        else:
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
    st.subheader("📊 保有銘柄")

    holdings = db.get_holdings()
    if not holdings:
        st.info(
            "現在、保有銘柄はありません。\n\n"
            "買い注文したら「📝 記録」タブから取引を記録してください。"
        )
    else:
        for h in holdings:
            with st.container(border=True):
                st.markdown(f"### {h['code']} {h['name']}")
                col1, col2 = st.columns(2)
                col1.metric("保有株数", f"{h['shares']}株")
                col2.metric("平均取得単価", f"{h['avg_cost']:,.0f}円")
                st.caption(
                    "含み損益は現在値取得のインフラ実装後に表示されます（運用開始時に対応）"
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

    # フォームバージョン（成功時に +1 することで次の描画を空フォーム化）
    if "trade_form_v" not in st.session_state:
        st.session_state.trade_form_v = 0
    _v = st.session_state.trade_form_v

    with st.form(f"trade_form_v{_v}", clear_on_submit=False):
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
            " エラー時は入力値が保持されるので、足りない項目だけ埋めて再送信できます。"
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
                # バージョン据え置き → 同じフォーム扱い → 入力値が保持される
            else:
                db.save_trade({
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
                # バージョン +1 で次回は新しいフォームキー → 自動で空フォームに
                st.session_state.trade_form_v += 1
                st.rerun()

    st.divider()
    st.subheader("📜 取引履歴")
    trades = db.get_trade_history()
    if not trades:
        st.caption("まだ取引履歴はありません。")
    else:
        for t in trades:
            emoji = "🟢" if t["side"] == "buy" else "🔴"
            c_text, c_btn = st.columns([5, 1])
            with c_text:
                st.markdown(
                    f"{emoji} **{t['trade_date']}** {t['code']} {t.get('name', '')} "
                    f"{t['shares']}株 × {t['price']:,.0f}円"
                )
            with c_btn:
                if st.button("🗑️", key=f"del_trade_{t['id']}", help="この記録を削除"):
                    db.delete_trade(t["id"])
                    st.session_state["_pending_trade_msg"] = (
                        f"取引を削除しました: {t['code']} {t['shares']}株 × "
                        f"{t['price']:,.0f}円（{t['trade_date']}）"
                    )
                    st.rerun()

        # ── 危険ゾーン：全削除 ──────────────────
        with st.expander("⚠️ 危険ゾーン：取引履歴を全削除"):
            st.caption(
                "テストデータの一括クリアや、SBI連動の手動入力をやり直したいときに使います。"
                " 削除した取引は復元できません。"
            )
            confirm = st.checkbox(
                "本当に全件削除することを理解しました",
                key="confirm_clear_trades",
            )
            disabled = not confirm
            if st.button(
                "🗑️ 全取引履歴を削除",
                type="secondary",
                disabled=disabled,
                key="btn_clear_trades",
            ):
                deleted = db.clear_all_trades()
                st.session_state["_pending_trade_msg"] = (
                    f"🗑️ 取引履歴を全削除しました（{deleted}件）"
                )
                st.rerun()


# ───── 📈 成績タブ ─────────────────────
with tab_stat:
    st.subheader("💰 実現損益（あなたの売買成績）")

    pl = db.get_realized_profit_summary()
    if pl["sell_count"] == 0:
        st.info("売却がまだないため、実現損益は未計上です。")
    else:
        c1, c2 = st.columns(2)
        total_pl = pl["total_realized_pl"]
        c1.metric(
            "実現損益（累計）",
            f"{total_pl:+,.0f}円",
            delta=f"{total_pl:+,.0f}" if total_pl else None,
        )
        c2.metric("売却回数", f"{pl['sell_count']}回")

        if pl["by_code"]:
            st.markdown("**銘柄別の実現損益**")
            for item in pl["by_code"]:
                sign = "🟢" if item["realized_pl"] > 0 else "🔴"
                st.markdown(
                    f"- {sign} **{item['code']} {item['name']}**: "
                    f"{item['realized_pl']:+,.0f}円"
                )

    st.divider()
    st.subheader("🤖 AI推奨の成績")

    stats = db.get_performance_stats()
    if not stats["total_recommendations"]:
        st.info("データ蓄積中です。1〜2週間の運用後に勝率・平均リターンが表示されます。")
    else:
        col1, col2 = st.columns(2)
        col1.metric("累計推奨", f"{stats['total_recommendations']}件")
        col2.metric(
            "勝率",
            f"{stats['win_rate_pct']:.0f}%" if stats["win_rate_pct"] is not None else "—",
        )
        col3, col4 = st.columns(2)
        col3.metric(
            "平均リターン",
            f"{stats['avg_return_pct']:+.2f}%" if stats["avg_return_pct"] is not None else "—",
        )
        col4.metric(
            "最大ドローダウン",
            f"{stats['max_drawdown_pct']:.2f}%" if stats["max_drawdown_pct"] is not None else "—",
        )
        st.caption(f"集計期間: 直近{stats['period_days']}日")

    st.divider()
    st.caption(
        "※ 月次で Claude が過去の推奨を振り返り、"
        "`reflections/YYYY-MM.md` にレポートを残します（運用開始後に実装予定）。"
    )
