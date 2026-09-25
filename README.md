# LPortMan (Local Port Manager)

ローカル開発で使うポートを調べて台帳にまとめ、プロジェクト同士の衝突を見つける Windows 用のツールです。

プロジェクトが増えると、開発サーバやエミュレータのポートは既定値 (3000 / 5173 / 8080 / 9099 ...) の
取り合いになります。しかもブラウザの localStorage / IndexedDB は **scheme + host + port** ごとに
分かれるので、後からポートを変えると保存データも別物になります。LPortMan は、どのプロジェクトが
どのポートを使うかを一覧にし、新しいポートを被らない場所から払い出します。

- 設定ファイルからポートを読み取る: `package.json` の scripts / `vite.config.*` / `firebase.json` /
  docker compose / `.env` / `pyproject.toml`
- いま待ち受けているポートを、プロセス・プロジェクトと突き合わせる (WSL / Docker の中継も推定)
- Windows のポート除外範囲 (Hyper-V / WSL / Docker が確保し、空いて見えても使えない) と動的範囲を確認する
- 台帳 (予約) と、予約したポートがプロジェクトの設定に反映されたかの追跡
- 衝突の解消案 (提案のみ)
- Claude などの AI エージェントが読める `ports.json` と CLI
- 画面 (tkinter)。LPortMan 自身はポートを使いません

調べる対象のプロジェクトのファイルには**書き込みません**。`.env` は `PORT` 系のキーの数値だけを読みます。

操作マニュアル (画面付き): [docs/manual.md](docs/manual.md)

## 導入

### exe (Python 不要)

[Releases](https://github.com/lancard-aikawa/LPortMan/releases) から `LPortMan-<版>-win-x64.zip` を
取って展開し、`lportmanw.exe` を起動します。

- `lportmanw.exe` … 画面 (ダブルクリックで開く)
- `lportman.exe` … CLI (`lportman.exe suggest 5173` など)
- 台帳などのデータは exe の隣の `data\` に作られます。フォルダごと移動・コピーできます
- 署名の無い exe なので、初回に SmartScreen の警告が出ることがあります (「詳細情報」→「実行」)

### uv tool (Python の開発者向け)

```
uv tool install git+https://github.com/lancard-aikawa/LPortMan
lportman-gui        # 画面
lportman --help     # CLI
```

### ソースから

```
git clone https://github.com/lancard-aikawa/LPortMan
cd LPortMan
uv sync
LPortMan.bat        # 画面 (uv run lportman gui と同じ)
```

## 最初にやること

1. 画面を開き、**設定** タブで「調べるフォルダ」(例: `C:\Repos`) を追加して「保存して再スキャン」
2. **グラフ** タブで、どのプロジェクトがどのポートでぶつかっているかを見る
3. (任意) `lportman link` で `%USERPROFILE%\.lportman` → `data\` のジャンクションを作る。
   AI エージェントなどから固定のパスで `ports.json` を読めるようになります

## CLI

```
lportman scan                     # 調査して data\ports.json を更新
lportman list [--conflicts]       # ポート一覧 / 注意のみ (--json で JSON)
lportman check 5173 3000          # 使ってよいか (使用不可があれば終了コード 1)
lportman suggest 5173             # 空きの候補。既定ポートを渡すと +10000 / +20000 を優先
lportman reserve 15173 --name "MyApp dev" --project "C:\Repos\MyApp"   # 台帳に予約 (使用不可なら拒否)
lportman unreserve 15173 --name "MyApp dev"
lportman plan                     # 衝突の解消案 (提案のみ) を data\plan.md に出力
lportman link                     # %USERPROFILE%\.lportman のジャンクションを作る
lportman gui                      # 画面
```

(ソースから動かすときは `uv run lportman ...`、exe なら `lportman.exe ...`)

## Claude Code などと組み合わせる

AI エージェントに新しいプロジェクトを作らせると、既定の 5173 や 3000 のまま使い始めがちです。
例えば `CLAUDE.md` に次のように書いておくと、ポートを決める前に LPortMan を確認させられます。

````markdown
## ローカルのポート

開発サーバ・エミュレータ・docker compose のポートを新しく決める / 変える前に確認すること。

- 一覧: `%USERPROFILE%\.lportman\ports.json`
- 候補: `lportman suggest <既定ポート>` / 確認: `lportman check <port>` (使用不可なら終了コード 1)
- 決めたら `lportman reserve <port> --name "<用途>" --project "<フォルダ>"` で台帳に登録する
- 既存プロジェクトのポートは勝手に変えない。変えるときは `lportman plan` の解消案を示して相談する
````

`ports.json` の `about` には、その環境で CLI を呼ぶためのコマンドが書かれています。

## データ (`data\`)

置き場所: exe なら exe の隣の `data\`、ソースからならリポジトリの `data\`、
`uv tool install` なら `%LOCALAPPDATA%\LPortMan`。

| ファイル | 内容 | 編集 |
|---|---|---|
| `config.json` | 調べるフォルダ、払い出し範囲、サンプル扱いなど | 画面の設定タブ、または手で |
| `registry.json` | 台帳 (手で決めた割り当て・予約) | 画面、CLI、または手で |
| `ports.json` | 調査結果。AI エージェントが読む | 自動 |
| `seen.json` | ポートを初めて見つけた日時 (新着の判定用) | 自動 |
| `plan.md` | 衝突の解消案。フルパスを含むので手元用 | 自動 |

`%USERPROFILE%\.lportman` は `data\` へのディレクトリジャンクションです
(ハードリンクはドライブを跨げず、エディタの置き換え保存で切れるため使いません)。

`config.json` の主な項目:

- `roots`: 調べるフォルダ。`tier` は `normal` / `private` (私的) / `scratch` (相談段階。衝突の重さを 1 段下げる)。
  `mask_paths` を true にすると `ports.json` でフルパスを伏せ、`<label>\相対パス` で出す
- `sample_globs` (既定 `test\*`): 当てはまるプロジェクトはサンプル扱い。衝突を 1 段軽くし、解消案では据え置き
- `assign_range` (既定 20000-29999): 空きの候補を探す範囲
- `new_days` (既定 7): この日数以内に初めて見つかったポートを新着にする
- `editor`: 画面の「設定を編集」「台帳を編集」で使うエディタ。空なら VS Code > メモ帳

## 判定

| 区分 | 意味 |
|---|---|
| 明示 | 設定に数値で書かれている |
| 既定 | ツールの既定値から推定 (例: `vite` だけなら 5173) |
| 参照 | `.env` の `DB_PORT` など。接続先の可能性が高いので衝突判定には使わない |
| 台帳 | `registry.json` の予約 |

注意の重さ:

- 高: 明示どうしで複数プロジェクトが同じポートを使っている / Windows の除外範囲内
- 中: 明示と既定の衝突 / 動的範囲内 / 宣言元以外のプロセスが使用中
- 低: 既定どうしの衝突 / 別系統ツールの有名ポートを使用

## 解消案

`lportman plan` / 画面の「解消案」タブ。**提案のみ**で、プロジェクトのファイルは変更しません。
共有プロジェクトは関係者と相談して決め、決まったら台帳に登録します (画面から一括登録できます)。

- 衝突しているプロジェクトごとにずらし幅を 1 つ決める (+10000、埋まっていれば +10100, +10200 ...)
- ずらすのは 1024〜9999 のポートと、実際に衝突しているポートだけ。80/443 や大きい番号は動かさない
- 据え置きにする優先順: 稼働中 > 台帳で反映済み > 通常 > 私的 > 相談中 > サンプル

## 開発

```
uv sync
uv run pytest -q                       # テスト
./packaging/build.ps1                  # exe のビルド (dist\LPortMan\)
uv run python tools/manual_shots.py    # マニュアルの画面画像を撮る (docs\images\、git 管理外)
```

リリースはタグを打つと GitHub Actions がビルドして、ドラフトの Release を作ります
(`pyproject.toml` と `src/lportman/__init__.py` の版、`CHANGELOG.md` の節を先に揃えること)。

```
git tag v0.1.0 && git push origin v0.1.0
```

## ライセンス

[MIT](LICENSE)
