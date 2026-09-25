"""PyInstaller の入口。lportman.exe (コンソール) と lportmanw.exe (窓なし) で共用する。

- 引数あり: CLI (`lportman.exe check 5173` など)
- 引数なし: 窓なし版は画面を開く。コンソール版はヘルプを出す
"""

import sys


def main() -> int:
    if len(sys.argv) == 1 and sys.stdout is None:  # 窓なし版 (console=False) は stdout が無い
        from lportman import gui

        gui.main()
        return 0
    from lportman.cli import main as cli_main

    if len(sys.argv) == 1:
        sys.argv.append("--help")
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
