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


def test_a_set_list_names_its_songs_in_order_and_checks_them(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sets").mkdir()
    (tmp_path / "sets" / "gig.toml").write_text(
        'title = "Temple gig"\nsongs = ["example-waltz", "example-seven"]\n')
    assert c.load_set("gig") == ("Temple gig", ["example-waltz", "example-seven"])
    (tmp_path / "sets" / "bad.toml").write_text('songs = ["example-waltz", "nope"]\n')
    with pytest.raises(c.ConfigError, match="no song file for nope"):
        c.load_set("bad")
    with pytest.raises(c.ConfigError, match="unknown set 'missing'"):
        c.load_set("missing")


def test_next_and_previous_song_are_actions_for_a_pedal():
    from accompanist.controller import Controller
    from accompanist.output import RecordingPort, SafeOutput

    ctl = Controller(c.from_dict({"controls": {"pc:66": "song_next"}}), SafeOutput(RecordingPort()))
    ctl.on_midi(0.0, "pc", 66, 127)
    assert ctl.song_step == 1


def test_run_moves_through_a_set_with_the_bracket_keys(tmp_path, monkeypatch, capsys):
    """The live loop, with fake ports and a scripted keyboard: ] twice, then q."""
    import shutil
    from pathlib import Path

    from accompanist import cli

    root = Path(__file__).parent.parent
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sets").mkdir()
    (tmp_path / "sets" / "gig.toml").write_text('songs = ["example-waltz", "example-seven"]\n')
    (tmp_path / "config.toml").write_text('[output]\nport = "fake"\n[[inputs]]\nport = "fake in"\n')

    class Keys:
        script = iter([None] * 5 + ["]"] + [None] * 5 + ["]"] + [None] * 5 + ["q"])

        def poll(self):
            return next(self.script, "q")

        def close(self):
            pass

    monkeypatch.setattr(cli, "KeyReader", Keys)
    class Port:
        def close(self):
            pass

    monkeypatch.setattr(cli, "open_inputs", lambda cfg, q, missing=None: [Port()])
    class Out(RecordingPort):
        def close(self):
            pass

    monkeypatch.setattr(cli, "open_output", lambda cfg: Out())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["run", "--set", "gig", "--no-record"]) in (0, None)
    out = capsys.readouterr().out
    assert "Song 2/2: Example in seven" in out
    assert "last song of the set" in out


def test_a_song_selects_its_mainstage_patch_by_program_change():
    from accompanist import cli

    port = RecordingPort()
    cfg = c.from_dict({"song": {"patch": 3}})
    assert cli.select_patch(SafeOutput(port), cfg)
    assert [(m.type, m.channel, m.program) for m in port.sent] == [("program_change", 15, 2)]
    assert not cli.select_patch(SafeOutput(RecordingPort()), c.from_dict({}))


def test_changing_song_while_the_band_plays_fades_it_out_first(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    from accompanist import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / "sets").mkdir()
    (tmp_path / "sets" / "gig.toml").write_text('songs = ["example-waltz", "example-seven"]\n')
    (tmp_path / "config.toml").write_text('[output]\nport = "fake"\n[[inputs]]\nport = "fake in"\n')
    sent = []

    class Keys:
        script = iter(["s"] + [None] * 50 + ["]", "]"])

        def poll(self):
            k = next(self.script, None)
            if k is None and "Song 2/2" in capsys.readouterr().out + "".join(sent_text):
                return "q"
            return k

        def close(self):
            pass

    sent_text = []

    class Out(RecordingPort):
        def send(self, msg):
            sent.append(msg)

        def close(self):
            pass

    class Port:
        def close(self):
            pass

    real_say = cli.say
    monkeypatch.setattr(cli, "say", lambda m: (sent_text.append(str(m)), real_say(m)))
    monkeypatch.setattr(cli, "KeyReader", Keys)
    monkeypatch.setattr(cli, "open_inputs", lambda cfg, q, missing=None: [Port()])
    monkeypatch.setattr(cli, "open_output", lambda cfg: Out())
    cli.main(["run", "--set", "gig", "--no-record"])
    assert any("Song 2/2" in m for m in sent_text) and any("fading out" in m for m in sent_text)
    fades = [m.value for m in sent if m.type == "control_change" and m.control == 11
             and m.channel == 1]                                   # the bass channel
    assert fades and fades[0] > 100 and min(fades) < 20 and fades[-1] == 127
