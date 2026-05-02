"""Supabase をバックエンドにしたデータアクセス層（Phase 2 で SQLite から移行）。

仕様書§9 のスキーマを Supabase（クラウド PostgreSQL）で実装。
Streamlit Cloud のファイルシステム消失問題を回避するため、永続化先を外部DBに移行。

同じ public 関数シグネチャを維持するので、呼び出し側（app.py / run_batch.py）の変更は不要。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from supabase import Client, create_client

from src.secrets_loader import get_secret

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# クライアント（プロセス内シングルトン）
# ─────────────────────────────────────────

_client: Client | None = None


def _get_client() -> Client:
    """Supabase クライアントを取得（必要時のみ初期化）。"""
    global _client
    if _client is None:
        url = get_secret("SUPABASE_URL")
        # 書き込みも行うため service_role キーを使う（RLS無効でシンプル運用）
        key = get_secret("SUPABASE_SERVICE")
        _client = create_client(url, key)
    return _client


def init_db() -> None:
    """互換のためのスタブ。Supabase ではテーブル作成は SQL Editor で済んでいる。"""
    return


# ─────────────────────────────────────────
# 取引（trades）
# ─────────────────────────────────────────

def save_trade(trade: dict[str, Any]) -> int:
    """取引を1件保存し、新規 id を返す。"""
    client = _get_client()
    payload = {
        "side": trade["side"],
        "code": trade["code"],
        "name": trade.get("name", ""),
        "shares": int(trade["shares"]),
        "price": float(trade["price"]),
        "trade_date": trade.get("date") or trade.get("trade_date"),
    }
    response = client.table("trades").insert(payload).execute()
    return int(response.data[0]["id"]) if response.data else 0


def get_trade_history() -> list[dict[str, Any]]:
    """新しい順に取引履歴を返す。"""
    client = _get_client()
    response = (
        client.table("trades")
        .select("*")
        .order("trade_date", desc=True)
        .order("id", desc=True)
        .execute()
    )
    return response.data or []


def delete_trade(trade_id: int) -> None:
    """指定 id の取引を削除。"""
    client = _get_client()
    client.table("trades").delete().eq("id", trade_id).execute()


def clear_all_trades() -> int:
    """全取引を削除。削除件数を返す。"""
    client = _get_client()
    count_response = client.table("trades").select("id", count="exact").execute()
    count = count_response.count or 0
    if count > 0:
        # id != 0 で実質全件削除
        client.table("trades").delete().neq("id", 0).execute()
    return count


def get_holdings() -> list[dict[str, Any]]:
    """取引履歴から保有銘柄を集計（買い増し・売却を反映）。"""
    client = _get_client()
    response = (
        client.table("trades")
        .select("*")
        .order("trade_date", desc=False)
        .order("id", desc=False)
        .execute()
    )
    rows = response.data or []

    holdings: dict[str, dict[str, Any]] = {}
    for t in rows:
        code = t["code"]
        shares = int(t["shares"])
        price = float(t["price"])
        if code not in holdings:
            holdings[code] = {
                "code": code,
                "name": t["name"] or "",
                "shares": 0,
                "total_cost": 0.0,
            }
        if t["side"] == "buy":
            holdings[code]["shares"] += shares
            holdings[code]["total_cost"] += shares * price
        else:  # sell
            h = holdings[code]
            if h["shares"] > 0:
                avg = h["total_cost"] / h["shares"]
                h["shares"] -= shares
                h["total_cost"] -= shares * avg
        if holdings[code]["shares"] <= 0:
            holdings.pop(code, None)

    return [
        {
            "code": h["code"],
            "name": h["name"],
            "shares": h["shares"],
            "avg_cost": h["total_cost"] / h["shares"] if h["shares"] > 0 else 0,
        }
        for h in holdings.values()
    ]


# ─────────────────────────────────────────
# AI推奨（recommendations）
# ─────────────────────────────────────────

def save_recommendation(rec: dict[str, Any]) -> int:
    client = _get_client()
    payload = {
        "batch_datetime": rec["batch_datetime"],
        "code": rec["code"],
        "name": rec.get("name", ""),
        "recommendation": rec["recommendation"],
        "tier": rec.get("tier"),
        "reasoning_json": json.dumps(rec.get("reasoning", []), ensure_ascii=False),
        "risks_json": json.dumps(rec.get("risks", []), ensure_ascii=False),
        "citation_count": rec.get("citation_count", 0),
        "latest_close": rec.get("latest_close"),
        "market_cap": rec.get("market_cap"),
    }
    response = client.table("recommendations").insert(payload).execute()
    return int(response.data[0]["id"]) if response.data else 0


def get_latest_batch_datetime() -> str | None:
    """recommendations テーブルから最新の batch_datetime を返す（軽量1行クエリ）。

    アプリのヘッダー「最終バッチ」表示用。市場概況は朝のみ更新だが、
    recommendations は朝・昼・夕の3バッチすべてで更新されるため、
    こちらを参照する方が「直近のデータ更新時刻」として正確。

    Returns:
        "YYYY-MM-DD HH:MM" 形式の文字列。未投入時は None。
    """
    client = _get_client()
    response = (
        client.table("recommendations")
        .select("batch_datetime")
        .order("batch_datetime", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0].get("batch_datetime")


def get_todays_recommendations() -> list[dict[str, Any]]:
    """最新バッチの推奨一覧を返す。"""
    client = _get_client()
    latest = (
        client.table("recommendations")
        .select("batch_datetime")
        .order("batch_datetime", desc=True)
        .limit(1)
        .execute()
    )
    if not latest.data:
        return []
    max_dt = latest.data[0]["batch_datetime"]

    response = (
        client.table("recommendations")
        .select("*")
        .eq("batch_datetime", max_dt)
        .order("id", desc=False)
        .execute()
    )

    result = []
    for r in response.data or []:
        d = dict(r)
        d["reasoning"] = json.loads(d.pop("reasoning_json", None) or "[]")
        d["risks"] = json.loads(d.pop("risks_json", None) or "[]")
        d["market_cap_oku"] = (d.get("market_cap") or 0) / 1e8
        d["updated_at"] = d.get("batch_datetime", "")
        result.append(d)
    return result


# ─────────────────────────────────────────
# 市場概況（market_overview）
# ─────────────────────────────────────────

def save_market_overview(overview: dict[str, Any]) -> int:
    client = _get_client()
    payload = {
        "batch_datetime": overview["batch_datetime"],
        "summary": overview["summary"],
        "search_count": overview.get("search_count", 0),
        "citation_count": overview.get("citation_count", 0),
    }
    response = client.table("market_overview").insert(payload).execute()
    return int(response.data[0]["id"]) if response.data else 0


def get_latest_market_overview() -> dict[str, Any] | None:
    client = _get_client()
    response = (
        client.table("market_overview")
        .select("*")
        .order("batch_datetime", desc=True)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    d = dict(response.data[0])
    d["updated_at"] = d.get("batch_datetime", "")
    return d


# ─────────────────────────────────────────
# 候補銘柄（candidates）
# ─────────────────────────────────────────

def save_candidates(batch_date: str, candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    client = _get_client()
    payloads = [
        {
            "batch_date": batch_date,
            "code": c.get("code"),
            "name": c.get("name", ""),
            "market_cap": c.get("market_cap"),
            "profit_value": str(c.get("profit_value")) if c.get("profit_value") is not None else None,
            "latest_close": c.get("latest_close"),
            "disclosed_date": c.get("disclosed_date"),
        }
        for c in candidates
    ]
    client.table("candidates").insert(payloads).execute()


# ─────────────────────────────────────────
# テクニカル指標キャッシュ
# ─────────────────────────────────────────

def save_technical_cache(code: str, target_date: str, indicators: dict[str, float]) -> None:
    client = _get_client()
    payload = {
        "code": code,
        "target_date": target_date,
        "ma5": indicators.get("ma5"),
        "ma25": indicators.get("ma25"),
        "rsi14": indicators.get("rsi14"),
    }
    client.table("technical_cache").upsert(payload).execute()


# ─────────────────────────────────────────
# 成績統計
# ─────────────────────────────────────────

def get_performance_stats(days: int = 30) -> dict[str, Any]:
    client = _get_client()
    response = client.table("recommendations").select("id", count="exact").execute()
    total = response.count or 0
    return {
        "total_recommendations": total,
        "win_rate_pct": None,
        "avg_return_pct": None,
        "max_drawdown_pct": None,
        "period_days": days,
    }


def get_realized_profit_summary() -> dict[str, Any]:
    """取引履歴から実現損益を集計（加重平均法）。"""
    client = _get_client()
    response = (
        client.table("trades")
        .select("*")
        .order("trade_date", desc=False)
        .order("id", desc=False)
        .execute()
    )
    rows = response.data or []

    state: dict[str, dict[str, Any]] = {}
    realized: dict[str, float] = {}
    sell_count = 0

    for t in rows:
        code = t["code"]
        shares = int(t["shares"])
        price = float(t["price"])
        if code not in state:
            state[code] = {"shares": 0, "total_cost": 0.0, "name": t["name"] or ""}
            realized[code] = 0.0
        s = state[code]
        if t["side"] == "buy":
            s["shares"] += shares
            s["total_cost"] += shares * price
        else:  # sell
            if s["shares"] > 0:
                avg_cost = s["total_cost"] / s["shares"]
                profit = (price - avg_cost) * shares
                realized[code] += profit
                s["shares"] -= shares
                s["total_cost"] -= shares * avg_cost
                sell_count += 1

    by_code = [
        {"code": code, "name": state[code]["name"], "realized_pl": pl}
        for code, pl in realized.items()
        if pl != 0
    ]
    return {
        "total_realized_pl": sum(realized.values()),
        "trade_count": len(rows),
        "sell_count": sell_count,
        "by_code": by_code,
    }


# ─────────────────────────────────────────
# 初期シード（DB が空のとき mock_data を投入）
# ─────────────────────────────────────────

def seed_if_empty() -> None:
    """recommendations が空なら mock_data から初期データを投入。"""
    client = _get_client()
    response = (
        client.table("recommendations").select("id", count="exact").limit(1).execute()
    )
    if (response.count or 0) > 0:
        return

    from src import mock_data

    batch_dt = datetime.now().strftime("%Y-%m-%d %H:%M")

    ov = mock_data.get_latest_market_overview()
    save_market_overview({
        "batch_datetime": batch_dt,
        "summary": ov["summary"],
        "search_count": ov.get("search_count", 0),
        "citation_count": ov.get("citation_count", 0),
    })

    for rec in mock_data.get_todays_recommendations():
        save_recommendation({
            "batch_datetime": batch_dt,
            "code": rec["code"],
            "name": rec["name"],
            "recommendation": rec["recommendation"],
            "tier": "A",
            "reasoning": rec["reasoning"],
            "risks": rec["risks"],
            "citation_count": rec.get("citation_count", 0),
            "latest_close": rec.get("latest_close"),
            "market_cap": (rec.get("market_cap_oku") or 0) * 1e8,
        })
