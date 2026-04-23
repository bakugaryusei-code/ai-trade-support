"""SQLite データベース操作（仕様書§9 準拠）。

テーブル:
  - trades:           ユーザーの取引履歴
  - recommendations:  AI推奨履歴（バッチが書き込み、UIが読み取り）
  - market_overview:  市場概況履歴（同上）
  - candidates:       日次スクリーニング結果
  - technical_cache:  テクニカル指標キャッシュ（MA/RSI）

関数は UI から呼ばれる get_* 系と、バッチから呼ばれる save_* 系で構成。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator

from src.config import DB_PATH


# ─────────────────────────────────────────
# スキーマ
# ─────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    side        TEXT    NOT NULL CHECK(side IN ('buy', 'sell')),
    code        TEXT    NOT NULL,
    name        TEXT,
    shares      INTEGER NOT NULL,
    price       REAL    NOT NULL,
    trade_date  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recommendations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_datetime  TEXT    NOT NULL,
    code            TEXT    NOT NULL,
    name            TEXT,
    recommendation  TEXT    NOT NULL CHECK(recommendation IN ('buy', 'sell', 'hold')),
    tier            TEXT    CHECK(tier IN ('A', 'B', 'C')),
    reasoning_json  TEXT,
    risks_json      TEXT,
    citation_count  INTEGER DEFAULT 0,
    latest_close    REAL,
    market_cap      REAL,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_overview (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_datetime  TEXT    NOT NULL,
    summary         TEXT    NOT NULL,
    search_count    INTEGER DEFAULT 0,
    citation_count  INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_date      TEXT    NOT NULL,
    code            TEXT    NOT NULL,
    name            TEXT,
    market_cap      REAL,
    profit_value    REAL,
    latest_close    REAL,
    disclosed_date  TEXT,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS technical_cache (
    code         TEXT    NOT NULL,
    target_date  TEXT    NOT NULL,
    ma5          REAL,
    ma25         REAL,
    rsi14        REAL,
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, target_date)
);
"""


# ─────────────────────────────────────────
# 接続・初期化
# ─────────────────────────────────────────

def _get_db_path() -> Path:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """テーブルを作成（既存ならスキップ）。起動時に毎回呼んでよい。"""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


# ─────────────────────────────────────────
# 取引（trades）: UIが直接CRUDする
# ─────────────────────────────────────────

def save_trade(trade: dict[str, Any]) -> int:
    """取引を1件保存。

    trade = {"side": "buy"|"sell", "code", "name", "shares", "price", "date"}
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO trades (side, code, name, shares, price, trade_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade["side"],
                trade["code"],
                trade.get("name", ""),
                int(trade["shares"]),
                float(trade["price"]),
                trade.get("date") or trade.get("trade_date"),
            ),
        )
        return cur.lastrowid or 0


def get_trade_history() -> list[dict[str, Any]]:
    """新しい順に取引履歴を返す。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY trade_date DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_trade(trade_id: int) -> None:
    """指定IDの取引を1件削除。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))


def clear_all_trades() -> int:
    """全取引を削除。削除件数を返す。デバッグ/リセット用。"""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM trades")
        return cur.rowcount


def get_holdings() -> list[dict[str, Any]]:
    """取引履歴から保有銘柄を集計（買い増し・売却を反映）。"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY trade_date ASC, id ASC"
        ).fetchall()

    holdings: dict[str, dict[str, Any]] = {}
    for t in rows:
        code = t["code"]
        if code not in holdings:
            holdings[code] = {
                "code": code,
                "name": t["name"] or "",
                "shares": 0,
                "total_cost": 0.0,
            }
        if t["side"] == "buy":
            holdings[code]["shares"] += t["shares"]
            holdings[code]["total_cost"] += t["shares"] * t["price"]
        else:  # sell
            h = holdings[code]
            if h["shares"] > 0:
                avg = h["total_cost"] / h["shares"]
                h["shares"] -= t["shares"]
                h["total_cost"] -= t["shares"] * avg
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
# AI推奨（recommendations）: バッチが書き込み、UIが読む
# ─────────────────────────────────────────

def save_recommendation(rec: dict[str, Any]) -> int:
    """AI推奨を1件保存。reasoning / risks はリストのまま渡してOK。"""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO recommendations
                (batch_datetime, code, name, recommendation, tier,
                 reasoning_json, risks_json, citation_count, latest_close, market_cap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["batch_datetime"],
                rec["code"],
                rec.get("name", ""),
                rec["recommendation"],
                rec.get("tier"),
                json.dumps(rec.get("reasoning", []), ensure_ascii=False),
                json.dumps(rec.get("risks", []), ensure_ascii=False),
                rec.get("citation_count", 0),
                rec.get("latest_close"),
                rec.get("market_cap"),
            ),
        )
        return cur.lastrowid or 0


def get_todays_recommendations() -> list[dict[str, Any]]:
    """最新バッチの推奨一覧を返す（UI 表示用フィールドを補完）。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(batch_datetime) AS m FROM recommendations"
        ).fetchone()
        max_dt = row["m"] if row else None
        if not max_dt:
            return []
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE batch_datetime = ? ORDER BY id",
            (max_dt,),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["reasoning"] = json.loads(d.pop("reasoning_json") or "[]")
        d["risks"] = json.loads(d.pop("risks_json") or "[]")
        d["market_cap_oku"] = (d.get("market_cap") or 0) / 1e8
        d["updated_at"] = d.get("batch_datetime", "")
        result.append(d)
    return result


# ─────────────────────────────────────────
# 市場概況（market_overview）
# ─────────────────────────────────────────

def save_market_overview(overview: dict[str, Any]) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO market_overview
                (batch_datetime, summary, search_count, citation_count)
            VALUES (?, ?, ?, ?)
            """,
            (
                overview["batch_datetime"],
                overview["summary"],
                overview.get("search_count", 0),
                overview.get("citation_count", 0),
            ),
        )
        return cur.lastrowid or 0


def get_latest_market_overview() -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM market_overview "
            "ORDER BY batch_datetime DESC, id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["updated_at"] = d.get("batch_datetime", d.get("created_at", ""))
    return d


# ─────────────────────────────────────────
# 候補銘柄（candidates）
# ─────────────────────────────────────────

def save_candidates(batch_date: str, candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO candidates
                (batch_date, code, name, market_cap, profit_value,
                 latest_close, disclosed_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch_date,
                    c.get("code"),
                    c.get("name", ""),
                    c.get("market_cap"),
                    c.get("profit_value"),
                    c.get("latest_close"),
                    c.get("disclosed_date"),
                )
                for c in candidates
            ],
        )


# ─────────────────────────────────────────
# テクニカル指標キャッシュ
# ─────────────────────────────────────────

def save_technical_cache(code: str, target_date: str, indicators: dict[str, float]) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO technical_cache
                (code, target_date, ma5, ma25, rsi14)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                code,
                target_date,
                indicators.get("ma5"),
                indicators.get("ma25"),
                indicators.get("rsi14"),
            ),
        )


# ─────────────────────────────────────────
# 成績統計
# ─────────────────────────────────────────

def get_performance_stats(days: int = 30) -> dict[str, Any]:
    """AI推奨の成績集計。

    Note: N日前の推奨 vs 現在価格で勝率計算するには、現在価格取得インフラが必要。
    ここではまず「累計推奨数」だけ返す。詳細成績は Step 8（運用後）で実装。
    """
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM recommendations").fetchone()
        total = row["c"] if row else 0
    return {
        "total_recommendations": total,
        "win_rate_pct": None,
        "avg_return_pct": None,
        "max_drawdown_pct": None,
        "period_days": days,
    }


def get_realized_profit_summary() -> dict[str, Any]:
    """取引履歴から実現損益を集計。

    売却時に「(売価 − 平均取得単価) × 売却株数」を実現損益として加算する。
    買い→売りの順で平均取得単価を都度更新し、FIFO的ではなく加重平均法で計算。

    Returns:
        {
          "total_realized_pl": 累計の実現損益（円）,
          "trade_count":       取引総数,
          "sell_count":        売却取引の数,
          "by_code":           銘柄別の {code, name, realized_pl}（損益ゼロは除外）,
        }
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY trade_date ASC, id ASC"
        ).fetchall()

    state: dict[str, dict[str, Any]] = {}  # code -> shares, total_cost, name
    realized: dict[str, float] = {}
    sell_count = 0

    for t in rows:
        code = t["code"]
        if code not in state:
            state[code] = {"shares": 0, "total_cost": 0.0, "name": t["name"] or ""}
            realized[code] = 0.0
        s = state[code]
        if t["side"] == "buy":
            s["shares"] += t["shares"]
            s["total_cost"] += t["shares"] * t["price"]
        else:  # sell
            if s["shares"] > 0:
                avg_cost = s["total_cost"] / s["shares"]
                profit = (t["price"] - avg_cost) * t["shares"]
                realized[code] += profit
                s["shares"] -= t["shares"]
                s["total_cost"] -= t["shares"] * avg_cost
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
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM recommendations").fetchone()
        if row and row["c"] > 0:
            return

    # 遅延 import（循環参照回避）
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
