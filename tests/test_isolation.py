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


def test_monitor_stops_cleanly_and_saves_its_recording(tmp_path, monkeypatch):
    """Ctrl-C in monitor once crashed (it closed a headband it never opened) before the
    WAV was saved."""
    import queue as q

    from accompanist import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[[inputs]]\nname = "steel"\naudio = "x"\naudio_channel = 2\n')

    class FakeAudio:
        def __init__(self, icfg, out):
            self.icfg, self.name, self.sample_rate = icfg, "fake", 48000.0
        def close(self):
            pass

    monkeypatch.setattr("accompanist.audio_io.AudioInput", FakeAudio)
    monkeypatch.setattr(cli, "open_inputs", lambda cfg, qq, *a: [])
    calls = {"n": 0}
    def stop(*a, **k):
        calls["n"] += 1
        raise KeyboardInterrupt
    monkeypatch.setattr(q.Queue, "get", stop)
    assert cli.main(["monitor", "--input", "steel", "--record-audio", str(tmp_path / "s.wav")]) == 0
    assert (tmp_path / "s.wav").exists()
