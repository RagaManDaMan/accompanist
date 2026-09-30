"""The four feel knobs: algorithmic (0) to humanize (1), one per voice."""
import statistics

import pytest

from accompanist import config as c, feel, params as registry, simulate
from accompanist.controller import Controller
from accompanist.output import RecordingPort, SafeOutput
from accompanist.recording import load_take
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_each_knob_at_its_default_gives_the_detailed_defaults():
    for knob in feel.MACROS:
        x = registry.get(knob).default
        for key, value in feel.settings(knob, x):
            assert value == registry.get(key).default, (knob, key)


def test_a_simple_gui_needs_only_the_primary_controls():
    primary = [p.key for p in registry.PARAMS if p.primary]
    assert set(primary) == {"pad.feel", "pulse.feel", "drums.feel", "response.feel",
                            "pad.enabled", "pulse.enabled", "drums.enabled", "response.enabled"}
    assert all(p["primary"] in (True, False) for p in registry.schema())


@pytest.mark.parametrize("x", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_every_knob_position_gives_valid_settings(x):
    for knob in feel.MACROS:
        feel.settings(knob, x)                           # raises if anything is out of range


def test_the_ends_of_the_pad_knob():
    cfg = c.from_dict({"pad": {"feel": 0.0}})
    assert (cfg.pad.change_on, cfg.pad.variation, cfg.pad.revoice_bars, cfg.dynamics.duck) == \
           ("bar", 0.0, 0, 0.0)
    cfg = c.from_dict({"pad": {"feel": 1.0}})
    assert cfg.pad.change_on == "beat" and cfg.pad.variation == 0.9 and cfg.pad.strum_ms == 40


def test_an_explicit_setting_wins_at_start_but_a_live_knob_takes_over():
    cfg = c.from_dict({"pad": {"feel": 1.0, "variation": 0.2}})
    assert cfg.pad.variation == 0.2 and cfg.pad.strum_ms == 40
    ctl = Controller(cfg, SafeOutput(RecordingPort()))
    ctl.set_param("pad.feel", 0.0)
    assert ctl.cfg.pad.variation == 0.0 and ctl.cfg.pad.strum_ms == 0
    assert ctl.overrides == {"pad.feel": 0.0}             # the knob, not what it moved


def test_a_knob_on_a_midi_controller():
    ctl = Controller(c.from_dict({"controls": {"21": "drums.feel"}}), SafeOutput(RecordingPort()))
    ctl.on_cc(0.0, 21, 127)
    assert ctl.cfg.drums.feel == 1.0 and ctl.cfg.drums.timing_ms == 15


def band(**feels):
    cfg = c.from_dict({"drums": {"enabled": True, "feel": feels.get("drums", 0.4)},
                       "pulse": {"feel": feels.get("bass", 0.3)}, "lock": {"auto": False},
                       "groove": {"auto": False}, "harmony": {"root": "D"}})
    return simulate.run(cfg, onsets=load_take(FIXTURES / "melody-90bpm.jsonl"), total=40.0)


def test_algorithmic_drums_are_exact_and_humanized_ones_are_not():
    def hits(res):
        return [(t, m.velocity) for t, m in res.timeline if m.type == "note_on" and m.channel == 9
                and m.note == 42]
    # Fully algorithmic: drums and bass at 0 (the bass knob also sets following your dynamics).
    exact, human = hits(band(drums=0.0, bass=0.0)), hits(band(drums=1.0))
    assert len({v for _, v in exact}) <= 3                 # even: hit, ghost, accent levels only
    assert len({v for _, v in human}) > 5                  # varied
    gaps = lambda hs: [round(b - a, 3) for (a, _), (b, _) in zip(hs, hs[1:])]
    assert statistics.pstdev(gaps(human)) > statistics.pstdev(gaps(exact))


def test_humanized_bass_is_laid_back_and_varied():
    def bass(res):
        return [(t, m.velocity) for t, m in res.timeline if m.type == "note_on" and m.channel == 1]
    rigid, human = bass(band(bass=0.0)), bass(band(bass=1.0))
    assert len({v for _, v in human}) > len({v for _, v in rigid})


def test_humanizing_is_repeatable():
    a = [(t, str(m)) for t, m in band(drums=1.0, bass=1.0).timeline]
    b = [(t, str(m)) for t, m in band(drums=1.0, bass=1.0).timeline]
    assert a == b
