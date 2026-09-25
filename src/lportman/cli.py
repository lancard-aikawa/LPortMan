"""lportman CLI.

  lportman scan              調査して data/ports.json を更新
  lportman list [--json]     ポート一覧 (--conflicts で衝突のみ)
  lportman check <port>...   そのポートを使ってよいか
  lportman suggest [base]    空きポート候補 (base=既定ポートなら +10000/+20000 を優先)
  lportman reserve <port> --name N [--project P] [--note T]   台帳に予約を追加
  lportman unreserve <port> --name N                           台帳から予約を削除
  lportman plan [--out FILE] 衝突の解消案 (提案のみ) を Markdown で出力
  lportman link              %USERPROFILE%\\.lportman -> data/ のジャンクション作成
  lportman gui               画面を開く
"""

from __future__ import annotations

import argparse
import json
import sys

from lportman import __version__, analyze, plan, store


def _print_json(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def cmd_scan(args: argparse.Namespace) -> int:
    report = analyze.run_and_save()
    if not report.config.get("roots"):
        print(f"調べるフォルダが未設定です。{store.CONFIG_FILE} の roots に追加するか、画面の設定タブで追加してください。")
    high = sum(c.severity == "high" for c in report.conflicts)
    print(f"プロジェクト {len(report.projects)} / ポート {len(report.ports)} / "
          f"待ち受け {len(report.listeners)} / 衝突 {len(report.conflicts)} (高 {high})")
    print(f"出力: {store.PORTS_FILE}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    report = analyze.run_and_save()
    data = analyze.to_json(report)
    if args.conflicts:
        if args.json:
            _print_json(data["conflicts"])
        else:
            for c in report.conflicts:
                print(f"[{analyze.SEVERITY_LABEL[c.severity]}] {c.port:>5}  {c.message}")
        return 0
    if args.json:
        _print_json(data)
        return 0
    for port, pi in sorted(report.ports.items()):
        sev = analyze.SEVERITY_LABEL.get(pi.severity or "", " ")
        live_mark = "稼働" if pi.live else "    "
        for i, u in enumerate(pi.uses):
            who = u.project.name if u.project else "(台帳)"
            head = f"{port:>5} [{sev}] {live_mark}" if i == 0 else " " * 16
            print(f"{head}  {analyze.KIND_LABEL[u.kind]}  {who:<28} {u.service}  ({u.file})")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    report = analyze.build_report()
    results = [analyze.check_port(report, p) for p in args.ports]
    if args.json:
        _print_json(results)
    else:
        label = {"free": "使用可", "warn": "注意", "busy": "使用不可"}
        for r in results:
            print(f"{r['port']}: {label[r['status']]}")
            for reason in r["reasons"]:
                print(f"  - {reason}")
    return 0 if all(r["status"] != "busy" for r in results) else 1


def cmd_suggest(args: argparse.Namespace) -> int:
    report = analyze.build_report()
    ports = analyze.suggest_ports(report, args.base, args.count)
    if args.json:
        _print_json(ports)
    else:
        print(" ".join(map(str, ports)))
    return 0


def cmd_reserve(args: argparse.Namespace) -> int:
    report = analyze.build_report()
    res = analyze.check_port(report, args.port)
    if res["status"] == "busy" and not args.force:
        print(f"{args.port} は使用不可のため登録しません (--force で強行):")
        for reason in res["reasons"]:
            print(f"  - {reason}")
        return 1
    entry = store.add_reservation(args.port, args.name, args.project or "", args.note or "")
    print(f"登録しました: {entry}")
    return 0


def cmd_unreserve(args: argparse.Namespace) -> int:
    if store.remove_reservation(args.port, args.name):
        print(f"削除しました: {args.port} {args.name}")
        return 0
    print(f"見つかりません: {args.port} {args.name}")
    return 1


def cmd_plan(args: argparse.Namespace) -> int:
    report = analyze.build_report()
    text = plan.plan_markdown(report, plan.make_plan(report))
    out = args.out or str(store.PLAN_FILE)
    if out == "-":
        sys.stdout.write(text)
    else:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"出力: {out}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    print(store.make_link())
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from lportman import gui
    gui.main()
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="lportman", description="Local Port Manager")
    parser.add_argument("--version", action="version", version=f"lportman {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="調査して ports.json を更新")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("list", help="ポート一覧")
    p.add_argument("--json", action="store_true")
    p.add_argument("--conflicts", action="store_true", help="衝突のみ")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="ポートを使ってよいか確認")
    p.add_argument("ports", type=int, nargs="+")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("suggest", help="空きポート候補")
    p.add_argument("base", type=int, nargs="?", help="元の既定ポート (例: 5173)")
    p.add_argument("-n", "--count", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("reserve", help="台帳に予約を追加")
    p.add_argument("port", type=int)
    p.add_argument("--name", required=True, help="用途 (例: MyApp dev)")
    p.add_argument("--project", help="プロジェクトのフォルダ")
    p.add_argument("--note")
    p.add_argument("--force", action="store_true", help="使用不可でも登録する")
    p.set_defaults(func=cmd_reserve)

    p = sub.add_parser("unreserve", help="台帳から予約を削除")
    p.add_argument("port", type=int)
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_unreserve)

    p = sub.add_parser("plan", help="衝突の解消案 (提案のみ)")
    p.add_argument("--out", help="出力先 (既定: data/plan.md、- で標準出力)")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("link", help="%%USERPROFILE%%\\.lportman のジャンクション作成")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("gui", help="画面を開く")
    p.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
