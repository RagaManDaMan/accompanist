from pathlib import Path

import pytest

from accompanist import charts, config as c, simulate
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput

CHART = str(Path(__file__).parent / "fixtures" / "charts" / "form-test.musicxml")


def form(chart):
    return [" ".join(ch.text for _, ch in bar.changes) or "%" for bar in chart.bars]


def test_repeats_and_endings_unroll_like_ireal_pro():
    ch = charts.load(CHART)
    assert ch.title == "Form Test" and ch.beats_per_bar == 4
    # |: Cmaj7 [1. Dm7b5 G7b9 | Am7 :| [2. F/A | % | Bb7   (the 1st ending is two bars long)
    assert form(ch) == ["Cmaj7", "Dm7(b5) G7(b9)", "Am7", "Cmaj7", "F/A", "%", "Bb7"]
    assert [b.section for b in ch.bars] == ["A", "", "", "A", "B", "", ""]


def test_chord_contents():
    ch = charts.load(CHART)
    dm, g7 = [c for _, c in ch.bars[1].changes]
    assert dm.pitch_classes == {2, 5, 8, 0}                       # D F Ab C
    assert g7.pitch_classes == {7, 11, 2, 5, 8}                   # G B D F + Ab (b9)
    f_over_a = ch.bars[4].changes[0][1]
    assert f_over_a.bass == 9 and f_over_a.text == "F/A"
    assert ch.bars[6].changes[0][1].text == "Bb7"


def test_chord_at_beats_and_bars_without_chords():
    ch = charts.load(CHART)
    assert ch.chord_at(4)[0].text == "Dm7(b5)"                    # bar 2 beat 1
    assert ch.chord_at(6)[0].text == "G7(b9)"                     # bar 2 beat 3
    assert ch.chord_at(20)[0].text == "F/A"                       # bar 6 (%): F/A carries on
    assert ch.chord_at(28)[0].text == "Cmaj7"                     # the form loops


def test_transpose_renames_chords():
    g7 = charts.load(CHART).bars[1].changes[1][1]
    up = g7.transposed(3)
    assert up.root == 10 and up.text == "Bb7(b9)"
    assert charts.load(CHART).bars[4].changes[0][1].transposed(2).text == "G/B"


@pytest.mark.parametrize("content,msg", [("not xml", "not a readable MusicXML"),
                                          ("<score-partwise/>", "no <part>")])
def test_bad_charts_are_readable(tmp_path, content, msg):
    f = tmp_path / "bad.musicxml"
    f.write_text(content)
    with pytest.raises(c.ConfigError, match=msg):
        charts.load(f)


def test_chart_model_needs_a_chart():
    with pytest.raises(c.ConfigError, match="needs a chart"):
        Controller(c.from_dict({"harmony": {"model": "chart"}}), SafeOutput(RecordingPort()))


def test_count_in_then_the_pad_plays_the_chart_on_the_beat():
    period = 60 / 100          # 0.6 s: on the simulation's 5 ms tick grid, like real key presses
    taps = [(1.0 + i * period, "tap_tempo") for i in range(4)]
    bar1 = 1.0 + 4 * period                                       # the beat after the last tap
    cfg = c.from_dict({"harmony": {"model": "chart", "chart": CHART}})
    res = simulate.run(cfg, onsets=[], actions=taps, total=bar1 + 28 * period + 0.1)
    assert res.engine.locked                                      # the count-in locks the tempo
    changes = [((t - bar1) / period, w[7:]) for t, w in res.log if w.startswith("pad -> ")]
    assert [(round(b), w) for b, w in changes] == [
        (0, "Cmaj7"), (4, "Dm7(b5)"), (6, "G7(b9)"), (8, "Am7"), (12, "Cmaj7"), (16, "F/A"), (24, "Bb7"),
        (28, "Cmaj7")]                                            # ... and the form loops
    assert all(abs(b - round(b)) < 0.1 for b, _ in changes)       # on the beat (5 ms ticks)
    s = res.controller.get_state(res.end_time)
    assert s["chart"]["bar"] == 1 and s["chart"]["title"] == "Form Test"


def test_restart_puts_bar_one_on_the_next_beat():
    period = 60 / 100
    taps = [(1.0 + i * period, "tap_tempo") for i in range(4)]
    restart_at = 1.0 + 9.5 * period                               # mid-way through bar 2
    cfg = c.from_dict({"harmony": {"model": "chart", "chart": CHART}})
    res = simulate.run(cfg, onsets=[], actions=taps + [(restart_at, "chart_restart")],
                       total=restart_at + 2 * period)
    assert res.controller.get_state(res.end_time)["chart"]["bar"] == 1
    assert [w for _, w in res.log if w.startswith("pad")][-1] == "pad -> Cmaj7"


def test_transpose_is_live():
    cfg = c.from_dict({"harmony": {"model": "chart", "chart": CHART}})
    ctl = Controller(cfg, SafeOutput(RecordingPort()))
    assert ctl.engine.harmony.propose(0.0).label() == "Cmaj7"
    ctl.set_param("harmony.transpose", 2)
    assert ctl.engine.harmony.propose(0.0).label() == "Dmaj7"
