# AI Trade Support Dashboard

個別株スイングトレード（SBI証券S株）のAI支援ダッシュボード。

## 概要

- 10万円元手でSBI証券のS株（単元未満株）を使ったスイングトレード
- AIが情報収集・分析・売買推奨を自動実行（Claude API）
- ユーザーは1日3回スマホで確認し、SBIアプリから手動注文
- 東証プライム市場の TOPIX 500 構成銘柄（Core30 + Large70 + Mid400）から候補銘柄を抽出、時価総額500億円以上・黒字企業に絞り込み

## 技術スタック

- **Python 3.12**
- **Streamlit**（ダッシュボードUI）
- **SQLite**（データ永続化）
- **J-Quants API**（株価・財務データ）
- **Claude API**（分析・推奨生成）
- **GitHub Actions**（1日3回のバッチ実行）
- **Streamlit Community Cloud**（ホスティング）

## アーキテクチャ

```
[GitHub Actions]          [GitHub Repo]         [Streamlit Cloud]
  ↓ 1日3回起動              ↑ SQLite commit       ↑ コード読込
  J-Quants取得              ↑                     ↑
  Claude分析                ↑                     ↓ URL発行
  SQLite更新 ───────────────┘                     ↓
                                                [ユーザー（スマホ）]
```

## セットアップ（開発環境）

```bash
# 1. リポジトリをクローン
git clone https://github.com/bakugaryusei-code/ai-trade-support.git
cd ai-trade-support

# 2. 仮想環境を作成
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # macOS/Linux

# 3. 依存ライブラリをインストール
pip install -r requirements.txt

# 4. APIキーを設定
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
# secrets.toml を開いて実キーを記入

# 5. Streamlitアプリを起動
streamlit run app.py
```

## セキュリティ

- APIキーは `.streamlit/secrets.toml` で管理（`.gitignore` 対象）
- 本番デプロイ時は Streamlit Community Cloud の Secrets 機能を使用
- GitHubには機密情報を絶対にコミットしない

## ライセンス

個人利用（非公開プロジェクト）
