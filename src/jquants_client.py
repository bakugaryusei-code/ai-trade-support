"""J-Quants API V2 クライアント。

認証：ダッシュボードで発行した APIキーを `x-api-key` ヘッダーで送るだけ。
V1 の refresh_token → id_token 変換フローは V2 では不要（廃止）。

レスポンスは統一形式：
  {"data": [...], "pagination_key": "..."}

無料プランはレートリミット 5 req/分・データ 12週間遅延に注意。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import requests

from src.config import JQUANTS_BASE_URL
from src.secrets_loader import get_secret

logger = logging.getLogger(__name__)


class JQuantsClient:
    """J-Quants API V2 の軽量クライアント。

    Example:
        client = JQuantsClient()
        stocks = client.get_listed_info(code="7203")      # トヨタ自動車
        quotes = client.get_daily_quotes(code="7203")
        summary = client.get_financial_summary(code="7203")
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or get_secret("JQUANTS_API_KEY")

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """V2認証付きGETリクエスト（1回のみ）。"""
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

        Args:
            code: 指定すればその1銘柄のみ。省略時は全銘柄（約4,000件）。

        Returns:
            銘柄情報のリスト。Code / CompanyName / MarketCode / Sector17Code など。
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

        Args:
            code: 銘柄コード（例: "7203"）。
            from_date: 開始日（省略時は30日前）。
            to_date: 終了日（省略時は今日）。

        Returns:
            日次株価のリスト。V2のカラム名は短縮形：
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

    def get_financial_summary(self, code: str) -> list[dict[str, Any]]:
        """財務サマリーを取得（V2: /fins/summary、旧 /v1/fins/statements 相当）。

        黒字判定・時価総額推定・決算タイミングの把握に使う。
        """
        params = {"code": code}
        return self._get_all("/fins/summary", params)
