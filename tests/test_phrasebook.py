"""Your phrase library: phrases cut from what you play, speech left out, recalled in the key
of the moment."""
import random

import pytest

from accompanist import config as c, phrasebook as pb
from accompanist.audio_notes import AudioEvent


def melody(start, pitches, step=0.3, length=0.25, vel=80):
    return [(start + i * step, p, vel, length) for i, p in enumerate(pitches)]


D_MINOR_LINES = [62, 64, 65, 67, 69, 67, 65, 64, 62]


def test_notes_are_cut_into_phrases_at_rests_with_their_key():
    notes = melody(0.0, D_MINOR_LINES) + melody(5.0, [69, 70, 69, 67, 65, 62])
    phrases = pb.phrases_from_notes(notes, "take", ["test"])
    assert len(phrases) == 2
    assert phrases[0].notes[0][0] == 0.0 and len(phrases[0].notes) == 9
    assert (phrases[0].tonic, phrases[0].mode) in ((2, "minor"), (5, "major"))
    assert phrases[1].tags == ("test",)


def test_speech_is_left_out():
    chatter = melody(0.0, [60, 63, 58, 61, 66, 59, 62], step=0.12, length=0.07)
    assert pb.phrases_from_notes(chatter) == []                       # short syllables
    off = melody(0.0, [60, 62, 64, 65], length=0.3)
    assert pb.phrases_from_notes(off, cents=[40, -38, 35, -42]) == []  # off-pitch
    assert pb.phrases_from_notes(off, cents=[5, -8, 3, 10])            # in tune: kept


def test_alap_is_tagged_rubato():
    alap = [(0.0, 62, 70, 1.4), (1.6, 64, 70, 0.4), (2.1, 65, 70, 2.2), (4.6, 64, 70, 0.6),
            (5.4, 62, 70, 1.8)]
    (ph,) = pb.phrases_from_notes(alap)
    assert "rubato" in ph.tags
    (steady,) = pb.phrases_from_notes(melody(0.0, D_MINOR_LINES))
    assert "rubato" not in steady.tags


def test_the_library_keeps_phrases_and_sources_across_opens(tmp_path):
    book = pb.Phrasebook.open(tmp_path / "lib")
    book.add(pb.phrases_from_notes(melody(0.0, D_MINOR_LINES)), "take-1")
    again = pb.Phrasebook.open(tmp_path / "lib")
    assert len(again.phrases) == 1 and again.has("take-1") and not again.has("take-2")


def test_recall_moves_a_phrase_to_the_key_of_the_moment(tmp_path):
    book = pb.Phrasebook.open(tmp_path / "lib")
    book.add([pb.Phrase(tuple(melody(0.0, D_MINOR_LINES)), 2, "minor")])
    g_minor = {7, 9, 10, 0, 2, 3, 5}
    got = book.choose([(0, 67, 80), (0.3, 69, 80)], (7, "minor"), g_minor, random.Random(0),
                      0.75, 8, register=68)
    assert got and all(p % 12 in g_minor for _, p, _ in got)
    assert got[0][1] % 12 == 7                                        # D moved up to G


def test_events_from_audio_become_phrases_and_octave_slips_are_forgotten():
    evs = []
    t = 0.0
    for p in D_MINOR_LINES:
        evs.append(AudioEvent(t, "on", p, 80, cents=3))
        if p == 65:                                                   # a slip, then the fix
            evs.append(AudioEvent(t, "on", 77, 80, corrected=True, cents=2))
            p = 77
        evs.append(AudioEvent(t + 0.25, "off", p))
        t += 0.3
    (ph,) = pb.phrases_from_events(evs, "rec")
    assert 77 in [p for _, p, _, _ in ph.notes] and 65 not in [p for _, p, _, _ in ph.notes]


def test_library_command_adds_takes_once(tmp_path, monkeypatch, capsys):
    from accompanist import cli
    from accompanist.recording import Recorder

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(f'[library]\npath = "{tmp_path / "lib"}"\n')
    rec = Recorder(tmp_path / "takes" / "t.jsonl")
    for t, p, v, _ in melody(0.0, D_MINOR_LINES) + melody(4.0, D_MINOR_LINES[::-1]):
        rec.note_on(t, p, v)
    rec.close()
    assert cli.main(["library", "add", str(tmp_path / "takes")]) == 0
    assert cli.main(["library", "add", str(tmp_path / "takes")]) == 0
    out = capsys.readouterr().out
    assert "Added 2 phrases" in out and "1 files already" in out
    cli.main(["library", "stats"])
    assert "2 phrases" in capsys.readouterr().out
