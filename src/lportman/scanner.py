"""設定ファイルからポート宣言を拾う (静的調査)。

対象: package.json scripts / vite.config.* / firebase.json / docker-compose / .env / pyproject.toml
ファイルには一切書き込まない。.env は PORT 系のキーの数値だけを読む。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# kind:
#   explicit … 設定に数値で明記されている
#   default  … ツールの既定値から推定 (設定を書けば変わる)
#   ref      … .env の DB_PORT 等。接続先の可能性が高く、衝突判定には使わない
KINDS = ("explicit", "default", "ref")


@dataclass
class Finding:
    port: int
    service: str
    family: str
    kind: str
    file: str  # プロジェクトからの相対パス
    detail: str


@dataclass
class Project:
    path: str
    name: str
    root_label: str
    tier: str
    mask: bool
    findings: list[Finding] = field(default_factory=list)

    def display_path(self, root_path: str) -> str:
        if not self.mask:
            return self.path
        rel = os.path.relpath(self.path, root_path)
        return f"<{self.root_label}>\\{rel}"


MARKER_FILES = {"package.json", "firebase.json", "pyproject.toml"}
COMPOSE_RE = re.compile(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$", re.I)
VITE_CONFIG_RE = re.compile(r"^vite\.config\.(m|c)?(js|ts)$", re.I)
ENV_RE = re.compile(r"^\.env(\.[\w-]+)?$", re.I)
ENV_SKIP_SUFFIX = (".example", ".sample", ".template")


def is_marker(name: str) -> bool:
    return (
        name in MARKER_FILES
        or bool(COMPOSE_RE.match(name))
        or bool(VITE_CONFIG_RE.match(name))
        or (bool(ENV_RE.match(name)) and not name.lower().endswith(ENV_SKIP_SUFFIX))
    )


# ---------------------------------------------------------------- package.json

# (コマンドに対する正規表現, サービス名, family, 既定ポート or None=ポートを持たない)
SCRIPT_TOOLS: list[tuple[re.Pattern[str], str, str, int | None]] = [
    (re.compile(r"\bvite\s+build\b"), "", "", None),
    (re.compile(r"\bvitepress\s+build\b"), "", "", None),
    (re.compile(r"\bvite\s+preview\b"), "Vite preview", "vite", 4173),
    (re.compile(r"\bvitepress\s+(dev|serve)\b"), "VitePress", "vite", 5173),
    (re.compile(r"(^|[\s/])vite(\s+(dev|serve)\b|\s*$|\s+-)"), "Vite dev", "vite", 5173),
    (re.compile(r"\bnext\s+(dev|start)\b"), "Next.js", "web", 3000),
    (re.compile(r"\bnuxi?\s+(dev|preview|start)\b"), "Nuxt", "web", 3000),
    (re.compile(r"\bng\s+serve\b"), "Angular", "web", 4200),
    (re.compile(r"\bember\s+(serve|server|s)\b"), "Ember", "web", 4200),
    (re.compile(r"\bastro\s+(dev|preview)\b"), "Astro", "web", 4321),
    (re.compile(r"\bstorybook\s+dev\b|\bstart-storybook\b"), "Storybook", "web", 6006),
    (re.compile(r"\bremix-serve\b"), "Remix", "web", 3000),
    (re.compile(r"\breact-scripts\s+start\b"), "Create React App", "web", 3000),
    (re.compile(r"\bwebpack(-dev-server|\s+serve)\b"), "webpack dev server", "web", 8080),
    (re.compile(r"\bwrangler\s+dev\b"), "Cloudflare Wrangler", "cloudflare", 8787),
    (re.compile(r"\bfirebase\s+serve\b"), "Firebase serve", "firebase", 5000),
    (re.compile(r"\bhttp-server\b"), "http-server", "web", 8080),
    (re.compile(r"\blive-server\b"), "live-server", "web", 8080),
    (re.compile(r"--servedir\b"), "esbuild serve", "web", 8000),
    (re.compile(r"\bgatsby\s+develop\b"), "Gatsby", "web", 8000),
    (re.compile(r"\bexpo\s+start\b"), "Expo", "web", 8081),
    (re.compile(r"\bparcel(?!\s+build)\b"), "Parcel", "web", 1234),
    (re.compile(r"(^|\s)(npx\s+)?serve(\s|$)"), "serve", "web", 3000),
]

PORT_ARG_RE = re.compile(r"(?:--port[=\s]+|(?<!\S)-p\s+|\bPORT=)(\d{2,5})\b")
SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\|")


def _parse_script(name: str, cmd: str, vite_port: int | None) -> list[Finding]:
    out: list[Finding] = []
    for seg in SEGMENT_SPLIT_RE.split(cmd):
        seg = seg.strip()
        if not seg:
            continue
        tool: tuple[str, str, int | None] | None = None
        for pat, service, family, port in SCRIPT_TOOLS:
            if pat.search(seg):
                tool = (service, family, port)
                break
        if tool is not None and tool[2] is None:
            continue  # build 系
        detail = f"scripts.{name}: {cmd[:80]}"
        explicit = PORT_ARG_RE.search(seg)
        if explicit:
            service, family = (tool[0], tool[1]) if tool else ("npm script", "web")
            out.append(Finding(int(explicit.group(1)), service, family, "explicit", "package.json", detail))
        elif tool is not None:
            service, family, port = tool
            if family == "vite" and service == "Vite dev" and vite_port:
                out.append(Finding(vite_port, service, family, "explicit", "vite.config", detail))
            else:
                out.append(Finding(port, service, family, "default", "package.json", detail))  # type: ignore[arg-type]
    return out


def parse_package_json(path: Path, vite_port: int | None) -> list[Finding]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    out: list[Finding] = []
    for name, cmd in scripts.items():
        if isinstance(cmd, str):
            out.extend(_parse_script(name, cmd, vite_port))
    return out


# ---------------------------------------------------------------- vite.config

VITE_SERVER_RE = re.compile(r"\bserver\s*:\s*\{[^{}]*?\bport\s*:\s*(\d{2,5})", re.S)
VITE_PREVIEW_RE = re.compile(r"\bpreview\s*:\s*\{[^{}]*?\bport\s*:\s*(\d{2,5})", re.S)


def parse_vite_config(path: Path) -> tuple[int | None, list[Finding]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, []
    out: list[Finding] = []
    server = VITE_SERVER_RE.search(text)
    preview = VITE_PREVIEW_RE.search(text)
    server_port = int(server.group(1)) if server else None
    if server_port:
        out.append(Finding(server_port, "Vite dev", "vite", "explicit", path.name, "server.port"))
    if preview:
        out.append(Finding(int(preview.group(1)), "Vite preview", "vite", "explicit", path.name, "preview.port"))
    return server_port, out


# ---------------------------------------------------------------- firebase.json

FIREBASE_EMULATORS: dict[str, tuple[str, int]] = {
    "auth": ("Firebase Auth emulator", 9099),
    "functions": ("Firebase Functions emulator", 5001),
    "firestore": ("Firebase Firestore emulator", 8080),
    "database": ("Firebase Realtime Database emulator", 9000),
    "hosting": ("Firebase Hosting emulator", 5000),
    "pubsub": ("Firebase Pub/Sub emulator", 8085),
    "storage": ("Firebase Storage emulator", 9199),
    "eventarc": ("Firebase Eventarc emulator", 9299),
    "dataconnect": ("Firebase Data Connect emulator", 9399),
    "tasks": ("Firebase Cloud Tasks emulator", 9499),
    "apphosting": ("Firebase App Hosting emulator", 5002),
}
# 設定に書かなくても起動するもの
FIREBASE_ALWAYS: dict[str, tuple[str, int]] = {
    "hub": ("Firebase Emulator Hub", 4400),
    "logging": ("Firebase Emulator Logging", 4500),
}


def parse_firebase_json(path: Path) -> list[Finding]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    emu = data.get("emulators") if isinstance(data, dict) else None
    if not isinstance(emu, dict):
        return []
    out: list[Finding] = []

    def add(key: str, service: str, default: int, conf: Any) -> None:
        port = conf.get("port") if isinstance(conf, dict) else None
        if isinstance(port, int) or (isinstance(port, str) and port.isdigit()):
            out.append(Finding(int(port), service, "firebase", "explicit", "firebase.json", f"emulators.{key}.port"))
        else:
            out.append(Finding(default, service, "firebase", "default", "firebase.json", f"emulators.{key} (既定)"))

    for key, (service, default) in FIREBASE_EMULATORS.items():
        if key in emu:
            add(key, service, default, emu[key])
            if key == "firestore" and isinstance(emu[key], dict) and emu[key].get("websocketPort"):
                out.append(Finding(int(emu[key]["websocketPort"]), "Firebase Firestore emulator (WebSocket)",
                                   "firebase", "explicit", "firebase.json", "emulators.firestore.websocketPort"))
    ui = emu.get("ui")
    if not (isinstance(ui, dict) and ui.get("enabled") is False):
        add("ui", "Firebase Emulator UI", 4000, ui)
    for key, (service, default) in FIREBASE_ALWAYS.items():
        add(key, service, default, emu.get(key))
    return out


# ---------------------------------------------------------------- docker compose

YAML_KEY_RE = re.compile(r"^(\s*)([\w.-]+)\s*:\s*(#.*)?$")
PORTS_KEY_RE = re.compile(r"^(\s*)ports\s*:\s*(#.*)?$")
VAR_DEFAULT_RE = re.compile(r"\$\{\w+:?-(\d+)\}")
PUBLISHED_RE = re.compile(r"published\s*:\s*[\"']?(\d{2,5})")


def _compose_host_ports(item: str) -> list[int]:
    item = VAR_DEFAULT_RE.sub(r"\1", item).split("#", 1)[0].strip().strip("\"'")
    item = item.split("/", 1)[0]
    parts = item.split(":")
    if len(parts) < 2:
        return []  # "3000" 単独はホスト側がランダム
    host = parts[-2]
    if "-" in host:
        a, _, b = host.partition("-")
        if a.isdigit() and b.isdigit() and 0 <= int(b) - int(a) <= 20:
            return list(range(int(a), int(b) + 1))
        return []
    return [int(host)] if host.isdigit() else []


def parse_compose(path: Path) -> list[Finding]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[Finding] = []
    keys: list[tuple[int, str]] = []  # (indent, key) のスタック
    ports_indent: int | None = None
    service = "?"
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if ports_indent is not None:
            if indent > ports_indent or (indent == ports_indent and line.lstrip().startswith("-")):
                for m in PUBLISHED_RE.finditer(line):
                    out.append(Finding(int(m.group(1)), f"compose: {service}", "docker", "explicit", path.name, line.strip()))
                if line.lstrip().startswith("-"):
                    for port in _compose_host_ports(line.lstrip()[1:]):
                        out.append(Finding(port, f"compose: {service}", "docker", "explicit", path.name, line.strip()))
                continue
            ports_indent = None
        if PORTS_KEY_RE.match(line):
            while keys and keys[-1][0] >= indent:
                keys.pop()
            service = keys[-1][1] if keys else "?"
            ports_indent = indent
            continue
        m = YAML_KEY_RE.match(line)
        if m:
            while keys and keys[-1][0] >= indent:
                keys.pop()
            keys.append((indent, m.group(2)))
    return out


# ---------------------------------------------------------------- .env

ENV_PORT_RE = re.compile(
    r"^\s*(?:export\s+)?((?:[A-Z0-9_]+_)?PORT(?:_[A-Z0-9_]+)?)\s*=\s*[\"']?(\d{2,5})\b", re.M
)


def parse_env(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[Finding] = []
    for key, value in ENV_PORT_RE.findall(text):
        kind = "explicit" if key == "PORT" else "ref"
        out.append(Finding(int(value), f".env {key}", "env", kind, path.name, key))
    return out


# ---------------------------------------------------------------- pyproject.toml

PY_TOOLS = [
    (re.compile(r"[\"']streamlit\b"), "Streamlit", 8501),
    (re.compile(r"[\"']gradio\b"), "Gradio", 7860),
    (re.compile(r"[\"']uvicorn\b"), "uvicorn", 8000),
    (re.compile(r"[\"']flask\b", re.I), "Flask", 5000),
]


def parse_pyproject(path: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [
        Finding(port, service, "python", "default", "pyproject.toml", "依存関係から推定")
        for pat, service, port in PY_TOOLS
        if pat.search(text)
    ]


# ---------------------------------------------------------------- 走査


def parse_dir(dirpath: Path, files: list[str]) -> list[Finding]:
    """1ディレクトリ内の対象ファイルをまとめて解析する (vite.config を先に読むため)。"""
    out: list[Finding] = []
    vite_port: int | None = None
    for name in files:
        if VITE_CONFIG_RE.match(name):
            vite_port, found = parse_vite_config(dirpath / name)
            out.extend(found)
    for name in files:
        p = dirpath / name
        if name == "package.json":
            out.extend(parse_package_json(p, vite_port))
        elif name == "firebase.json":
            out.extend(parse_firebase_json(p))
        elif name == "pyproject.toml":
            out.extend(parse_pyproject(p))
        elif COMPOSE_RE.match(name):
            out.extend(parse_compose(p))
        elif ENV_RE.match(name) and not name.lower().endswith(ENV_SKIP_SUFFIX):
            out.extend(parse_env(p))
    return out


def scan_root(
    root: dict[str, Any], max_depth: int, exclude: set[str], sample_globs: list[str] = (),  # type: ignore[assignment]
) -> list[Project]:
    root_path = os.path.normpath(root["path"])
    if not os.path.isdir(root_path):
        return []
    base_depth = root_path.rstrip("\\").count("\\")
    marker_dirs: dict[str, list[str]] = {}
    git_dirs: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root_path):
        if ".git" in dirnames or ".git" in filenames:
            git_dirs.add(dirpath)
        depth = dirpath.count("\\") - base_depth
        dirnames[:] = [] if depth >= max_depth else [
            d for d in dirnames if d not in exclude and not d.startswith(".")
            or d in (".devcontainer",)
        ]
        markers = [f for f in filenames if is_marker(f)]
        if markers:
            marker_dirs[dirpath] = markers

    def project_root_of(d: str) -> str:
        # 1. スキャンルートより下で最も近い git リポジトリ
        cur = d
        while len(cur) > len(root_path):
            if cur in git_dirs:
                return cur
            cur = os.path.dirname(cur)
        # 2. git 外なら、祖先の中で最も上にある marker ディレクトリ
        top = d
        cur = os.path.dirname(d)
        while len(cur) > len(root_path):
            if cur in marker_dirs:
                top = cur
            cur = os.path.dirname(cur)
        return top

    projects: dict[str, Project] = {}
    for d, files in sorted(marker_dirs.items()):
        found = parse_dir(Path(d), files)
        if not found:
            continue
        proot = project_root_of(d)
        proj = projects.get(proot)
        if proj is None:
            rel = os.path.relpath(proot, root_path)
            tier = root.get("tier", "normal")
            if tier == "normal" and any(fnmatch.fnmatch(rel.lower(), g.lower()) for g in sample_globs):
                tier = "sample"
            proj = projects[proot] = Project(
                path=proot, name=root["label"] if rel == "." else rel, root_label=root["label"],
                tier=tier, mask=bool(root.get("mask_paths")),
            )
        sub = os.path.relpath(d, proot)
        for f in found:
            if sub != ".":
                f.file = f"{sub}\\{f.file}"
        proj.findings.extend(found)

    for proj in projects.values():
        seen: set[tuple[int, str, str]] = set()
        uniq: list[Finding] = []
        for f in proj.findings:
            key = (f.port, f.service, f.kind)
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        proj.findings = sorted(uniq, key=lambda f: (f.port, f.service))
    return list(projects.values())


def scan_all(config: dict[str, Any]) -> list[Project]:
    exclude = set(config.get("exclude_dirs", []))
    max_depth = int(config.get("max_depth", 6))
    projects: list[Project] = []
    for root in config.get("roots", []):
        projects.extend(scan_root(root, max_depth, exclude, config.get("sample_globs", [])))
    return projects
