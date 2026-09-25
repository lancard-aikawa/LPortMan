"""衝突の解消案 (提案のみ。プロジェクトのファイルは変更しない)。

方針: 衝突しているプロジェクトごとに「ずらし幅」を1つ決め、そのプロジェクトの
全ポートを同じ幅でずらす (例: +10000 なら 9099 -> 19099, 8080 -> 18080)。
プロジェクト単位で幅が揃っていると覚えやすく、相談もしやすい。

据え置き (+0) の優先順: 稼働中 > 台帳で予約済み > tier normal > 衝突ポートが多い > 名前順。
どれを据え置くかは相談で入れ替えてよい。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from lportman import analyze, scanner
from lportman.wellknown import BY_PORT

# +10000 系を基本に、埋まっていれば +10100, +10200 ... と細かくずらす
OFFSETS = [base + sub for base in (10000, 20000, 30000) for sub in range(0, 1000, 100)]
TIER_ORDER = {"normal": 0, "private": 1, "scratch": 2, "sample": 3}
SHIFT_RANGE = (1024, 10000)  # 一律にずらすのはこの範囲 (開発ツールの既定ポート帯)


@dataclass
class Change:
    finding: scanner.Finding
    new_port: int


@dataclass
class PlanItem:
    project: scanner.Project
    offset: int | None  # 0 = 据え置き / None = 候補が見つからない
    conflict_ports: list[int]
    changes: list[Change] = field(default_factory=list)
    pending: bool = False  # 台帳に予約済みで、設定の変更待ち

    @property
    def label(self) -> str:
        if self.pending:
            return "予約済み (設定の変更待ち)"
        if self.offset is None:
            return "候補なし (個別に相談)"
        if self.offset == 0:
            return "据え置き (同時に動かすときだけ調整)" if self.project.tier == "sample" else "据え置き"
        return f"+{self.offset}"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def make_plan(report: analyze.Report) -> list[PlanItem]:
    # 対象: 高・中の「複数プロジェクトが使用」に関わるプロジェクト
    involved: dict[str, set[int]] = {}
    for port, pi in report.ports.items():
        if not any(c.parties and c.severity in ("high", "mid") for c in pi.conflicts):
            continue
        for u in pi.uses:
            if u.project and u.kind != "ref":
                involved.setdefault(_norm(u.project.path), set()).add(port)
    if not involved:
        return []
    projects = {_norm(p.path): p for p in report.projects}
    live_projects = {_norm(o.path) for o in report.live_owner.values() if o}
    # 台帳の予約が設定に反映済みのプロジェクトは据え置き優先、未反映は「移行予定」
    reserved_projects = {_norm(e["project"]) for e in report.registry_status if e["status"] == "ok"}
    pending_projects = {_norm(e["project"]) for e in report.registry_status if e["status"] == "unapplied"}

    # 動かさないもの: 対象外プロジェクトの明示ポート・台帳・対象外プロセスの待ち受け
    taken: set[int] = set()
    for port, pi in report.ports.items():
        for u in pi.uses:
            if u.kind == "reserved" or (u.kind == "explicit" and u.party not in involved):
                taken.add(port)
    for lst in report.listeners:
        owner = report.live_owner.get((lst.port, lst.pid))
        if not owner or _norm(owner.path) not in involved:
            taken.add(lst.port)

    def ok(port: int, shifted: bool) -> bool:
        if not 1024 <= port <= 65535 or port in taken:
            return False
        if report.in_excluded(port) or report.in_dynamic(port):
            return False
        return not (shifted and port in BY_PORT)

    order = sorted(
        involved,
        key=lambda k: (
            k not in live_projects,
            k not in reserved_projects,
            TIER_ORDER.get(projects[k].tier, 9),
            -len(involved[k]),
            projects[k].name.lower(),
        ),
    )
    items: list[PlanItem] = []
    for key in order:
        proj = projects[key]
        fs = [f for f in proj.findings if f.kind != "ref"]
        ports = {f.port for f in fs}
        # ずらすのは開発ツールの既定ポート帯と、実際に衝突しているポート
        shift = {p for p in ports if SHIFT_RANGE[0] <= p < SHIFT_RANGE[1]} | involved[key]
        item = PlanItem(proj, None, sorted(involved[key]))
        items.append(item)
        if key in pending_projects:
            item.pending = True  # 新しいポートは台帳側で taken に入っている。旧ポートは空く予定
            continue
        if proj.tier == "sample" or not (ports & taken):
            item.offset = 0  # サンプルは同時に動かすときだけ調整すればよい
            taken.update(ports)
            continue
        for off in OFFSETS:
            if all(ok(p + off, True) for p in shift):
                item.offset = off
                taken.update(p + off for p in shift)
                taken.update(ports - shift)
                item.changes = [Change(f, f.port + off) for f in fs if f.port in shift]
                break
    return items


def _where(f: scanner.Finding) -> str:
    """変更箇所の説明。既定値から推定したものは「明記が必要」と書く。"""
    if f.kind == "default":
        if f.file.endswith("firebase.json"):
            key = f.detail.split(" ", 1)[0]
            return f"{f.file} に `{key}.port` を追記"
        if f.family == "vite":
            return f"{f.file} の {f.detail.split(':', 1)[0]} に `--port` を追記 (または vite.config の server.port)"
        return f"{f.file} ({f.detail}) にポート指定を追記"
    return f"{f.file} ({f.detail})"


def plan_markdown(report: analyze.Report, items: list[PlanItem]) -> str:
    lines = [
        "# ポート衝突の解消案",
        "",
        f"生成: {report.generated_at} (LPortMan)",
        "",
        "**これは提案です。** 共有プロジェクトが含まれるため、変更は関係者と相談のうえで行ってください。",
        "このファイルは手元用です (フルパスを含むため配布しない。相談には該当部分だけを抜き出して使う)。",
        "LPortMan はプロジェクトのファイルを変更しません。決まったら `lportman reserve` で台帳に登録します。",
        "",
        "方針: プロジェクトごとにずらし幅を1つ決め、開発ツールの既定ポート帯 (1024-9999) と"
        "衝突しているポートを同じ幅でずらす (例: +10000 なら 9099 → 19099)。"
        "80/443 や大きい番号のポートは動かさない。どれを据え置くかは入れ替えてかまいません。",
        "",
        "サンプル (config.json の sample_globs に一致) は据え置き。同時に動かすときだけ調整してください。",
        "",
        "## 一覧",
        "",
        "| プロジェクト | 区分 | 提案 | 衝突しているポート |",
        "|---|---|---|---|",
    ]
    for it in items:
        tier = analyze.TIER_LABEL.get(it.project.tier, "") or it.project.root_label
        prop = it.label
        lines.append(f"| {it.project.name} | {tier} | {prop} | {', '.join(map(str, it.conflict_ports))} |")

    for it in items:
        if not it.changes:
            continue
        path = it.project.path  # plan.md は手元用 (配布しない) なのでフルパス。reserve コマンドにそのまま使える
        lines += [
            "",
            f"## {it.project.name} (+{it.offset})",
            "",
            f"`{path}`",
            "",
            "| サービス | 現在 | 提案 | 変更箇所 |",
            "|---|---|---|---|",
        ]
        for c in it.changes:
            f = c.finding
            lines.append(f"| {f.service} | {f.port} | {c.new_port} | {_where(f)} |")
        lines += [
            "",
            "台帳に登録するコマンド (合意後):",
            "",
            "```",
            *[
                f'lportman reserve {c.new_port} --name "{c.finding.service}" --project "{path}"'
                for c in it.changes
            ],
            "```",
        ]
    lines.append("")
    return "\n".join(lines)
