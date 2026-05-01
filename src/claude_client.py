"""Claude API クライアント。

用途別に Haiku（軽量・安い）と Sonnet（詳細分析）を使い分ける。
Web検索ツール（Anthropic提供のサーバーサイドツール）にも対応。
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from anthropic import Anthropic

from src.config import MODEL_HEAVY, MODEL_LIGHT
from src.secrets_loader import get_secret

logger = logging.getLogger(__name__)


# ─── Anthropic 公式価格表（USD / 1M トークン）2026-04 時点 ───
# https://platform.claude.com/docs/en/about-claude/models/overview
_PRICE_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # model_id_prefix: (input_$/1M, output_$/1M)
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
}


def _price_for(model: str) -> tuple[float, float]:
    """モデルIDから (input単価, output単価) を返す。未知モデルは Sonnet 価格でフォールバック。"""
    for prefix, price in _PRICE_USD_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return _PRICE_USD_PER_MTOK["claude-sonnet-4-6"]


def _log_usage(model: str, usage: Any) -> None:
    """1リクエストの input / output トークン数とコスト概算を INFO ログに出す。

    Anthropic SDK は response.usage に input_tokens / output_tokens / cache_*
    を返す。後段でログを集計してバッチ全体のコスト概算を取れるよう、
    1行で機械可読な形で出力する。
    """
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    in_price, out_price = _price_for(model)
    cost_usd = in_tok * in_price / 1e6 + out_tok * out_price / 1e6
    logger.info(
        f"[claude_usage] model={model} input={in_tok} output={out_tok} "
        f"cache_create={cache_create} cache_read={cache_read} "
        f"cost_usd={cost_usd:.4f}"
    )


class WebSearchResult(TypedDict):
    """`ask_with_web_search` の返却型。"""

    text: str
    citations: list[dict[str, Any]]  # {url, title, cited_text}
    search_queries: list[str]


class ClaudeClient:
    """Claude API の軽量クライアント。

    Example:
        client = ClaudeClient()
        text = client.ask("こんにちは")
        text = client.ask("詳細分析お願い", heavy=True)  # Sonnet で呼ぶ
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._client = Anthropic(api_key=api_key or get_secret("ANTHROPIC_API_KEY"))

    def ask(
        self,
        prompt: str,
        *,
        heavy: bool = False,
        system: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """1往復のやり取り。プレーンなテキスト返却。

        Args:
            prompt: ユーザーメッセージ（テキスト）。
            heavy: True なら Sonnet、False なら Haiku を使う。
            system: 任意のシステムプロンプト。
            max_tokens: 応答の最大トークン数。
        """
        model = MODEL_HEAVY if heavy else MODEL_LIGHT
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        # 大きい max_tokens の場合、Anthropic SDK は「10分超のリクエストは
        # ストリーミング必須」というガードで ValueError を投げる。Tier分類は
        # 単発の同期呼び出しのため、timeout を明示的に20分まで延ばして
        # 非ストリーミングを継続する（実測では Haiku 32K で 30-90秒程度）。
        client = (
            self._client.with_options(timeout=1200.0)
            if max_tokens > 16000
            else self._client
        )
        response = client.messages.create(**kwargs)
        _log_usage(model, response.usage)
        # 応答は content blocks のリスト。テキストだけを結合して返す。
        parts: list[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)

    def ask_with_web_search(
        self,
        prompt: str,
        *,
        heavy: bool = True,
        system: str | None = None,
        max_tokens: int = 4096,
        max_searches: int = 3,
    ) -> WebSearchResult:
        """Web検索ツール付きで質問。

        Anthropicサーバーサイドの web_search ツールを有効化。
        Claudeが必要に応じて自動で検索を実行し、結果を参照して回答する。

        Args:
            prompt: ユーザーメッセージ。
            heavy: True なら Sonnet（Web検索時はこちらを推奨）。
            system: 任意のシステムプロンプト。
            max_tokens: 応答の最大トークン数。
            max_searches: 1リクエスト内での最大検索回数。

        Returns:
            text / citations（引用URL）/ search_queries（実行されたクエリ）
        """
        model = MODEL_HEAVY if heavy else MODEL_LIGHT
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": max_searches,
                }
            ],
        }
        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)
        _log_usage(model, response.usage)

        text_parts: list[str] = []
        citations: list[dict[str, Any]] = []
        search_queries: list[str] = []

        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
                for c in getattr(block, "citations", None) or []:
                    citations.append(
                        {
                            "url": getattr(c, "url", None),
                            "title": getattr(c, "title", None),
                            "cited_text": getattr(c, "cited_text", None),
                        }
                    )
            elif block_type == "server_tool_use":
                if getattr(block, "name", None) == "web_search":
                    query = (getattr(block, "input", None) or {}).get("query")
                    if query:
                        search_queries.append(query)

        return {
            "text": "".join(text_parts),
            "citations": citations,
            "search_queries": search_queries,
        }
