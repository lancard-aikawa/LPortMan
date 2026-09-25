"""グラフ表示 (tkinter Canvas)。

- 衝突マトリクス: 行=プロジェクト、列=衝突ポート。誰と誰がどのポートでぶつかっているか
- ポート分布: 0-65535 上の宣言ポートと、払い出し範囲・動的範囲・除外範囲の帯

色: 種別は1色 (青) の濃淡 (量ではなく強さの順序)。衝突の重さは状態色で、必ず文字 (高/中/低) を添える。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import TYPE_CHECKING, Any

from lportman import analyze, plan

if TYPE_CHECKING:
    from lportman.gui import App

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# 種別: 青のランプの段階 (台帳 > 明示 > 既定)
KIND_FILL = {"reserved": "#104281", "explicit": "#2a78d6", "default": "#b7d3f6"}
KIND_TEXT = {"reserved": "#ffffff", "explicit": "#ffffff", "default": INK}
# 状態色 (重さ)。色だけにせず文字を添える
SEV_FILL = {"high": "#d03b3b", "mid": "#fab219", "low": "#e1e0d9"}
SEV_TEXT = {"high": "#ffffff", "mid": INK, "low": INK}
LIVE = "#0ca30c"
BAR = "#2a78d6"
ASSIGN_BAND = "#cde2fb"
DYNAMIC_BAND = "#ecebe6"
EXCLUDED = "#d03b3b"
EXCLUDED_STIPPLE = "gray50"  # 衝突「高」の塗りつぶしの棒と区別するため網かけ


class Tooltip:
    """Canvas 上に描くツールチップ。"""

    def __init__(self, canvas: tk.Canvas) -> None:
        self.c = canvas

    def show(self, x: float, y: float, lines: list[str]) -> None:
        self.hide()
        text = "\n".join(lines)
        t = self.c.create_text(x + 14, y + 14, text=text, anchor=tk.NW, fill=INK,
                               font=("", 9), width=460, tags=("tip",))
        x0, y0, x1, y1 = self.c.bbox(t)
        # 右端・下端からはみ出すなら左上に出す。左端・上端より外には出さない
        vx0, vy0 = self.c.canvasx(0), self.c.canvasy(0)
        vw = self.c.canvasx(self.c.winfo_width())
        vh = self.c.canvasy(self.c.winfo_height())
        dx = -(x1 - x0) - 28 if x1 + 8 > vw else 0
        dy = -(y1 - y0) - 28 if y1 + 8 > vh else 0
        dx = max(dx, vx0 + 8 - x0)
        dy = max(dy, vy0 + 8 - y0)
        self.c.move(t, dx, dy)
        x0, y0, x1, y1 = self.c.bbox(t)
        r = self.c.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4, fill="#ffffff", outline=AXIS,
                                    tags=("tip",))
        self.c.tag_lower(r, t)

    def hide(self) -> None:
        self.c.delete("tip")


def _scrolled_canvas(parent: tk.Misc) -> tk.Canvas:
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)
    c = tk.Canvas(frame, background=SURFACE, highlightthickness=0)
    ysb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=c.yview)
    xsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=c.xview)
    c.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    xsb.pack(side=tk.BOTTOM, fill=tk.X)
    ysb.pack(side=tk.RIGHT, fill=tk.Y)
    c.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    c.bind("<MouseWheel>", lambda e: c.yview_scroll(-1 if e.delta > 0 else 1, "units"))
    c.bind("<Shift-MouseWheel>", lambda e: c.xview_scroll(-1 if e.delta > 0 else 1, "units"))
    return c


class GraphView:
    def __init__(self, parent: ttk.Frame, app: App) -> None:
        self.app = app
        bar = ttk.Frame(parent)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        self.mode = tk.StringVar(value="matrix")
        ttk.Radiobutton(bar, text="衝突マトリクス", value="matrix", variable=self.mode,
                        command=self.redraw).pack(side=tk.LEFT)
        ttk.Radiobutton(bar, text="ポート分布", value="dist", variable=self.mode,
                        command=self.redraw).pack(side=tk.LEFT, padx=(8, 0))
        self.include_low = tk.BooleanVar(value=False)
        self.low_check = ttk.Checkbutton(bar, text="重さ「低」も含める", variable=self.include_low,
                                         command=self.redraw)
        self.low_check.pack(side=tk.LEFT, padx=(16, 0))
        self.caption = ttk.Label(bar, foreground=INK2)
        self.caption.pack(side=tk.LEFT, padx=16)

        self.c = _scrolled_canvas(parent)
        self.tip = Tooltip(self.c)
        self.hits: list[tuple[tuple[float, float, float, float], list[str], str | None]] = []
        self.c.bind("<Motion>", self._on_motion)
        self.c.bind("<Leave>", lambda e: self.tip.hide())
        self.c.bind("<Double-1>", self._on_double)
        self.c.bind("<Configure>", lambda e: self.mode.get() == "dist" and self.redraw())
        self.font = tkfont.nametofont("TkDefaultFont")

    # ------------------------------------------------------------ 共通

    def redraw(self) -> None:
        self.c.delete("all")
        self.hits.clear()
        r = self.app.report
        if r is None:
            return
        if self.mode.get() == "matrix":
            self.low_check.state(["!disabled"])
            self._draw_matrix(r)
        else:
            self.low_check.state(["disabled"])
            self._draw_dist(r)

    def _hit(self, box: tuple[float, float, float, float], lines: list[str], path: str | None = None) -> None:
        self.hits.append((box, lines, path))

    def _find(self, e: tk.Event) -> tuple[list[str], str | None] | None:
        x, y = self.c.canvasx(e.x), self.c.canvasy(e.y)
        for (x0, y0, x1, y1), lines, path in reversed(self.hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return lines, path
        return None

    def _on_motion(self, e: tk.Event) -> None:
        found = self._find(e)
        if found is None:
            self.tip.hide()
            return
        self.tip.show(self.c.canvasx(e.x), self.c.canvasy(e.y), found[0])

    def _on_double(self, e: tk.Event) -> None:
        found = self._find(e)
        if found and found[1] and os.path.isdir(found[1]):
            os.startfile(found[1])

    def _legend(self, x: float, y: float, items: list[tuple[str, ...]]) -> float:
        """凡例を1行で描く。items = (塗り, 枠, 中の文字, 説明[, 網かけ])。描いた右端の x を返す。"""
        for fill, outline, inner, label, *rest in items:
            self.c.create_rectangle(x, y, x + 30, y + 16, fill=fill, outline=outline, width=2 if outline else 0,
                                    stipple=rest[0] if rest else "")
            if inner:
                self.c.create_text(x + 15, y + 8, text=inner, font=("", 8),
                                   fill=INK if fill in (KIND_FILL["default"], SEV_FILL["mid"], SEV_FILL["low"], SURFACE) else "#ffffff")
            t = self.c.create_text(x + 36, y + 8, text=label, anchor=tk.W, fill=INK2, font=("", 9))
            x = self.c.bbox(t)[2] + 16
        return x

    # ------------------------------------------------------------ 衝突マトリクス

    def _draw_matrix(self, r: analyze.Report) -> None:
        sevs = ("high", "mid", "low") if self.include_low.get() else ("high", "mid")
        show_default = self.app.show_default.get()
        q = self.app.filter_var.get().strip().lower()

        cols: list[int] = []
        rows: dict[str, Any] = {}  # party -> Project or None
        for port, pi in sorted(r.ports.items()):
            party_conf = [c for c in pi.conflicts if c.parties and c.severity in sevs]
            if not party_conf:
                continue
            uses = [u for u in pi.uses if u.kind != "ref" and (show_default or u.kind != "default")]
            if len({u.party for u in uses}) < 2:
                continue
            cols.append(port)
            for u in uses:
                rows.setdefault(u.party, u.project)

        def row_name(party: str) -> str:
            proj = rows[party]
            return proj.name if proj else party.removeprefix("registry:") + " (台帳)"

        if q:
            keep_rows = {p for p in rows if q in row_name(p).lower()}
            keep_cols = [c for c in cols if q in str(c)]
            if keep_cols:
                cols = keep_cols
            elif keep_rows:
                rows = {p: v for p, v in rows.items() if p in keep_rows}

        if not cols or not rows:
            self.caption.configure(text="")
            self.c.create_text(20, 20, anchor=tk.NW, fill=INK2, font=("", 11),
                               text="表示する衝突がありません (「重さ「低」も含める」や絞り込みを確認)")
            self.c.configure(scrollregion=(0, 0, 400, 60))
            return

        # 解消案のずらし幅
        offsets: dict[str, str] = {}
        for it in plan.make_plan(r):
            key = os.path.normcase(os.path.normpath(it.project.path))
            offsets[key] = "予約済み" if it.pending else it.label.split(" ", 1)[0]

        cell_map: dict[tuple[str, int], analyze.Use] = {}
        for port in cols:
            for u in r.ports[port].uses:
                if u.kind == "ref" or (not show_default and u.kind == "default"):
                    continue
                prev = cell_map.get((u.party, port))
                order = {"reserved": 0, "explicit": 1, "default": 2}
                if prev is None or order[u.kind] < order[prev.kind]:
                    cell_map[(u.party, port)] = u
        live_cells = {
            (os.path.normcase(os.path.normpath(o.path)), port)
            for (port, _pid), o in r.live_owner.items() if o
        }

        # 衝突ポート数の多い順
        order_rows = sorted(rows, key=lambda p: (-sum((p, c) in cell_map for c in cols), row_name(p).lower()))

        cw, ch, gap = 50, 26, 2
        label_w = max(self.font.measure(row_name(p) + "  [相談中]") for p in order_rows) + 24
        top = 70
        left = 12 + label_w
        tiers = analyze.TIER_LABEL

        # 凡例
        lx = self._legend(12, 10, [
            (KIND_FILL["explicit"], "", "明示", "設定に明記"),
            (KIND_FILL["default"], "", "既定", "ツール既定値から推定"),
            (KIND_FILL["reserved"], "", "台帳", "台帳で予約"),
            (SURFACE, LIVE, "", "稼働中"),
        ])
        self._legend(lx + 8, 10, [
            (SEV_FILL["high"], "", "高", ""), (SEV_FILL["mid"], "", "中", ""), (SEV_FILL["low"], "", "低", "重さ"),
        ])

        # 列見出し: ポート番号 + 重さ
        for j, port in enumerate(cols):
            x = left + j * cw
            pi = r.ports[port]
            sev = min((c.severity for c in pi.conflicts if c.parties), key=analyze.SEVERITY_ORDER.__getitem__)
            self.c.create_text(x + cw / 2, top - 30, text=str(port), fill=INK, font=("", 9, "bold"))
            self.c.create_rectangle(x + gap, top - 20, x + cw - gap, top - 4, fill=SEV_FILL[sev], width=0)
            self.c.create_text(x + cw / 2, top - 12, text=analyze.SEVERITY_LABEL[sev], fill=SEV_TEXT[sev],
                               font=("", 8, "bold"))
            wk = ", ".join(w.service for w in pi.wellknown)
            self._hit((x, top - 38, x + cw, top), [f"ポート {port}"] + [f"- {c.message}" for c in pi.conflicts]
                      + ([f"よく使われる: {wk}"] if wk else []))
        right = left + len(cols) * cw
        self.c.create_text(right + 14, top - 12, text="解消案", anchor=tk.W, fill=INK2, font=("", 9, "bold"))

        # 行
        for i, party in enumerate(order_rows):
            y = top + i * ch
            proj = rows[party]
            if i % 2 == 0:
                self.c.create_rectangle(0, y, right + 120, y + ch, fill="#f4f4f2", width=0)
            tier = tiers.get(proj.tier, "") if proj else ""
            name = row_name(party) + (f"  [{tier}]" if tier else "")
            self.c.create_text(12, y + ch / 2, text=name, anchor=tk.W,
                               fill=MUTED if proj and proj.tier in analyze.LIGHT_TIERS else INK, font=("", 9))
            path = proj.path if proj else None
            self._hit((0, y, left, y + ch), [name, path or "", "ダブルクリックでフォルダを開く"] if path else [name], path)
            self.c.create_text(right + 14, y + ch / 2, text=offsets.get(party, ""), anchor=tk.W, fill=INK2,
                               font=("", 9))
            for j, port in enumerate(cols):
                x = left + j * cw
                self.c.create_line(x + cw / 2, y, x + cw / 2, y + ch, fill=GRID)
                u = cell_map.get((party, port))
                if u is None:
                    continue
                live = (party, port) in live_cells
                self.c.create_rectangle(x + gap + 1, y + gap + 1, x + cw - gap - 1, y + ch - gap - 1,
                                        fill=KIND_FILL[u.kind], outline=LIVE if live else "",
                                        width=3 if live else 0)
                self.c.create_text(x + cw / 2, y + ch / 2, text=analyze.KIND_LABEL[u.kind],
                                   fill=KIND_TEXT[u.kind], font=("", 8))
                self._hit((x, y, x + cw, y + ch), [
                    f"{row_name(party)} : {port}",
                    f"{u.service} ({analyze.KIND_LABEL[u.kind]})",
                    f"{u.file}  {u.detail}".strip(),
                ] + (["稼働中"] if live else []), path)

        h = top + len(order_rows) * ch + 20
        self.c.configure(scrollregion=(0, 0, right + 140, h))
        self.caption.configure(text=f"{len(order_rows)} プロジェクト × {len(cols)} ポート。セルにマウスで詳細")

    # ------------------------------------------------------------ ポート分布

    def _draw_dist(self, r: analyze.Report) -> None:
        w = max(self.c.winfo_width(), 700)
        left, right = 60, w - 30
        split = left + (right - left) * 0.6  # 0-9999 を 60% に拡大

        def xpos(port: float) -> float:
            if port < 10000:
                return left + (split - left) * port / 10000
            return split + (right - split) * (port - 10000) / (65536 - 10000)

        lo, hi = r.config.get("assign_range", [20000, 29999])
        top = 70
        base = max(260, min(self.c.winfo_height() - 50, 560))
        self._legend(12, 10, [
            (BAR, "", "", "宣言されたポート (高さ=使うプロジェクト数)"),
            (SEV_FILL["high"], "", "高", ""), (SEV_FILL["mid"], "", "中", "衝突の重さ"),
            (SURFACE, LIVE, "", "稼働中"),
        ])
        self._legend(12, 34, [
            (ASSIGN_BAND, "", "", f"払い出し範囲 {lo}-{hi}"),
            (DYNAMIC_BAND, "", "", f"動的範囲 {r.dynamic[0]}-{r.dynamic[1]}"),
            (EXCLUDED, "", "", "Windows 除外範囲", EXCLUDED_STIPPLE),
        ])

        # 帯
        def band(a: float, b: float, fill: str, lines: list[str], stipple: str = "") -> None:
            x0, x1 = xpos(a), max(xpos(b + 1), xpos(a) + 3)
            self.c.create_rectangle(x0, top, x1, base, fill=fill, width=0, stipple=stipple)
            self._hit((x0, top, x1, base), lines)

        band(r.dynamic[0], r.dynamic[1], DYNAMIC_BAND, [f"動的範囲 {r.dynamic[0]}-{r.dynamic[1]}",
                                                        "OS が一時的に割り当てる。固定ポートには使わない"])
        band(lo, hi, ASSIGN_BAND, [f"払い出し範囲 {lo}-{hi}", "新しいポートはここから (lportman suggest)"])
        for a, b, admin in r.excluded:
            band(a, b, EXCLUDED, [f"Windows 除外範囲 {a}-{b}" + (" (管理者設定)" if admin else ""),
                                  "空いて見えても bind が EACCES で失敗する"], EXCLUDED_STIPPLE)

        # 軸
        self.c.create_line(left, base, right, base, fill=AXIS, width=2)
        self.c.create_line(split, top - 6, split, base + 6, fill=MUTED, dash=(3, 3))
        self.c.create_text(split, top - 10, text="ここから縮尺が変わる", fill=MUTED, font=("", 8))
        for t in (0, 1024, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000,
                  20000, 30000, 40000, 50000, 60000, 65535):
            x = xpos(t)
            self.c.create_line(x, base, x, base + 5, fill=AXIS)
            self.c.create_text(x, base + 16, text=str(t), fill=MUTED, font=("", 8))

        # 棒
        max_parties = 1
        items = []
        for port, pi in sorted(r.ports.items()):
            parties = {u.party for u in pi.uses if u.kind != "ref"}
            if not parties:
                continue
            max_parties = max(max_parties, len(parties))
            items.append((port, pi, parties))
        unit = (base - top - 30) / max_parties
        for n in range(1, max_parties + 1):  # 目盛り (プロジェクト数)
            y = base - n * unit
            if n % 2 == 0 or n == 1:
                self.c.create_line(left, y, right, y, fill=GRID)
                self.c.create_text(left - 8, y, text=str(n), anchor=tk.E, fill=MUTED, font=("", 8))
        self.c.create_text(left - 8, top - 10, text="数", anchor=tk.E, fill=MUTED, font=("", 8))

        for port, pi, parties in items:
            x = xpos(port)
            y = base - len(parties) * unit
            sev = pi.severity if pi.severity in ("high", "mid") else None
            fill = SEV_FILL[sev] if sev else BAR
            self.c.create_rectangle(x - 2, y, x + 2, base - 1, fill=fill, width=0)
            if pi.live:
                self.c.create_oval(x - 5, base + 24, x + 5, base + 34, fill=LIVE, width=0)
            names = sorted({u.project.name if u.project else "(台帳)" for u in pi.uses if u.kind != "ref"})
            self._hit((x - 5, top, x + 5, base + 36), [
                f"ポート {port}  ({len(parties)} プロジェクト)",
                *[f"- {n}" for n in names[:12]],
                *(["  ..."] if len(names) > 12 else []),
                *[f"! {c.message.split(':', 1)[0] if c.parties else c.message}" for c in pi.conflicts],
                *([f"稼働中: {', '.join(sorted({x.process for x in pi.live}))}"] if pi.live else []),
            ])

        # 番号ラベルは棒の上に重ねる (棒に隠れないよう最後に描く)
        placed: list[tuple[float, float, float, float]] = []
        for port, pi, parties in sorted(items, key=lambda t: -len(t[2])):
            x, y = xpos(port), base - len(parties) * unit
            if not (len(parties) >= 3 or pi.severity == "high"):
                continue
            wlab = self.font.measure(str(port)) / 2 + 3
            box = (x - wlab, y - 16, x + wlab, y - 2)
            if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed):
                continue
            placed.append(box)
            self.c.create_rectangle(*box, fill=SURFACE, width=0)  # 隣の棒と重なっても読めるように
            self.c.create_text(x, y - 9, text=str(port), fill=INK2, font=("", 8))

        self.c.configure(scrollregion=(0, 0, w, base + 60))
        self.caption.configure(text="0-9999 を拡大表示。棒・帯にマウスで詳細")
