from accompanist.harmony import PitchClassTracker, choose_voicing


def test_dominant_pitch_class_wins():
    tr = PitchClassTracker()
    for i, n in enumerate([62, 64, 62, 67, 62]):
        tr.add(i * 0.5, n)
    h = tr.snapshot(3.0)
    assert max(range(12), key=lambda pc: h[pc]) == 2  # D


def test_memory_decays():
    tr = PitchClassTracker(half_life_s=10)
    tr.add(0.0, 62, 127)
    before = tr.snapshot(0.0)[2]
    after = tr.snapshot(10.0)[2]   # one half-life later
    assert after == __import__("pytest").approx(before * 0.5, rel=0.01)


def test_open_voicing_when_no_third_played():
    h = [0.0] * 12
    h[2] = 3.0
    v = choose_voicing(h, 2)
    assert v.third is None and len(v.notes) == 3


def test_minor_and_major_thirds():
    h = [0.0] * 12
    h[7], h[10] = 3.0, 2.0
    assert choose_voicing(h, 7).third == 3
    h = [0.0] * 12
    h[2], h[6] = 3.0, 2.0
    assert choose_voicing(h, 2).third == 4


def test_voicing_stays_in_range():
    for pc in range(12):
        v = choose_voicing([1.0] * 12, pc)
        assert all(36 <= n <= 96 for n in v.notes)
