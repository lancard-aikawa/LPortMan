"""マニュアル (docs/manual.md) の画面画像を撮る。

    uv run python tools/manual_shots.py            # デモ環境で撮る -> docs/images (コミットする)
    uv run python tools/manual_shots.py --real     # 自分の環境で撮る -> docs/images-local (git 管理外)
    set ONLY=s_ports_menu & uv run python tools/manual_shots.py   # 一部だけ (関数名をカンマ区切り)

デモ環境: 一時フォルダに架空のプロジェクトを作り、空いているドライブ文字に subst で割り当てて
(X:\\Repos のような中立的なパスに見せる) 調べる。稼働中のプロセスも架空のものに差し替えるので、
実際のプロジェクト名・パス・ユーザー名は写らない。終わったら subst を外し、一時フォルダを消す。

どちらのモードでも LPORTMAN_DATA を一時フォルダに向けるので、本物の台帳・seen.json は変わらない。
撮影中はウィンドウを最前面に出して画面をキャプチャするので、マウス・キーボードに触らないこと。
"""
import ctypes
import ctypes.wintypes
import gc
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

PROJECT = Path(__file__).resolve().parents[1]
REAL = "--real" in sys.argv
OUT = str(PROJECT / "docs" / ("images-local" if REAL else "images"))
os.makedirs(OUT, exist_ok=True)

# 本物の data/ を汚さないよう、一時フォルダを使わせる (lportman の import より前に設定する)
TMP = tempfile.mkdtemp(prefix="lportman_manual_")
if REAL and (PROJECT / "data").is_dir():
    shutil.copytree(PROJECT / "data", TMP, dirs_exist_ok=True)
os.environ["LPORTMAN_DATA"] = TMP

from lportman import gui, live, store  # noqa: E402

messagebox.askyesno = lambda *a, **k: True

# ---------------------------------------------------------------- デモ環境

DEMO_BASE = ""
DRIVE = ""
DEMO_ROOT = ""


def _write(path: Path, content: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(content, indent=2) if isinstance(content, dict) else content
    path.write_text(text, encoding="utf-8")


def build_demo(root: Path) -> None:
    """架空のプロジェクト群。衝突・既定値・compose・.env・サンプルが一通り出るように作る。"""
    def repo(name: str) -> Path:
        p = root / name
        (p / ".git").mkdir(parents=True, exist_ok=True)
        return p

    p = repo("shop-web")
    _write(p / "package.json", {"scripts": {"dev": "vite --port 3000", "preview": "vite preview",
                                            "emulators": "firebase emulators:start"}})
    _write(p / "firebase.json", {"emulators": {"auth": {"port": 9099}, "firestore": {"port": 8080},
                                               "functions": {"port": 5001}, "storage": {"port": 9199},
                                               "ui": {"enabled": True}}})
    p = repo("admin-console")
    _write(p / "package.json", {"scripts": {"dev": "vite", "build": "vite build"}})
    _write(p / "firebase.json", {"emulators": {"auth": {"port": 9099}, "firestore": {"port": 8080},
                                               "ui": {"enabled": True}}})
    p = repo("mobile-app")
    _write(p / "firebase.json", {"emulators": {"auth": {"port": 9099}, "storage": {"port": 9199}}})
    p = repo("blog")
    _write(p / "package.json", {"scripts": {"dev": "next dev", "start": "next start"}})
    p = repo("api-server")
    _write(p / "docker-compose.yml",
           "services:\n  web:\n    build: .\n    ports:\n      - \"3000:3000\"\n"
           "  db:\n    image: postgres:16\n    ports:\n      - \"5432:5432\"\n")
    _write(p / ".env", "PORT=3000\nDB_PORT=5432\n")
    p = repo("docs-site")
    _write(p / "package.json", {"scripts": {"dev": "vitepress dev docs"}})
    p = repo("desktop-app")
    _write(p / "package.json", {"scripts": {"dev": "vite", "tauri": "tauri dev"}})
    _write(p / "vite.config.ts", "export default defineConfig({\n  server: { port: 1420, strictPort: true },\n})\n")
    p = repo("chat-bot")
    _write(p / "package.json", {"scripts": {"dev": "wrangler dev"}})
    p = repo("analytics")
    _write(p / "pyproject.toml", '[project]\nname = "analytics"\ndependencies = ["streamlit>=1.40"]\n')
    p = root / "test" / "vite-sample"
    _write(p / "package.json", {"scripts": {"dev": "vite"}})


def fake_listeners() -> list[live.Listener]:
    r = DEMO_ROOT
    return [
        live.Listener(135, "0.0.0.0", 1024, "svchost.exe", "", ""),
        live.Listener(445, "0.0.0.0", 4, "System", "", ""),
        live.Listener(3000, "127.0.0.1", 18244, "node.exe", rf"{r}\shop-web", ""),
        live.Listener(4000, "127.0.0.1", 18302, "node.exe", rf"{r}\shop-web", ""),
        live.Listener(4400, "127.0.0.1", 18302, "node.exe", rf"{r}\shop-web", ""),
        live.Listener(5173, "::1", 20516, "node.exe", rf"{r}\admin-console", ""),
        live.Listener(8080, "127.0.0.1", 18390, "java.exe", rf"{r}\shop-web", ""),
        live.Listener(9099, "127.0.0.1", 18302, "node.exe", rf"{r}\shop-web", ""),
        live.Listener(11434, "127.0.0.1", 9120, "ollama.exe", "", ""),
        live.Listener(23817, "127.0.0.1", 7788, f"Code.exe {live.CLAUDE_IDE_LABEL}",
                      r"C:\Program Files\Microsoft VS Code", ""),
        live.Listener(49664, "0.0.0.0", 812, "lsass.exe", "", ""),
    ]


def setup_demo() -> None:
    global DEMO_BASE, DRIVE, DEMO_ROOT
    DEMO_BASE = tempfile.mkdtemp(prefix="lportman_demo_")
    used = {d[0] for d in os.listdrives()} if hasattr(os, "listdrives") else set()
    for letter in reversed(string.ascii_uppercase[3:]):  # Z から空きを探す
        if letter not in used and not os.path.exists(f"{letter}:\\"):
            DRIVE = letter
            break
    if not DRIVE:
        raise RuntimeError("subst に使える空きドライブ文字がありません")
    subprocess.run(["subst", f"{DRIVE}:", DEMO_BASE], check=True)
    DEMO_ROOT = f"{DRIVE}:\\Repos"
    build_demo(Path(DEMO_ROOT))
    # 実際の環境が写らないよう、候補フォルダと稼働中のプロセスを差し替える
    gui.find_dev_folders = lambda: [(DEMO_ROOT, 9)]
    live.listeners = fake_listeners
    live.docker_compose_dirs = lambda: {}


def cleanup() -> None:
    if DRIVE:
        subprocess.run(["subst", f"{DRIVE}:", "/d"], check=False)
    if DEMO_BASE:
        shutil.rmtree(DEMO_BASE, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)


if not REAL:
    setup_demo()

# ---------------------------------------------------------------- 画面

W, H = 1200, 720
root = tk.Tk()
root.geometry(f"{W}x{H}+30+30")
root.attributes("-topmost", True)
app = gui.App(root)
app.auto_live.set(False)
_orig_refresh = app.refresh_views
SHOWN_PORTS = str(PROJECT / "data" / "ports.json") if REAL else r"C:\Tools\LPortMan\data\ports.json"
SHOWN_LINK = str(store.LINK_DIR) if REAL else r"C:\Users\you\.lportman"


def _refresh():
    _orig_refresh()
    # 一時フォルダ・実際のユーザー名を写さない
    app.status.set(app.status.get().replace(str(store.PORTS_FILE), SHOWN_PORTS))
    app.link_label.configure(text=f"参照先: {SHOWN_LINK}")


app.refresh_views = _refresh
server = None
if REAL:
    server = subprocess.Popen([sys.executable, "-m", "http.server", "28123", "--bind", "127.0.0.1"],
                              cwd=str(PROJECT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def grab(name: str, x: int, y: int, w: int, h: int) -> None:
    ps = ("Add-Type -AssemblyName System.Drawing;"
          f"$b=New-Object System.Drawing.Bitmap {w},{h};$g=[System.Drawing.Graphics]::FromImage($b);"
          f"$g.CopyFromScreen({x},{y},0,0,$b.Size);$b.Save('{OUT}\\{name}.png')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("shot", name, flush=True)


def _rect(w: tk.Misc) -> tuple[int, int, int, int]:
    """タイトルバーも含めたウィンドウ全体の位置。"""
    rect = ctypes.wintypes.RECT()
    hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def shoot(name: str, win: tk.Misc | None = None) -> None:
    w = win or root
    root.update()
    w.update()
    time.sleep(0.2)
    grab(name, *_rect(w))


def shoot_with_popup(name: str, open_popup) -> None:
    """ポップアップ (右クリックメニュー) を開いた状態で撮り、Esc で閉じる。"""
    rect = _rect(root)

    def later():
        time.sleep(0.8)
        grab(name, *rect)
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


def latest(cls):
    objs = [o for o in gc.get_objects() if isinstance(o, cls)]
    return objs[-1] if objs else None


steps = []


def step(fn):
    steps.append(fn)
    return fn


@step
def s_firstrun():
    """初回ダイアログ (デモのみ。調べるフォルダが未設定の状態から始まる)。"""
    d = latest(gui.FirstRunDialog)
    if d is None:
        return
    d.win.attributes("-topmost", True)
    shoot("00_firstrun", d.win)
    d.start()  # 候補 (デモのフォルダ) を選んで開始 -> 再スキャン


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
    want = "28123" if REAL else "5173"
    item = row(t, lambda i: t.set(i, "port") == want)
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
    d = latest(gui.ReserveDialog)
    d.win.attributes("-topmost", True)
    d.win.geometry("+380+260")
    root.update()
    d.fill_suggest()  # 「空きを入れる」を押した状態にする
    d.note.set("既定ポートから +10000")
    shoot("09_reserve_dialog", d.win)
    d.win.destroy()


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
def s_add_new():
    """新着の例: 最初のスキャンのあとで、既定値のまま作られたプロジェクトが増えた。"""
    if not REAL:
        p = Path(DEMO_ROOT) / "landing-page"
        (p / ".git").mkdir(parents=True, exist_ok=True)
        _write(p / "package.json", {"scripts": {"dev": "vite", "preview": "vite preview"}})
        app.start_scan()


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
        root.destroy()
        return
    fn = steps[idx["i"]]
    idx["i"] += 1
    fn()
    root.after(600, run)


try:
    root.after(500, run)
    root.mainloop()
finally:
    if server:
        server.kill()
    cleanup()
