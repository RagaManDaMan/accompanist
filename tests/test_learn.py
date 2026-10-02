"""`accompanist learn`: the pure part (what a switch sends; rewriting config.toml)."""
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from accompanist import config as c
from accompanist.learn import (classify_switch, controls_toml, pick_expression, update_config)

P = "Live Logic"


def test_a_momentary_cc_switch():
    first = [(P, "cc", 80, 127), (P, "cc", 80, 0)]
    second = [(P, "cc", 80, 127), (P, "cc", 80, 0)]
    assert classify_switch(first, second) == (P, "cc", 80, False)


def test_a_latching_cc_switch():
    assert classify_switch([(P, "cc", 81, 127)], [(P, "cc", 81, 0)]) == (P, "cc", 81, True)


def test_program_change_and_note_switches():
    assert classify_switch([(P, "pc", 4, 127)], [(P, "pc", 4, 127)]) == (P, "pc", 4, False)
    assert classify_switch([(P, "note", 36, 100), (P, "note", 36, 0)], []) == (P, "note", 36, False)
    assert classify_switch([], []) is None


def test_the_expression_pedal_is_the_controller_swept_through_many_values():
    sweep = [(P, "cc", 11, v) for v in range(0, 128, 9)]
    noise = [(P, "cc", 80, 127), (P, "cc", 80, 0)]
    assert pick_expression(noise + sweep) == (P, 11)
    assert pick_expression(noise) is None


OLD = '''[output]
port = "IAC Driver Bus 1"

[[inputs]]
name = "keys"
port = "LPK25"

[[inputs]]
name = "sax"
audio = "Scarlett Solo"

[pulse]
velocity = 90

[controls]
85 = "panic"

[lock]
auto = false
'''


def test_config_is_rewritten_and_still_loads():
    block = controls_toml({"tap_tempo": ("pc", 0, False), "lock_toggle": ("cc", 81, True)},
                          (11, "pad.feel"), program_bank=4)
    new = update_config(OLD, block, P)
    data = tomllib.loads(new)
    assert data["pulse"]["velocity"] == 90 and data["lock"]["auto"] is False   # rest kept
    assert [i["name"] for i in data["inputs"]] == ["keys", "sax", "pedal"]
    assert data["inputs"][2] == {"name": "pedal", "port": P, "role": "control"}
    assert "85" not in data["controls"]                                        # replaced
    cfg = c.from_dict(data)
    assert cfg.program_bank == 4 and cfg.controls[("pc", 0)].target == "tap_tempo"
    assert cfg.controls[("cc", 81)].latching and cfg.controls[("cc", 11)].target == "pad.feel"


def test_two_expression_pedals_and_banks_of_ten_like_an_fcb1010():
    block = controls_toml({"tap_tempo": ("pc", 1, False), "panic": ("pc", 10, False)},
                          [(7, "pad.feel"), (27, "response.feel")], program_bank=10)
    cfg = c.from_dict(tomllib.loads(update_config(OLD, block, "USB MIDI Interface")))
    assert cfg.program_bank == 10
    assert cfg.controls[("cc", 7)].target == "pad.feel"
    assert cfg.controls[("cc", 27)].target == "response.feel"


def test_the_owners_fcb1010_session_panic_and_resume_on_one_switch():
    """Regression: the same switch for panic and resume wrote a duplicate key and crashed."""
    from accompanist.learn import assign

    switches = {}
    for action, pc in (("tap_tempo", 11), ("lock_toggle", 22), ("chord_toggle", 33),
                       ("panic", 44), ("resume", 44), ("song_start", 55)):
        assign(switches, action, ("pc", pc, False))
    assert switches["panic_toggle"] == ("pc", 44, False) and "panic" not in switches
    assert "resume" not in switches
    block = controls_toml(switches, [(27, "pad.feel")])
    cfg = c.from_dict(tomllib.loads(update_config(OLD, block, "GHMidi Interface")))
    assert cfg.controls[("pc", 44)].target == "panic_toggle"
    assert cfg.controls[("pc", 55)].target == "song_start"


def test_one_switch_cannot_do_two_other_things():
    from accompanist.learn import assign

    switches = {}
    assign(switches, "tap_tempo", ("pc", 11, False))
    message = assign(switches, "lock_toggle", ("pc", 11, False))
    assert "already tap_tempo" in message and switches == {"tap_tempo": ("pc", 11, False)}


def test_a_pedal_already_listed_is_not_added_twice():
    text = OLD.replace('port = "LPK25"', f'port = "{P}"')
    new = update_config(text, controls_toml({"panic": ("cc", 85, False)}, None), P)
    assert tomllib.loads(new)["inputs"][0]["port"] == P and new.count(P) == 1


def test_tap_and_hold_switches_are_written_and_load():
    from accompanist.learn import has_release

    press = [(P, "cc", 80, 127), (P, "cc", 80, 0)]
    assert has_release(press, "cc", 80) and not has_release([(P, "pc", 4, 127)], "pc", 4)
    block = controls_toml({"finish": ("cc", 82, False)}, None,
                          tap_hold=[("cc", 80, "tap_tempo", "song_start")])
    cfg = c.from_dict(tomllib.loads(update_config(OLD, block, P)))
    ctl = cfg.controls[("cc", 80)]
    assert (ctl.target, ctl.hold) == ("tap_tempo", "song_start")
    assert cfg.controls[("cc", 82)].target == "finish"


def test_a_pedal_listed_by_part_of_its_name_is_not_added_again():
    text = OLD.replace('port = "LPK25"', 'port = "MK3"')
    new = update_config(text, controls_toml({"panic": ("note", 53, False)}, None),
                        "Keystation Mini 32 MK3 USB Audio Device")
    assert "Keystation" not in new
