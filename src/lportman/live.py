"""実際に待ち受け中のポートと、Windows のポート除外範囲を調べる。"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass
class Listener:
    port: int
    address: str
    pid: int
    process: str
    cwd: str
    exe: str


def _proc_info(pid: int, cache: dict[int, tuple[str, str, str]]) -> tuple[str, str, str]:
    if pid not in cache:
        name = cwd = exe = ""
        try:
            p = psutil.Process(pid)
            name = p.name()
            try:
                cwd = p.cwd()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                pass
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        cache[pid] = (name, cwd, exe)
    return cache[pid]


CLAUDE_IDE_LABEL = "[Claude Code IDE 連携]"


def claude_ide_ports() -> set[int]:
    """Claude Code 拡張の IDE 連携サーバのポート (~/.claude/ide/<port>.lock)。

    10000-65535 からランダムに選ばれ、VS Code のウィンドウを開き直すたびに変わる。
    古いロックファイルも残るので、実際に待ち受けているかは呼び出し側で突き合わせる。
    """
    ide_dir = Path.home() / ".claude" / "ide"
    try:
        return {int(p.stem) for p in ide_dir.glob("*.lock") if p.stem.isdigit()}
    except OSError:
        return set()


def listeners() -> list[Listener]:
    """TCP で LISTEN しているソケット (IPv4/IPv6 は同一ポートなら1件にまとめる)。"""
    ide_ports = claude_ide_ports()
    cache: dict[int, tuple[str, str, str]] = {}
    seen: dict[tuple[int, int], Listener] = {}
    for c in psutil.net_connections(kind="tcp"):
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        pid = c.pid or 0
        key = (c.laddr.port, pid)
        if key in seen:
            continue
        name, cwd, exe = _proc_info(pid, cache) if pid else ("System", "", "")
        if c.laddr.port in ide_ports and name.lower() == "code.exe":
            name = f"{name} {CLAUDE_IDE_LABEL}"
        seen[key] = Listener(c.laddr.port, c.laddr.ip, pid, name, cwd, exe)
    return sorted(seen.values(), key=lambda x: (x.port, x.pid))


def _netsh(*args: str) -> str:
    """netsh を実行して出力を返す (コンソールのコードページ次第で UTF-8 / cp932 どちらも来る)。"""
    try:
        raw = subprocess.run(
            ["netsh", *args], capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


_RANGE_LINE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s*(\*)?\s*$")


def excluded_ranges() -> list[tuple[int, int, bool]]:
    """netsh の除外範囲。(開始, 終了, 管理者設定か)。

    Hyper-V / WSL / Docker が起動時に動的に確保するため、再起動で変わる。
    この範囲のポートは空いて見えても bind で EACCES になる。
    """
    out: list[tuple[int, int, bool]] = []
    for proto in ("ipv4", "ipv6"):
        text = _netsh("int", proto, "show", "excludedportrange", "protocol=tcp")
        for line in text.splitlines():
            m = _RANGE_LINE_RE.match(line)
            if m:
                out.append((int(m.group(1)), int(m.group(2)), bool(m.group(3))))
    return sorted(set(out))


def dynamic_range() -> tuple[int, int]:
    """OS が一時的に使うポート範囲 (エフェメラル)。既定 49152-65535。"""
    nums = [int(n) for n in re.findall(r":\s*(\d+)", _netsh("int", "ipv4", "show", "dynamicport", "tcp"))]
    if len(nums) >= 2:
        return nums[0], nums[0] + nums[1] - 1
    return 49152, 65535


_DOCKER_PORT_RE = re.compile(r":(\d+)(?:-(\d+))?->")


def docker_compose_dirs() -> dict[int, str]:
    """Docker Desktop で動いているコンテナの 公開ポート -> compose の作業フォルダ。

    Docker が起動していなければ空。WSL 内の docker は見ない (重いため)。
    """
    try:
        raw = subprocess.run(
            ["docker", "ps", "--format",
             '{{.Ports}}|{{.Label "com.docker.compose.project.working_dir"}}'],
            capture_output=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if raw.returncode != 0:
        return {}
    out: dict[int, str] = {}
    for line in raw.stdout.decode("utf-8", errors="replace").splitlines():
        ports, _, workdir = line.rpartition("|")
        if not workdir:
            continue
        for m in _DOCKER_PORT_RE.finditer(ports):
            a = int(m.group(1))
            b = int(m.group(2) or a)
            for port in range(a, b + 1):
                out[port] = workdir
    return out


# 終了させない (OS・VS Code・WSL/Docker の中継など、落とすと巻き添えが大きいもの)
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "spoolsv.exe", "explorer.exe", "dwm.exe",
    "code.exe", "wslrelay.exe", "wslservice.exe", "wslhost.exe", "vmmem", "vmmemwsl",
    "com.docker.backend.exe", "docker desktop.exe", "vpnkit.exe",
}


def protected_reason(pid: int) -> str | None:
    """終了させてはいけない理由。問題なければ None。"""
    if pid <= 4 or pid == os.getpid():
        return "システムまたは LPortMan 自身のプロセス"
    try:
        name = psutil.Process(pid).name().lower()
    except psutil.NoSuchProcess:
        return "プロセスは既に終了している"
    except psutil.AccessDenied:
        return "アクセス権が無い (別ユーザー・管理者のプロセス)"
    if name in PROTECTED_PROCESSES:
        return f"{name} は終了対象外 (OS・VS Code・WSL/Docker 関連)"
    return None


def describe_process(pid: int) -> dict[str, object]:
    p = psutil.Process(pid)
    try:
        cmd = " ".join(p.cmdline())
    except (psutil.AccessDenied, psutil.ZombieProcess):
        cmd = ""
    try:
        children = p.children(recursive=True)
    except psutil.Error:
        children = []
    return {"pid": pid, "name": p.name(), "cmdline": cmd, "children": [(c.pid, c.name()) for c in children]}


def kill_process(pid: int, timeout: float = 3.0) -> list[int]:
    """子プロセスごと終了する。終了した PID を返す。"""
    reason = protected_reason(pid)
    if reason:
        raise PermissionError(reason)
    root = psutil.Process(pid)
    procs = root.children(recursive=True) + [root]
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    return [p.pid for p in procs]
