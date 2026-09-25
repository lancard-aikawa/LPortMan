# 変更履歴

<!-- 見出しは `## <版番号>` の形にすること (release.yml がこの節を Release の本文に使う) -->

## 0.2.0

- `lportman claude-md`: AI エージェント (Claude Code など) 向けの CLAUDE.md の節を作る。
  コマンドのパスを入れ方に合わせて埋め、`--write` で目印付きの節として書き込む (再実行で差し替え)
- `lportman project [フォルダ]`: そのプロジェクトのポート・衝突・台帳の反映状況をまとめて表示

## 0.1.0

最初の公開版。

- 設定ファイル (package.json / vite.config / firebase.json / docker compose / .env / pyproject.toml) からポートを調査
- 待ち受け中のポートとプロセス・プロジェクトの突き合わせ (WSL / Docker の中継、Claude Code IDE 連携のポートを識別)
- Windows のポート除外範囲・動的範囲の確認
- 台帳 (予約) と、予約したポートがプロジェクトの設定に反映されたかの追跡
- 衝突の解消案 (提案のみ) と、画面からの一括予約
- 画面: 一覧 / グラフ (衝突マトリクス・ポート分布) / 稼働状況の自動更新 / プロセスの終了 / 新着 / 設定
- AI エージェント向けの `ports.json` と CLI (scan / list / check / suggest / reserve / plan / link)
- 初回起動時に、調べるフォルダを選ぶダイアログ (よくある開発用フォルダを自動で候補に出す)
