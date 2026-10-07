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


def test_every_section_in_a_config_file_is_read():
    from accompanist import params as registry

    for section in registry.SECTIONS:
        for p in registry.PARAMS:
            if p.section != section or not p.live and p.type is not bool:
                continue
            if p.type is bool:
                value = not p.default
            elif p.choices:
                value = next((ch for ch in p.choices if ch != p.default), None)
            elif p.type in (int, float) and p.max is not None and p.max != p.default:
                value = p.type(p.max)
            else:
                continue
            if value is None:
                continue
            name = p.key.split(".", 1)[1]
            cfg = c.from_dict({section: {name: value}})
            assert getattr(getattr(cfg, section), name) == value, p.key
            break


def test_part_of_a_name_is_enough_when_it_is_clear():
    names = ["example-waltz", "kann-pona-pokkile", "lady-sings-the-blues"]
    assert c.resolve_name("lady", names, "song") == "lady-sings-the-blues"
    assert c.resolve_name("Kann Pona", names, "song") == "kann-pona-pokkile"
    assert c.resolve_name("blues", names, "song") == "lady-sings-the-blues"   # inside the name
    assert c.resolve_name("nothing", names, "song") == "nothing"              # said later
    with pytest.raises(c.ConfigError, match="could be"):
        c.resolve_name("a", names, "song")


def test_tab_completion_offers_what_each_option_takes(capsys):
    from accompanist import cli

    def values(*words):
        cli.main(["complete", "value", *words])
        return capsys.readouterr().out.split()

    assert "count" in values("rehearse", "--", "--start")
    assert "ritardando" in values("rehearse", "--", "--finish")
    assert "swing" in values("rehearse", "--", "--style")
    assert "lady-sings-the-blues" in values("rehearse", "--", "--song")
    assert "voice" in values("run", "--", "--preset")
    assert values("rehearse", "--", "--once") == ["__files__"]
    assert {"add", "stats", "ragas"} <= set(values("library", "--", "library"))
