"""A gig must not stop over a fixable mistake: broken song, broken config, a bug mid-set."""
import argparse

import pytest

from accompanist import cli, config as c

GOOD = '[pad]\nchannel = 3\n'


def args(tmp_path, song=None):
    return argparse.Namespace(config=str(tmp_path / "config.toml"), preset=None, song=song,
                              chart=None, transpose=None, tempo=None)


def test_a_good_config_is_kept_as_the_last_good_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(GOOD)
    assert cli.load_for_run(args(tmp_path)).pad.channel == 3
    assert (tmp_path / "config.toml.last-good").read_text() == GOOD


def test_a_broken_config_falls_back_to_the_last_good_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(GOOD)
    cli.load_for_run(args(tmp_path))
    (tmp_path / "config.toml").write_text('[pad]\nchannel = 30\n')            # a typo
    assert cli.load_for_run(args(tmp_path)).pad.channel == 3
    assert "Using the last copy that worked" in capsys.readouterr().out
    assert (tmp_path / "config.toml.last-good").read_text() == GOOD           # not overwritten


def test_without_a_last_good_copy_the_error_is_readable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[pad]\nchannel = 30\n')
    with pytest.raises(c.ConfigError, match="channel"):
        cli.load_for_run(args(tmp_path))


def test_a_broken_song_plays_with_the_config_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(GOOD)
    (tmp_path / "songs").mkdir()
    (tmp_path / "songs" / "bad.toml").write_text('[drums]\npattern = "nope"\n')
    cfg = cli.load_for_run(args(tmp_path, song="bad"))
    assert cfg.pad.channel == 3 and cfg.drums.pattern == "basic"
    assert "The song 'bad' has a problem" in capsys.readouterr().out


def test_an_internal_error_is_logged_once_and_counted(tmp_path, capsys):
    guard = cli.LiveGuard(tmp_path / "logs" / "errors.log")
    for _ in range(50):
        try:
            raise ValueError("boom")
        except ValueError as e:
            guard.report(e)
    log = (tmp_path / "logs" / "errors.log").read_text()
    assert log.count("ValueError: boom") == 1 and guard.count == 50
    assert capsys.readouterr().out.count("internal error") == 1


def test_soundcheck_plays_every_voice_that_is_on_on_its_channel():
    cfg = c.from_dict({"drums": {"enabled": True}, "percussion": {"enabled": True},
                       "piano": {"enabled": True}, "response": {"enabled": True},
                       "pad": {"channel": 3}})
    plan = cli.soundcheck_plan(cfg)
    channels = {label.split(" ")[0]: ch for label, ch, _ in plan}
    assert channels == {"pad": 3, "bass": 2, "drums": 10, "percussion": 11, "piano": 5,
                        "guitar": 4}
    assert all(notes for _, _, notes in plan)


def test_the_recorder_switch_presses_and_releases_its_cc():
    from accompanist.output import RecordingPort, SafeOutput

    port = RecordingPort()
    cfg = c.from_dict({"output": {"recorder_cc": 119}})
    assert cli.press_recorder(SafeOutput(port), cfg)
    assert [(m.channel, m.control, m.value) for m in port.sent] == [(15, 119, 127), (15, 119, 0)]
    assert not cli.press_recorder(SafeOutput(RecordingPort()), c.from_dict({}))
