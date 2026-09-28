import pytest
from accompanist import config as c


def test_defaults_work_with_empty_dict():
    cfg = c.from_dict({})
    assert cfg.output.virtual_name == "Accompanist" and cfg.pad.channel == 1


def test_unknown_key_is_caught():
    with pytest.raises(c.ConfigError, match="unknown"):
        c.from_dict({"tempo": {"initial_bmp": 80}})


def test_naming_a_port_disables_virtual():
    cfg = c.from_dict({"output": {"port": "IAC Driver Bus 1"}})
    assert cfg.output.port == "IAC Driver Bus 1" and cfg.output.virtual_name is None


@pytest.mark.parametrize("name,pc", [("D", 2), ("f#", 6), ("Bb", 10), ("auto", None)])
def test_root_names(name, pc):
    assert c.parse_root(name) == pc


def test_bad_channel_rejected():
    with pytest.raises(c.ConfigError, match="1-16"):
        c.from_dict({"pad": {"channel": 0}})


def test_planned_roles_give_a_clear_error():
    with pytest.raises(c.ConfigError, match="planned"):
        c.from_dict({"inputs": [{"port": "Mic", "role": "voice"}]})


def test_load_from_file(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[[inputs]]\nport = "EWI"\nchannel = 2\n[harmony]\nroot = "D"\n')
    cfg = c.load(f)
    assert cfg.inputs[0].channel == 2 and cfg.root_pc == 2
