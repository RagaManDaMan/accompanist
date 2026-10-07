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
    answers = iter(["tha", "", "r", "thom", "dhi nam", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "open_output", lambda cfg: RecordingPort())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["kitmap", "percussion", "--make", "mridangam", "--low", "40",
                     "--high", "50"]) == 0
    kit = tala.load_kit("mridangam")
    assert kit.strokes == {"tha": (40,), "thom": (42,), "dhi": (43,), "nam": (43,)}
