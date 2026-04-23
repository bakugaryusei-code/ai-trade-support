"""Streamlit UI 開発中の仮データ。

Step 6 で SQLite 実装完了後、この関数群の中身を DB アクセスに置き換える。
呼び出し側（app.py）のインターフェースは変えない。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


def get_latest_market_overview() -> dict[str, Any]:
    """最新の市場概況。"""
    return {
        "summary": (
            "**日経平均**: 59,140円（前日比 -0.6%、4日続落）。一時場中6万円台を記録したが、"
            "利益確定売りで反落。**TOPIX**: 3,716pt。**NT倍率**: 15.91倍（過去最高）。\n\n"
            "**上昇セクター**: 半導体・AI関連（東エレク、SBG、アドバンテスト）。"
            "**下落セクター**: 鉱業（-4.3%）、輸出関連（中東情勢リスク）。\n\n"
            "**注目材料**: ①企業決算集計で2025年度は2.7%増収・4.3%経常増益、"
            "②TSIホールディングス（3608）が自社株買い・増配発表で+27.4%、"
            "③ホルムズ海峡の地政学リスク継続。"
        ),
        "updated_at": "2026-04-23 08:00",
        "search_count": 3,
        "citation_count": 16,
    }


def get_todays_recommendations() -> list[dict[str, Any]]:
    """今日のAI推奨（Tier A 銘柄）。"""
    return [
        {
            "code": "6758",
            "name": "ソニーグループ",
            "recommendation": "buy",
            "latest_close": 13000.0,
            "market_cap_oku": 150000,
            "reasoning": [
                "3Q決算が大幅上振れ、通期を2度上方修正（9,477億円→1兆1,300億円へ7.6%上方修正）",
                "アナリストコンセンサスは「強気買い」、平均目標株価は現在値から約46%上",
                "PER 16.8倍 / PBR 2.33倍 / 予想ROE 14.7% で割安感あり",
                "本決算発表が5月中旬に予定、決算カタリストが近い",
            ],
            "risks": [
                "3Q単体で親会社帰属利益が赤字転落（ソニー生命関連の一時的損失）",
                "NT倍率15.91（過去最高）による需給悪化の可能性",
                "円高・地政学リスクによる輸出企業への逆風",
            ],
            "citation_count": 7,
            "updated_at": "2026-04-23 08:05",
        },
        {
            "code": "8306",
            "name": "三菱UFJフィナンシャル",
            "recommendation": "hold",
            "latest_close": 1800.0,
            "market_cap_oku": 200000,
            "reasoning": [
                "金利上昇局面で銀行株は中期的に追い風",
                "直近はNT倍率高騰の反動でバリュー株に買い戻しの可能性",
                "本決算（5月）まで材料待ちの状況",
            ],
            "risks": [
                "本決算次第で不良債権比率の悪化リスク",
                "米金利動向の不確実性",
            ],
            "citation_count": 5,
            "updated_at": "2026-04-23 08:10",
        },
    ]


def get_holdings() -> list[dict[str, Any]]:
    """保有銘柄（現時点では空）。"""
    return []


def get_trade_history() -> list[dict[str, Any]]:
    """取引履歴（現時点では空）。"""
    return []


def get_performance_stats() -> dict[str, Any]:
    """AI推奨の成績統計。"""
    return {
        "total_recommendations": 0,
        "win_rate_pct": None,      # 未計算（データ不足）
        "avg_return_pct": None,
        "max_drawdown_pct": None,
        "period_days": 0,
    }


def add_trade(trade: dict[str, Any]) -> None:
    """取引を記録する（Step 6 で SQLite INSERT に置き換え）。"""
    # Streamlit の session_state から読み書きする簡易実装は app.py 側で行う
    pass
