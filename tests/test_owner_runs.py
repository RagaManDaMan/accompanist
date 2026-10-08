"""The owner's own runs, replayed: what he heard go wrong must not come back.

Each fixture is a take from a real run (notes and key presses, with how the run was
started). A test here pins what he asked for after hearing it.
"""
from pathlib import Path

from accompanist import cli, config as c, simulate
from accompanist.recording import load_actions, load_run, load_take

FIX = Path(__file__).parent / "fixtures"
OWNER_BAND = ("pad", "pulse", "drums", "percussion", "piano", "response")


def replay(name):
    take = FIX / name

    class A:
        pass
    a = A()
    for k in cli.RUN_ARGS:
        setattr(a, k, None)
    a.cmd = "run"
    for k, v in load_run(take).items():
        setattr(a, k, v)
    over = cli.chart_overrides(a)
    for voice in OWNER_BAND:                                   # his band, as in his config
        over.setdefault(voice, {})["enabled"] = True
    cfg = c.load(None, a.preset, over)
    res = simulate.run(cfg, onsets=load_take(take), actions=load_actions(take))
    return cfg, res


def notes(cfg, res, voice):
    ch = getattr(cfg, voice).channel - 1
    return [(t, m.note) for t, m in res.timeline if m.type == "note_on" and m.velocity and m.channel == ch]


def test_gowrimanohari_run_2026_10_08():
    """Sung in Gowrimanohari, Sa = C, rupakam, 90 bpm; s pressed at 137.7 s."""
    cfg, res = replay("gowrimanohari-rupakam-voice.jsonl")
    s = 137.7
    for voice in ("drums", "percussion", "pulse"):              # no beat before s
        assert all(t >= s for t, _ in notes(cfg, res, voice)), voice
    guitar = notes(cfg, res, "response")
    gowri = {0, 2, 3, 5, 7, 9, 11}
    assert guitar and all(n % 12 in gowri for _, n in guitar)  # in the rāga
    first_sung = load_take(FIX / "gowrimanohari-rupakam-voice.jsonl")[0][0]
    assert min(t for t, _ in guitar) >= first_sung + cfg.response.warmup_s   # listened first
    sung = sorted(n for _, n, _ in load_take(FIX / "gowrimanohari-rupakam-voice.jsonl"))
    played = sorted(n for _, n in guitar)
    assert played[len(played) // 2] >= sung[len(sung) // 2] + 7              # above the voice
    bass = notes(cfg, res, "pulse")
    assert len({n % 12 for _, n in bass}) >= 5                                # not just Sa and Pa
