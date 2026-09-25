"""CLAUDE.md の節の書き込み (claude_md.write) のテスト。"""

from pathlib import Path

from lportman import claude_md


def test_write_appends_then_replaces(tmp_path: Path) -> None:
    f = tmp_path / "CLAUDE.md"
    f.write_text("# 既存\n\nほかの節\n", encoding="utf-8")

    assert "追記" in claude_md.write(f)
    first = f.read_text(encoding="utf-8")
    assert first.startswith("# 既存\n\nほかの節\n")
    assert first.count("lportman:begin") == 1

    # 節の中を手で変えても、再実行でその節だけが差し替わる
    f.write_text(first.replace("## ローカルのポート", "## 手で変えた見出し") + "\n末尾の節\n", encoding="utf-8")
    assert "差し替え" in claude_md.write(f)
    second = f.read_text(encoding="utf-8")
    assert second.count("lportman:begin") == 1
    assert "手で変えた見出し" not in second
    assert "## ローカルのポート" in second
    assert second.startswith("# 既存") and second.rstrip().endswith("末尾の節")


def test_write_creates_file(tmp_path: Path) -> None:
    f = tmp_path / "sub" / "CLAUDE.md"
    assert "作成" in claude_md.write(f)
    text = f.read_text(encoding="utf-8")
    assert text.startswith("<!-- lportman:begin") and text.rstrip().endswith("<!-- lportman:end -->")
