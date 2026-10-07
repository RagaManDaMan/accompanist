"""How a song starts (start.shape): count, a band intro, the drums alone, you alone, a drone."""
from accompanist import config as c, simulate


def song(shape, bars=2, **more):
    return c.from_dict({"song": {"tempo": 100, "count": 4},
                        "harmony": {"keys": "D minor"},
                        "drums": {"enabled": True},
                        "start": {"shape": shape, "bars": bars}, **more})


def notes(res, ch, t0=0.0, t1=1e9):
    return [(t, m.note) for t, m in res.timeline
            if m.type == "note_on" and m.velocity and m.channel == ch - 1 and t0 <= t < t1]


BEAT = 0.6
START = 1.0                        # s pressed here; clicks on the next 4 beats
INTRO = START + 5 * BEAT           # the intro's 1 (clicks on the 4 beats after s)


def test_an_intro_has_the_band_on_the_home_chord_before_you_play():
    cfg = song("intro")
    res = simulate.run(cfg, onsets=[], actions=[(START, "song_start")], total=INTRO + 8 * BEAT)
    pad = notes(res, cfg.pad.channel, INTRO, INTRO + 8 * BEAT)
    bass = notes(res, cfg.pulse.channel, INTRO, INTRO + 8 * BEAT)
    drums = notes(res, cfg.drums.channel, INTRO, INTRO + 8 * BEAT)
    assert pad and {n % 12 for _, n in pad} <= {2, 5, 9, 0}               # D minor, by itself
    assert bass and drums


def test_the_song_proper_starts_on_the_bar_after_the_intro():
    cfg = song("intro", bars=2)
    res = simulate.run(cfg, onsets=[], actions=[(START, "song_start")], total=INTRO + 7.5 * BEAT)
    assert res.engine.in_intro                                             # 8 beats: not yet
    res = simulate.run(cfg, onsets=[], actions=[(START, "song_start")], total=INTRO + 8.5 * BEAT)
    assert not res.engine.in_intro and res.engine.beat_count <= 2          # bar 1 for you


def test_a_drums_start_is_the_drums_alone_then_the_band_with_you():
    cfg = song("drums")
    you = [(INTRO + 8 * BEAT + i * BEAT / 2, (62, 65, 69, 72)[i % 4], 90) for i in range(16)]
    res = simulate.run(cfg, onsets=you, actions=[(START, "song_start")], total=INTRO + 16 * BEAT)
    assert notes(res, cfg.drums.channel, INTRO, INTRO + 8 * BEAT)
    assert not notes(res, cfg.pad.channel, INTRO, INTRO + 8 * BEAT)
    assert not notes(res, cfg.pulse.channel, INTRO, INTRO + 8 * BEAT)
    assert notes(res, cfg.pulse.channel, INTRO + 8 * BEAT)                # then the bass, with you


def test_a_you_start_has_no_count():
    cfg = song("you")
    res = simulate.run(cfg, onsets=[], actions=[(START, "song_start")], total=INTRO + 2 * BEAT)
    assert not notes(res, cfg.drums.channel)                               # no clicks, nothing


def test_a_drone_holds_the_home_chord_then_s_again_counts_in():
    cfg = song("drone")
    again = START + 6.0
    res = simulate.run(cfg, onsets=[], actions=[(START, "song_start"), (again, "song_start")],
                       total=again + 4 * BEAT + 4 * BEAT)
    drone = notes(res, cfg.pad.channel, START, again)
    assert drone and {n % 12 for _, n in drone} <= {2, 5, 9, 0}
    assert not notes(res, cfg.drums.channel, START, again)                 # free time: no beat
    clicks = notes(res, cfg.drums.channel, again, again + 4 * BEAT + 0.1)
    assert len(clicks) == 4 and not res.engine.droning


import pytest

from accompanist import cli, reckoner
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput


@pytest.mark.parametrize("start", ["count", "intro", "drums", "drone"])
@pytest.mark.parametrize("finish", ["chord", "tag", "ritardando", "button", "piano-tag"])
def test_rehearse_plays_the_start_then_the_finish_and_stops(start, finish):
    cfg = c.load(None, overrides={"start": {"shape": start}, "ending": {"shape": finish}},
                 song="kann-pona-pokkile")
    port = RecordingPort()
    r = cli.Rehearsal(Controller(cfg, SafeOutput(port)), cfg)
    t, said = 0.0, []
    while not r.step(t, said.append):
        t += 0.005
        assert t < 90, said
    assert r.eng.finished and r.phase == "finish"
    sounding = [m for m in port.sent if m.type == "note_on" and m.velocity]
    assert {m.channel for m in sounding} >= {cfg.pad.channel - 1, cfg.pulse.channel - 1}


def test_the_reckoner_says_how_each_song_starts_and_finishes():
    cfg = c.load(None, overrides={"start": {"shape": "intro", "bars": 2},
                                  "ending": {"shape": "tag"}}, song="kann-pona-pokkile")
    card = reckoner.card(cfg, "kann-pona-pokkile")
    text = reckoner.format_card(card, 1)
    assert "2 bars on the home chord" in text and "4 bars more" in text
    assert "3/4" in text and "138 bpm" in text and text.startswith("1. ")


def a_set(tmp_path, monkeypatch, extra=""):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sets").mkdir()
    f = tmp_path / "sets" / "gig.toml"
    f.write_text('# my gig\ntitle = "Gig"\nsongs = ["example-waltz", "example-seven"]\n' + extra)
    return f


def test_a_set_keeps_each_songs_start_and_finish(tmp_path, monkeypatch):
    f = a_set(tmp_path, monkeypatch)
    c.save_plan("gig", "example-waltz", start="intro", finish="ritardando", bars=2)
    c.save_plan("gig", "example-waltz", start="drums", finish="tag", bars=4)   # replaces
    c.save_plan("gig", "example-seven", start="drone", finish="button")
    text = f.read_text()
    assert text.startswith("# my gig") and text.count("[plan.example-waltz]") == 1
    assert c.set_plan("gig") == {"example-waltz": {"start": "drums", "bars": 4, "finish": "tag"},
                                 "example-seven": {"start": "drone", "finish": "button"}}
    cfg = c.load(None, overrides=c.plan_overrides("gig", "example-waltz"), song="example-waltz")
    assert (cfg.start.shape, cfg.start.bars, cfg.ending.shape) == ("drums", 4, "tag")


def test_a_bad_plan_is_a_readable_error(tmp_path, monkeypatch):
    a_set(tmp_path, monkeypatch, '\n[plan.example-waltz]\nstart = "fanfare"\n')
    with pytest.raises(c.ConfigError, match="plan.example-waltz.*start"):
        c.set_plan("gig")
    with pytest.raises(c.ConfigError, match="isn't in the set"):
        c.save_plan("gig", "lady-sings-the-blues", start="count")


def test_rehearse_tries_others_from_the_menu_and_keeps_one(tmp_path, monkeypatch):
    a_set(tmp_path, monkeypatch)
    answers = iter(["2c", "k", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "open_output", lambda cfg: RecordingPort())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    clock = iter(x * 0.01 for x in range(10 ** 7))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    assert cli.main(["rehearse", "--song", "waltz"]) == 0
    assert c.set_plan("gig")["example-waltz"] == {"start": "intro", "bars": 4, "finish": "tag"}


def test_a_plan_can_move_the_key_and_choose_a_style(tmp_path, monkeypatch):
    a_set(tmp_path, monkeypatch)
    c.save_plan("gig", "example-waltz", start="count", finish="chord", transpose=-2, style="latin")
    entry = c.set_plan("gig")["example-waltz"]
    assert entry["transpose"] == -2 and entry["style"] == "latin"
    cfg = c.load(None, overrides=c.plan_layers(entry), song="example-waltz")
    assert cfg.song.transpose == -2 and cfg.percussion.enabled and cfg.drums.style == "latin"
    with pytest.raises(c.ConfigError, match="style"):
        c.save_plan("gig", "example-waltz", style="polka")


def test_transpose_moves_the_keys_the_root_and_a_chart():
    cfg = c.load(None, overrides={"song": {"transpose": 2}, "harmony": {"keys": "F lydian, D minor"}})
    assert cfg.harmony.keys == "G lydian, E minor"
    cfg = c.load(None, overrides={"song": {"transpose": -1}, "harmony": {"root": "C"}})
    assert cfg.harmony.root == "B"
    cfg = c.load(None, overrides={"song": {"transpose": 3}}, song="lady-sings-the-blues")
    assert cfg.harmony.transpose == 3


def test_every_style_pack_loads_on_every_song():
    for style in c.available_styles():
        for song in c.available_songs():
            c.load(None, overrides=c.plan_layers({"style": style}), song=song)


def test_rehearse_keeps_key_and_style_too(tmp_path, monkeypatch):
    a_set(tmp_path, monkeypatch)
    answers = iter([">>", "swing", "k", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "open_output", lambda cfg: RecordingPort())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    clock = iter(x * 0.01 for x in range(10 ** 7))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    assert cli.main(["rehearse", "--song", "waltz"]) == 0
    entry = c.set_plan("gig")["example-waltz"]
    assert entry["transpose"] == 2 and entry["style"] == "swing"


def test_a_raga_or_a_tala_brings_the_percussion_forward():
    plain = c.load(None)
    raga = c.load(None, overrides={"harmony": {"model": "modal", "keys": "C gowrimanohari"}})
    tala = c.load(None, overrides={"song": {"tala": "rupakam", "count": 3}})
    western = c.load(None, overrides={"harmony": {"model": "modal", "keys": "C dorian"}})
    assert raga.percussion.presence > plain.percussion.presence and raga.percussion.enabled
    assert tala.percussion.presence > plain.percussion.presence
    assert western.percussion.presence == plain.percussion.presence
    mine = c.load(None, overrides={"harmony": {"model": "modal", "keys": "C sahana"},
                                   "percussion": {"presence": 0.2}})
    assert mine.percussion.presence == 0.2                                 # yours win
