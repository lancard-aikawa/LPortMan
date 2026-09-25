"""マニュアル (docs/manual.md) の画面画像を docs/images に撮る。

    uv run python tools/manual_shots.py            # 全部
    set ONLY=s_ports_menu & uv run python tools/manual_shots.py   # 一部だけ (関数名をカンマ区切り)

data/ を一時フォルダに複製し、LPORTMAN_DATA をそこへ向けて撮るので、本物の台帳・seen.json は変わらない。
撮影中はウィンドウを最前面に出して画面をキャプチャするので、マウス・キーボードに触らないこと。
画像には実際のプロジェクト名・パスが写るため、docs/images は git 管理外。
"""
import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

PROJECT = Path(__file__).resolve().parents[1]
OUT = str(PROJECT / "docs" / "images")
os.makedirs(OUT, exist_ok=True)

# 本物の data/ を汚さないよう、一時コピーを使わせる (lportman の import より前に設定する)
TMP = tempfile.mkdtemp(prefix="lportman_manual_")
if (PROJECT / "data").is_dir():
    shutil.copytree(PROJECT / "data", TMP, dirs_exist_ok=True)
os.environ["LPORTMAN_DATA"] = TMP

from lportman import gui, store  # noqa: E402
messagebox.askyesno = lambda *a, **k: True

W, H = 1200, 720
root = tk.Tk()
root.geometry(f"{W}x{H}+30+30")
root.attributes("-topmost", True)
app = gui.App(root)
app.auto_live.set(False)
REAL_PORTS = str(PROJECT / "data" / "ports.json")  # 画面に一時フォルダのパスを写さない
_orig_refresh = app.refresh_views


def _refresh():
    _orig_refresh()
    app.status.set(app.status.get().replace(str(store.PORTS_FILE), REAL_PORTS))
    app.link_label.configure(text=f"参照先: {store.LINK_DIR}")


app.refresh_views = _refresh
server = subprocess.Popen([sys.executable, "-m", "http.server", "28123", "--bind", "127.0.0.1"],
                          cwd=str(PROJECT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def grab(name: str, x: int, y: int, w: int, h: int) -> None:
    ps = ("Add-Type -AssemblyName System.Drawing;"
          f"$b=New-Object System.Drawing.Bitmap {w},{h};$g=[System.Drawing.Graphics]::FromImage($b);"
          f"$g.CopyFromScreen({x},{y},0,0,$b.Size);$b.Save('{OUT}\\{name}.png')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("shot", name, flush=True)


def shoot(name: str, win: tk.Misc | None = None) -> None:
    w = win or root
    root.update()
    w.update()
    time.sleep(0.2)
    # タイトルバーも含めたウィンドウ全体
    rect = ctypes.wintypes.RECT()
    hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    grab(name, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def shoot_with_popup(name: str, open_popup) -> None:
    """ポップアップ (右クリックメニュー) を開いた状態で撮り、Esc で閉じる。"""
    rect = ctypes.wintypes.RECT()
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))

    def later():
        time.sleep(0.8)
        grab(name, rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
    threading.Thread(target=later, daemon=True).start()
    open_popup()


def tab(t) -> None:
    app.nb.select(t)
    root.update()


def row(tree, pred):
    def walk(parent=""):
        for i in tree.get_children(parent):
            if pred(i):
                return i
            r = walk(i)
            if r:
                return r
        return None
    return walk()


steps = []


def step(fn):
    steps.append(fn)
    return fn


@step
def s_graph():
    tab(app.tab_graph)
    app.graph.mode.set("matrix")
    app.graph.redraw()
    shoot("01_graph_matrix")


@step
def s_graph_dist():
    app.graph.mode.set("dist")
    app.graph.redraw()
    shoot("02_graph_dist")
    app.graph.mode.set("matrix")


@step
def s_ports():
    tab(app.tab_ports)
    shoot("03_ports")


@step
def s_projects():
    tab(app.tab_projects)
    shoot("04_projects")


@step
def s_live():
    tab(app.tab_live)
    app.live_tick_once()


@step
def s_live2():
    t = app.live_tree
    item = row(t, lambda i: t.set(i, "port") == "28123")
    if item:
        t.selection_set(item)
        t.see(item)
    shoot("05_live")


@step
def s_live_menu():
    t = app.live_tree
    item = t.selection()[0]
    root.update()
    x, y, w, h = t.bbox(item, "port")
    ev = tk.Event()
    ev.y = y + 3
    ev.x_root = t.winfo_rootx() + 260
    ev.y_root = t.winfo_rooty() + y + 12
    shoot_with_popup("06_live_menu", lambda: app.show_context_menu(t, ev))


@step
def s_conflicts():
    tab(app.tab_conflicts)
    shoot("07_conflicts")


@step
def s_check():
    app.port_var.set("5173")
    app.check_port()
    shoot("08_check")


@step
def s_dialog():
    app.port_var.set("")
    tab(app.tab_ports)
    t = app.ports_tree
    # 例: 既定値で使っている最初のプロジェクト (その既定ポートの代わりを予約する)
    item = row(t, lambda i: t.set(i, "kind") == "既定" and t.set(i, "project") != "")
    t.selection_set(item)
    t.see(item)
    root.update()
    app.open_reserve_dialog()
    dlg = app.root.winfo_children()[-1]
    dlg.attributes("-topmost", True)
    dlg.geometry("+380+260")
    root.update()
    # 「空きを入れる」を押した状態にする
    d = [o for o in gc_objects() if isinstance(o, gui.ReserveDialog)][-1]
    d.fill_suggest()
    d.note.set("既定ポートから +10000")
    shoot("09_reserve_dialog", dlg)
    dlg.destroy()


def gc_objects():
    import gc
    return gc.get_objects()


@step
def s_plan():
    tab(app.tab_plan)
    t = app.plan_tree
    # 例: ずらし幅の提案がある最初のプロジェクト
    item = next(i for i, it in app.plan_items.items() if it.changes)
    t.selection_set(item)
    shoot("10_plan")


@step
def s_plan_reserve():
    # 反映済みの例: 衝突していない明示ポートを持つ最初のプロジェクトを、そのまま予約
    r = app.report
    proj, f = next((p, f) for p in r.projects for f in p.findings
                   if f.kind == "explicit" and not r.ports[f.port].conflicts and f.port >= 1024)
    store.add_reservation(f.port, f.service, proj.path, "既存の設定をそのまま予約")
    app.reserve_plan()  # 選択中の解消案を台帳へ (一時データ)。中で再スキャンする


@step
def s_registry():
    tab(app.tab_registry)
    shoot("11_registry")


@step
def s_new():
    tab(app.tab_ports)
    app.only_new.set(True)
    app.refresh_views()
    shoot("12_new")
    app.only_new.set(False)
    app.refresh_views()


@step
def s_ranges():
    tab(app.tab_ranges)
    shoot("13_ranges")


@step
def s_settings():
    tab(app.tab_settings)
    shoot("14_settings")


@step
def s_ports_menu():
    tab(app.tab_ports)
    t = app.ports_tree
    # 例: 一覧の上の方にある、ポートのグループの2行目 (番号の無い行でも右クリックで番号が取れる)
    item = row(t, lambda i: t.set(i, "port") == "" and t.set(i, "project") != "")
    t.selection_set(item)
    t.yview_moveto(0)
    root.update()
    x, y, w, h = t.bbox(item, "project")
    ev = tk.Event()
    ev.y = y + 3
    ev.x_root = t.winfo_rootx() + x + 40
    ev.y_root = t.winfo_rooty() + y + 12
    shoot_with_popup("15_ports_menu", lambda: app.show_context_menu(t, ev))


idx = {"i": 0}
if os.environ.get("ONLY"):
    steps[:] = [f for f in steps if f.__name__ in os.environ["ONLY"].split(",")]


def run():
    if app.report is None or app.scanning or app.live_busy:
        root.after(200, run)
        return
    if idx["i"] >= len(steps):
        server.kill()
        root.destroy()
        shutil.rmtree(TMP, ignore_errors=True)
        return
    fn = steps[idx["i"]]
    idx["i"] += 1
    fn()
    root.after(500, run)


root.after(500, run)
root.mainloop()
