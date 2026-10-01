import dataclasses
import json

import pytest

from accompanist import config as c, params as registry
from accompanist.cli import main
from accompanist.controller import Controller, cc_to_value, format_status
from accompanist.output import RecordingPort, SafeOutput


def make(d=None, preset=None):
    port = RecordingPort()
    return port, Controller(c.from_dict(d or {}, preset), SafeOutput(port))


# ---- registry ------------------------------------------------------------------
def test_every_config_field_comes_from_the_registry():
    for section, cls in c.SECTION_CLASSES.items():
        fields = {f.name for f in dataclasses.fields(cls)}
        assert fields == {p.name for p in registry.section_params(section)}


def test_every_default_is_valid_and_has_ui_metadata():
    for p in registry.PARAMS:
        assert registry.coerce(p, p.default) == p.default
        assert p.label and p.help and p.group
        if p.type in (int, float) and not p.deprecated:
            assert p.min is not None and p.max is not None and p.step, p.key


def test_params_json_is_the_schema(capsys):
    assert main(["params", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    keys = {p["key"] for p in data["params"]}
    assert "tempo.prior_bpm" in keys and "pad.velocity" in keys
    assert {"ambient", "modal-drone"} <= set(data["presets"])
    assert "panic" in data["actions"]


def test_validation_messages_come_from_the_registry():
    with pytest.raises(c.ConfigError, match=r"\[pad\] velocity: must be 1-127"):
        c.from_dict({"pad": {"velocity": 200}})
    with pytest.raises(c.ConfigError, match="must be true or false"):
        c.from_dict({"pulse": {"enabled": "yes"}})
    with pytest.raises(c.ConfigError, match="whole number"):
        c.from_dict({"pulse": {"beats_per_bar": 3.5}})


# ---- layering: defaults < preset < config.toml < live ----------------------------
def test_layering_order():
    default = registry.get("pad.lag_beats").default
    assert c.from_dict({}).pad.lag_beats == default
    assert c.from_dict({}, "ambient").pad.lag_beats == 8          # preset over default
    cfg = c.from_dict({"pad": {"lag_beats": 12}}, "ambient")
    assert cfg.pad.lag_beats == 12                                # config over preset
    assert cfg.pad.overlap_s == 2.0                               # untouched preset value stays
    _, ctl = make({"pad": {"lag_beats": 12}}, "ambient")
    ctl.set_param("pad.lag_beats", 20)
    assert ctl.cfg.pad.lag_beats == 20                            # live over config
    assert ctl.get_state(0.0)["overrides"] == {"pad.lag_beats": 20}


def test_preset_named_in_the_config_file(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('preset = "modal-drone"\n[pad]\nvelocity = 60\n')
    cfg = c.load(f)
    assert cfg.preset == "modal-drone" and cfg.harmony.switch_margin == 2.5 and cfg.pad.velocity == 60
    assert c.load(f, "ambient").preset == "ambient"               # --preset wins


def test_user_presets_and_their_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "presets").mkdir()
    (tmp_path / "presets" / "mine.toml").write_text("[pulse]\nvelocity = 99\n")
    (tmp_path / "presets" / "bad.toml").write_text('[[inputs]]\nport = "X"\n')
    assert c.from_dict({}, "mine").pulse.velocity == 99
    with pytest.raises(c.ConfigError, match="may only set"):
        c.from_dict({}, "bad")
    with pytest.raises(c.ConfigError, match="unknown preset 'nope'.*ambient"):
        c.from_dict({}, "nope")


def test_existing_config_files_keep_working(tmp_path):
    """The shape of a V0 config.toml, including keys the new tempo estimator ignores."""
    f = tmp_path / "config.toml"
    f.write_text('''
[output]
virtual_name = "Accompanist"
[[inputs]]
name = "keys"
port = "LPK25"
role = "note_source"
[tempo]
initial_bpm = 70
alpha = 0.10
tolerance = 0.3
max_gap_beats = 6
[harmony]
root = "D"
half_life_s = 30
[pad]
enabled = true
channel = 1
lag_beats = 8
min_change_beats = 32
idle_release_s = 3600
[pulse]
enabled = true
channel = 2
idle_stop_s = 3600
min_confidence = 0.3
phase_gain = 0.1
[panic]
# cc = 85
''')
    cfg = c.load(f)
    assert cfg.tempo.alpha == 0.10 and cfg.root_pc == 2 and cfg.pad.min_change_beats == 32


# ---- live set_param ----------------------------------------------------------------
def test_set_param_is_validated_and_readable():
    _, ctl = make()
    for key, value, msg in [("pad.velocity", 300, "1-127"), ("pad.nope", 1, "unknown parameter"),
                            ("pad.channel", 3, "restart"), ("tempo.tolerance", 0.2, "no longer used"),
                            ("harmony.root", "H", "note name")]:
        with pytest.raises(c.ConfigError, match=msg):
            ctl.set_param(key, value)
    with pytest.raises(c.ConfigError, match="min_bpm < max_bpm"):
        ctl.set_param("tempo.min_bpm", 200)
    assert ctl.cfg.tempo.min_bpm == 40                            # a rejected change is undone
    assert ctl.overrides == {}


def test_live_param_applies_at_the_next_tick():
    port, ctl = make({"harmony": {"root": "D"}, "pad": {"lag_beats": 0, "min_change_beats": 0}})
    ctl.on_note(0.0, 62, 100)
    ctl.tick(0.0)
    ctl.tick(0.1)
    first = [m for m in port.sent if m.type == "note_on"]
    assert first and all(m.velocity == 55 for m in first)
    ctl.set_param("pad.velocity", 90)
    ctl.set_param("pad.octave", 4)                                # a new voicing, an octave up
    ctl.tick(0.2)
    ctl.tick(0.3)
    later = [m for m in port.sent if m.type == "note_on"][len(first):]
    assert later and all(m.velocity == 90 for m in later)
    assert ctl.get_state(0.3)["pad_notes"][0] == 12 * 5 + 2      # D4


def test_disabling_the_pad_live_releases_it():
    port, ctl = make({"harmony": {"root": "D"}, "pad": {"lag_beats": 0, "min_change_beats": 0}})
    ctl.on_note(0.0, 62, 100)
    for t in (0.0, 0.1):
        ctl.tick(t)
    assert ctl.engine.out.sounding
    ctl.set_param("pad.enabled", False)
    ctl.tick(0.2)
    assert ctl.engine.out.sounding == set()


# ---- CCs, keys, tap tempo ---------------------------------------------------------------
def test_controls_map_ccs_to_actions_and_params():
    _, ctl = make({"controls": {"85": "panic", "86": "resume", "7": "pad.velocity", "64": "pulse.enabled"}})
    assert ctl.on_cc(0.0, 85, 127) == "panic" and ctl.engine.muted
    assert ctl.on_cc(0.0, 86, 0) is None and ctl.engine.muted     # released switch: nothing
    ctl.on_cc(0.0, 86, 127)
    assert not ctl.engine.muted
    ctl.on_cc(0.0, 7, 127)
    assert ctl.cfg.pad.velocity == 127
    ctl.on_cc(0.0, 64, 0)
    assert ctl.cfg.pulse.enabled is False
    assert ctl.on_cc(0.0, 20, 127) is None                        # unmapped


def test_legacy_panic_cc_still_works():
    _, ctl = make({"panic": {"cc": 85}})
    ctl.on_cc(0.0, 85, 127)
    assert ctl.engine.muted


@pytest.mark.parametrize("controls,msg", [({"x": "panic"}, "not a controller"),
                                           ({"200": "panic"}, "0-127"),
                                           ({"5": "explode"}, "not an action"),
                                           ({"5": "pad.channel"}, "while running")])
def test_bad_controls_are_readable(controls, msg):
    with pytest.raises(c.ConfigError, match=msg):
        c.from_dict({"controls": controls})


def test_cc_scaling():
    assert cc_to_value(registry.get("pad.velocity"), 0) == 1
    assert cc_to_value(registry.get("pad.velocity"), 127) == 127
    assert cc_to_value(registry.get("tempo.alpha"), 127) == pytest.approx(1.0)
    assert cc_to_value(registry.get("harmony.root"), 0) == "auto"


def test_a_count_off_sets_tempo_and_octave():
    _, ctl = make()
    for i in range(4):
        ctl.tap_tempo(10 + i * 0.5)
    ctl.tick(11.5 + 0.5 * 1.2)                                   # the 5th tap did not come
    assert ctl.engine.tempo.bpm == pytest.approx(120)
    assert ctl.cfg.tempo.prior_bpm == 120                         # octave follows the count
    assert ctl.overrides["tempo.prior_bpm"] == 120
    assert ctl.cfg.tempo.prior_sigma_oct == ctl.cfg.tempo.tap_sigma_oct == 0.3
    assert ctl.engine.locked and ctl.engine.groove.label().startswith("4/4")


def test_slow_taps_start_over():
    _, ctl = make()
    for t in (0.0, 0.5, 1.0, 5.0):                                # a long gap before the 4th
        ctl.tap_tempo(t)
    ctl.tick(7.0)
    assert not ctl.engine.locked and ctl.engine.groove.meter is None


def test_state_is_a_plain_dict_and_status_formats_it():
    _, ctl = make({"harmony": {"root": "D"}})
    ctl.on_note(0.0, 62, 100)
    ctl.tick(0.5)
    s = ctl.get_state(0.5)
    json.dumps(s)                                                 # plain data, UI-ready
    assert s["heard"] == "D4" and s["root"] == "D" and s["muted"] is False
    assert "bpm" in format_status(s) and "heard D4" in format_status(s)


def test_every_action_reports_what_happened():
    _, ctl = make()
    assert ctl.do("lock", 0.0) == "can't lock yet: nothing heard"
    ctl.on_note(0.0, 62, 90)
    ctl.tick(0.1)
    assert ctl.do("unlock", 0.1) == "tempo not locked"
    assert ctl.do("lock", 0.1).startswith("tempo LOCKED at")
    assert ctl.do("lock", 0.2).startswith("already LOCKED")
    assert ctl.do("unlock", 0.3) == "tempo unlocked"
    assert ctl.do("lock_toggle", 0.3).startswith("tempo LOCKED")
    assert ctl.do("lock_toggle", 0.3) == "tempo unlocked"
    assert ctl.do("chord_toggle", 0.3).startswith("chord HELD: D")
    assert ctl.do("chord_hold", 0.3).startswith("already holding")
    assert ctl.do("chord_toggle", 0.3) == "chords follow you again"
    assert ctl.do("chord_release", 0.3) == "no chord held"
    assert ctl.do("panic", 0.4).startswith("PANIC")
    assert ctl.do("lock", 0.5).startswith("can't lock while muted")
    assert ctl.do("resume", 0.6) == "resumed"
    assert ctl.do("tap_tempo", 1.0) == "tap 1"
    assert ctl.do("tap_tempo", 1.5) == "tap 2 (120 bpm)"


def test_status_flags_come_first_so_a_narrow_window_still_shows_them():
    _, ctl = make()
    ctl.on_note(0.0, 62, 90)
    ctl.tick(0.1)
    ctl.lock(0.1)
    assert format_status(ctl.get_state(0.1)).startswith("0:00.1  LOCKED ")
    ctl.panic()
    assert format_status(ctl.get_state(0.2)).startswith("0:00.2  MUTED ")



# ---- pedals: program changes, notes, latching switches, banks -------------------------
def test_program_changes_and_notes_trigger_actions():
    _, ctl = make({"controls": {"pc:0": "lock_toggle", "note:36": "panic"}})
    ctl.on_note(0.0, 62, 90)
    ctl.tick(0.1)
    assert ctl.on_midi(0.1, "pc", 0, 127)[0] == "lock_toggle" and ctl.engine.locked
    assert ctl.on_midi(0.2, "note", 36, 100)[0] == "panic" and ctl.engine.muted
    assert ctl.on_midi(0.3, "note", 36, 0) == (None, None)         # a note-off is no press


def test_a_pedal_bank_switch_does_not_remap_the_switches():
    _, ctl = make({"controls": {"program_bank": 4, "pc:0": "lock_toggle"}})
    ctl.on_note(0.0, 62, 90)
    ctl.tick(0.1)
    assert ctl.on_midi(0.1, "pc", 8, 127)[0] == "lock_toggle"      # switch 1, in bank 3
    assert ctl.on_midi(0.2, "pc", 9, 127) == (None, None)          # switch 2: not mapped


def test_one_switch_for_panic_and_resume():
    _, ctl = make({"controls": {"pc:44": "panic_toggle"}})
    assert ctl.on_midi(0.0, "pc", 44, 127)[1].startswith("PANIC") and ctl.engine.muted
    assert ctl.on_midi(0.5, "pc", 44, 127)[1] == "resumed" and not ctl.engine.muted


def test_a_latching_switch_acts_on_every_press():
    _, ctl = make({"controls": {"80": {"action": "lock_toggle", "latching": True}}})
    ctl.on_note(0.0, 62, 90)
    ctl.tick(0.1)
    ctl.on_cc(0.1, 80, 127)
    assert ctl.engine.locked
    ctl.on_cc(0.2, 80, 0)                                           # the next press sends 0
    assert not ctl.engine.locked


def test_only_ccs_can_set_parameters_and_old_panic_cc_still_works():
    with pytest.raises(c.ConfigError, match="only a CC"):
        c.from_dict({"controls": {"pc:3": "pad.feel"}})
    _, ctl = make({"panic": {"cc": 85}})
    ctl.on_cc(0.0, 85, 127)
    assert ctl.engine.muted


def test_a_control_input_role_is_accepted():
    cfg = c.from_dict({"inputs": [{"name": "pedal", "port": "Live Logic", "role": "control"}]})
    assert cfg.inputs[0].role == "control"


def _tap_hold_ctl():
    from accompanist.output import RecordingPort, SafeOutput

    cfg = c.from_dict({"controls": {"cc:80": {"tap": "tap_tempo", "hold": "song_start"},
                                    "cc:81": {"tap": "lock_toggle", "hold": "chord_toggle"},
                                    "cc:83": "panic_toggle"},
                       "song": {"tempo": 120, "count": 4}, "lock": {"auto": False}})
    return Controller(cfg, SafeOutput(RecordingPort()))


def test_tap_and_hold_a_count_off_taps_count_on_the_press():
    ctl = _tap_hold_ctl()
    for i in range(4):                                   # four quick taps: 4/4 at 100
        ctl.on_midi(1.0 + i * 0.6, "cc", 80, 127)
        ctl.on_midi(1.1 + i * 0.6, "cc", 80, 0)
        ctl.tick(1.1 + i * 0.6)
    for k in range(200):
        ctl.tick(3.5 + k * 0.01)
    assert ctl.engine.groove.label().startswith("4/4")
    assert abs(ctl.engine.tempo.bpm - 100) < 1


def test_holding_the_count_switch_starts_the_song_instead():
    ctl = _tap_hold_ctl()
    ctl.on_midi(1.0, "cc", 80, 127)
    for k in range(80):
        ctl.tick(1.0 + k * 0.01)                          # held 0.8 s
    ctl.on_midi(1.8, "cc", 80, 0)
    assert ctl._taps == [] and ctl.engine._count_total == 4          # counting in at 120
    assert ctl.take_holds()[0][1] == "song_start"


def test_lock_acts_on_release_and_hold_holds_the_chord():
    ctl = _tap_hold_ctl()
    for i in range(8):
        ctl.on_note(i * 0.5, 62 + (i % 3) * 2, 90)
    for k in range(400):
        ctl.tick(k * 0.01)
    assert ctl.on_midi(4.0, "cc", 81, 127) == (None, None)            # nothing yet
    action, _ = ctl.on_midi(4.2, "cc", 81, 0)
    assert action == "lock_toggle" and ctl.engine.locked
    ctl.on_midi(5.0, "cc", 81, 127)
    for k in range(80):
        ctl.tick(5.0 + k * 0.01)
    ctl.on_midi(5.8, "cc", 81, 0)
    assert ctl.engine.locked and ctl.engine.chord_held                # held: chord, not lock


def test_tap_and_hold_needs_a_switch_with_a_release():
    with pytest.raises(c.ConfigError, match="momentary CC or a note"):
        c.from_dict({"controls": {"pc:3": {"tap": "finish", "hold": "panic"}}})
