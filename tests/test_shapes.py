"""Bass shapes (pulse.movement) and drum dynamics (drums.dynamics)."""
from accompanist import config as c
from accompanist.harmony import Voicing
from accompanist.output import RecordingPort, SafeOutput
from accompanist.responders import DrumResponder, PulseResponder

D_MINOR = Voicing(root_pc=2, third=3, notes=(50, 53, 57))
D_MINOR_SCALE = {2, 4, 5, 7, 9, 10, 1}           # with the raised 7th


def bass_line(movement, bars=16, bpb=4, eighths=False):
    """The bass notes on the beats (and, with eighths, all notes in order): a note on every
    beat (no rhythm variety)."""
    cfg = c.from_dict({"pulse": {"movement": movement, "rhythm": 0.0}})
    port = RecordingPort()
    out = SafeOutput(port)
    bass = PulseResponder(cfg.pulse, out)
    on_beats = []
    for beat in range(bars * bpb):
        n = len(port.sent)
        bass.on_beat(beat * 0.5, 2, bar_position=beat % bpb, chord=D_MINOR,
                     beats_per_bar=bpb, scale=D_MINOR_SCALE, period=0.5)
        on_beats += [m.note for m in port.sent[n:] if m.type == "note_on"][:1]
        out.flush(beat * 0.5 + 0.49)
    return [m.note for m in port.sent if m.type == "note_on"] if eighths else on_beats


def test_no_movement_is_the_root_on_every_beat():
    assert set(bass_line(0.0)) == {12 * 3 + 2}


def test_movement_plays_shapes_that_land_on_the_root_each_bar():
    for bpb in (3, 4, 5):
        notes = bass_line(0.9, bpb=bpb)
        root = 12 * (c.from_dict({}).pulse.octave + 1) + 2
        assert all(n in (root, root + 12) for n in notes[::bpb])     # the 1 of every bar
        assert len(set(notes)) >= 3                                 # not one note
        assert all(n % 12 in D_MINOR_SCALE for n in notes)          # in the key
        assert max(abs(a - b) for a, b in zip(notes, notes[1:])) <= 12
        everything = bass_line(0.9, bpb=bpb, eighths=True)
        assert all(n % 12 in D_MINOR_SCALE for n in everything)


def test_shapes_vary_and_some_have_eighth_notes():
    notes = bass_line(0.9, bars=32, bpb=3)
    bars = [tuple(notes[i:i + 3]) for i in range(0, len(notes), 3)]
    assert len(set(bars)) >= 6                                    # not one riff
    longest = max(sum(1 for _ in g) for _, g in __import__("itertools").groupby(bars))
    assert longest <= 2 * c.from_dict({}).pulse.shape_bars        # never loops a riff on
    assert len(bass_line(0.9, bars=32, bpb=3, eighths=True)) > len(notes)


def test_a_chord_change_lands_on_the_new_root():
    cfg = c.from_dict({"pulse": {"movement": 1.0}})
    port = RecordingPort()
    bass = PulseResponder(cfg.pulse, SafeOutput(port))
    g = Voicing(root_pc=7, third=3, notes=(55, 58, 62))
    for beat in range(6):
        chord = D_MINOR if beat < 2 else g
        bass.on_beat(beat * 0.5, chord.root_pc, bar_position=beat % 4, chord=chord, beats_per_bar=4)
    notes = [m.note for m in port.sent if m.type == "note_on"]
    assert notes[2] % 12 == 7                                       # G, as the chord changes


def drum_notes(dynamics, gain=1.0, busy=0.5, bars=16, bpb=4):
    cfg = c.from_dict({"drums": {"enabled": True, "pattern": "basic", "dynamics": dynamics}})
    port = RecordingPort()
    out = SafeOutput(port)
    drums = DrumResponder(cfg.drums, out)
    for beat in range(bars * bpb):
        drums.on_beat(beat * 0.5, 0.5, gain, form_beat=beat, beats_per_bar=bpb, busy=busy)
        drums.tick(beat * 0.5 + 0.49)
    return [(m.note, m.velocity) for m in port.sent if m.type == "note_on"]


def test_no_dynamics_is_the_pattern_alone():
    notes = drum_notes(0.0)
    assert {n for n, _ in notes} == {36, 38, 42}


def test_dynamics_mark_phrases_with_fills_and_crashes():
    notes = drum_notes(1.0, bars=32)
    crashes = [v for n, v in notes if n == 49]
    assert 1 <= len(crashes) <= 3                                   # at most one per 8 bars


def test_dynamics_follow_you_harder_and_lift_when_busy():
    def mean_kick(**kw):
        vs = [v for n, v in drum_notes(**kw) if n == 36]
        return sum(vs) / len(vs)

    soft_even, soft_dyn = mean_kick(dynamics=0.0, gain=0.7), mean_kick(dynamics=1.0, gain=0.7)
    loud_even, loud_dyn = mean_kick(dynamics=0.0, gain=1.3), mean_kick(dynamics=1.0, gain=1.3)
    assert loud_dyn - soft_dyn > loud_even - soft_even               # a wider range
    assert mean_kick(dynamics=1.0, busy=1.0) > mean_kick(dynamics=1.0, busy=0.0)


def bass_rhythms(bpm, rhythm=1.0, bars=64, bpb=4):
    """{rhythm: notes per beat} over many bars at this tempo."""
    period = 60 / bpm
    cfg = c.from_dict({"pulse": {"movement": 0.9, "rhythm": rhythm}})
    port = RecordingPort()
    out = SafeOutput(port)
    bass = PulseResponder(cfg.pulse, out)
    counts = {}
    for beat in range(bars * bpb):
        now = beat * period
        bass.on_beat(now, 2, bar_position=beat % bpb, chord=D_MINOR, beats_per_bar=bpb,
                     scale=D_MINOR_SCALE, period=period)
        out.flush(now + period * 0.99)
        counts.setdefault(bass.rhythm, []).append(now)
    ons = [m for m in port.sent if m.type == "note_on"]
    return counts, len(ons) / (bars * bpb)


def test_bass_rhythms_vary_between_half_beat_double_and_triplets():
    counts, per_beat = bass_rhythms(100)
    assert {"half", "beat", "double", "triplet"} <= set(counts)


def test_at_a_fast_tempo_the_bass_leans_to_half_time_never_double():
    counts, per_beat = bass_rhythms(230)
    assert "double" not in counts and "triplet" not in counts and "half" in counts
    assert per_beat < 1.0                                          # sparser than every beat


def test_no_rhythm_variety_is_a_note_every_beat():
    counts, per_beat = bass_rhythms(230, rhythm=0.0)
    assert set(counts) == {"beat"}
