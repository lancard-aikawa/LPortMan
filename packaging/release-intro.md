<!-- Release の本文の後半 (初めての方へ)。変更点は CHANGELOG.md から release.yml が差し込む -->

## 初めての方へ

`LPortMan-<版>-win-x64.zip` を展開し、`lportmanw.exe` を起動します (Windows 10 / 11)。

1. 初回に出る「調べるフォルダを選んでください」で、普段リポジトリを置いているフォルダ (例: `C:\Repos`) を選んで「開始」
2. **グラフ** タブで、どのプロジェクトがどのポートでぶつかっているかを確認
3. 新しいポートは「ポート確認」に既定ポート (例: `5173`) を入れて「空きを提案」→「予約に追加」

- `lportman.exe` は CLI です (`lportman.exe suggest 5173` など)。AI エージェントから呼ぶのに使えます
- 台帳などのデータは exe の隣の `data\` に作られます。フォルダごと移動・コピーできます
- 調べる対象のプロジェクトのファイルには書き込みません
- 署名の無い exe なので、初回に SmartScreen の警告が出ることがあります (「詳細情報」→「実行」)

詳しくは [README](https://github.com/lancard-aikawa/LPortMan#readme) と
[操作マニュアル](https://github.com/lancard-aikawa/LPortMan/blob/main/docs/manual.md) を見てください。
