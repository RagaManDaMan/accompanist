"""Song files: a per-song setup, layered above config.toml, with a one-press count-in."""
import pytest

from accompanist import config as c, simulate
from accompanist.cli import voices_summary
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput


def write(tmp_path, name, text):
    (tmp_path / "songs").mkdir(exist_ok=True)
    (tmp_path / "songs" / f"{name}.toml").write_text(text)


def test_layering_defaults_preset_config_song_command_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('preset = "ambient"\n[pad]\nvelocity = 60\nlag_beats = 12\n')
    write(tmp_path, "tune", '[song]\ntempo = 100\n[pad]\nlag_beats = 3\n')
    cfg = c.build("config.toml", song="tune", overrides={"song": {"tempo": 90}})
    assert cfg.pad.overlap_s == 2.0          # from the preset
    assert cfg.pad.velocity == 60            # config.toml over the preset
    assert cfg.pad.lag_beats == 3            # the song over config.toml
    assert cfg.song.tempo == 90              # the command line over the song
    assert cfg.song.title == "tune"          # the file name, when no title is given


def test_a_song_cannot_touch_the_rig(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "bad", '[[inputs]]\nport = "X"\n')
    with pytest.raises(c.ConfigError, match="belong in config.toml"):
        c.build(song="bad")
    with pytest.raises(c.ConfigError, match="unknown song 'nope'"):
        c.build(song="nope")


def test_a_chart_path_may_be_relative_to_the_song_file(tmp_path, monkeypatch):
    from pathlib import Path
    src = Path(__file__).parent / "fixtures" / "charts" / "form-test.musicxml"
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "charted", '[harmony]\nmodel = "chart"\nchart = "form-test.musicxml"\n')
    (tmp_path / "songs" / "form-test.musicxml").write_text(src.read_text())
    cfg = c.build(song="charted")
    assert Path(cfg.harmony.chart).exists()


def test_built_in_example_songs_load():
    for name in ("example-waltz", "example-seven"):
        cfg = c.build(song=name)
        assert cfg.song.title and cfg.song.tempo and cfg.song.count
        assert "Song:" in voices_summary(cfg)


def test_s_counts_in_at_the_song_tempo_and_meter_then_drums_lead():
    cfg = c.build(song="example-waltz", overrides={"song": {"tempo": 100}})
    period = 0.6
    notes = [(8.0 + i * period, 62 + (i % 3) * 2, 90) for i in range(12)]   # you come in later
    res = simulate.run(cfg, onsets=notes, actions=[(1.0, "song_start")], total=16.0)
    clicks = [t for t, m in res.timeline if m.type == "note_on" and m.channel == 9
              and m.note == cfg.drums.count_in_note]
    assert [round(t - 1.0, 2) for t in clicks[:3]] == pytest.approx([0.6, 1.2, 1.8], abs=0.011)
    assert res.engine.groove.label().startswith("3/4") and res.engine.locked
    first_drum = min(t for t, m in res.timeline if m.type == "note_on" and m.channel == 9
                     and m.note != cfg.drums.count_in_note)
    first_pad = min(t for t, m in res.timeline if m.type == "note_on"
                    and m.channel == cfg.pad.channel - 1)
    assert first_drum == pytest.approx(1.0 + 4 * period, abs=0.011)       # on 1 after the count
    assert first_pad >= 8.0                                                 # the pad waits for you


def test_without_a_song_tempo_s_says_to_count_off():
    ctl = Controller(c.from_dict({}), SafeOutput(RecordingPort()))
    assert "count off with t" in ctl.do("song_start", 0.0)


def test_chart_restart_still_works_as_before():
    from pathlib import Path
    chart = str(Path(__file__).parent / "fixtures" / "charts" / "form-test.musicxml")
    ctl = Controller(c.from_dict({"harmony": {"model": "chart", "chart": chart, "chart_bpm": 100}}),
                     SafeOutput(RecordingPort()))
    assert ctl.do("chart_restart", 0.0).startswith("counting in at 100 bpm: 1 2 3 4")
