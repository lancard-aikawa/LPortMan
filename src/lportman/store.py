"""データフォルダ (config / registry / ports.json) の読み書き。

正本は data/ に置く。置き場所は動かし方で決まる:
  exe            … exe の隣 (フォルダごと移動・コピーできるように)
  ソースから     … リポジトリ直下
  インストール版 (uv tool install など) … %LOCALAPPDATA%\\LPortMan%USERPROFILE%\\.lportman からは
ディレクトリジャンクションで参照する (ハードリンクはドライブを跨げず、
エディタの置き換え保存で切れるため使わない)。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

FROZEN = bool(getattr(sys, "frozen", False))  # PyInstaller でビルドした exe か
_SRC_ROOT = Path(__file__).resolve().parents[2]
if FROZEN:
    MODE = "exe"
    PROJECT_DIR = Path(sys.executable).resolve().parent
    _DEFAULT_DATA = PROJECT_DIR / "data"
elif (_SRC_ROOT / "pyproject.toml").exists():
    MODE = "source"
    PROJECT_DIR = _SRC_ROOT
    _DEFAULT_DATA = PROJECT_DIR / "data"
else:
    MODE = "installed"
    PROJECT_DIR = _SRC_ROOT
    _DEFAULT_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LPortMan"
DATA_DIR = Path(os.environ.get("LPORTMAN_DATA", _DEFAULT_DATA))
LINK_DIR = Path.home() / ".lportman"

CONFIG_FILE = DATA_DIR / "config.json"
REGISTRY_FILE = DATA_DIR / "registry.json"
PORTS_FILE = DATA_DIR / "ports.json"
PLAN_FILE = DATA_DIR / "plan.md"
SEEN_FILE = DATA_DIR / "seen.json"  # ポートを初めて見つけた日時 (新着表示用)

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    # tier: normal / private (私的・消えても困りにくい) / scratch (相談段階。衝突の重さを1段下げる)
    # mask_paths: ports.json (Claude 向け) でフルパスを伏せ、<label>\相対パス で出す
    # 例: {"path": "C:\\Repos", "label": "Repos", "tier": "normal", "mask_paths": false}
    # 空なら画面の設定タブで追加する
    "roots": [],
    # サンプル・試し書き扱いにするプロジェクト (スキャンルートからの相対パスに対する glob)。
    # 衝突の重さを下げ、解消案では据え置きにする
    "sample_globs": ["test\\*"],
    # この日数以内に初めて見つかったポートを「新着」として表示する
    "new_days": 7,
    # 「設定を編集」「台帳を編集」で使うエディタの実行ファイル。空なら VS Code (code) > メモ帳
    "editor": "",
    "max_depth": 6,
    "exclude_dirs": [
        "node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist", "build",
        ".next", ".nuxt", ".svelte-kit", ".output", ".turbo", ".cache", "coverage",
        ".dart_tool", "target", "vendor", "Pods", ".gradle", ".idea", ".vs",
    ],
    # 新規ポートの払い出し範囲と、既定ポートからのずらし幅
    "assign_range": [20000, 29999],
    "offsets": [10000, 20000],
}

DEFAULT_REGISTRY: dict[str, Any] = {
    "version": 1,
    # 手で決めた割り当て・予約。例:
    # {"port": 25173, "name": "MyApp dev", "project": "C:\\Repos\\MyApp", "note": ""}
    "reservations": [],
}


def cli_command() -> str:
    """Claude など外から LPortMan の CLI を呼ぶときのコマンド (ports.json の説明に書く)。"""
    if MODE == "exe":
        return f'"{PROJECT_DIR / "lportman.exe"}"'
    if MODE == "source":
        return f'uv run --project "{PROJECT_DIR}" lportman'
    return "lportman"


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        save_json(path, default)
        return json.loads(json.dumps(default))
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """一時ファイルに書いてから置き換える (書きかけの JSON を読ませない)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def load_config() -> dict[str, Any]:
    cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    missing = [k for k in DEFAULT_CONFIG if k not in cfg]
    for key in missing:
        cfg[key] = DEFAULT_CONFIG[key]
    if missing:  # 新しい設定項目をファイルにも足して、編集できるようにする
        save_json(CONFIG_FILE, cfg)
    return cfg


def load_registry() -> dict[str, Any]:
    reg = load_json(REGISTRY_FILE, DEFAULT_REGISTRY)
    reg.setdefault("reservations", [])
    return reg


def add_reservation(port: int, name: str, project: str = "", note: str = "") -> dict[str, Any]:
    reg = load_registry()
    entry: dict[str, Any] = {"port": int(port), "name": name}
    if project:
        entry["project"] = project
    if note:
        entry["note"] = note
    reg["reservations"].append(entry)
    reg["reservations"].sort(key=lambda r: int(r.get("port", 0)))
    save_json(REGISTRY_FILE, reg)
    return entry


def remove_reservation(port: int, name: str) -> bool:
    reg = load_registry()
    before = len(reg["reservations"])
    reg["reservations"] = [
        r for r in reg["reservations"] if not (int(r.get("port", 0)) == port and r.get("name") == name)
    ]
    if len(reg["reservations"]) == before:
        return False
    save_json(REGISTRY_FILE, reg)
    return True


def open_in_editor(path: os.PathLike[str] | str, config: dict[str, Any] | None = None) -> None:
    """テキストエディタで開く。config の editor > VS Code (code) > メモ帳 の順。"""
    editor = (config or {}).get("editor") or shutil.which("code") or "notepad.exe"
    # code は code.cmd (バッチ) なので、そのまま CreateProcess に渡せる
    subprocess.Popen([editor, str(path)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def load_seen() -> dict[str, Any] | None:
    if not SEEN_FILE.exists():
        return None
    try:
        with SEEN_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_seen(data: dict[str, Any]) -> None:
    save_json(SEEN_FILE, data)


def link_status() -> str:
    """ジャンクションの状態を返す: ok / missing / other:<target> / notlink"""
    if not os.path.lexists(LINK_DIR):
        return "missing"
    try:
        target = Path(os.readlink(LINK_DIR))
    except OSError:
        return "notlink"
    target_s = str(target).removeprefix("\\\\?\\")
    if Path(target_s).resolve() == DATA_DIR.resolve():
        return "ok"
    return f"other:{target_s}"


def make_link() -> str:
    """%USERPROFILE%\\.lportman -> data/ のジャンクションを作る。"""
    status = link_status()
    if status == "ok":
        return f"既に作成済み: {LINK_DIR} -> {DATA_DIR}"
    if status != "missing":
        raise RuntimeError(f"{LINK_DIR} が既に存在します ({status})。確認して手で削除してください。")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(LINK_DIR), str(DATA_DIR)],
        check=True, capture_output=True,
    )
    return f"作成しました: {LINK_DIR} -> {DATA_DIR}"
