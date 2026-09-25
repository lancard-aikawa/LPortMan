"""LPortMan の画面 (tkinter)。ポートを使わないネイティブ UI。"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from typing import Any

from lportman import analyze, graph, live, plan, store
from lportman.wellknown import WELLKNOWN

SEV_BG = {"high": "#f8d7da", "mid": "#fff3cd", "low": "#e8f0fb"}
LIVE_INTERVAL_MS = 5000  # 稼働状況の自動更新間隔
LIVE_FG = "#116329"
GROUP_BG = "#f4f4f4"


def ensure_notebook_style(name: str = "App.TNotebook") -> str:
    style = ttk.Style()
    try:
        style.configure(f"{name}.Tab", padding=[14, 6])
        style.map(
            f"{name}.Tab",
            background=[("selected", "#ffffff"), ("!selected", "#e8e8e8")],
            foreground=[("selected", "#000000"), ("!selected", "#757575")],
            font=[("selected", ("", 10, "bold")), ("!selected", ("", 9))],
        )
        style.configure("Treeview", rowheight=22)
    except tk.TclError:
        pass
    return name


def make_tree(
    parent: tk.Misc, columns: list[tuple[str, str, int]], show_tree: bool = False,
) -> ttk.Treeview:
    """スクロールバー付き Treeview。columns = [(id, 見出し, 幅)]"""
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.BOTH, expand=True)
    tree = ttk.Treeview(
        frame, columns=[c[0] for c in columns], show="tree headings" if show_tree else "headings",
    )
    if show_tree:
        tree.column("#0", width=28, stretch=False)
    for cid, heading, width in columns:
        tree.heading(cid, text=heading, command=lambda c=cid: _sort_by(tree, c))
        tree.column(cid, width=width, stretch=width >= 200, anchor=tk.E if cid == "port" else tk.W)
    ysb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    xsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
    tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    xsb.pack(side=tk.BOTTOM, fill=tk.X)
    ysb.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    for sev, bg in SEV_BG.items():
        tree.tag_configure(sev, background=bg)
    tree.tag_configure("live", foreground=LIVE_FG)
    tree.tag_configure("group", background=GROUP_BG)
    tree.tag_configure("dim", foreground="#8a8a8a")
    tree.tag_configure("new", font=("", 9, "bold"))
    return tree


def _sort_by(tree: ttk.Treeview, col: str) -> None:
    """見出しクリックで並べ替え (トップレベル行のみ)。数値列は数値として比較。"""
    desc = getattr(tree, "_sort_desc", {}).get(col, False)
    items = [(tree.set(k, col), k) for k in tree.get_children("")]

    def key(v: tuple[str, str]) -> tuple[int, Any]:
        s = v[0].replace(",", "")
        return (0, int(s)) if s.isdigit() else (1, s)

    items.sort(key=key, reverse=desc)
    for i, (_, k) in enumerate(items):
        tree.move(k, "", i)
    tree._sort_desc = {col: not desc}  # type: ignore[attr-defined]


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.report: analyze.Report | None = None
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.scanning = False

        root.title("LPortMan - Local Port Manager")
        root.geometry("1280x760")
        root.minsize(760, 420)

        # 1. フッターのボタン行を先に (side=BOTTOM)
        bar = ttk.Frame(root)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        ttk.Button(bar, text="閉じる", command=root.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="データフォルダを開く", command=self.open_data_dir).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(bar, text="台帳を編集", command=self.edit_registry).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(bar, text="設定を編集", command=self.edit_config).pack(side=tk.RIGHT, padx=(0, 6))
        self.scan_btn = ttk.Button(bar, text="再スキャン (F5)", command=self.start_scan)
        self.scan_btn.pack(side=tk.LEFT)
        self.auto_live = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text=f"稼働状況を自動更新 ({LIVE_INTERVAL_MS // 1000}秒)",
                        variable=self.auto_live).pack(side=tk.LEFT, padx=(10, 0))
        self.link_label = ttk.Label(bar, foreground="#555")
        self.link_label.pack(side=tk.LEFT, padx=12)

        # 2. ステータス行
        self.status = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.status, anchor=tk.W, relief=tk.SUNKEN, padding=(6, 2)).pack(
            side=tk.BOTTOM, fill=tk.X)

        # 3. ツールバー
        tool = ttk.Frame(root)
        tool.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(tool, text="絞り込み:").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh_views())
        ttk.Entry(tool, textvariable=self.filter_var, width=24).pack(side=tk.LEFT, padx=(4, 10))
        self.only_conflict = tk.BooleanVar(value=False)
        self.show_ref = tk.BooleanVar(value=False)
        self.show_default = tk.BooleanVar(value=True)
        self.only_new = tk.BooleanVar(value=False)
        ttk.Checkbutton(tool, text="注意ありのみ", variable=self.only_conflict,
                        command=self.refresh_views).pack(side=tk.LEFT)
        ttk.Checkbutton(tool, text="新着のみ", variable=self.only_new,
                        command=self.refresh_views).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(tool, text="既定(推定)を表示", variable=self.show_default,
                        command=self.refresh_views).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(tool, text="参照(.env *_PORT)を表示", variable=self.show_ref,
                        command=self.refresh_views).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Separator(tool, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        ttk.Label(tool, text="ポート確認:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        port_entry = ttk.Entry(tool, textvariable=self.port_var, width=8)
        port_entry.pack(side=tk.LEFT, padx=4)
        port_entry.bind("<Return>", lambda e: self.check_port())
        ttk.Button(tool, text="確認", command=self.check_port).pack(side=tk.LEFT)
        ttk.Button(tool, text="空きを提案", command=self.suggest).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(tool, text="予約に追加...", command=self.open_reserve_dialog).pack(side=tk.LEFT, padx=(4, 0))

        # 4. 本体を最後に
        self.nb = ttk.Notebook(root, style=ensure_notebook_style())
        self.nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.tab_ports = ttk.Frame(self.nb)
        self.tab_projects = ttk.Frame(self.nb)
        self.tab_live = ttk.Frame(self.nb)
        self.tab_conflicts = ttk.Frame(self.nb)
        self.tab_ranges = ttk.Frame(self.nb)
        self.tab_check = ttk.Frame(self.nb)
        self.tab_registry = ttk.Frame(self.nb)
        self.tab_graph = ttk.Frame(self.nb)
        self.tab_plan = ttk.Frame(self.nb)
        self.tab_settings = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_graph, text="グラフ")
        self.nb.add(self.tab_ports, text="ポート一覧")
        self.nb.add(self.tab_projects, text="プロジェクト")
        self.nb.add(self.tab_live, text="稼働中")
        self.nb.add(self.tab_conflicts, text="注意")
        self.nb.add(self.tab_registry, text="台帳")
        self.nb.add(self.tab_plan, text="解消案")
        self.nb.add(self.tab_ranges, text="除外範囲・既定ポート")
        self.nb.add(self.tab_check, text="確認結果")
        self.nb.add(self.tab_settings, text="設定")

        self.ports_tree = make_tree(self.tab_ports, [
            ("port", "ポート", 70), ("sev", "注意", 50), ("live", "稼働", 50), ("new", "新着", 50),
            ("kind", "区分", 50), ("project", "プロジェクト", 300), ("service", "サービス", 220), ("file", "ファイル", 260),
            ("note", "メモ", 360),
        ])
        self.ports_tree.bind("<Double-1>", lambda e: self.open_selected(self.ports_tree))

        self.projects_tree = make_tree(self.tab_projects, [
            ("name", "プロジェクト / ポート", 340), ("tier", "区分", 70), ("service", "サービス", 240),
            ("kind", "種別", 60), ("file", "ファイル", 280), ("path", "パス / 詳細", 360),
        ], show_tree=True)
        self.projects_tree.bind("<Double-1>", lambda e: self.open_selected(self.projects_tree))

        live_bar = ttk.Frame(self.tab_live)
        live_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(live_bar, text="選択したプロセスを終了...", command=self.kill_selected).pack(side=tk.LEFT)
        ttk.Label(live_bar, foreground="#555", text=(
            "子プロセスごと終了する。OS・VS Code・WSL/Docker の中継などは終了対象外。"
        )).pack(side=tk.LEFT, padx=12)
        self.live_tree = make_tree(self.tab_live, [
            ("port", "ポート", 70), ("address", "アドレス", 120), ("pid", "PID", 70),
            ("process", "プロセス", 180), ("project", "プロジェクト", 280), ("cwd", "作業フォルダ", 420),
        ])
        self.live_tree.bind("<Double-1>", lambda e: self.open_selected(self.live_tree))

        self.conflict_tree = make_tree(self.tab_conflicts, [
            ("sev", "重さ", 50), ("port", "ポート", 70), ("message", "内容", 1000),
        ])

        self._build_ranges_tab()
        self._build_check_tab()
        self._build_registry_tab()
        self.graph = graph.GraphView(self.tab_graph, self)
        self._build_plan_tab()
        self._build_settings_tab()
        for tree in (self.ports_tree, self.projects_tree, self.live_tree, self.registry_tree):
            tree.bind("<Button-3>", lambda e, t=tree: self.show_context_menu(t, e))

        self._paths: dict[tuple[str, str], str] = {}  # (tree, item) -> 開くフォルダ (item ID は tree ごとに重複する)
        root.bind("<F5>", lambda e: self.start_scan())
        self.update_link_label()
        self.root.after(100, self.poll_queue)
        self._mtimes: dict[Any, float] = {}
        self.live_busy = False
        self._first_run_shown = False
        self.start_scan()
        self.root.after(1000, self.watch_files)
        self.root.after(LIVE_INTERVAL_MS, self.live_tick)

    # ------------------------------------------------------------ タブ構築

    def _build_ranges_tab(self) -> None:
        pw = ttk.PanedWindow(self.tab_ranges, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=1)
        pw.add(right, weight=2)
        self.range_label = ttk.Label(left, padding=(4, 4), justify=tk.LEFT)
        self.range_label.pack(side=tk.TOP, fill=tk.X)
        self.range_tree = make_tree(left, [("start", "開始", 90), ("end", "終了", 90), ("admin", "管理者設定", 90)])
        ttk.Label(right, text="よく使われるポート (予約表の初期値)", padding=(4, 4)).pack(side=tk.TOP, fill=tk.X)
        self.wk_tree = make_tree(right, [("port", "ポート", 70), ("service", "サービス", 400)])
        for w in sorted(WELLKNOWN, key=lambda w: w.port):
            self.wk_tree.insert("", tk.END, values=(w.port, w.service))

    def _build_registry_tab(self) -> None:
        bar = ttk.Frame(self.tab_registry)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Button(bar, text="追加...", command=self.open_reserve_dialog).pack(side=tk.LEFT)
        ttk.Button(bar, text="選択を削除", command=self.remove_reservation).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(bar, foreground="#555", text=(
            "手で決めた割り当て・予約。ここに載せたポートは、他のプロジェクトからは使用不可として扱われる。"
        )).pack(side=tk.LEFT, padx=12)
        self.registry_tree = make_tree(self.tab_registry, [
            ("port", "ポート", 70), ("name", "用途", 220), ("project", "プロジェクト", 320),
            ("applied", "反映", 110), ("state", "状態", 360), ("note", "メモ", 200),
        ])
        self.registry_tree.bind("<Double-1>", lambda e: self.open_selected(self.registry_tree))

    def _build_plan_tab(self) -> None:
        bar = ttk.Frame(self.tab_plan)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Button(bar, text="選択したプロジェクトの案を台帳に登録...",
                   command=self.reserve_plan).pack(side=tk.LEFT)
        ttk.Button(bar, text="plan.md に書き出して開く", command=self.export_plan).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(bar, foreground="#555", text=(
            "提案のみ。プロジェクトのファイルは変更しない。相談で決まったものだけ台帳に登録する (複数選択可)。"
        )).pack(side=tk.LEFT, padx=12)
        pw = ttk.PanedWindow(self.tab_plan, orient=tk.VERTICAL)
        pw.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        top = ttk.Frame(pw)
        frame = ttk.Frame(pw)
        pw.add(top, weight=2)
        pw.add(frame, weight=3)
        self.plan_tree = make_tree(top, [
            ("name", "プロジェクト", 380), ("tier", "区分", 70), ("proposal", "提案", 200),
            ("conflicts", "衝突しているポート", 260), ("changes", "変更", 420),
        ])
        self.plan_tree.configure(selectmode=tk.EXTENDED, height=8)
        self.plan_tree.bind("<Double-1>", lambda e: self.open_selected(self.plan_tree))
        self.plan_items: dict[str, plan.PlanItem] = {}
        ysb = ttk.Scrollbar(frame, orient=tk.VERTICAL)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        self.plan_text = tk.Text(frame, wrap=tk.WORD, font=("Consolas", 10), padx=8, pady=8,
                                 yscrollcommand=ysb.set)
        self.plan_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ysb.configure(command=self.plan_text.yview)
        self.plan_md = ""

    def _build_check_tab(self) -> None:
        self.check_text = tk.Text(self.tab_check, wrap=tk.WORD, font=("Consolas", 10), padx=8, pady=8)
        self.check_text.pack(fill=tk.BOTH, expand=True)
        self.check_text.insert("1.0", "上部の「ポート確認」にポート番号を入れて「確認」または「空きを提案」を押してください。\n"
                               "「空きを提案」は番号を入れるとその +10000 / +20000 を優先します (例: 5173 -> 15173)。")
        self.check_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------ スキャン

    def start_scan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.scan_btn.configure(state=tk.DISABLED)
        self.status.set("スキャン中...")

        def work() -> None:
            try:
                self.queue.put(("done", analyze.run_and_save()))
            except Exception:
                self.queue.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "live":
                    self.live_busy = False
                    # 全体スキャン中に届いた古い結果は捨てる。変化があるときだけ描き直す
                    if not self.scanning and self.report is not None and \
                            payload.live_signature() != self.report.live_signature():
                        self.report = payload
                        self.refresh_views()
                    continue
                if kind == "live_error":
                    self.live_busy = False
                    continue
                self.scanning = False
                self.scan_btn.configure(state=tk.NORMAL)
                if kind == "done":
                    self.report = payload
                    self.refresh_views()
                    if not payload.config.get("roots"):
                        self.guide_to_settings()
                        if not self._first_run_shown:
                            # 初回: 調べるフォルダが未設定なら、最初の設定のダイアログを出す
                            self._first_run_shown = True
                            FirstRunDialog(self)
                else:
                    self.status.set("スキャン失敗")
                    messagebox.showerror("LPortMan", payload)
        except queue.Empty:
            pass
        self.root.after(150, self.poll_queue)

    def live_tick(self) -> None:
        """稼働状況だけを定期的に更新する (設定ファイルは読み直さない)。"""
        r = self.report
        if self.auto_live.get() and r is not None and not self.scanning and not self.live_busy:
            self.live_busy = True

            def work() -> None:
                try:
                    new = analyze.refresh_live(r)
                    if new.live_signature() != r.live_signature():
                        store.save_json(store.PORTS_FILE, analyze.to_json(new))
                    self.queue.put(("live", new))
                except Exception:
                    self.queue.put(("live_error", traceback.format_exc()))

            threading.Thread(target=work, daemon=True).start()
        self.root.after(LIVE_INTERVAL_MS, self.live_tick)

    def kill_selected(self) -> None:
        sel = self.live_tree.selection()
        if not sel:
            messagebox.showinfo("LPortMan", "稼働中タブで終了するプロセスを選んでください。", parent=self.root)
            return
        pid = int(self.live_tree.set(sel[0], "pid"))
        reason = live.protected_reason(pid)
        if reason:
            messagebox.showwarning("LPortMan", f"PID {pid} は終了できません。\n\n{reason}", parent=self.root)
            return
        try:
            info = live.describe_process(pid)
        except Exception as e:
            messagebox.showerror("LPortMan", f"プロセス情報を取得できません: {e}", parent=self.root)
            return
        ports = sorted({x.port for x in (self.report.listeners if self.report else []) if x.pid == pid})
        children = info["children"]
        msg = (
            f"{info['name']} (PID {pid}) を終了しますか?\n\n"
            f"待ち受け: {', '.join(map(str, ports)) or '-'}\n"
            f"コマンド: {str(info['cmdline'])[:300]}\n"
        )
        if children:
            msg += f"\n子プロセス {len(children)} 個も終了します: " + \
                ", ".join(f"{n} ({c})" for c, n in children[:8]) + (" ..." if len(children) > 8 else "")
        if not messagebox.askyesno("LPortMan", msg, icon=messagebox.WARNING, parent=self.root):
            return
        try:
            killed = live.kill_process(pid)
        except Exception as e:
            messagebox.showerror("LPortMan", f"終了できませんでした: {e}", parent=self.root)
            return
        self.status.set(f"終了しました: PID {', '.join(map(str, killed))}")
        self.live_busy = False
        self.root.after(300, self.live_tick_once)

    def live_tick_once(self) -> None:
        """自動更新の設定に関係なく、今すぐ稼働状況を更新する。"""
        r = self.report
        if r is None or self.scanning or self.live_busy:
            return
        self.live_busy = True
        threading.Thread(target=lambda: self.queue.put(("live", analyze.refresh_live(r))), daemon=True).start()

    # ------------------------------------------------------------ 表示

    def _save_tree_state(self) -> dict[str, tuple[tuple[str, ...] | None, float]]:
        """描き直しで選択とスクロール位置が飛ばないよう控えておく。"""
        state = {}
        for tree in (self.ports_tree, self.projects_tree, self.live_tree, self.conflict_tree,
                     self.registry_tree):
            sel = tree.selection()
            state[str(tree)] = (tuple(str(v) for v in tree.item(sel[0], "values")) if sel else None,
                                tree.yview()[0])
        return state

    def _restore_tree_state(self, state: dict[str, tuple[tuple[str, ...] | None, float]]) -> None:
        for tree in (self.ports_tree, self.projects_tree, self.live_tree, self.conflict_tree,
                     self.registry_tree):
            values, y = state.get(str(tree), (None, 0.0))

            def walk(parent: str = "") -> str | None:
                for item in tree.get_children(parent):
                    if tuple(str(v) for v in tree.item(item, "values")) == values:
                        return item
                    found = walk(item)
                    if found:
                        return found
                return None

            if values is not None and (item := walk()):
                tree.selection_set(item)
            tree.yview_moveto(y)

    def _match(self, *texts: object) -> bool:
        q = self.filter_var.get().strip().lower()
        return not q or any(q in str(t).lower() for t in texts)

    def _visible(self, u: analyze.Use) -> bool:
        if u.kind == "ref" and not self.show_ref.get():
            return False
        if u.kind == "default" and not self.show_default.get():
            return False
        return True

    def refresh_views(self) -> None:
        r = self.report
        if r is None:
            return
        tree_state = self._save_tree_state()
        self._paths.clear()
        for tree in (self.ports_tree, self.projects_tree, self.live_tree, self.conflict_tree, self.range_tree):
            tree.delete(*tree.get_children())

        # ポート一覧
        group = False
        n_ports = 0
        for port, pi in sorted(r.ports.items()):
            uses = [u for u in pi.uses if self._visible(u)]
            if not uses:
                continue
            if self.only_conflict.get() and not pi.conflicts:
                continue
            if self.only_new.get():
                uses = [u for u in uses if r.is_new(u)]
                if not uses:
                    continue
            note = " / ".join(c.message for c in pi.conflicts)
            if not self._match(port, note, *[f"{u.project.name if u.project else ''} {u.service} {u.file}" for u in uses]):
                continue
            n_ports += 1
            group = not group
            live_names = ", ".join(sorted({x.process for x in pi.live}))
            for i, u in enumerate(uses):
                tags = [pi.severity] if pi.severity else (["group"] if group else [])
                if pi.live:
                    tags.append("live")
                if u.kind in ("default", "ref"):
                    tags.append("dim")
                if r.is_new(u):
                    tags.append("new")
                tier = analyze.TIER_LABEL.get(u.project.tier, "") if u.project else ""
                name = (u.project.name if u.project else "(台帳)") + (f"  [{tier}]" if tier else "")
                item = self.ports_tree.insert("", tk.END, values=(
                    port if i == 0 else "",
                    analyze.SEVERITY_LABEL.get(pi.severity or "", "") if i == 0 else "",
                    ("稼働" if pi.live else "") if i == 0 else "",
                    "新" if r.is_new(u) else "",
                    analyze.KIND_LABEL[u.kind], name, u.service, u.file,
                    (note or (f"待ち受け: {live_names}" if live_names else "")) if i == 0 else u.detail,
                ), tags=tags)
                if u.project:
                    self._paths[str(self.ports_tree), item] = u.project.path

        # プロジェクト
        for proj in sorted(r.projects, key=lambda p: (p.root_label, p.name.lower())):
            key = analyze._norm(proj.path)
            fs = [f for f in proj.findings if (f.kind != "ref" or self.show_ref.get())
                  and (f.kind != "default" or self.show_default.get())]
            new_ports = {f.port for f in fs if f"{key}|{f.port}" in r.new_keys}
            if self.only_new.get():
                fs = [f for f in fs if f.port in new_ports]
            if not fs or not self._match(proj.name, proj.path, *[f"{f.port} {f.service}" for f in fs]):
                continue
            pid = self.projects_tree.insert("", tk.END, open=True, values=(
                proj.name + ("  [新着あり]" if new_ports else ""),
                analyze.TIER_LABEL.get(proj.tier, "") or proj.root_label, "", "", "", proj.path,
            ), tags=["group"] + (["new"] if new_ports else []))
            self._paths[str(self.projects_tree), pid] = proj.path
            for f in fs:
                pi = r.ports.get(f.port)
                sev = pi.severity if pi else None
                if self.only_conflict.get() and not sev:
                    continue
                tags = [sev] if sev else []
                if pi and pi.live:
                    tags.append("live")
                if f.port in new_ports:
                    tags.append("new")
                item = self.projects_tree.insert(pid, tk.END, values=(
                    f"    {f.port}", "新着" if f.port in new_ports else "", f.service, analyze.KIND_LABEL[f.kind], f.file, f.detail,
                ), tags=tags)
                self._paths[str(self.projects_tree), item] = proj.path

        # 稼働中
        for lst in r.listeners:
            owner = r.live_owner.get((lst.port, lst.pid))
            if not self._match(lst.port, lst.process, owner.name if owner else "", lst.cwd):
                continue
            item = self.live_tree.insert("", tk.END, values=(
                lst.port, lst.address, lst.pid, lst.process, owner.name if owner else "", lst.cwd,
            ), tags=["live"] if owner else [])
            if owner:
                self._paths[str(self.live_tree), item] = owner.path
            elif lst.cwd:
                self._paths[str(self.live_tree), item] = lst.cwd

        # 注意
        for c in r.conflicts:
            if self._match(c.port, c.message):
                self.conflict_tree.insert("", tk.END, values=(
                    analyze.SEVERITY_LABEL[c.severity], c.port, c.message), tags=[c.severity])

        # 除外範囲
        for a, b, admin in r.excluded:
            self.range_tree.insert("", tk.END, values=(a, b, "*" if admin else ""))
        lo, hi = r.config.get("assign_range", [20000, 29999])
        self.range_label.configure(text=(
            f"Windows のポート除外範囲 (Hyper-V / WSL / Docker が確保。再起動で変わる)\n"
            f"動的ポート範囲: {r.dynamic[0]} - {r.dynamic[1]}\n"
            f"払い出し範囲 (config.json): {lo} - {hi}"
        ))

        # 台帳
        self.registry_tree.delete(*self.registry_tree.get_children())
        notes = {(int(x.get("port", 0)), x.get("name", "")): x.get("note", "")
                 for x in store.load_registry().get("reservations", [])}
        n_unapplied = 0
        for st in r.registry_status:
            port = st["port"]
            pi = r.ports.get(port)
            parts = [st["message"]] if st["status"] not in ("ok", "noproject") else []
            parts += [c.message for c in (pi.conflicts if pi else [])]
            live_names = ", ".join(sorted({x.process for x in pi.live})) if pi else ""
            if live_names:
                parts.append(f"待ち受け中: {live_names}")
            tags = []
            if st["status"] in ("unapplied", "missing", "noscan"):
                tags.append("mid")
                n_unapplied += 1
            elif pi and pi.severity:
                tags.append(pi.severity)
            item = self.registry_tree.insert("", tk.END, values=(
                port, st["name"], st["project"], analyze.REG_STATUS_LABEL.get(st["status"], ""),
                " / ".join(parts), notes.get((port, st["name"]), ""),
            ), tags=tags)
            if st["project"]:
                self._paths[str(self.registry_tree), item] = st["project"]
        self.nb.tab(self.tab_registry, text=f"台帳 (未反映 {n_unapplied})" if n_unapplied else "台帳")

        self.graph.redraw()

        # 解消案
        items = plan.make_plan(r)
        self.plan_tree.delete(*self.plan_tree.get_children())
        self.plan_items.clear()
        for it in items:
            item = self.plan_tree.insert("", tk.END, values=(
                it.project.name, analyze.TIER_LABEL.get(it.project.tier, "") or it.project.root_label,
                it.label, ", ".join(map(str, it.conflict_ports)),
                ", ".join(f"{c.finding.port}->{c.new_port}" for c in it.changes),
            ), tags=(["dim"] if it.offset == 0 or it.pending else []))
            self.plan_items[item] = it
            self._paths[str(self.plan_tree), item] = it.project.path
        self.plan_md = plan.plan_markdown(r, items)
        self.plan_text.configure(state=tk.NORMAL)
        self.plan_text.delete("1.0", tk.END)
        self.plan_text.insert("1.0", self.plan_md)
        self.plan_text.configure(state=tk.DISABLED)

        self._restore_tree_state(tree_state)
        high = sum(c.severity == "high" for c in r.conflicts)
        n_new = len({k for k in r.new_keys})
        self.nb.tab(self.tab_conflicts, text=f"注意 ({len(r.conflicts)})")
        self.status.set(
            f"{r.generated_at}  |  プロジェクト {len(r.projects)}  ポート {len(r.ports)} (表示 {n_ports})  "
            f"待ち受け {len(r.listeners)}  注意 {len(r.conflicts)} (高 {high})  新着 {n_new}  "
            f"台帳未反映 {n_unapplied}  |  出力: {store.PORTS_FILE}"
        )

    # ------------------------------------------------------------ 操作

    def _show_check(self, text: str) -> None:
        self.check_text.configure(state=tk.NORMAL)
        self.check_text.delete("1.0", tk.END)
        self.check_text.insert("1.0", text)
        self.check_text.configure(state=tk.DISABLED)
        self.nb.select(self.tab_check)

    def _port_input(self) -> int | None:
        s = self.port_var.get().strip()
        if not s:
            return None
        if not s.isdigit():
            messagebox.showwarning("LPortMan", "ポート番号を数字で入力してください。")
            raise ValueError
        return int(s)

    def check_port(self) -> None:
        if self.report is None:
            return
        try:
            port = self._port_input()
        except ValueError:
            return
        if port is None:
            return
        res = analyze.check_port(self.report, port)
        label = {"free": "使用可", "warn": "注意 (使えるが推奨しない)", "busy": "使用不可"}[res["status"]]
        lines = [f"{port}: {label}", ""] + [f"  - {x}" for x in res["reasons"]]
        if res["status"] != "free":
            lines += ["", "代わりの候補: " + " ".join(map(str, analyze.suggest_ports(self.report, port)))]
        self._show_check("\n".join(lines))

    def suggest(self) -> None:
        if self.report is None:
            return
        try:
            base = self._port_input()
        except ValueError:
            return
        ports = analyze.suggest_ports(self.report, base, 10)
        head = f"{base} の代わりの空きポート候補" if base else "空きポート候補"
        self._show_check(f"{head}:\n\n  " + "  ".join(map(str, ports)) +
                         "\n\n決めたら「台帳を編集」で registry.json の reservations に追記すると、"
                         "以後は他のプロジェクトから使用中として扱われます。")

    def _selected_context(self) -> tuple[int | None, str]:
        """いま開いているタブの選択行から (ポート, プロジェクトのパス) を拾う。"""
        tab = self.nb.select()
        tree = {
            str(self.tab_ports): self.ports_tree, str(self.tab_projects): self.projects_tree,
            str(self.tab_live): self.live_tree,
        }.get(tab)
        if tree is None or not tree.selection():
            return None, ""
        item = tree.selection()[0]
        path = self._paths.get((str(tree), item), "")
        col = "port" if "port" in tree["columns"] else "name"
        port: int | None = None
        cur = item
        while cur and port is None:  # ポート一覧はグループ先頭行にしか番号が無いので遡る
            v = str(tree.set(cur, col)).strip()
            if v.isdigit():
                port = int(v)
            cur = tree.prev(cur) if tree is self.ports_tree else ""
        return port, path

    def open_reserve_dialog(self) -> None:
        if self.report is None:
            return
        port, path = self._selected_context()
        s = self.port_var.get().strip()
        if s.isdigit():
            port = int(s)
        ReserveDialog(self, port, path)

    def remove_reservation(self) -> None:
        sel = self.registry_tree.selection()
        if not sel:
            return
        port, name = self.registry_tree.set(sel[0], "port"), self.registry_tree.set(sel[0], "name")
        if messagebox.askyesno("LPortMan", f"台帳から削除しますか?\n\n{port}  {name}", parent=self.root):
            store.remove_reservation(int(port), name)
            self.start_scan()

    def reserve_plan(self) -> None:
        its = [self.plan_items[i] for i in self.plan_tree.selection() if i in self.plan_items]
        its = [it for it in its if it.changes]
        if not its:
            messagebox.showinfo("LPortMan", "ずらし幅の提案があるプロジェクトを一覧から選んでください。\n"
                                "(据え置き・予約済み・候補なしは登録するものがありません)", parent=self.root)
            return
        lines = []
        for it in its:
            lines.append(f"{it.project.name} ({it.label})")
            lines += [f"    {c.finding.service}: {c.finding.port} -> {c.new_port}" for c in it.changes]
        if not messagebox.askyesno(
                "LPortMan", "相談で合意済みですか? 次のポートを台帳に登録します。\n"
                "(プロジェクトの設定ファイルは変更しません。登録後は台帳タブで「未反映」として追跡されます)\n\n"
                + "\n".join(lines[:40]) + ("\n..." if len(lines) > 40 else ""), parent=self.root):
            return
        existing = {(int(x.get("port", 0)), x.get("name"), x.get("project"))
                    for x in store.load_registry().get("reservations", [])}
        n = 0
        for it in its:
            for c in it.changes:
                key = (c.new_port, c.finding.service, it.project.path)
                if key in existing or any(k[0] == c.new_port and k[2] == it.project.path for k in existing):
                    continue  # 同じプロジェクトの同じポートは 1 回だけ (compose と .env の両方にある場合など)
                existing.add(key)
                store.add_reservation(c.new_port, c.finding.service, it.project.path,
                                      f"解消案 +{it.offset} (元 {c.finding.port})")
                n += 1
        self.status.set(f"台帳に {n} 件登録しました")
        self.start_scan()
        self.nb.select(self.tab_registry)

    def _row_port(self, tree: ttk.Treeview, item: str) -> int | None:
        col = "port" if "port" in tree["columns"] else "name"
        cur: str = item
        while cur:
            v = str(tree.set(cur, col)).strip()
            if v.isdigit():
                return int(v)
            cur = tree.prev(cur) if tree is self.ports_tree else ""
        return None

    def show_context_menu(self, tree: ttk.Treeview, e: tk.Event) -> None:
        item = tree.identify_row(e.y)
        if not item:
            return
        tree.selection_set(item)
        port = self._row_port(tree, item)
        path = self._paths.get((str(tree), item), "")
        cols = tree["columns"]
        service = tree.set(item, "service") if "service" in cols else (
            tree.set(item, "name") if tree is self.registry_tree else "")

        def copy(text: str) -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status.set(f"コピーしました: {text[:120]}")

        m = tk.Menu(self.root, tearoff=False)
        if port:
            m.add_command(label=f"ポート番号をコピー ({port})", command=lambda: copy(str(port)))
        if path:
            m.add_command(label="パスをコピー", command=lambda: copy(path))
        if port and path:
            cmd = f'lportman reserve {port} --name "{service or "dev"}" --project "{path}"'
            m.add_command(label="reserve コマンドをコピー", command=lambda: copy(cmd))
        if port:
            m.add_separator()
            m.add_command(label=f"{port} を確認", command=lambda: (self.port_var.set(str(port)), self.check_port()))
            m.add_command(label=f"{port} の代わりの空きを提案",
                          command=lambda: (self.port_var.set(str(port)), self.suggest()))
            m.add_command(label="予約に追加...", command=lambda: ReserveDialog(self, port, path))
        if tree is self.live_tree:
            m.add_separator()
            m.add_command(label="このプロセスを終了...", command=self.kill_selected)
        if path and os.path.isdir(path):
            m.add_separator()
            m.add_command(label="フォルダを開く", command=lambda: os.startfile(path))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------------ 設定タブ

    def _build_settings_tab(self) -> None:
        f = self.tab_settings
        # フッター (保存) を先に
        bar = ttk.Frame(f)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        ttk.Button(bar, text="保存して再スキャン", command=self.save_settings).pack(side=tk.LEFT)
        ttk.Button(bar, text="元に戻す", command=self.load_settings).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="config.json を開く", command=self.edit_config).pack(side=tk.LEFT, padx=(6, 0))
        self.settings_msg = ttk.Label(bar, foreground="#555")
        self.settings_msg.pack(side=tk.LEFT, padx=12)

        # 調べるフォルダ
        roots = ttk.LabelFrame(f, text="調べるフォルダ", padding=6)
        roots.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        form = ttk.Frame(roots)
        form.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.root_path = tk.StringVar()
        self.root_label = tk.StringVar()
        self.root_tier = tk.StringVar(value="normal")
        self.root_mask = tk.BooleanVar(value=False)
        ttk.Label(form, text="フォルダ").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(form, textvariable=self.root_path, width=48).grid(row=0, column=1, sticky=tk.EW, padx=4)
        ttk.Button(form, text="参照...", command=self._browse_root).grid(row=0, column=2)
        ttk.Label(form, text="名前").grid(row=0, column=3, sticky=tk.W, padx=(12, 0))
        ttk.Entry(form, textvariable=self.root_label, width=12).grid(row=0, column=4, padx=4)
        ttk.Label(form, text="区分").grid(row=0, column=5, sticky=tk.W, padx=(12, 0))
        ttk.Combobox(form, textvariable=self.root_tier, width=10, state="readonly",
                     values=["normal", "private", "scratch"]).grid(row=0, column=6, padx=4)
        ttk.Checkbutton(form, text="ports.json でパスを伏せる", variable=self.root_mask).grid(
            row=0, column=7, padx=(12, 0))
        btns = ttk.Frame(form)
        btns.grid(row=1, column=0, columnspan=8, sticky=tk.W, pady=(6, 0))
        ttk.Button(btns, text="追加", command=lambda: self._root_apply(new=True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="選択を更新", command=lambda: self._root_apply(new=False)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="選択を削除", command=self._root_remove).pack(side=tk.LEFT)
        ttk.Label(btns, foreground="#555", text=(
            "区分: normal=通常 / private=私的 / scratch=相談中 (衝突の重さを1段下げる)")).pack(side=tk.LEFT, padx=12)
        form.columnconfigure(1, weight=1)
        self.roots_tree = make_tree(roots, [
            ("path", "フォルダ", 420), ("label", "名前", 100), ("tier", "区分", 90), ("mask", "パスを伏せる", 100),
        ])
        self.roots_tree.configure(height=5)
        self.roots_tree.bind("<<TreeviewSelect>>", lambda e: self._root_select())

        lower = ttk.Frame(f)
        lower.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(8, 0))

        # サンプル扱い
        samples = ttk.LabelFrame(lower, text="サンプル扱い (スキャンルートからの相対パスの glob)", padding=6)
        samples.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        srow = ttk.Frame(samples)
        srow.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))
        self.sample_entry = tk.StringVar()
        ttk.Entry(srow, textvariable=self.sample_entry, width=30).pack(side=tk.LEFT)
        ttk.Button(srow, text="追加", command=self._sample_add).pack(side=tk.LEFT, padx=4)
        ttk.Button(srow, text="選択を削除", command=self._sample_remove).pack(side=tk.LEFT)
        ttk.Label(samples, foreground="#555", text="例: test\\*   *\\*-examples   vendor-samples\\*"
                  ).pack(side=tk.BOTTOM, anchor=tk.W)
        self.sample_list = tk.Listbox(samples, height=6, activestyle="none")
        self.sample_list.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # その他
        other = ttk.LabelFrame(lower, text="その他", padding=6)
        other.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.assign_lo = tk.StringVar()
        self.assign_hi = tk.StringVar()
        self.new_days = tk.StringVar()
        self.editor_var = tk.StringVar()
        self.max_depth = tk.StringVar()
        rows = [
            ("払い出し範囲", None),
            ("新着とみなす日数", self.new_days),
            ("フォルダを潜る深さ", self.max_depth),
            ("エディタ (空なら VS Code > メモ帳)", self.editor_var),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(other, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)
            if var is None:
                rng = ttk.Frame(other)
                rng.grid(row=i, column=1, sticky=tk.W, padx=6)
                ttk.Entry(rng, textvariable=self.assign_lo, width=8).pack(side=tk.LEFT)
                ttk.Label(rng, text=" - ").pack(side=tk.LEFT)
                ttk.Entry(rng, textvariable=self.assign_hi, width=8).pack(side=tk.LEFT)
            else:
                ttk.Entry(other, textvariable=var, width=40 if var is self.editor_var else 8).grid(
                    row=i, column=1, sticky=tk.W, padx=6)
        self.load_settings()

    def guide_to_settings(self) -> None:
        self.nb.select(self.tab_settings)
        self.settings_msg.configure(
            text="まず「調べるフォルダ」を追加して「保存して再スキャン」を押してください。",
            foreground="#8a5a00")
        self.status.set("調べるフォルダが未設定です (設定タブ)")

    def load_settings(self) -> None:
        cfg = store.load_config()
        self._cfg = cfg
        self.roots_tree.delete(*self.roots_tree.get_children())
        for r in cfg.get("roots", []):
            self.roots_tree.insert("", tk.END, values=(
                r.get("path", ""), r.get("label", ""), r.get("tier", "normal"), "はい" if r.get("mask_paths") else ""))
        self.sample_list.delete(0, tk.END)
        for g in cfg.get("sample_globs", []):
            self.sample_list.insert(tk.END, g)
        lo, hi = cfg.get("assign_range", [20000, 29999])
        self.assign_lo.set(str(lo))
        self.assign_hi.set(str(hi))
        self.new_days.set(str(cfg.get("new_days", 7)))
        self.max_depth.set(str(cfg.get("max_depth", 6)))
        self.editor_var.set(cfg.get("editor", ""))
        self.settings_msg.configure(text="")

    def save_settings(self) -> None:
        try:
            lo, hi = int(self.assign_lo.get()), int(self.assign_hi.get())
            days, depth = int(self.new_days.get()), int(self.max_depth.get())
        except ValueError:
            self.settings_msg.configure(text="数値の欄に数字以外が入っています。", foreground="#8a1c1c")
            return
        if not (1024 <= lo <= hi <= 65535):
            self.settings_msg.configure(text="払い出し範囲は 1024-65535 の中で 開始 <= 終了 にしてください。",
                                        foreground="#8a1c1c")
            return
        cfg = store.load_config()  # 画面に出していない項目 (exclude_dirs 等) は保つ
        cfg["roots"] = [
            {"path": v[0], "label": v[1], "tier": v[2], "mask_paths": v[3] == "はい"}
            for v in (self.roots_tree.item(i, "values") for i in self.roots_tree.get_children())
        ]
        cfg["sample_globs"] = list(self.sample_list.get(0, tk.END))
        cfg["assign_range"] = [lo, hi]
        cfg["new_days"] = days
        cfg["max_depth"] = depth
        cfg["editor"] = self.editor_var.get().strip()
        store.save_json(store.CONFIG_FILE, cfg)
        self._mtimes[store.CONFIG_FILE] = store.CONFIG_FILE.stat().st_mtime  # 自分の保存で二重スキャンしない
        self.settings_msg.configure(text="保存しました。", foreground="#116329")
        self.start_scan()

    def _browse_root(self) -> None:
        d = filedialog.askdirectory(parent=self.root, initialdir=self.root_path.get() or "C:\\")
        if d:
            self.root_path.set(os.path.normpath(d))
            if not self.root_label.get():
                self.root_label.set(os.path.basename(os.path.normpath(d)))

    def _root_select(self) -> None:
        sel = self.roots_tree.selection()
        if sel:
            v = self.roots_tree.item(sel[0], "values")
            self.root_path.set(v[0])
            self.root_label.set(v[1])
            self.root_tier.set(v[2])
            self.root_mask.set(v[3] == "はい")

    def _root_apply(self, new: bool) -> None:
        path = self.root_path.get().strip()
        label = self.root_label.get().strip() or os.path.basename(path)
        if not path or not os.path.isdir(path):
            self.settings_msg.configure(text=f"フォルダが見つかりません: {path}", foreground="#8a1c1c")
            return
        values = (os.path.normpath(path), label, self.root_tier.get(), "はい" if self.root_mask.get() else "")
        sel = self.roots_tree.selection()
        if new or not sel:
            self.roots_tree.insert("", tk.END, values=values)
        else:
            self.roots_tree.item(sel[0], values=values)
        self.settings_msg.configure(text="未保存の変更があります。", foreground="#8a5a00")

    def _root_remove(self) -> None:
        for i in self.roots_tree.selection():
            self.roots_tree.delete(i)
        self.settings_msg.configure(text="未保存の変更があります。", foreground="#8a5a00")

    def _sample_add(self) -> None:
        g = self.sample_entry.get().strip()
        if g and g not in self.sample_list.get(0, tk.END):
            self.sample_list.insert(tk.END, g)
            self.sample_entry.set("")
            self.settings_msg.configure(text="未保存の変更があります。", foreground="#8a5a00")

    def _sample_remove(self) -> None:
        for i in reversed(self.sample_list.curselection()):
            self.sample_list.delete(i)
        self.settings_msg.configure(text="未保存の変更があります。", foreground="#8a5a00")

    def export_plan(self) -> None:
        store.save_text(store.PLAN_FILE, self.plan_md)
        self._open_file(store.PLAN_FILE)

    def open_selected(self, tree: ttk.Treeview) -> None:
        sel = tree.selection()
        if sel and (path := self._paths.get((str(tree), sel[0]))) and os.path.isdir(path):
            os.startfile(path)

    def _open_file(self, path: os.PathLike[str] | str) -> None:
        """テキストエディタで開く (.json の既定の関連付けはブラウザのことが多いので使わない)。"""
        try:
            store.open_in_editor(path, self.report.config if self.report else None)
        except OSError as e:
            messagebox.showerror("LPortMan", f"エディタを起動できません:\n{e}\n\n"
                                 "config.json の editor にエディタの実行ファイルを指定してください。")

    def watch_files(self) -> None:
        """config / registry が保存されたら再スキャンする。"""
        for path in (store.CONFIG_FILE, store.REGISTRY_FILE):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            prev = self._mtimes.get(path)
            self._mtimes[path] = mtime
            if prev is not None and prev != mtime:
                self.status.set(f"{path.name} の変更を検出。再スキャンします...")
                if path == store.CONFIG_FILE:
                    self.load_settings()
                self.start_scan()
        self.root.after(1000, self.watch_files)

    def open_data_dir(self) -> None:
        store.DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(store.DATA_DIR)

    def edit_registry(self) -> None:
        store.load_registry()  # 無ければ作る
        self._open_file(store.REGISTRY_FILE)

    def edit_config(self) -> None:
        store.load_config()
        self._open_file(store.CONFIG_FILE)

    def update_link_label(self) -> None:
        st = store.link_status()
        text = {
            "ok": f"参照先: {store.LINK_DIR}",
            "missing": f"{store.LINK_DIR} 未作成 (lportman link)",
        }.get(st, f"{store.LINK_DIR}: {st}")
        self.link_label.configure(text=text)


def find_dev_folders() -> list[tuple[str, int]]:
    """よくある開発用フォルダのうち、実在して git リポジトリを含むもの。(パス, リポジトリ数)"""
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, "source", "repos"), os.path.join(home, "repos"), os.path.join(home, "src"),
        os.path.join(home, "dev"), os.path.join(home, "projects"), os.path.join(home, "workspace"),
        os.path.join(home, "Documents", "GitHub"), os.path.join(home, "Documents", "Projects"),
    ]
    for drive in ("C", "D", "E", "F"):
        cands += [f"{drive}:\\{name}" for name in ("Repos", "repos", "src", "dev", "projects", "work", "workspace")]
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for c in cands:
        key = os.path.normcase(c)
        if key in seen or not os.path.isdir(c):
            continue
        seen.add(key)
        n = 0
        try:
            for e in os.scandir(c):  # 直下と、その 1 つ下まで (C:\Repos\group\repo の形も多い)
                if not e.is_dir() or e.name.startswith("."):
                    continue
                if os.path.exists(os.path.join(e.path, ".git")):
                    n += 1
                    continue
                try:
                    n += sum(1 for f in os.scandir(e.path)
                             if f.is_dir() and os.path.exists(os.path.join(f.path, ".git")))
                except OSError:
                    pass
        except OSError:
            continue
        if n:
            out.append((os.path.normpath(c), n))
    return out


class FirstRunDialog:
    """初回 (調べるフォルダが未設定) に出す。何をするツールかと、最初に決めることを案内する。"""

    def __init__(self, app: App) -> None:
        self.app = app
        win = self.win = tk.Toplevel(app.root)
        win.title("LPortMan へようこそ")
        win.transient(app.root)
        win.resizable(True, True)
        win.minsize(560, 360)

        # フッターを先に
        bar = ttk.Frame(win)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=10)
        ttk.Button(bar, text="あとで", command=self.later).pack(side=tk.RIGHT)
        self.start_btn = ttk.Button(bar, text="開始 (保存してスキャン)", command=self.start)
        self.start_btn.pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(bar, text="フォルダを追加...", command=self.add_folder).pack(side=tk.LEFT)
        self.msg = ttk.Label(win, foreground="#8a1c1c", padding=(12, 0))
        self.msg.pack(side=tk.BOTTOM, fill=tk.X)

        body = ttk.Frame(win, padding=(16, 14, 16, 4))
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        ttk.Label(body, text="調べるフォルダを選んでください", font=("", 12, "bold")).pack(anchor=tk.W)
        ttk.Label(body, justify=tk.LEFT, wraplength=620, foreground="#52514e", text=(
            "LPortMan は、指定したフォルダの下にあるプロジェクトの設定ファイル (package.json、"
            "firebase.json、docker-compose.yml、.env など) を読んで、どのプロジェクトがどのポートを使うかを"
            "一覧にします。プロジェクトのファイルには書き込みません。\n\n"
            "普段リポジトリを置いているフォルダ (例: C:\\Repos) を選んでください。"
            "複数選べます。あとから設定タブで変更できます。"
        )).pack(anchor=tk.W, pady=(6, 10), fill=tk.X)

        ttk.Label(body, text="見つかった開発用フォルダ:").pack(anchor=tk.W)
        self.list_frame = ttk.Frame(body)
        self.list_frame.pack(anchor=tk.W, fill=tk.X, pady=(4, 0))
        self.vars: list[tuple[str, tk.BooleanVar]] = []
        for path, n in find_dev_folders():
            self._add_row(path, f"git リポジトリ {n} 件", checked=True)
        if not self.vars:
            self.empty = ttk.Label(self.list_frame, foreground="#898781",
                                   text="(見つかりませんでした。「フォルダを追加...」で選んでください)")
            self.empty.pack(anchor=tk.W)

        win.bind("<Escape>", lambda e: self.later())
        win.protocol("WM_DELETE_WINDOW", self.later)
        win.update_idletasks()
        # 親ウィンドウの中央に出す
        x = app.root.winfo_rootx() + (app.root.winfo_width() - win.winfo_reqwidth()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.grab_set()
        win.focus_set()

    def _add_row(self, path: str, note: str, checked: bool) -> None:
        if getattr(self, "empty", None) is not None:
            self.empty.destroy()
            self.empty = None
        var = tk.BooleanVar(value=checked)
        row = ttk.Frame(self.list_frame)
        row.pack(anchor=tk.W, fill=tk.X, pady=1)
        ttk.Checkbutton(row, text=path, variable=var).pack(side=tk.LEFT)
        ttk.Label(row, text=f"  {note}", foreground="#898781").pack(side=tk.LEFT)
        self.vars.append((path, var))

    def add_folder(self) -> None:
        d = filedialog.askdirectory(parent=self.win, title="調べるフォルダを選ぶ")
        if not d:
            return
        d = os.path.normpath(d)
        for path, var in self.vars:
            if os.path.normcase(path) == os.path.normcase(d):
                var.set(True)
                return
        self._add_row(d, "追加したフォルダ", checked=True)
        self.msg.configure(text="")

    def start(self) -> None:
        chosen = [p for p, v in self.vars if v.get()]
        if not chosen:
            self.msg.configure(text="フォルダを 1 つ以上選んでください (「フォルダを追加...」で選べます)。")
            return
        cfg = store.load_config()
        used = {r.get("label") for r in cfg.get("roots", [])}
        for path in chosen:
            label = os.path.basename(path.rstrip("\\")) or path[:1]
            base, i = label, 2
            while label in used:  # 名前 (label) は ports.json でパスを伏せるときに使うので重複させない
                label, i = f"{base}{i}", i + 1
            used.add(label)
            cfg.setdefault("roots", []).append({"path": path, "label": label, "tier": "normal", "mask_paths": False})
        store.save_json(store.CONFIG_FILE, cfg)
        self.app._mtimes[store.CONFIG_FILE] = store.CONFIG_FILE.stat().st_mtime  # 自分の保存で二重スキャンしない
        self.win.destroy()
        self.app.load_settings()
        self.app.nb.select(self.app.tab_graph)
        self.app.start_scan()

    def later(self) -> None:
        self.win.destroy()
        self.app.guide_to_settings()


class ReserveDialog:
    """台帳に予約を1件追加する。"""

    def __init__(self, app: App, port: int | None, project_path: str) -> None:
        self.app = app
        report = app.report
        assert report is not None
        win = self.win = tk.Toplevel(app.root)
        win.title("予約に追加")
        win.transient(app.root)
        win.resizable(True, False)

        # フッターを先に
        bar = ttk.Frame(win)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=8)
        ttk.Button(bar, text="キャンセル", command=win.destroy).pack(side=tk.RIGHT)
        ttk.Button(bar, text="登録", command=self.save).pack(side=tk.RIGHT, padx=(0, 6))
        self.msg = tk.StringVar()
        ttk.Label(win, textvariable=self.msg, foreground="#8a1c1c", padding=(10, 0), wraplength=560,
                  justify=tk.LEFT).pack(side=tk.BOTTOM, fill=tk.X)

        body = ttk.Frame(win, padding=10)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        body.columnconfigure(1, weight=1)

        self.projects = sorted(report.projects, key=lambda p: p.name.lower())
        self.port = tk.StringVar(value=str(port) if port else "")
        self.name = tk.StringVar()
        self.project = tk.StringVar()
        self.note = tk.StringVar()
        if project_path:
            match = [p for p in self.projects if os.path.normcase(p.path) == os.path.normcase(project_path)]
            self.project.set(match[0].name if match else project_path)
            if port:
                svc = [f.service for p in match for f in p.findings if f.port == port]
                self.name.set(svc[0] if svc else "")

        rows = [("ポート", self.port), ("用途", self.name), ("プロジェクト", self.project), ("メモ", self.note)]
        for i, (label, var) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky=tk.W, pady=3, padx=(0, 8))
            if var is self.project:
                w: ttk.Widget = ttk.Combobox(
                    body, textvariable=var, values=["(なし)"] + [p.name for p in self.projects])
            else:
                w = ttk.Entry(body, textvariable=var, width=12 if var is self.port else 50)
            w.grid(row=i, column=1, sticky=tk.W if var is self.port else tk.EW, pady=3)
        port_row = ttk.Frame(body)
        port_row.grid(row=0, column=2, sticky=tk.W, padx=(6, 0))
        ttk.Button(port_row, text="確認", command=self.check).pack(side=tk.LEFT)
        ttk.Button(port_row, text="空きを入れる", command=self.fill_suggest).pack(side=tk.LEFT, padx=(4, 0))

        if self.port.get():
            self.check()
        win.bind("<Return>", lambda e: self.save())
        win.bind("<Escape>", lambda e: win.destroy())
        win.grab_set()

    def _port(self) -> int | None:
        s = self.port.get().strip()
        return int(s) if s.isdigit() else None

    def _project_path(self) -> str:
        v = self.project.get().strip()
        if not v or v == "(なし)":
            return ""
        for p in self.projects:
            if p.name == v:
                return p.path
        return v  # 一覧に無いフォルダを直接入力した場合

    def check(self) -> dict[str, Any] | None:
        port = self._port()
        if port is None:
            self.msg.set("ポート番号を数字で入力してください。")
            return None
        # 登録先プロジェクト自身の宣言・待ち受けは障害ではない
        res = analyze.check_port(self.app.report, port, self._project_path() or None)  # type: ignore[arg-type]
        reasons, status = res["reasons"], res["status"]
        head = {"free": "使用可", "warn": "注意", "busy": "使用不可"}[status]
        self.msg.set(f"{port}: {head}" + ("\n" + "\n".join(f"- {x}" for x in reasons) if reasons else ""))
        return {"status": status}

    def fill_suggest(self) -> None:
        cands = analyze.suggest_ports(self.app.report, self._port(), 1)  # type: ignore[arg-type]
        if cands:
            self.port.set(str(cands[0]))
            self.check()

    def save(self) -> None:
        res = self.check()
        if res is None:
            return
        if not self.name.get().strip():
            self.msg.set("用途を入力してください。")
            return
        if res["status"] == "busy" and not messagebox.askyesno(
                "LPortMan", "使用不可と判定されています。それでも登録しますか?", parent=self.win):
            return
        store.add_reservation(self._port() or 0, self.name.get().strip(), self._project_path(),
                              self.note.get().strip())
        self.win.destroy()
        self.app.start_scan()
        self.app.nb.select(self.app.tab_registry)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
