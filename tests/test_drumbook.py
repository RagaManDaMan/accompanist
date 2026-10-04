"""Real drummers' grooves: building the library from the Groove MIDI Dataset's layout, and the
drums playing a groove's bars (with their timing) and a fill at phrase ends."""
import mido
import pytest

from accompanist import config as c, drumbook as db, simulate


def midi(path, hits, bpm=120):
    m = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    m.tracks.append(tr)
    t = 0
    for beat, note, vel in sorted(hits):
        tick = int(beat * 480)
        tr.append(mido.Message("note_on", note=note, velocity=vel, time=tick - t, channel=9))
        tr.append(mido.Message("note_off", note=note, velocity=0, time=0, channel=9))
        t = tick
    path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(path))


def fake_dataset(root):
    swing = [(b + o, n, v) for bar in range(4) for b, o, n, v in
             [(bar * 4 + k, off, 51, 90) for k in range(4) for off in (0.0, 0.66)] +
             [(bar * 4 + 1, 0.0, 44, 70), (bar * 4 + 3, 0.0, 44, 70)]]
    midi(root / "d1" / "groove.mid", swing)
    fill = [(k * 0.25, 38 if k % 2 else 43, 60 + 4 * k) for k in range(16)]
    midi(root / "d1" / "fill.mid", fill)
    (root / "info.csv").write_text(
        "drummer,session,id,style,bpm,beat_type,time_signature,midi_filename,audio_filename,"
        "duration,split\n"
        "d1,s,1,jazz/swing,120,beat,4-4,d1/groove.mid,x,8,train\n"
        "d1,s,2,jazz,120,fill,4-4,d1/fill.mid,x,2,train\n")


def test_the_library_is_built_from_the_dataset_layout(tmp_path):
    fake_dataset(tmp_path / "gmd")
    assert db.build(tmp_path / "gmd", tmp_path / "lib" / "drums.json") == 2
    book = db.DrumBook.open(tmp_path / "lib")
    assert "jazz" in book.styles() and "jazz/swing" in book.styles()
    g = book.choose("jazz", 130, "beat", __import__("random").Random(0))
    assert g.style == "jazz/swing" and len(g.bars) == 4
    assert any(abs(b - 0.66) < 0.01 and n == 51 for b, n, _ in g.bars[0])   # swung eighth kept


def test_the_drums_play_the_groove_and_fill_into_phrases(tmp_path, monkeypatch):
    fake_dataset(tmp_path / "gmd")
    db.build(tmp_path / "gmd", tmp_path / "lib" / "drums.json")
    notes = [(0.2 + i * 0.25, 62 + i % 5, 80) for i in range(160)]
    taps = [(1.0 + i * 0.5, "tap_tempo") for i in range(4)]
    cfg = c.from_dict({"drums": {"enabled": True, "style": "jazz", "dynamics": 1.0},
                       "library": {"path": str(tmp_path / "lib")}, "lock": {"auto": False},
                       "harmony": {"root": "D"}})
    res = simulate.run(cfg, onsets=notes, actions=taps, total=40.0)
    hits = [(t, m.note) for t, m in res.timeline if m.type == "note_on" and m.channel == 9]
    rides = sorted(t for t, n in hits if n == 51 and 5 < t < 15)
    gaps = {round(b - a, 2) for a, b in zip(rides, rides[1:])}
    assert {0.33, 0.17} <= gaps                                # swung: long-short at 120 bpm
    assert any(n == 38 for t, n in hits) and any(n == 49 for t, n in hits)   # fills, crashes
