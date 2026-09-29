import json

import pytest

from accompanist import config as c, simulate
from accompanist.cli import main
from accompanist.recording import FORMAT, Recorder, TakeError, auto_path, load_take


def test_roundtrip_preserves_notes_and_relative_timing(tmp_path):
    f = tmp_path / "t.jsonl"
    rec = Recorder(f)
    rec.note_on(1000.0, 62, 90, "keys")   # absolute clock: only offsets matter
    rec.note_on(1000.5, 64, 70, "keys")
    rec.close()
    assert load_take(f) == [(0.0, 62, 90), (0.5, 64, 70)]


def test_take_is_flushed_per_note(tmp_path):
    f = tmp_path / "t.jsonl"
    rec = Recorder(f)
    rec.note_on(0.0, 60, 80)
    assert len(f.read_text().splitlines()) == 2   # header + note, before close()
    rec.close()


def test_replay_of_a_recorded_take_matches_the_live_simulation(tmp_path):
    onsets, _ = simulate.scripted_performance()
    f = tmp_path / "t.jsonl"
    rec = Recorder(f)
    for t, n, v in onsets:
        rec.note_on(t, n, v)
    rec.close()
    replayed = [(t + 1.0, n, v) for t, n, v in load_take(f)]
    unlocked = {"lock": {"auto": False}}  # so the pad releases in the silence at the end
    a = simulate.run(c.from_dict(unlocked), onsets=onsets)
    b = simulate.run(c.from_dict(unlocked), onsets=replayed)
    assert [w for _, w in a.log] == [w for _, w in b.log]
    assert b.engine.out.sounding == set()


@pytest.mark.parametrize("content", ["", "not json\n", json.dumps({"format": "other"}) + "\n",
                                     json.dumps({"format": FORMAT}) + "\n"])
def test_bad_takes_are_rejected(tmp_path, content):
    f = tmp_path / "bad.jsonl"
    f.write_text(content)
    with pytest.raises(TakeError):
        load_take(f)


def test_missing_take(tmp_path):
    with pytest.raises(TakeError, match="not found"):
        load_take(tmp_path / "nope.jsonl")


def test_auto_path_is_a_timestamped_take():
    p = auto_path("takes")
    assert p.parent.name == "takes" and p.name.startswith("take-") and p.suffix == ".jsonl"


def test_replay_command_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)               # no config.toml here: defaults are used
    onsets, _ = simulate.scripted_performance()
    f = tmp_path / "t.jsonl"
    rec = Recorder(f)
    for t, n, v in onsets:
        rec.note_on(t, n, v)
    rec.close()
    assert main(["replay", str(f)]) == 0
    out = capsys.readouterr().out
    assert "Pad changes" in out and "Tempo while you played" in out
    # A steady take locks the groove by default, and silence never ends a lock.
    assert "LOCKED" in out and "Still LOCKED at the end" in out


def test_replay_command_reports_bad_take_cleanly(tmp_path, capsys):
    assert main(["replay", str(tmp_path / "missing.jsonl")]) == 2
    assert "not found" in capsys.readouterr().err


def test_actions_are_recorded_and_replayed(tmp_path, capsys, monkeypatch):
    from accompanist.recording import load_actions

    monkeypatch.chdir(tmp_path)
    f = tmp_path / "t.jsonl"
    rec = Recorder(f)
    for i in range(40):                          # 20 s of steady quarter notes at 120
        rec.note_on(100.0 + i * 0.5, 62, 90)
        if i == 20:
            rec.action(100.0 + i * 0.5 + 0.1, "lock")
    rec.action(125.0, "unlock")
    rec.close()
    assert load_actions(f) == [(10.1, "lock"), (25.0, "unlock")]
    assert len(load_take(f)) == 40               # actions are not notes
    (tmp_path / "config.toml").write_text('[lock]\nauto = false\n')
    assert main(["replay", str(f)]) == 0
    out = capsys.readouterr().out
    assert "LOCKED" in out and "unlocked" in out and "lock at 0:10.1" in out


def test_startup_summary_names_every_voice_and_its_channel():
    from accompanist.cli import voices_summary

    s = voices_summary(c.from_dict({"drums": {"enabled": True, "pattern": "soft"}}))
    assert "pad ch 1 (drone" in s and "bass ch 2" in s and "drums ch 10 (soft)" in s
    assert "drums: off" in voices_summary(c.from_dict({}))
