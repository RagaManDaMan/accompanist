"""Tāla percussion: thekas and sarvalaghu, through a kit of your instrument's keys."""
import pytest

from accompanist import cli, config as c, simulate, tala
from accompanist.output import RecordingPort, SafeOutput


def test_a_theka_is_beats_of_strokes():
    assert tala.parse_beats("Dha Dhin | DhaGe TiRaKiTa .") == [
        ["Dha"], ["Dhin"], ["Dha", "Ge"], ["Ti", "Ra", "Ki", "Ta"], ["."]]


def test_a_kit_finds_any_stroke_through_its_parts_or_kin():
    kit = tala.Kit("t", {"na": (60,), "ge": (40,), "tin": (62,), "ka": (41,)})
    assert kit.notes("Dha") == (60, 40)                    # na + ge
    assert kit.notes("Dhin") == (62, 40)                   # tin + ge
    assert kit.notes("Kat") == (41,)                       # kin
    assert kit.notes("Thom")                               # a mridangam stroke on a tabla kit
    assert kit.notes("Xyz") == ()


@pytest.mark.parametrize("name,beats", [("tintal", 16), ("ektal", 12), ("jhaptal", 10),
                                        ("rupak", 7), ("keherwa", 8), ("dadra", 6),
                                        ("adi", 8), ("rupakam", 6), ("misra-chapu", 7),
                                        ("khanda-chapu", 5), ("khanda-jhampa", 8)])
def test_every_tala_has_a_cycle(name, beats):
    cyc = tala.cycle_for(c.from_dict({"song": {"tala": name}}))
    assert len(cyc.beats) == beats and 0 in cyc.group_starts


def test_a_theka_of_the_wrong_length_says_so():
    with pytest.raises(c.ConfigError, match="one word per beat"):
        tala.cycle_for(c.from_dict({"percussion": {"tala": "dadra", "theka": "Dha Dhi Na"}}))


def run_tala(name, count, **perc):
    cfg = c.from_dict({"percussion": {"enabled": True, **perc}, "drums": {"enabled": False},
                       "song": {"tala": name, "count": count, "tempo": 100},
                       "harmony": {"root": "C"}})
    res = simulate.run(cfg, onsets=[], actions=[(1.0, "song_start")], total=16)
    hits = [(t, m.note, m.velocity) for t, m in res.timeline if m.type == "note_on"
            and m.velocity and m.channel == cfg.percussion.channel - 1]
    return res, hits


def test_the_band_plays_the_tala_with_the_sam_on_the_songs_one():
    res, hits = run_tala("misra-chapu", 7)
    assert res.engine.tala_player is not None and not res.engine.tala_problem
    one = 1.0 + 8 * 0.6                                     # clicks from a beat after s: 7, then 1
    first = [h for h in hits if h[0] >= one - 0.01]
    sam = [v for t, _, v in first if abs(t - one) < 0.01]
    later = [v for t, _, v in first if abs(t - one - 0.6) < 0.01]
    assert sam and later and max(sam) > max(later)         # the sam is accented
    cycle = [v for t, _, v in first if abs(t - (one + 7 * 0.6)) < 0.01]
    assert cycle and max(cycle) == max(sam)                # and again each cycle


def test_a_bad_theka_never_stops_the_band():
    res, hits = run_tala("dadra", 6, theka="Dha Dhi")
    assert res.engine.tala_player is None and "tāla percussion off" in res.engine.tala_problem
    assert hits                                            # the patterns play instead


def test_kit_auto_takes_your_mridangam_for_a_carnatic_tala(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tala.save_kit("mridangam", {"tha": [50], "thom": [40], "dhi": [52], "nam": [51], "ki": [53]})
    assert tala.kit_for("auto", "carnatic") == "mridangam"
    assert tala.kit_for("auto", "hindustani") == "gm-tabla"       # no tabla kit yet
    p = tala.TalaPlayer(c.from_dict({"song": {"tala": "adi"}}), SafeOutput(RecordingPort()))
    assert p.kit.name == "mridangam" and not p.missing


def test_kitmap_make_names_each_key_into_a_kit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text("")           # its own folder, never the real one
    answers = iter(["tha", "", "r", "thom", "dhi nam", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "open_output", lambda cfg: RecordingPort())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["kitmap", "percussion", "--make", "mridangam", "--low", "40",
                     "--high", "50"]) == 0
    kit = tala.load_kit("mridangam")
    assert kit.strokes == {"tha": (40,), "thom": (42,), "dhi": (43,), "nam": (43,)}


def test_spellings_meet_and_several_keys_are_alternatives():
    import random

    kit = tala.Kit("m", {"tom": (60, 69), "dhim": (55,), "tam": (53,), "tin": (57,)})
    assert kit.choices("Thom") == [(60, 69)] and kit.choices("Dheem") == [(55,)]
    assert kit.choices("Tham") == [(53,)]
    rng = random.Random(1)
    picks = {kit.pick("Thom", rng) for _ in range(30)}
    assert picks == {(60,), (69,)}                          # one at a time, varied
    assert kit.notes("Dhi", kin_first=True) == (57,)         # a mridangam Dhi: one stroke
    assert len(kit.notes("Dhi")) == 2                        # a tabla Dhi: two


def test_a_kit_file_keeps_each_key_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tala.save_kit("k", {"taka": [48, 48, 54]})
    assert tala.load_kit("k").strokes["taka"] == (48, 54)


def test_a_mora_is_three_statements_two_gaps_landing_exactly():
    import random

    from accompanist import solkattu

    rng = random.Random(0)
    for total in range(7, 40):
        m = solkattu.mora(total, rng)
        assert m[-1] == (total, "tām", True)                     # sealed on the sam
        assert max(at for at, _, _ in m[:-1]) < total
        assert sum(1 for _, _, accent in m[:-1] if accent) == 3  # three statements
    for total in (12, 16, 20, 28):                               # Nelson: short statements
        s, g = solkattu.mora_shapes(total)[0]                    # take a sounded gap of 2+
        assert 3 * s + 2 * g == total and (s >= 5 or g >= 2)


def test_solkattu_weights_and_strokes():
    from accompanist import solkattu

    assert solkattu.pulses("ta ka di mi , tām ta3") == 4 + 1 + 2 + 3
    assert solkattu.stroke("ta") == "tha" and solkattu.stroke("ṭa") == "ta"   # dental, retroflex
    assert solkattu.stroke("tām") == "tam" and solkattu.stroke("mi") == "tom"


@pytest.mark.parametrize("name,count", [("adi", 4), ("misra-chapu", 7), ("rupakam", 3),
                                        ("tintal", 4)])
def test_f_ends_the_song_with_a_mora_on_the_sam(name, count):
    cfg = c.from_dict({"percussion": {"enabled": True}, "drums": {"enabled": True},
                       "ending": {"shape": "mora"}, "harmony": {"root": "C"},
                       "song": {"tala": name, "count": count, "tempo": 90}})
    P, one = 60 / 90, 1 + (count + 1) * 60 / 90
    notes = [(one + i * P, 60 + (i % 5) * 2, 80) for i in range(10)]
    res = simulate.run(cfg, onsets=notes, actions=[(1.0, "song_start"), (one + 11.3 * P, "finish")],
                       total=one + 40 * P)
    tl = res.timeline
    bass = [t for t, m in tl if m.type == "note_on" and m.velocity and m.channel == cfg.pulse.channel - 1]
    perc = [t for t, m in tl if m.type == "note_on" and m.velocity
            and m.channel == cfg.percussion.channel - 1]
    beats, cycle = (bass[-1] - one) / P, len(res.engine.tala_player.cycle.beats)
    assert res.engine.finished and abs(beats / cycle - round(beats / cycle)) < 0.01   # on a sam
    assert abs(perc[-1] - bass[-1]) < 0.02                                           # tām with it


def test_now_and_then_a_mora_marks_the_sam():
    cfg = c.from_dict({"percussion": {"enabled": True, "moras": 1.0, "mora_beats": 3},
                       "drums": {"enabled": False}, "harmony": {"root": "C"},
                       "song": {"tala": "adi", "count": 4, "tempo": 90}})
    res = simulate.run(cfg, onsets=[], actions=[(1.0, "song_start")], total=25)
    assert res.engine.tala_player.mora_text and "tām" in res.engine.tala_player.mora_text


def test_without_a_tala_a_mora_finish_is_a_last_chord():
    cfg = c.from_dict({"ending": {"shape": "tihai"}, "harmony": {"root": "C"},
                       "drums": {"enabled": True}, "song": {"tempo": 90, "count": 4}})
    res = simulate.run(cfg, onsets=[(4.0 + i * 0.66, 60, 80) for i in range(8)],
                       actions=[(1.0, "song_start"), (8.0, "finish")], total=20)
    assert res.engine.finished and res.engine._ending_shape == "chord"


def test_a_song_with_a_tempo_waits_for_s_however_steady_the_singing():
    cfg = c.from_dict({"percussion": {"enabled": True}, "drums": {"enabled": True},
                       "harmony": {"root": "C"}, "song": {"tala": "adi", "count": 4, "tempo": 90}})
    steady = [(1.0 + i * 0.5, (60, 62, 64, 65)[i % 4], 80) for i in range(60)]   # 120 bpm, clear
    res = simulate.run(cfg, onsets=steady, actions=[(20.0, "song_start")], total=30)
    beat = [t for t, m in res.timeline if m.type == "note_on" and m.velocity
            and m.channel in (cfg.drums.channel - 1, cfg.percussion.channel - 1, cfg.pulse.channel - 1)]
    assert beat and min(beat) >= 20.0                     # nothing before s: sing in the open


def test_in_a_raga_every_guitar_note_stays_in_it():
    from accompanist.output import RecordingPort, SafeOutput
    from accompanist.response import ResponseResponder

    r = ResponseResponder(c.from_dict({}).response, SafeOutput(RecordingPort()))
    r.key = (0, "gowrimanohari")
    gowri = {0, 2, 3, 5, 7, 9, 11}
    out = r.in_key([60, 61, 63, 66, 68], [0.1] * 4, None, gowri, 0.6)
    assert all(p % 12 in gowri for p in out)
    r.key = (0, "major")                                  # elsewhere: quick passing notes stay
    assert r.in_key([60, 61, 62], [0.1, 0.1], None, {0, 2, 4, 5, 7, 9, 11}, 0.6)[1] == 61
