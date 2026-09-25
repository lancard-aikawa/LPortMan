"""AI エージェント (Claude Code など) 向けの CLAUDE.md の節を作る。

`lportman claude-md` が使う。コマンドのパスは動き方 (exe / uv tool / ソース) に合わせて埋める。
`--write` では目印のコメントで囲んだ節として書き込み、2 回目からはその節だけを差し替える。
"""

from __future__ import annotations

import re
from pathlib import Path

from lportman import store

BEGIN = "<!-- lportman:begin (lportman claude-md が生成。手で直すと次回の生成で上書きされる) -->"
END = "<!-- lportman:end -->"
_BLOCK_RE = re.compile(r"<!-- lportman:begin.*?-->.*?<!-- lportman:end -->", re.S)


def ports_json_path() -> str:
    """エージェントに読ませる ports.json のパス。ジャンクションがあればそちら (場所が変わらない)。"""
    if store.link_status() == "ok":
        return r"%USERPROFILE%\.lportman\ports.json"
    return str(store.PORTS_FILE)


def render() -> str:
    cmd = store.cli_command()
    ports = ports_json_path()
    return f"""{BEGIN}
## ローカルのポート (LPortMan)

このマシンでは複数のプロジェクトが開発サーバ・エミュレータ・docker compose のポートを
取り合っている (3000 / 5173 / 8080 / 9099 などの既定値)。ポートが変わるとブラウザの
localStorage / IndexedDB も別物になる (origin は scheme+host+port) ので、後から変えるのは高くつく。
**ポートは LPortMan の台帳で管理する。**

CLI: `{cmd} <コマンド>` (以下 `lportman` と書く)
一覧: `{ports}` (プロジェクトごとの使用ポート・稼働状況・衝突・台帳。古いことがあるので判断は CLI で)

### 場面ごとの手順

**新しくポートを決めるとき** (新しいプロジェクト、開発サーバ・エミュレータ・compose の追加):

1. 既定値のまま使わない。`lportman suggest <ツールの既定ポート>` で候補を取る (例: Vite なら 5173)
2. 決めたポートを設定ファイルに**明記**する (package.json の `--port`、vite.config の `server.port`、
   firebase.json の `emulators.*.port`、compose の `ports`)。Firebase Emulator は hub / logging / ui も含めて
   全部書く (書かないと 4400 / 4500 / 4000 で他のプロジェクトと衝突する)
3. `lportman reserve <port> --name "<用途>" --project "<プロジェクトのフォルダ>"` で台帳に登録する

**作業を始めるとき / ポートの状況を知りたいとき**:
`lportman project <プロジェクトのフォルダ>` で、そのプロジェクトのポート・衝突・台帳の反映状況を見る。

**起動が「ポートが使用中」(EADDRINUSE) や EACCES で失敗したとき**:
`lportman check <port>` で誰が使っているかを見る。
- 別のプロジェクトのサーバが動いている → ユーザーに伝える。**プロセスを勝手に終了しない**
- 「Windows のポート除外範囲内」→ そのポートは使えない。`lportman suggest` で別の番号にする
- `Code.exe [Claude Code IDE 連携]` → VS Code のウィンドウを開き直せば空く一時ポート

**設定ファイルのポートを書き換えたあと**:
`lportman project <フォルダ>` で、台帳の予約が「反映済み」になったかを確かめる。

**既存のポートの変更**:
既存プロジェクトのポートは勝手に変えない (共有プロジェクトがある)。衝突を解消したいときは
`lportman plan --out -` の解消案を示して、ユーザーと相談してから。

### 使ってはいけないポート

- 49152 以上 (OS の動的範囲) と、Windows の除外範囲 (空いて見えても bind が失敗する。再起動で変わる)
- `lportman check <port>` が「使用不可」を返すポート (終了コード 1)

検算: `lportman check <port>` が「使用可」を返し、`lportman project <フォルダ>` で予約が「反映済み」になること。
{END}
"""


def write(path: Path) -> str:
    """CLAUDE.md に節を書き込む。既にあればその節だけ差し替える。結果の説明を返す。"""
    block = render().rstrip("\n")
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if _BLOCK_RE.search(text):
            new = _BLOCK_RE.sub(lambda _m: block, text, count=1)
            action = "差し替えました"
        else:
            new = text.rstrip("\n") + "\n\n" + block + "\n"
            action = "末尾に追記しました"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = block + "\n"
        action = "作成しました"
    path.write_text(new, encoding="utf-8", newline="\n")
    return f"{path} に LPortMan の節を{action}"
