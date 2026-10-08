"""Tests must never write into the owner's own accompanist folder (kits, sets, songs)."""
import os
from pathlib import Path

from accompanist import cli

REPO = Path(__file__).resolve().parents[1]


def test_a_command_run_elsewhere_stays_there(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cli.main(["complete", "songs"])
    assert Path.cwd() == tmp_path                    # not moved into the real folder
    assert not (tmp_path / "config.toml").exists()


def test_the_home_note_is_the_tests_own():
    assert not str(cli.home_note()).startswith(str(Path(os.path.expanduser("~real"))))
    assert str(cli.home_note()).startswith(os.environ["HOME"])
