@echo off
rem LPortMan の画面を起動する。.venv が無ければ uv sync で作る。
cd /d "%~dp0"
if not exist ".venv\Scripts\lportman-gui.exe" (
    echo 初回セットアップ中 ^(uv sync^)...
    uv sync || (pause & exit /b 1)
)
start "" ".venv\Scripts\lportman-gui.exe"
