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

        response = self._client.messages.create(**kwargs)
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
