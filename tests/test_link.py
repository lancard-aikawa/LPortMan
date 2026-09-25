"""ジャンクション (%USERPROFILE%\\.lportman) の作成・張り直しのテスト。実物ではなく一時フォルダで試す。"""

import subprocess
from pathlib import Path

import pytest

from lportman import store


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    link = tmp_path / "link"
    data = tmp_path / "data"
    old = tmp_path / "old-data"
    data.mkdir()
    old.mkdir()
    (old / "registry.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "LINK_DIR", link)
    monkeypatch.setattr(store, "DATA_DIR", data)
    return link, data, old


def junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_make_link_creates(paths: tuple[Path, Path, Path]) -> None:
    link, data, _ = paths
    assert "作成" in store.make_link()
    assert store.link_status() == "ok"


def test_force_repoints_without_deleting_old_data(paths: tuple[Path, Path, Path]) -> None:
    link, data, old = paths
    junction(link, old)
    assert store.link_status().startswith("other:")
    with pytest.raises(RuntimeError, match="--force"):
        store.make_link()
    store.make_link(force=True)
    assert store.link_status() == "ok"
    assert (old / "registry.json").exists()  # リンク先 (前の版のデータ) は消えない


def test_force_never_removes_real_folder(paths: tuple[Path, Path, Path]) -> None:
    link, _, _ = paths
    link.mkdir()
    (link / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="実体のフォルダ"):
        store.make_link(force=True)
    assert (link / "keep.txt").exists()
