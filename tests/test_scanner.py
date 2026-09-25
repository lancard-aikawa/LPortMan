"""設定ファイルからのポート読み取り (scanner) と、台帳の反映判定のテスト。"""

import json
from pathlib import Path

from lportman import analyze, scanner


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ports(findings: list[scanner.Finding]) -> set[tuple[int, str]]:
    return {(f.port, f.kind) for f in findings}


# ---------------------------------------------------------------- package.json


def test_package_scripts_explicit_and_default(tmp_path: Path) -> None:
    p = write(tmp_path / "package.json", json.dumps({"scripts": {
        "dev": "vite dev --port 3000",
        "preview": "vite preview",
        "build": "vite build",
        "start": "next start -p 4001",
        "cf": "wrangler dev",
    }}))
    got = ports(scanner.parse_package_json(p, None))
    assert (3000, "explicit") in got
    assert (4173, "default") in got
    assert (4001, "explicit") in got
    assert (8787, "default") in got
    assert all(port != 5173 for port, _ in got)  # build は数えない


def test_vite_config_overrides_default(tmp_path: Path) -> None:
    write(tmp_path / "vite.config.ts", "export default defineConfig({ server: { port: 1420, strictPort: true } })")
    write(tmp_path / "package.json", json.dumps({"scripts": {"dev": "vite"}}))
    found = scanner.parse_dir(tmp_path, ["vite.config.ts", "package.json"])
    assert (1420, "explicit") in ports(found)
    assert (5173, "default") not in ports(found)


# ---------------------------------------------------------------- firebase.json


def test_firebase_explicit_and_implicit_ports(tmp_path: Path) -> None:
    p = write(tmp_path / "firebase.json", json.dumps({"emulators": {
        "auth": {"port": 19099},
        "firestore": {},
        "ui": {"enabled": True},
    }}))
    got = ports(scanner.parse_firebase_json(p))
    assert (19099, "explicit") in got
    assert (8080, "default") in got  # port 未指定の firestore
    assert (4000, "default") in got  # ui
    assert (4400, "default") in got  # hub は書かなくても起動する
    assert (4500, "default") in got  # logging も


def test_firebase_ui_disabled(tmp_path: Path) -> None:
    p = write(tmp_path / "firebase.json", json.dumps({"emulators": {"ui": {"enabled": False}}}))
    assert 4000 not in {f.port for f in scanner.parse_firebase_json(p)}


# ---------------------------------------------------------------- docker compose


def test_compose_ports(tmp_path: Path) -> None:
    p = write(tmp_path / "docker-compose.yml", """
services:
  web:
    image: nginx
    volumes:
      - ./a:/a
    ports:
      - "8084:80"
      - 127.0.0.1:8443:443/tcp
      - ${PORT:-3000}:3000
      - "9000"
  db:
    #ports:
    #  - 27017:27017
    ports:
      - target: 5432
        published: 15432
""")
    found = scanner.parse_compose(p)
    assert {f.port for f in found} == {8084, 8443, 3000, 15432}
    assert {f.service for f in found if f.port == 8084} == {"compose: web"}
    assert {f.service for f in found if f.port == 15432} == {"compose: db"}


# ---------------------------------------------------------------- .env


def test_env_port_and_refs(tmp_path: Path) -> None:
    p = write(tmp_path / ".env", "PORT=3210\nDB_PORT=5432\nOAUTHLIB_INSECURE_TRANSPORT=1\nSECRET=abc\n")
    got = ports(scanner.parse_env(p))
    assert got == {(3210, "explicit"), (5432, "ref")}


def test_env_example_is_not_a_marker() -> None:
    assert scanner.is_marker(".env.local")
    assert not scanner.is_marker(".env.example")


# ---------------------------------------------------------------- 走査


def test_scan_root_groups_by_git_and_samples(tmp_path: Path) -> None:
    repo = tmp_path / "apps" / "shop"
    (repo / ".git").mkdir(parents=True)
    write(repo / "package.json", json.dumps({"scripts": {"dev": "vite"}}))
    write(repo / "functions" / "package.json", json.dumps({"scripts": {"serve": "firebase serve"}}))
    write(tmp_path / "test" / "demo" / "package.json", json.dumps({"scripts": {"dev": "next dev"}}))
    write(tmp_path / "apps" / "shop" / "node_modules" / "x" / "package.json",
          json.dumps({"scripts": {"dev": "vite --port 1111"}}))

    root = {"path": str(tmp_path), "label": "T", "tier": "normal"}
    projects = {p.name: p for p in scanner.scan_root(root, 6, {"node_modules", ".git"}, ["test\\*"])}

    shop = projects["apps\\shop"]
    assert {f.port for f in shop.findings} == {5173, 5000}  # サブフォルダも同じ git リポジトリにまとめる
    assert all(f.port != 1111 for f in shop.findings)  # node_modules は見ない
    assert projects["test\\demo"].tier == "sample"


# ---------------------------------------------------------------- 台帳の反映判定


def test_registry_status(tmp_path: Path) -> None:
    proj = scanner.Project(path=str(tmp_path), name="app", root_label="T", tier="normal", mask=False,
                           findings=[scanner.Finding(3000, "Vite dev", "vite", "explicit", "package.json", "")])
    reg = {"reservations": [
        {"port": 3000, "name": "Vite dev", "project": str(tmp_path)},
        {"port": 13000, "name": "Vite dev", "project": str(tmp_path)},
        {"port": 20000, "name": "memo"},
        {"port": 20001, "name": "x", "project": str(tmp_path / "missing")},
    ]}
    st = {e["port"]: e for e in analyze.registry_status(reg, [proj])}
    assert st[3000]["status"] == "ok"
    assert st[13000]["status"] == "unapplied"
    assert "3000" in st[13000]["message"]  # 旧ポートを示す
    assert st[20000]["status"] == "noproject"
    assert st[20001]["status"] == "missing"
