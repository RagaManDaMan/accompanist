"""A MIDI input that isn't plugged in: an error by default, a warning for `run`."""
import queue

import pytest

from accompanist import config as c, midi_io


class FakeMido:
    def get_input_names(self):
        return ["IAC Driver Bus 1"]

    def open_input(self, name, callback=None):
        return name


def test_a_missing_pedal_is_skipped_when_asked(monkeypatch):
    monkeypatch.setattr(midi_io, "_mido", lambda: FakeMido())
    cfg = c.from_dict({"inputs": [{"name": "sax", "audio": "Scarlett"},
                                  {"name": "pedal", "port": "GHMidi", "role": "control"},
                                  {"name": "iac", "port": "IAC"}]})
    missing = []
    assert midi_io.open_inputs(cfg, queue.Queue(), missing) == ["IAC Driver Bus 1"]
    assert [i.name for i, _ in missing] == ["pedal"]
    with pytest.raises(midi_io.PortError, match="No MIDI input port matching 'GHMidi'"):
        midi_io.open_inputs(cfg, queue.Queue())
