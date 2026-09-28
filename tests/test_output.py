from accompanist.output import RecordingPort, SafeOutput


def make():
    port = RecordingPort()
    return port, SafeOutput(port)


def kinds(port):
    return [(m.type, getattr(m, "note", None)) for m in port.sent if m.type.startswith("note")]


def test_tracks_sounding_notes():
    port, out = make()
    out.note_on(0, 60, 80)
    assert out.sounding == {(0, 60)}
    out.note_off(0, 60)
    assert out.sounding == set()


def test_scheduled_off_fires_on_time():
    port, out = make()
    out.note_on(0, 60, 80)
    out.note_off_at(1.0, 0, 60)
    out.flush(0.5)
    assert (0, 60) in out.sounding
    out.flush(1.0)
    assert out.sounding == set()


def test_retrigger_cancels_stale_scheduled_off():
    port, out = make()
    out.note_on(0, 60, 80)
    out.note_off_at(1.0, 0, 60)
    out.note_on(0, 60, 80)  # retriggered before the old off fired
    out.flush(2.0)
    assert (0, 60) in out.sounding


def test_panic_silences_everything_and_broadcasts():
    port, out = make()
    out.note_on(0, 60, 80)
    out.note_on(1, 40, 80)
    out.note_off_at(5.0, 0, 60)
    port.sent.clear()
    out.panic()
    assert out.sounding == set()
    ccs = [(m.channel, m.control) for m in port.sent if m.type == "control_change"]
    assert all((ch, cc) in ccs for ch in range(16) for cc in (120, 123))
    out.flush(10.0)  # scheduled off must not resurrect anything or double-send
    assert sum(1 for m in port.sent if m.type == "note_off") == 2
