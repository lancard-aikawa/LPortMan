"""静的調査・稼働状況・台帳・除外範囲を突き合わせて、ポート一覧と衝突を作る。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from lportman import live, scanner, store
from lportman.wellknown import BY_PORT, WELLKNOWN, WellKnown

SEVERITY_ORDER = {"high": 0, "mid": 1, "low": 2}
SEVERITY_LABEL = {"high": "高", "mid": "中", "low": "低"}
KIND_LABEL = {"explicit": "明示", "default": "既定", "ref": "参照", "reserved": "台帳"}
TIER_LABEL = {"normal": "", "private": "私的", "scratch": "相談中", "sample": "サンプル"}
LIGHT_TIERS = ("scratch", "sample")  # 衝突の重さを1段下げる


@dataclass
class Use:
    port: int
    service: str
    family: str
    kind: str  # explicit / default / ref / reserved
    file: str
    detail: str
    project: scanner.Project | None = None
    party: str = ""  # 衝突判定の単位 (同じ party 同士は衝突しない)


@dataclass
class Conflict:
    severity: str
    port: int
    message: str
    parties: list[str] = field(default_factory=list)


@dataclass
class PortInfo:
    port: int
    uses: list[Use] = field(default_factory=list)
    live: list[live.Listener] = field(default_factory=list)
    wellknown: list[WellKnown] = field(default_factory=list)
    excluded: bool = False
    dynamic: bool = False
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def severity(self) -> str | None:
        if not self.conflicts:
            return None
        return min((c.severity for c in self.conflicts), key=SEVERITY_ORDER.__getitem__)


@dataclass
class Report:
    generated_at: str
    config: dict[str, Any]
    projects: list[scanner.Project]
    listeners: list[live.Listener]
    excluded: list[tuple[int, int, bool]]
    dynamic: tuple[int, int]
    ports: dict[int, PortInfo]
    conflicts: list[Conflict]
    live_owner: dict[tuple[int, int], scanner.Project | None]
    # 台帳の予約ごとの反映状況 ({port, name, project, status, message})
    registry_status: list[dict[str, Any]] = field(default_factory=list)
    # 最近 (config の new_days 以内) 初めて見つかった "party|port"
    new_keys: set[str] = field(default_factory=set)

    def live_signature(self) -> frozenset[tuple[int, int, str]]:
        return frozenset((x.port, x.pid, x.process) for x in self.listeners)

    def is_new(self, u: Use) -> bool:
        return f"{u.party}|{u.port}" in self.new_keys

    def in_excluded(self, port: int) -> bool:
        return any(a <= port <= b for a, b, _ in self.excluded)

    def in_dynamic(self, port: int) -> bool:
        return self.dynamic[0] <= port <= self.dynamic[1]


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def norm_path(p: str) -> str:
    """パスの比較用 (大文字小文字・区切りを揃える)。"""
    return _norm(p)


# コンテナのポートを Windows 側で代理待ち受けするプロセス
DOCKER_RELAYS = {"wslrelay.exe", "com.docker.backend.exe", "vpnkit.exe", "docker-proxy.exe"}


def _project_containing(path: str, projects: list[scanner.Project]) -> scanner.Project | None:
    target = _norm(path)
    best: scanner.Project | None = None
    for proj in projects:
        pp = _norm(proj.path)
        if (target == pp or target.startswith(pp + os.sep)) and (best is None or len(pp) > len(_norm(best.path))):
            best = proj
    return best


def project_containing(path: str, projects: list[scanner.Project]) -> scanner.Project | None:
    """path を含む (または path に等しい) プロジェクトのうち、最も深いもの。"""
    return _project_containing(path, projects)


def is_relay(listener: live.Listener) -> bool:
    return listener.process.lower() in DOCKER_RELAYS


def _owner_of(
    listener: live.Listener, projects: list[scanner.Project], docker_dirs: dict[int, str],
) -> scanner.Project | None:
    if is_relay(listener):
        if listener.port in docker_dirs:
            return _project_containing(docker_dirs[listener.port], projects)
        # compose でこのポートを公開しているプロジェクトが1つだけなら、それとみなす
        cands = [p for p in projects if any(f.family == "docker" and f.port == listener.port for f in p.findings)]
        return cands[0] if len(cands) == 1 else None
    return _project_containing(listener.cwd, projects) if listener.cwd else None


def _downgrade(sev: str) -> str:
    return {"high": "mid", "mid": "low", "low": "low"}[sev]


def build_report(
    config: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    projects: list[scanner.Project] | None = None,
) -> Report:
    config = config or store.load_config()
    registry = registry or store.load_registry()
    if projects is None:
        projects = scanner.scan_all(config)
    listeners = live.listeners()
    excluded = live.excluded_ranges()
    dynamic = live.dynamic_range()

    by_path = {_norm(p.path): p for p in projects}
    ports: dict[int, PortInfo] = {}

    def info(port: int) -> PortInfo:
        if port not in ports:
            ports[port] = PortInfo(port, wellknown=BY_PORT.get(port, []))
        return ports[port]

    for proj in projects:
        for f in proj.findings:
            info(f.port).uses.append(
                Use(f.port, f.service, f.family, f.kind, f.file, f.detail, proj, _norm(proj.path))
            )
    for r in registry.get("reservations", []):
        try:
            port = int(r["port"])
        except (KeyError, TypeError, ValueError):
            continue
        proj_path = r.get("project") or ""
        proj = by_path.get(_norm(proj_path)) if proj_path else None
        party = _norm(proj_path) if proj_path else f"registry:{r.get('name', port)}"
        info(port).uses.append(
            Use(port, r.get("name", "台帳"), "registry", "reserved", "registry.json", r.get("note", ""), proj, party)
        )

    docker_dirs = live.docker_compose_dirs() if any(is_relay(x) for x in listeners) else {}
    live_owner: dict[tuple[int, int], scanner.Project | None] = {}
    for lst in listeners:
        live_owner[(lst.port, lst.pid)] = _owner_of(lst, projects, docker_dirs)
    # 中継経由で持ち主が決まったプロジェクトが公開している他のポートも、そのプロジェクトとみなす
    relay_owners = [o for lst in listeners if is_relay(lst) and (o := live_owner[(lst.port, lst.pid)])]
    for lst in listeners:
        if is_relay(lst) and live_owner[(lst.port, lst.pid)] is None:
            cands = {id(p): p for p in relay_owners
                     if any(f.family == "docker" and f.port == lst.port for f in p.findings)}
            if len(cands) == 1:
                live_owner[(lst.port, lst.pid)] = next(iter(cands.values()))
        if lst.port in ports:
            ports[lst.port].live.append(lst)

    tiers = {_norm(p.path): p.tier for p in projects}
    names = {_norm(p.path): p.name for p in projects}
    conflicts: list[Conflict] = []

    for port, pi in sorted(ports.items()):
        pi.excluded = any(a <= port <= b for a, b, _ in excluded)
        pi.dynamic = dynamic[0] <= port <= dynamic[1]
        active = [u for u in pi.uses if u.kind != "ref"]
        if not active:
            continue

        # 1. 複数プロジェクトが同じポートを宣言
        parties: dict[str, bool] = {}  # party -> explicit か
        for u in active:
            parties[u.party] = parties.get(u.party, False) or u.kind in ("explicit", "reserved")
        if len(parties) >= 2:
            n_explicit = sum(parties.values())
            sev = "high" if n_explicit >= 2 else "mid" if n_explicit == 1 else "low"
            non_scratch = [p for p in parties if tiers.get(p, "normal") not in LIGHT_TIERS]
            if len(non_scratch) <= 1:
                sev = _downgrade(sev)
            label = [names.get(p, p.removeprefix("registry:")) for p in parties]
            pi.conflicts.append(Conflict(
                sev, port, f"{len(parties)} プロジェクトが使用 (同時起動不可): " + ", ".join(label), label,
            ))

        # 2. Windows の除外範囲 / 動的範囲
        if pi.excluded:
            pi.conflicts.append(Conflict("high", port, "Windows のポート除外範囲内 (bind が EACCES で失敗する)"))
        elif pi.dynamic:
            pi.conflicts.append(Conflict("mid", port, "OS の動的ポート範囲内 (他プロセスに一時的に取られることがある)"))

        # 3. よく使われるポートを別系統のツールが明示使用
        families = {u.family for u in active}
        for u in active:
            if u.kind not in ("explicit", "reserved"):
                continue
            others = [w for w in pi.wellknown if w.family not in families]
            if others:
                pi.conflicts.append(Conflict(
                    "low", port, f"{u.service} が {', '.join(w.service for w in others)} の既定ポートを使用",
                ))
                break

        # 4. 宣言していない別プロセスが待ち受け中
        owners = {_norm(p.path) for lst in pi.live if (p := live_owner.get((lst.port, lst.pid)))}
        foreign = [lst for lst in pi.live if live_owner.get((lst.port, lst.pid)) is None]
        declared = {u.party for u in active}
        if pi.live and not (owners & declared):
            who = ", ".join(sorted({lst.process for lst in foreign} or {"?"}))
            if foreign and all(is_relay(x) for x in foreign):
                msg = f"WSL/Docker 経由で使用中 (どのプロジェクトか特定できず): {who}"
            else:
                msg = f"宣言元以外のプロセスが使用中: {who}"
            pi.conflicts.append(Conflict("mid", port, msg))

        conflicts.extend(pi.conflicts)

    conflicts.sort(key=lambda c: (SEVERITY_ORDER[c.severity], c.port))
    return Report(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        config=config, projects=projects, listeners=listeners,
        excluded=excluded, dynamic=dynamic, ports=ports, conflicts=conflicts, live_owner=live_owner,
        registry_status=registry_status(registry, projects),
    )


# ---------------------------------------------------------------- 台帳の反映状況

REG_STATUS_LABEL = {
    "ok": "反映済み", "unapplied": "未反映", "noproject": "", "noscan": "設定ファイルなし", "missing": "フォルダなし",
}


def registry_status(registry: dict[str, Any], projects: list[scanner.Project]) -> list[dict[str, Any]]:
    """予約したポートが、そのプロジェクトの設定に実際に書かれているか。"""
    by_path = {_norm(p.path): p for p in projects}
    out: list[dict[str, Any]] = []
    for r in registry.get("reservations", []):
        try:
            port = int(r["port"])
        except (KeyError, TypeError, ValueError):
            continue
        path = r.get("project") or ""
        entry = {"port": port, "name": r.get("name", ""), "project": path, "status": "noproject", "message": ""}
        out.append(entry)
        if not path:
            continue
        proj = by_path.get(_norm(path))
        if proj is None:
            if os.path.isdir(path):
                entry.update(status="noscan", message="ポートを書いた設定ファイルが見つからない")
            else:
                entry.update(status="missing", message="プロジェクトのフォルダが無い")
            continue
        if any(f.port == port and f.kind != "ref" for f in proj.findings):
            entry.update(status="ok", message="設定に反映済み")
            continue
        # 同じサービスの旧ポートが残っていれば示す
        old = sorted({f.port for f in proj.findings if f.service == entry["name"] and f.kind != "ref"})
        entry.update(status="unapplied", message=(
            f"設定はまだ {', '.join(map(str, old))} のまま" if old else f"設定に {port} が見当たらない"
        ))
    return out


# ---------------------------------------------------------------- 新着 (前回からの差分)


def update_seen(report: Report) -> None:
    """初めて見つかった日時を data/seen.json に記録し、report.new_keys を埋める。

    初回 (seen.json が無い) は全件を既存 (baseline) 扱いにして、新着にしない。
    消えたものも記録は残す (ドライブ未接続などで一時的に消えても、戻ったときに新着扱いしない)。
    """
    data = store.load_seen()
    first = data is None
    entries: dict[str, str] = dict(data.get("entries", {})) if data else {}
    now = datetime.now()
    stamp = now.isoformat(timespec="seconds")
    for pi in report.ports.values():
        for u in pi.uses:
            if u.kind == "ref":
                continue
            entries.setdefault(f"{u.party}|{u.port}", "baseline" if first else stamp)
    store.save_seen({"version": 1, "entries": entries})
    cutoff = (now - timedelta(days=int(report.config.get("new_days", 7)))).isoformat(timespec="seconds")
    report.new_keys = {k for k, v in entries.items() if v != "baseline" and v >= cutoff}


def refresh_live(report: Report) -> Report:
    """設定ファイルは読み直さず、待ち受け・除外範囲・台帳だけ更新した Report を作る。"""
    new = build_report(config=report.config, projects=report.projects)
    new.new_keys = report.new_keys
    return new


# ---------------------------------------------------------------- 空き確認・提案


def check_port(report: Report, port: int, own_project: str | None = None) -> dict[str, Any]:
    """status: free (使える) / warn (使えるが注意) / busy (使えない・使うべきでない)

    own_project を渡すと、そのプロジェクト自身の宣言・待ち受けは障害として数えない
    (設定にポートを書いてから台帳に予約する、という順で使うため)。
    """
    own = _norm(own_project) if own_project else None
    reasons: list[str] = []
    status = "free"

    def bump(s: str) -> None:
        nonlocal status
        order = ["free", "warn", "busy"]
        if order.index(s) > order.index(status):
            status = s

    if not 1 <= port <= 65535:
        return {"port": port, "status": "busy", "reasons": ["範囲外のポート番号"]}
    for lst in report.listeners:
        if lst.port == port:
            owner = report.live_owner.get((lst.port, lst.pid))
            if own and owner and _norm(owner.path) == own:
                continue
            bump("busy")
            extra = " / 起動ごとに変わる一時ポート。VS Code のウィンドウを開き直せば空く" \
                if live.CLAUDE_IDE_LABEL in lst.process else ""
            reasons.append(f"使用中: {lst.process} (PID {lst.pid}){extra}")
    if report.in_excluded(port):
        bump("busy")
        reasons.append("Windows のポート除外範囲内")
    elif report.in_dynamic(port):
        bump("warn")
        reasons.append("OS の動的ポート範囲内")
    pi = report.ports.get(port)
    for u in pi.uses if pi else []:
        if own and u.party == own:
            continue
        who = u.project.name if u.project else "台帳"
        if u.kind in ("explicit", "reserved"):
            bump("busy")
        else:
            bump("warn")
        reasons.append(f"{KIND_LABEL[u.kind]}: {who} / {u.service} ({u.file})")
    for w in BY_PORT.get(port, []):
        bump("warn")
        reasons.append(f"よく使われるポート: {w.service}")
    return {"port": port, "status": status, "reasons": reasons}


def suggest_ports(report: Report, base: int | None = None, count: int = 5) -> list[int]:
    """空きポート候補。base を渡すと base+offsets を優先 (例: 5173 -> 15173, 25173)。"""
    out: list[int] = []
    candidates: list[int] = []
    if base:
        candidates += [base + off for off in report.config.get("offsets", [10000, 20000])]
    lo, hi = report.config.get("assign_range", [20000, 29999])
    candidates += list(range(int(lo), int(hi) + 1))
    for port in candidates:
        if port in out or not 1024 <= port <= 65535:
            continue
        if check_port(report, port)["status"] == "free":
            out.append(port)
            if len(out) >= count:
                break
    return out


# ---------------------------------------------------------------- JSON 出力 (Claude 向け)


def root_path_of(report: Report, proj: scanner.Project) -> str:
    for r in report.config.get("roots", []):
        if r.get("label") == proj.root_label:
            return os.path.normpath(r["path"])
    return proj.path


def to_json(report: Report) -> dict[str, Any]:
    def ppath(proj: scanner.Project | None) -> str | None:
        return proj.display_path(root_path_of(report, proj)) if proj else None

    ports_out = []
    for port, pi in sorted(report.ports.items()):
        ports_out.append({
            "port": port,
            "severity": pi.severity,
            "uses": [
                {
                    "project": u.project.name if u.project else None,
                    "path": ppath(u.project),
                    "service": u.service,
                    "kind": u.kind,
                    "file": u.file,
                    "detail": u.detail,
                }
                for u in pi.uses
            ],
            "live": [{"pid": lst.pid, "process": lst.process} for lst in pi.live],
            "new": any(report.is_new(u) for u in pi.uses),
            "wellknown": [w.service for w in pi.wellknown],
            "notes": [c.message for c in pi.conflicts],
        })
    live_out = []
    for lst in report.listeners:
        owner = report.live_owner.get((lst.port, lst.pid))
        live_out.append({
            "port": lst.port, "address": lst.address, "pid": lst.pid, "process": lst.process,
            "project": owner.name if owner else None, "path": ppath(owner),
        })
    return {
        "generated_at": report.generated_at,
        "about": (
            "LPortMan が生成したローカルポート一覧。新しくポートを決めるときは "
            f"`{store.cli_command()} suggest [既定ポート]` で候補を取り、"
            "`check <port>` で確認し、決めたら `reserve <port> --name ... --project ...` "
            "で台帳に登録すること。既存プロジェクトのポート変更は `plan` の解消案を示して相談してから。"
        ),
        "policy": {
            "assign_range": report.config.get("assign_range"),
            "offsets": report.config.get("offsets"),
            "excluded_ranges": [[a, b] for a, b, _ in report.excluded],
            "dynamic_range": list(report.dynamic),
        },
        "kinds": {
            "explicit": "設定に明記", "default": "ツール既定値から推定",
            "ref": ".env の *_PORT (接続先の可能性。衝突判定対象外)", "reserved": "台帳 registry.json で予約",
        },
        "conflicts": [
            {"severity": c.severity, "port": c.port, "message": c.message} for c in report.conflicts
        ],
        "ports": ports_out,
        "registry": [
            {**e, "project": ppath(next((p for p in report.projects if _norm(p.path) == _norm(e["project"])), None))
             or e["project"]}
            for e in report.registry_status
        ],
        "live": live_out,
        "wellknown": [{"port": w.port, "service": w.service} for w in WELLKNOWN],
    }


def run_and_save() -> Report:
    report = build_report()
    update_seen(report)
    store.save_json(store.PORTS_FILE, to_json(report))
    return report
