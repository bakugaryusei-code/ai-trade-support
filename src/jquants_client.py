"""J-Quants API V2 クライアント。

認証：ダッシュボードで発行した APIキーを `x-api-key` ヘッダーで送るだけ。
V1 の refresh_token → id_token 変換フローは V2 では不要（廃止）。

レスポンスは統一形式：
  {"data": [...], "pagination_key": "..."}

プラン別レートリミット（5〜500 req/分）を自動で守る。
無料プランはデータ 12週間遅延あり。
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import date, timedelta
from typing import Any

import requests

from src.config import JQUANTS_BASE_URL, JQUANTS_RATE_LIMIT_PER_MIN
from src.secrets_loader import get_secret

logger = logging.getLogger(__name__)


class JQuantsClient:
    """J-Quants API V2 の軽量クライアント（レートリミット自動調整つき）。

    Example:
        client = JQuantsClient()                  # デフォルトは Free プラン
        client = JQuantsClient(plan="Light")      # Light 契約時
        stocks = client.get_listed_info(code="7203")
    """

    # J-Quants 側のカウント誤差を吸収するバッファ（秒）
    _WINDOW_SEC = 60.0
    _WINDOW_BUFFER_SEC = 5.0
    # コール間最小間隔への上乗せバッファ（秒）。
    # スライディングウィンドウ（_WINDOW_SEC + _WINDOW_BUFFER_SEC = 65秒に
    # rate_limit 件まで）が既に rate_limit×60/65 ≒ 92% の上限を作っているため、
    # 追加バッファは「ネットワーク遅延ゆらぎでサーバ側のカウントが分跨ぎする」
    # 程度の安全マージンで十分。0.2秒なら全プランで rate_limit の 80〜92% を出せる。
    # 旧値 2.0 は Free プラン（5/分=12秒/コール）想定で過大、Light（1秒/コール）
    # では実効レートを 1/3 に絞り込んでいた。
    _MIN_INTERVAL_BUFFER_SEC = 0.2

    def __init__(self, api_key: str | None = None, plan: str = "Free") -> None:
        self._api_key = api_key or get_secret("JQUANTS_API_KEY")
        self._rate_limit = JQUANTS_RATE_LIMIT_PER_MIN.get(plan, 5)
        # コール間の最小間隔（秒）。バーストを防ぎ均等配分する。
        self._min_interval = (60.0 / self._rate_limit) + self._MIN_INTERVAL_BUFFER_SEC
        self._last_call_time = 0.0
        # 直近 (60+buffer) 秒間のAPI呼び出し時刻を保持
        self._call_times: deque[float] = deque()

    def _throttle(self) -> None:
        """2段階のレートリミッタ。

        1. **最小間隔**：前回コールから最低 self._min_interval 秒空ける（バースト防止）
        2. **スライディングウィンドウ**：直近 (60+buffer) 秒間の呼び出し数を rate_limit 以下に保つ
        両方の条件を満たすまで待機する。
        """
        window = self._WINDOW_SEC + self._WINDOW_BUFFER_SEC

        # ── 1. 最小間隔チェック（バースト防止） ──
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        # ── 2. スライディングウィンドウチェック ──
        now = time.time()
        while self._call_times and now - self._call_times[0] > window:
            self._call_times.popleft()

        if len(self._call_times) >= self._rate_limit:
            oldest = self._call_times[0]
            wait_for = (oldest + window) - now
            if wait_for > 0:
                time.sleep(wait_for)
            now = time.time()
            while self._call_times and now - self._call_times[0] > window:
                self._call_times.popleft()

        call_time = time.time()
        self._last_call_time = call_time
        self._call_times.append(call_time)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """V2認証付きGETリクエスト（レートリミット遵守つき）。"""
        self._throttle()
        url = f"{JQUANTS_BASE_URL}{path}"
        response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """pagination_key を使い全ページを取得して data を結合する。"""
        merged: list[dict[str, Any]] = []
        current_params = dict(params) if params else {}
        while True:
            body = self._get(path, current_params)
            merged.extend(body.get("data", []))
            pagination_key = body.get("pagination_key")
            if not pagination_key:
                break
            current_params["pagination_key"] = pagination_key
        return merged

    def get_listed_info(self, code: str | None = None) -> list[dict[str, Any]]:
        """上場銘柄一覧を取得（V2: /equities/master）。

        Returns:
            銘柄情報のリスト。主要フィールド：
              Code / CoName / CoNameEn / Mkt / MktNm /
              S17 / S17Nm / S33 / S33Nm / ScaleCat / Mrgn / MrgnNm
        """
        params = {"code": code} if code else None
        return self._get_all("/equities/master", params)

    def get_daily_quotes(
        self,
        code: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """指定銘柄の日次株価を取得（V2: /equities/bars/daily）。

        V2 のカラム名は短縮形：
          Date / Code / O(始値) / H(高値) / L(安値) / C(終値) /
          Vo(出来高) / Va(売買代金) / AdjC(調整後終値) など。
        """
        if to_date is None:
            to_date = date.today()
        if from_date is None:
            from_date = to_date - timedelta(days=30)

        params = {
            "code": code,
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }
        return self._get_all("/equities/bars/daily", params)

    def get_daily_quotes_by_date(
        self,
        target_date: date,
    ) -> list[dict[str, Any]]:
        """指定日の **全上場銘柄** の日次株価を一括取得（V2: /equities/bars/daily）。

        V2 ドキュメントによれば、`/equities/bars/daily` は `code` または `date` の
        どちらかが必須で、`date` 単独指定時は全上場銘柄のデータが返る。
        pagination_key で複数ページに分割されるため `_get_all` で結合する。

        ※ 営業日でない日付（週末・祝日）を指定すると空配列が返る。
          呼び出し側で直近営業日まで遡る等のリトライ制御が必要。

        Args:
            target_date: 取得対象の日付。

        Returns:
            その日の全銘柄の株価データ。フィールドは get_daily_quotes と同じ。
        """
        params = {"date": target_date.strftime("%Y-%m-%d")}
        return self._get_all("/equities/bars/daily", params)

    def get_financial_summary(self, code: str) -> list[dict[str, Any]]:
        """財務サマリーを取得（V2: /fins/summary）。

        主要フィールド：
          DiscDate / Code / DocType / CurFYEn /
          Sales / OP / NP(純利益) / EPS / TA / Eq / EqAR /
          FNP(予想純利益) / ShOutFY(期末発行済株式数) など。
        """
        params = {"code": code}
        return self._get_all("/fins/summary", params)
