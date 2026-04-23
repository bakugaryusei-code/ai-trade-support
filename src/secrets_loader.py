"""APIキー等の機密情報を読み込むユーティリティ。

3つの環境で動作する：
  1. ローカル開発：`.streamlit/secrets.toml` を読む
  2. Streamlit Community Cloud：Streamlit側が自動で secrets.toml を作成するので上と同じ
  3. GitHub Actions：環境変数から読む（secrets.tomlが存在しない）

環境変数が設定されていればファイルより優先する。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path


_SECRETS_FILE = Path(".streamlit/secrets.toml")
_ENV_KEYS = ("JQUANTS_API_KEY", "ANTHROPIC_API_KEY")


def load_secrets() -> dict[str, str]:
    """secrets.toml を読み込み、環境変数で上書きした辞書を返す。"""
    secrets: dict[str, str] = {}

    if _SECRETS_FILE.exists():
        with _SECRETS_FILE.open("rb") as f:
            secrets = tomllib.load(f)

    for key in _ENV_KEYS:
        value = os.environ.get(key)
        if value:
            secrets[key] = value

    return secrets


def get_secret(key: str) -> str:
    """指定キーを取得。見つからなければ RuntimeError。"""
    secrets = load_secrets()
    if key not in secrets:
        raise RuntimeError(
            f"Secret '{key}' が見つかりません。\n"
            f"  - ローカル開発時：`.streamlit/secrets.toml` に記載\n"
            f"  - GitHub Actions：リポジトリの Secrets に登録\n"
            f"  - Streamlit Cloud：アプリ設定の Secrets に登録"
        )
    return secrets[key]
