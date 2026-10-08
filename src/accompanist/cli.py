"""accompanist devices | monitor | run [--record] | replay TAKE | simulate | params [--json]"""
from __future__ import annotations

import argparse
from typing import Optional
import collections
import json
import os
import queue
import re
import select
import shutil
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import params as registry
from .controller import Controller, clock, format_status
from .audio_io import AudioError
from .midi_io import PortError, list_ports, open_inputs, open_output
from .output import SafeOutput
from .recording import Recorder, TakeError, auto_path, load_actions, load_take


ARROWS = {"[C": "right", "[D": "left", "OC": "right", "OD": "left"}


class KeyReader:
    """Single-keypress input (no Enter needed) so the kill switch is one tap."""

    def __init__(self) -> None:
        self.enabled = sys.stdin.isatty()
        self._old = None
        if self.enabled:
            import termios
            import tty

            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)

    def poll(self):
        """One key: a character, or "left"/"right" for the arrow keys."""
        if not self.enabled:
            return None
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":                             # an escape sequence: maybe an arrow
            seq = ""
            while len(seq) < 2 and select.select([sys.stdin], [], [], 0.01)[0]:
                seq += sys.stdin.read(1)
            return ARROWS.get(seq)
        return ch

    def close(self) -> None:
        if self.enabled and self._old is not None:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


def cmd_devices(_args) -> int:
    ins, outs = list_ports()
    print("MIDI inputs:")
    for n in ins or ["(none)"]:
        print(f"  {n}")
    print("MIDI outputs:")
    for n in outs or ["(none)"]:
        print(f"  {n}")
    print("Audio inputs:")
    try:
        from .audio_io import list_inputs

        for name, channels, rate in list_inputs() or [("(none)", 0, 0)]:
            print(f"  {name}" + (f"  ({channels} in, {rate:g} Hz)" if channels else ""))
    except Exception as e:                       # audio is optional: MIDI still works
        print(f"  (unavailable: {e})")
    print("\nPut a distinctive part of a name in `port = \"...\"` (MIDI) or `audio = \"...\"` "
          "(audio) in your config.")
    return 0


def note_label(note: int) -> str:
    return cfgmod.note_name(note)


def kontakt_label(note: int) -> str:
    """The note as Kontakt, Logic and MainStage name it (middle C, 60, is C3 there)."""
    return cfgmod.NOTE_NAMES[note % 12] + str(note // 12 - 2)


def cmd_monitor(args) -> int:
    """Show what the configured inputs hear: MIDI messages, and notes heard in audio."""
    import numpy as np

    from .audio_io import AudioInput, WavWriter
    from .audio_notes import NoteTracker, db_of

    cfg = cfgmod.load(args.config, getattr(args, "preset", None))
    if getattr(args, "input", None):                 # just one input (by its name)
        chosen = [i for i in cfg.inputs if args.input.lower() in (i.name or "").lower()]
        if not chosen:
            raise cfgmod.ConfigError(f"no input named '{args.input}' in the config; inputs: "
                                     + ", ".join(i.name or i.port or i.audio or "?" for i in cfg.inputs))
        cfg.inputs[:] = chosen
    if args.record_audio and not any(i.is_audio for i in cfg.inputs):
        raise cfgmod.ConfigError("--record-audio needs an audio input in the config, e.g.\n"
                                 "  [[inputs]]\n  name = \"sax\"\n  audio = \"Scarlett Solo\"\n"
                                 "  audio_channel = 1")
    q: queue.Queue = queue.Queue()
    ports = open_inputs(cfg, q)
    for icfg in cfg.inputs:
        if not icfg.is_audio:
            ch = "all channels" if icfg.channel is None else f"channel {icfg.channel}"
            print(f"Listening to MIDI '{icfg.port}' ({ch}) as '{icfg.name or icfg.port}'")
    audio_q: queue.Queue = queue.Queue()
    audios, trackers, writer = [], {}, None
    for icfg in cfg.inputs:
        if icfg.is_audio:
            a = AudioInput(icfg, audio_q)
            audios.append(a)
            trackers[id(icfg)] = NoteTracker(cfg.audio, a.sample_rate)
            print(f"Listening to audio '{a.name}' input {icfg.audio_channel} at {a.sample_rate:g} Hz "
                  f"as '{icfg.name or icfg.audio}' (gate {cfg.audio.gate_db:g} dB)")
            if args.record_audio and writer is None:
                writer = WavWriter(args.record_audio, a.sample_rate)
                print(f"Recording that input to {writer.path}")
    print("Monitoring configured inputs. Play something; Ctrl-C to stop.\n")
    t0 = time.monotonic()
    level, last_level_print, sounding = -120.0, 0.0, {}
    try:
        while True:
            try:
                t, icfg, msg = q.get(timeout=0.02)
                if msg.type not in ("clock", "active_sensing"):
                    print(f"\r\x1b[K{t - t0:8.3f}  {icfg.name or icfg.port:<10} {msg}")
            except queue.Empty:
                pass
            while True:
                try:
                    t, icfg, block = audio_q.get_nowait()
                except queue.Empty:
                    break
                if writer is not None and icfg is audios[0].icfg:
                    writer.write(block)
                level = max(level - 1.0, db_of(block))           # a meter that falls slowly
                for ev in trackers[id(icfg)].process(block, t):
                    label = icfg.name or icfg.audio
                    if ev.kind == "on":
                        sounding[label] = ev
                        print(f"\r\x1b[K{ev.t - t0:8.3f}  {label:<10} {note_label(ev.note):<4} "
                              f"{ev.cents:+4.0f} cents  vel {ev.velocity:3d}  ({ev.db:5.1f} dB)")
                    else:
                        start = sounding.pop(label, None)
                        if start is not None:
                            print(f"\r\x1b[K{'':8}  {label:<10} {'':4} ...held {ev.t - start.t:4.2f} s")
            now = time.monotonic()
            if audios and now - last_level_print > 0.2:
                bar = "#" * max(0, int((level + 60) / 2))
                gate = "  (below gate: silence)" if level < cfg.audio.gate_db else ""
                sys.stdout.write(f"\r\x1b[Klevel {level:6.1f} dB |{bar:<30}|{gate}")
                sys.stdout.flush()
                last_level_print = now
    except KeyboardInterrupt:
        pass
    finally:
        for p in ports:
            p.close()
        for a in audios:
            a.close()
        for b in bodies:
            b.close()
        guard.summary()
        if writer is not None:
            writer.close()
            print(f"\nSaved {writer.path}: try `accompanist listen {writer.path}`")
    return 0


def soundcheck_plan(cfg) -> list[tuple[str, int, list[tuple[float, int, int]]]]:
    """(label, channel 1-16, [(seconds in, note, velocity)]) for each voice that is on: what
    `soundcheck` plays, so you can hear that each instrument in your DAW answers."""
    from .patterns import GM_DRUMS

    plan = []
    if cfg.pad.enabled:
        base = 12 * (cfg.pad.octave + 1)
        plan.append(("pad (a C major chord)", cfg.pad.channel,
                     [(0.0, base, 80), (0.0, base + 4, 80), (0.0, base + 7, 80)]))
    if cfg.pulse.enabled:
        base = 12 * (cfg.pulse.octave + 1)
        plan.append(("bass (C, G, C)", cfg.pulse.channel,
                     [(0.0, base, 100), (0.4, base + 7, 100), (0.8, base + 12, 100)]))
    if cfg.drums.enabled:
        plan.append(("drums (kick, snare, hat, crash)", cfg.drums.channel,
                     [(i * 0.3, GM_DRUMS[n], 100) for i, n in
                      enumerate(("kick", "snare", "hat", "crash"))]))
    if cfg.percussion.enabled:
        names = ("conga_mute", "conga_high", "conga_low", "claves", "shaker", "cowbell")
        plan.append(("percussion (" + ", ".join(names) + ")", cfg.percussion.channel,
                     [(i * 0.3, GM_DRUMS[n], 100) for i, n in enumerate(names)]))
    if cfg.piano.enabled:
        base = 12 * (cfg.piano.octave + 1)
        plan.append(("piano (C E G C)", cfg.piano.channel,
                     [(i * 0.25, base + d, 90) for i, d in enumerate((0, 4, 7, 12))]))
    if cfg.response.enabled:
        plan.append(("guitar / answer (E G A)", cfg.response.channel,
                     [(i * 0.3, 64 + d, 90) for i, d in enumerate((0, 3, 5))]))
    return plan


def cmd_library(args) -> int:
    """Your phrase library: add takes and recordings (folders too), or show what's in it."""
    from . import phrasebook as pb

    cfg = cfgmod.load(args.config) if Path(args.config).exists() else cfgmod.from_dict({})
    book = pb.Phrasebook.open(cfg.library.path)
    if args.action == "drums":
        from .drumbook import CREDIT, build

        if len(args.paths) != 1:
            print("accompanist library drums DATASET_FOLDER  (the Groove MIDI Dataset, unzipped:"
                  " the folder with info.csv)")
            return 1
        out = Path(cfg.library.path).expanduser() / "drums.json"
        n = build(Path(args.paths[0]).expanduser(), out)
        print(f"Built {n} grooves and fills into {out}\n  from the {CREDIT}.\n"
              f"Set [drums] style = \"jazz\" (or latin, funk, soul, rock...) in a song to use them.")
        return 0
    if args.action == "ragas":
        from .ragabook import CREDIT, build

        if len(args.paths) != 1:
            print("accompanist library ragas DATASET_FOLDER  (the Indian Art Music Raga Recognition"
                  " Dataset (features), unzipped: the RagaDataset folder)")
            return 1
        out = Path(cfg.library.path).expanduser() / "ragas.json"
        seen: set = set()

        def progress(tradition, name):
            if (tradition, name) not in seen:
                seen.add((tradition, name))
                print(f"\r\x1b[K  {tradition}: {len(seen)} rāgas ({name})", end="", flush=True)

        n = build(Path(args.paths[0]).expanduser(), out, progress)
        print(f"\nLearnt {n} rāgas into {out}\n  from the {CREDIT}.\n"
              f"`accompanist library stats` now names your sessions' rāgas from these.")
        return 0
    if args.action == "stats":
        tags: dict[str, int] = {}
        keys: dict[str, int] = {}
        for ph in book.phrases:
            for t in ph.tags or ("untagged",):
                tags[t] = tags.get(t, 0) + 1
            k = f"{cfgmod.NOTE_NAMES[ph.tonic]} {ph.mode}"
            keys[k] = keys.get(k, 0) + 1
        notes = sum(len(ph.notes) for ph in book.phrases)
        print(f"{len(book.phrases)} phrases, {notes} notes, from {len(book.sources())} "
              f"sources, in {book.path}")
        if tags:
            print("  tags: " + ", ".join(f"{t} {n}" for t, n in sorted(tags.items())))
        top = sorted(keys.items(), key=lambda kv: -kv[1])[:8]
        if top:
            print("  keys (Western view): " + ", ".join(f"{k} {n}" for k, n in top))
        print_raga_landscape(book.phrases, cfg.library.path)
        return 0
    files: list[Path] = []
    for p in map(lambda s: Path(s).expanduser(), args.paths):
        files += sorted(p.rglob("*")) if p.is_dir() else [p]
    files = [f for f in files if f.suffix.lower() in (".jsonl", ".wav")]
    if not files:
        print("Nothing to add: give takes (.jsonl), recordings (.wav) or folders of them.")
        return 1
    added = skipped = 0
    for f in files:
        key = pb.source_key(f)
        if book.has(key):
            skipped += 1
            continue
        try:
            phrases = (pb.from_take(f, args.tag) if f.suffix.lower() == ".jsonl"
                       else pb.from_audio(f, cfg, args.tag))
        except (TakeError, cfgmod.ConfigError) as e:
            print(f"  {f.name}: skipped ({str(e).splitlines()[0]})")
            continue
        n = book.add(phrases, key)
        added += n
        print(f"  {f.name}: {n} phrases")
    print(f"Added {added} phrases ({skipped} files already in the library). "
          f"Now {len(book.phrases)} phrases.")
    return 0


def print_raga_landscape(phrases, library=None) -> None:
    """Sessions of practice and class: each one's Sa and rāga, and the rāgas overall. With
    rāgas learnt from recordings (library ragas), named from how they're sung; else from
    their scales."""
    from . import ragam
    from .ragabook import RagaBook

    sessions: dict[str, list] = {}
    for ph in phrases:
        if "practice" in ph.tags or "class" in ph.tags:
            sessions.setdefault(ph.source, []).append(ph)
    if not sessions:
        return
    book = RagaBook.open(library) if library else None
    totals: dict[str, int] = {}
    print("  rāga view of your practice and classes"
          + (f" (by scales, and nearest of {len(book.ragas)} rāgas as sung in concert):" if book
             else " (by scales; `library ragas` adds how rāgas are sung, from recordings):"))
    for name, phs in sessions.items():
        given = next((p.sa for p in phs if p.sa is not None), None)
        sa, conf = (given, 1.0) if given is not None else ragam.sa_of(phs)
        prof = ragam.profile(phs, sa)
        best = ragam.identify(phs, sa, None, top=2)
        label = " / ".join(best[0][1]) if best else "?"
        other = f" (or {' / '.join(best[1][1])})" if len(best) > 1 else ""
        kind = "class" if any("class" in p.tags for p in phs) else "practice"
        found = "" if given is not None else ", found"
        print(f"    {name} ({kind}, {len(phs)} phrases): Sa = {cfgmod.NOTE_NAMES[sa]}{found}; "
              f"by its swaras {label}{other}; swaras {' '.join(ragam.swaras_used(prof))}")
        if book is not None:                     # the second opinion: as rāgas are sung
            sung = ragam.identify(phs, sa, book, top=3)
            print("      as sung in concert, nearest: " + ", ".join(n[0] for _, n in sung))
        parts = ragam.segments(phs, sa)
        if len(parts) > 1:                       # a session in more than one rāga
            print("      through the session: " + " → ".join(
                f"{' / '.join(names)} ({a + 1}-{b})" for a, b, names in parts))
            for a, b, names in parts:
                totals[" / ".join(names)] = totals.get(" / ".join(names), 0) + b - a
        else:
            totals[label] = totals.get(label, 0) + len(phs)
    print("  rāgas: " + ", ".join(f"{k} {n}" for k, n in sorted(totals.items(), key=lambda kv: -kv[1])))


def cmd_practice(args) -> int:
    """Listen while you practise (or teach): every musical phrase goes into your library.
    Notes only, no audio; speech is left out. No band plays. Ctrl-C to stop."""
    from . import phrasebook as pb
    from .audio_io import AudioInput
    from .audio_notes import NoteTracker

    cfg = cfgmod.load(args.config, args.preset)
    book = pb.Phrasebook.open(cfg.library.path)
    q: queue.Queue = queue.Queue()
    inputs = [i for i in cfg.inputs if i.is_audio and i.role == "note_source"]
    if not inputs:
        raise cfgmod.ConfigError("practice listens to an audio input: add one to [[inputs]]")
    audios, trackers, events = [], {}, {}
    for icfg in inputs:
        a = AudioInput(icfg, q)
        audios.append(a)
        trackers[id(icfg)] = NoteTracker(cfg.audio, a.sample_rate)
        events[id(icfg)] = []
    tags = list(args.tag) + ["practice"]
    session = f"practice {time.strftime('%Y-%m-%d %H:%M')}"
    sa = cfgmod.parse_root(args.sruti) if args.sruti else None
    print(f"Listening for phrases ({', '.join(a.name for a in audios)}), tagged "
          f"{', '.join(tags)}. Ctrl-C to stop.")
    total, last_flush = 0, time.monotonic()
    try:
        while True:
            try:
                t, icfg, block = q.get(timeout=0.1)
            except queue.Empty:
                t = None
            if t is not None:
                events[id(icfg)] += trackers[id(icfg)].process(block, t)
            now = time.monotonic()
            if now - last_flush >= PRACTICE_FLUSH_S:          # bank the finished phrases
                last_flush = now
                for k, evs in events.items():
                    if (not evs or now - evs[-1].t < pb.PHRASE_GAP_S * 2
                            or trackers[k].note is not None):
                        continue                              # nothing, or mid-phrase: wait
                    total += book.add(pb.phrases_from_events(evs, session, tags, sa))
                    events[k] = []
                sys.stdout.write(f"\r\x1b[K{total} phrases learnt so far")
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        for k, evs in events.items():
            if evs:
                total += book.add(pb.phrases_from_events(evs, session, tags, sa))
        for a in audios:
            a.close()
    print(f"\nLearnt {total} phrases. The library now has {len(book.phrases)}.")
    return 0


def cmd_muse(args) -> int:
    """Connect to the headband and show what it gives: heartbeats and gestures, live."""
    from .muse_io import MuseInput

    cfg = cfgmod.load(args.config) if Path(args.config).exists() else cfgmod.from_dict({})
    address = args.address or next((i.muse for i in cfg.inputs if i.is_body), None)
    if not address:
        raise cfgmod.ConfigError("give the headband's address (OpenMuse find shows it), or add "
                                 "[[inputs]] muse = \"...\" to config.toml")
    q: queue.Queue = queue.Queue()
    muse = MuseInput(address, q, raw=args.raw)
    print(f"Connecting to {address}... nod, tilt left twice, tilt right twice. Ctrl-C to stop."
          + (f" Saving the raw data to {args.raw}." if args.raw else ""))
    beats: list[float] = []
    try:
        while True:
            try:
                kind, t, value = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "beat":
                beats = (beats + [t])[-6:]
                gaps = " ".join(f"{b - a:.2f}" for a, b in zip(beats, beats[1:]))
                sys.stdout.write(f"\r\x1b[K♥ {value:5.1f} bpm" if value else
                                 f"\r\x1b[K♥ ... (seconds between beats: {gaps or '-'})")
                sys.stdout.flush()
            else:
                say(f"{kind}: {value}")
    except KeyboardInterrupt:
        print()
    finally:
        muse.close()
    return 0


def cmd_kitmap(args) -> int:
    """Play every note of a range on one voice's channel, one at a time, named: to find
    where a kit keeps its sounds."""
    from .levels import voice_cfg
    from .patterns import GM_DRUMS

    cfg = cfgmod.load(args.config, song=args.song)
    vc = voice_cfg(cfg, args.voice)
    out = SafeOutput(open_output(cfg.output))
    select_patch(out, cfg)
    names = {n: k for k, n in GM_DRUMS.items()}
    if args.make:
        return make_kit(args, out, vc.channel)
    print(f"Channel {vc.channel} ({args.voice}): notes {args.low}-{args.high}. Note what you "
          f"hear for each; Ctrl-C to stop.")
    try:
        for n in range(args.low, args.high + 1):
            print(f"  {n:3d}  {note_label(n):<4} {names.get(n, '')}", flush=True)
            out.note_on(vc.channel - 1, n, 100)
            time.sleep(KITMAP_STEP_S)
            out.note_off(vc.channel - 1, n)
    except KeyboardInterrupt:
        pass
    finally:
        out.panic()
    return 0


KIT_HINTS = {
    "mridangam": "tha (or ta), dhi, thom, nam, dheem, chapu, ki, tham, gumki",
    "tabla": "na (or ta), tin, tun, te, ge, ka, dha, dhin, tit, ke",
}


def make_kit(args, out, channel: int) -> int:
    """kitmap --make NAME: play each key; you name the stroke you hear; a kit file results."""
    from .tala import save_kit

    hint = KIT_HINTS.get(args.make.split("-")[0], KIT_HINTS["mridangam"] + "; " + KIT_HINTS["tabla"])
    print(f"Making kits/{args.make}.toml from channel {channel}, notes {args.low}-{args.high}.\n"
          f"Each key plays; type the stroke you hear ({hint}), Enter to skip it, r to hear it "
          f"again, q to finish.\n"
          f"Kontakt (Native Instruments India and others): open the instrument's Mapping Window; "
          f"it names the stroke of each key as it plays. Skip the pattern keys (the lowest, red "
          f"octave) and the fills: only single strokes.\n")
    strokes: dict[str, list[int]] = {}
    try:
        n = args.low
        while n <= args.high:
            out.note_on(channel - 1, n, 100)
            time.sleep(KITMAP_STEP_S)
            out.note_off(channel - 1, n)
            answer = input(f"  {n:3d} {note_label(n):<4} (Kontakt {kontakt_label(n):<4}) "
                           f"stroke? ").strip().lower()
            if answer == "r":
                continue
            if answer == "q":
                break
            if answer:
                for word in answer.replace(",", " ").split():
                    strokes.setdefault(word, []).append(n)
            n += 1
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        out.panic()
    if not strokes:
        print("No strokes named: no kit written.")
        return 0
    path = save_kit(args.make, strokes, f"{args.make} on channel {channel}")
    print(f"\nWrote {path}: {', '.join(strokes)}.\n"
          + (f"It plays a Carnatic tāla by itself (percussion.kit auto)." if args.make == "mridangam"
             else f"It plays a Hindustani tāla by itself (percussion.kit auto)." if args.make == "tabla"
             else f"Use it with [percussion] kit = \"{args.make}\"."))
    return 0


def cmd_soundcheck(args) -> int:
    """Play a few notes on each voice's channel in turn: is every instrument in MainStage or
    Logic set up and making sound?"""
    cfg = cfgmod.load(args.config)
    out = SafeOutput(open_output(cfg.output))
    where = cfg.output.port or f"virtual source '{cfg.output.virtual_name}'"
    plan = [p for p in soundcheck_plan(cfg) if not args.voice or p[0].startswith(args.voice)]
    print(f"Soundcheck to {where}. Listen for each voice" +
          ("; it repeats until Ctrl-C (fix MainStage while it plays)." if args.loop else
           "; Ctrl-C to stop.") + "\n")
    def rounds():
        yield from plan
        while args.loop:                         # (product() would build an endless list first)
            yield from plan

    try:
        for label, channel, notes in rounds():
            print(f"  channel {channel:2d}: {label}", flush=True)
            start = time.monotonic()
            for at, note, vel in notes:
                while time.monotonic() - start < at:
                    time.sleep(0.005)
                out.note_on(channel - 1, note, vel)
            end = start + max(at for at, _, _ in notes) + 0.8
            while time.monotonic() < end:
                time.sleep(0.01)
            out.panic()
            time.sleep(0.4)
    except KeyboardInterrupt:
        pass
    finally:
        out.panic()
    print("\nA voice you didn't hear: in MainStage, check that a keyboard listens to "
          "IAC Driver Bus 1 on that channel, that a strip uses that keyboard, and that its "
          "sound has something on those notes.")
    return 0


def press_recorder(out: SafeOutput, cfg) -> bool:
    """Press the DAW's record button over MIDI (output.recorder_cc): 127, then 0."""
    cc = cfg.output.recorder_cc
    if cc is None:
        return False
    ch = cfg.output.recorder_channel - 1
    out.control_change(ch, cc, 127)
    time.sleep(0.05)
    out.control_change(ch, cc, 0)
    return True


def band_channels(cfg) -> list[int]:
    """The MIDI channels (1-16) of the voices that are on."""
    voices = (cfg.pad, cfg.pulse, cfg.drums, cfg.percussion, cfg.piano, cfg.response)
    return sorted({v.channel for v in voices if v.enabled})


def apply_mix(out: SafeOutput, cfg) -> None:
    """Each voice's level trim (mix.*_db) as MIDI volume (CC7) on its channel."""
    from .levels import VOICES, cc7_value, voice_cfg

    for voice in VOICES:
        vc = voice_cfg(cfg, voice)
        if vc.enabled:
            out.control_change(vc.channel - 1, 7, cc7_value(getattr(cfg.mix, f"{voice}_db")))


def cmd_levels(args) -> int:
    """Line check: each voice alone, then you; measured from the DAW's recording; trims
    written into the song file so its levels are set over MIDI from then on."""
    from . import levels as lv
    from .audio_io import read_wav

    cfg = cfgmod.load(args.config, song=args.song)
    parts = lv.plan(cfg)
    if args.file:
        wav = Path(args.file).expanduser()
    else:
        out = SafeOutput(open_output(cfg.output))
        select_patch(out, cfg)
        apply_mix(out, cfg)
        for ch in band_channels(cfg):
            out.control_change(ch - 1, FADE_CC, 127)
        out.control_change(cfg.pad.channel - 1, cfg.pad.expression_cc or FADE_CC,
                           round(127 * LEVELS_PAD_EXPRESSION))
        began = time.time()
        if not press_recorder(out, cfg):
            print("Set [output] recorder_cc so this can start your recorder, or start "
                  "recording by hand now and press Enter.")
            input()
        print("Line check: each voice alone, then you. Keep the room quiet...")
        time.sleep(lv.LEAD_IN_S)
        t0 = time.monotonic()
        for p in parts:
            while time.monotonic() - t0 < p.start:
                time.sleep(0.005)
            if p.voice == "you":
                print(f"  YOUR TURN: play at your normal level for {lv.YOU_S:g} seconds... now!")
                time.sleep(lv.YOU_S)
                break
            print(f"  {p.voice} (channel {p.channel})")
            events = sorted([(at, "on", n, v) for at, n, v, _ in p.notes]
                            + [(at + length, "off", n, 0) for at, n, _, length in p.notes])
            for at, kind, n, v in events:
                while time.monotonic() - t0 < p.start + at:
                    time.sleep(0.002)
                if kind == "on":
                    out.note_on(p.channel - 1, n, v)
                else:
                    out.note_off(p.channel - 1, n)
            out.panic()
        time.sleep(1.0)
        press_recorder(out, cfg)
        print("Done. Finding the recording...")
        time.sleep(LEVELS_WAIT_S)
        folder = Path(cfg.mix.recordings).expanduser()
        wavs = sorted((p for p in folder.glob("*.wav") if p.stat().st_mtime >= began - 2),
                      key=lambda p: p.stat().st_mtime)
        if not wavs:
            raise cfgmod.ConfigError(f"no new recording in {folder}: check [mix] recordings, "
                                     f"or measure a file with --file")
        wav = wavs[-1]
    samples, rate = read_wav(wav)
    measured = lv.measure(samples, rate, parts)
    new = lv.trims(measured, cfg)
    print(f"\nLevels in {wav.name}:")
    for line in lv.describe(measured, new, cfg):
        print(line)
    found = {v: db for v, db in new.items() if db is not None}
    if not found:
        return 1
    if not args.song:
        print("\n(Give --song NAME to save these trims into that song.)")
        return 0
    path = cfgmod.song_path(args.song)
    if args.write or (sys.stdin.isatty() and input(f"\nWrite these trims into {path}? [y/n] ")
                      .strip().lower() == "y"):
        text = path.read_text()
        path.write_text(lv.update_song(text, found))
        print(f"Written. The song now sets its levels when it loads (CC7 per voice).")
    return 0


def learn_takes(cfg, paths) -> None:
    """After a run: its takes go into your phrase library (library.learn)."""
    if not (cfg.library.enabled and cfg.library.learn):
        return
    from . import phrasebook as pb

    book = pb.Phrasebook.open(cfg.library.path)
    total = 0
    for p in paths:
        p = Path(p)
        try:
            if p.exists() and not book.has(pb.source_key(p)):
                total += book.add(pb.from_take(p), pb.source_key(p))
        except (TakeError, OSError):
            continue
    if total:
        print(f"Learnt {total} of your phrases from this run (library: {len(book.phrases)}).")


def select_patch(out: SafeOutput, cfg) -> bool:
    """The song's MainStage patch (song.patch, numbered 1-128 as MainStage shows it), as a
    program change (0-127 on the wire) on output.patch_channel."""
    if cfg.song.patch is None:
        return False
    out.program_change(cfg.output.patch_channel - 1, cfg.song.patch - 1)
    return True


def cmd_patch(args) -> int:
    """Send one program change, to test MainStage's patch switching."""
    cfg = cfgmod.load(args.config)
    cfg.song.patch = args.number
    out = SafeOutput(open_output(cfg.output))
    select_patch(out, cfg)
    print(f"Asked for patch {args.number} (program change {args.number - 1} on the wire) on "
          f"channel {cfg.output.patch_channel}.")
    return 0


def cmd_recorder(args) -> int:
    """Press the recorder switch once: to teach MainStage the button, or to test it."""
    cfg = cfgmod.load(args.config)
    if cfg.output.recorder_cc is None:
        cfg.output.recorder_cc = args.cc
    out = SafeOutput(open_output(cfg.output))
    press_recorder(out, cfg)
    print(f"Sent cc {cfg.output.recorder_cc} (127, then 0) on channel "
          f"{cfg.output.recorder_channel}.")
    return 0


def cmd_check(args) -> int:
    """Before a gig: does everything load, is everything plugged in? Problems, and the fix."""
    from .audio_io import find_input
    from .midi_io import _mido, find_port
    from .output import RecordingPort

    problems = 0

    def report(ok: bool, what: str, detail: str = "") -> None:
        nonlocal problems
        problems += not ok
        print(f"  {'ok ' if ok else 'XX '} {what}" + (f": {detail}" if detail else ""))

    print(f"Config {args.config}:")
    try:
        cfg = cfgmod.load(args.config)
        Controller(cfg, SafeOutput(RecordingPort()))       # builds everything: patterns, models
        report(True, "loads")
    except cfgmod.ConfigError as e:
        report(False, "does not load", str(e))
        return 2
    if args.set:
        try:
            title, songs = cfgmod.load_set(args.set)
            report(True, f"set '{title}'", f"{len(songs)} songs")
        except cfgmod.ConfigError as e:
            report(False, f"set '{args.set}'", str(e))
            songs = []
    else:
        songs = args.songs or cfgmod.available_songs()
    print("Songs:")
    for name in songs:
        try:
            Controller(cfgmod.load(args.config, song=name), SafeOutput(RecordingPort()))
            report(True, name)
        except cfgmod.ConfigError as e:
            report(False, name, str(e))
    print("Devices:")
    mido = _mido()
    try:
        out = cfg.output.port
        if out:
            report(True, "output", find_port(mido.get_output_names(), out, "output"))
        else:
            report(True, "output", f"virtual source '{cfg.output.virtual_name}'")
    except PortError as e:
        report(False, "output", str(e).splitlines()[0])
    names = mido.get_input_names()
    for icfg in cfg.inputs:
        label = f"{icfg.name or icfg.port or icfg.audio or icfg.muse} ({icfg.role})"
        if icfg.is_body:
            from .muse_io import available
            problem = available()
            report(problem is None, label, problem or f"headband {icfg.muse} (switch it on; "
                                                     f"`accompanist muse` to test)")
            continue
        try:
            found = (find_input(icfg.audio)[1] if icfg.is_audio
                     else find_port(names, icfg.port, "input"))
            report(True, label, found)
        except (PortError, AudioError) as e:
            report(False, label, str(e).splitlines()[0] + "  (plug it in, or `run` carries on "
                                                          "without it)")
    print("\nAll good." if not problems else f"\n{problems} problem(s) above.")
    return 0 if not problems else 1


def cmd_learn(args) -> int:
    """Press each switch when asked; the [controls] of config.toml are written for you."""
    from .learn import (EXPRESSION_TARGETS, STEPS, TAP_HOLD_STEPS, assign, classify_switch,
                        controls_toml, has_release, pick_expression, update_config)
    from .midi_io import open_all_inputs

    cfg_path = Path(args.config)
    cfg = cfgmod.load(cfg_path)                  # a readable error now, not after all the pressing
    if not sys.stdin.isatty():
        raise cfgmod.ConfigError("learn asks questions: run it in a terminal")
    q: queue.Queue = queue.Queue()
    ports = open_all_inputs(q)
    keys = KeyReader()

    def as_msg(port, msg):
        if msg.type == "control_change":
            return (port, "cc", msg.control, msg.value)
        if msg.type == "program_change":
            return (port, "pc", msg.program, 127)
        if msg.type in ("note_on", "note_off"):
            return (port, "note", msg.note, msg.velocity if msg.type == "note_on" else 0)
        return None

    def drain():
        while not q.empty():
            q.get_nowait()

    def collect(seconds):
        """Messages for `seconds` (e.g. a switch's release, a pedal's sweep)."""
        out, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                _, port, msg = q.get(timeout=0.02)
            except queue.Empty:
                continue
            m = as_msg(port, msg)
            if m:
                out.append(m)
        return out

    def wait_press(prompt, timeout=None):
        """The first message after `prompt`, plus what follows within 0.6 s; None if Enter."""
        drain()
        print(prompt, end=" ", flush=True)
        start = time.monotonic()
        while timeout is None or time.monotonic() - start < timeout:
            if keys.poll() in ("\n", "\r"):
                print("(skipped)")
                return None
            try:
                _, port, msg = q.get(timeout=0.02)
            except queue.Empty:
                continue
            m = as_msg(port, msg)
            if m and (m[1] != "note" or m[3] > 0):
                return [m] + collect(0.6)
        print("(no answer)")
        return None

    def ask(prompt, choices):
        print(prompt, end=" ", flush=True)
        while True:
            k = keys.poll()
            if k and k.lower() in choices:
                print(k)
                return k.lower()
            time.sleep(0.02)

    switches, pedal_ports, tap_hold = {}, set(), []
    try:
        mode = ask("Learning your pedal/controller. How should the switches work?\n"
                   "  [1] one command per switch (a big pedal, e.g. the FCB1010)\n"
                   "  [2] tap and hold: each switch does two commands (a small 4-switch pedal;\n"
                   "      set its switches to send momentary CCs first)\n", {"1", "2"})
        steps = STEPS if mode == "1" else ()
        if mode == "2":
            print("\nFor each switch, press it once (a quick tap), or press Enter to skip.\n")
        for n, (tap, hold, label) in enumerate(TAP_HOLD_STEPS if mode == "2" else ()):
            press = wait_press(f"Switch {n + 1}: {label}. Tap it:")
            if not press:
                continue
            found = classify_switch(press, [])
            if not found:
                print("  (nothing usable heard)")
                continue
            port, kind, number, _ = found
            used = [(k, m) for k, m, _, _ in tap_hold] + [(k, m) for k, m, _ in switches.values()]
            if (kind, number) in used:
                print("  that switch already has a command: skipped")
                continue
            if hold and not has_release(press, kind, number):
                print(f"  {kind} {number} doesn't say when it comes up, so it can only tap "
                      f"(set it to a momentary CC in the pedal's app for hold)")
                assign(switches, tap, (kind, number, False))
            else:
                if hold:
                    tap_hold.append((kind, number, tap, hold))
                else:
                    assign(switches, tap, (kind, number, False))
                print(f"  {kind} {number}" + (" (tap and hold)" if hold else "") + f"  ('{port}')")
            pedal_ports.add(port)
        if mode == "1":
            print("\nFor each command, press the switch you want for it (twice), or press Enter "
                  "to skip.\n")
        for action, label in steps:
            first = wait_press(f"Press the switch for {label}:")
            if not first:
                continue
            second = wait_press("  ...and once more:", timeout=10) or []
            found = classify_switch(first, second)
            if not found:
                print("  (nothing usable heard)")
                continue
            port, kind, number, latching = found
            if kind == "note" and any(i.port and i.port.lower() in port.lower() and i.role == "note_source"
                                      for i in cfg.inputs):
                print(f"  that is a note on '{port}', which you play music on: use another switch")
                continue
            print(f"  {assign(switches, action, (kind, number, latching))}  ('{port}')")
            pedal_ports.add(port)
        expressions = []                           # (cc, knob): e.g. the FCB1010 has two
        while len(expressions) < len(EXPRESSION_TARGETS):
            which = "an" if not expressions else "another"
            sweep = wait_press(f"\nMove {which} EXPRESSION PEDAL from one end to the other "
                               f"(or Enter to skip):")
            if not sweep:
                break
            found = pick_expression(sweep + collect(2.0))
            if not found:
                print("  (that did not look like a pedal sweep)")
                continue
            port, cc = found
            menu = "  ".join(f"[{i + 1}] {t}" for i, t in enumerate(EXPRESSION_TARGETS))
            k = ask(f"\n  cc {cc} on '{port}'. Which knob should it turn? {menu}",
                    {str(i + 1) for i in range(len(EXPRESSION_TARGETS))})
            expressions.append((cc, EXPRESSION_TARGETS[int(k) - 1]))
            pedal_ports.add(port)
        bank = 0
        if any(kind == "pc" for kind, _, _ in switches.values()):
            k = ask("\nDo your pedal's bank up/down switches change these numbers? "
                    "[4] banks of 4 (Blackstar Live Logic)  [0] banks of 10 (Behringer FCB1010)  "
                    "[n] no", {"4", "0", "n"})
            bank = {"4": 4, "0": 10, "n": 0}[k]
        if not switches and not expressions and not tap_hold:
            print("\nNothing learnt; config.toml unchanged.")
            return 0
        block = controls_toml(switches, expressions, bank, tap_hold)
        print("\n" + block)
        port = next(iter(pedal_ports)) if len(pedal_ports) == 1 else None
        if ask(f"Write this into {cfg_path} (a backup is kept as {cfg_path}.bak)? [y/n]",
               {"y", "n"}) == "y":
            text = cfg_path.read_text()
            new = update_config(text, block, port)
            try:                                                      # check before writing
                cfgmod.from_dict(cfgmod.tomllib.loads(new))
            except (cfgmod.tomllib.TOMLDecodeError, cfgmod.ConfigError) as e:
                print(f"\nNot written: the result would not load ({e}). config.toml unchanged.")
                return 2
            cfg_path.with_name(cfg_path.name + ".bak").write_text(text)
            cfg_path.write_text(new)
            print(f"Written. `accompanist run` will use it" +
                  (f"; the pedal '{port}' is now an input with role = \"control\"." if port else "."))
        return 0
    except KeyboardInterrupt:
        print("\nStopped; config.toml unchanged.")
        return 0
    finally:
        keys.close()
        for p in ports:
            p.close()


def cmd_listen(args) -> int:
    """Run a recording through the note detector, offline: the audio version of replay."""
    from .audio_io import read_wav
    from .audio_notes import NoteTracker
    from .recording import Recorder

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    cfg = cfgmod.load(cfg_path) if cfg_path else cfgmod.from_dict({})
    samples, rate = read_wav(args.audio, args.channel)
    tracker = NoteTracker(cfg.audio, rate)
    events = []
    block = 512
    for i in range(0, len(samples), block):
        events += tracker.process(samples[i:i + block], i / rate)
    events += tracker.flush(len(samples) / rate)
    ons = [e for e in events if e.kind == "on"]
    ons = [e for i, e in enumerate(ons) if not (i + 1 < len(ons) and ons[i + 1].corrected
                                                and ons[i + 1].t == e.t)]   # keep the fixed note
    print(f"{args.audio}: {len(samples) / rate:.1f} s at {rate:g} Hz, channel {args.channel} "
          f"(config: {cfg_path or 'defaults'})\n")
    held = {}
    events = [e for e in events if not (e.kind == "off" and any(
        o.corrected and o.t == e.t and o.kind == "on" for o in events))]   # hide octave fixes
    fixes = sum(1 for e in events if e.corrected)
    for e in events:
        if e.kind == "on":
            held[e.note] = e
        elif e.note in held:
            on = held.pop(e.note)
            print(f"  {on.t:7.3f}s  {note_label(on.note):<4} {on.cents:+4.0f} cents  vel {on.velocity:3d}"
                  f"  held {e.t - on.t:5.2f}s")
    print(f"\n{len(ons)} notes heard" + (f" ({fixes} octave slips at attacks corrected)." if fixes else "."))
    if args.save_take:
        rec = Recorder(args.save_take)
        for e in ons:
            rec.note_on(e.t, e.note, e.velocity, "audio")
        rec.close()
        print(f"Saved as a take: {args.save_take} (accompanist replay {args.save_take})")
    return 0


def voices_summary(cfg) -> str:
    """What will play, and on which MIDI channel: shown at startup, so a setting that did not
    reach config.toml is visible before you play."""
    h = cfg.harmony
    if h.model == "chart":
        harmony = (f"chart {Path(h.chart).name}" + (f", transposed {h.transpose:+d}" if h.transpose else "")
                   + (f", {h.chart_bpm:g} bpm" if h.chart_bpm else ""))
    elif h.model == "modal":
        harmony = f"modal, root {h.root}, mode {h.mode}"
    else:
        harmony = f"drone, root {h.root}"
    pad = f"pad ch {cfg.pad.channel} ({harmony})" if cfg.pad.enabled else "pad: off"
    bass = f"bass ch {cfg.pulse.channel}" if cfg.pulse.enabled else "bass: off"
    drums = (f"drums ch {cfg.drums.channel} ({cfg.drums.pattern})" if cfg.drums.enabled
             else "drums: off ([drums] enabled = true)")
    perc = (f"percussion ch {cfg.percussion.channel} ({cfg.percussion.pattern})"
            if cfg.percussion.enabled else "")
    piano = f"piano ch {cfg.piano.channel}" if cfg.piano.enabled else ""
    response = (f"response ch {cfg.response.channel}" if cfg.response.enabled
                else "response: off")
    lock = "tempo lock: auto" if cfg.lock.auto else "tempo lock: manual (l)"
    voices = " | ".join(v for v in (pad, bass, drums, perc, response, piano, lock) if v)
    voices = f"Voices: {voices}"
    s = cfg.song
    if s.title:
        meter = {3: "3/4", 4: "4/4", 5: "5/4", 6: "6/8", 7: "7 (3+2+2)"}.get(s.count, "")
        start = f"{s.tempo:g} bpm {meter}".strip() if s.tempo else "count off with t"
        voices = f"Song: {s.title} ({start}; s = start)\n" + voices
    return voices


def chart_overrides(args) -> dict:
    """--chart FILE / --transpose N: play a chord chart (a command-line layer over config.toml).
    In a set (--set), also the set's plan for the song (how it starts and finishes)."""
    plan = cfgmod.plan_overrides(getattr(args, "set", None), getattr(args, "song", None))
    out = _chart_overrides(args)
    for section, values in plan.items():
        out.setdefault(section, {})
        out[section] = {**values, **out[section]}
    return out


def song_overrides(args, song: str) -> dict:
    """chart_overrides for one song of the set (its own plan)."""
    import copy
    a = copy.copy(args)
    a.song = song
    return chart_overrides(a)


METERS = {"3/4": 3, "3/8": 3, "4/4": 4, "2/2": 4, "5/4": 5, "5/8": 5, "6/8": 6, "6/4": 6,
          "7/4": 7, "7/8": 7, "12/8": 4}


def meter_of(text: str) -> tuple[int, str]:
    """--time-sig: a time signature (3/4, 6/8, 7/8...) or a tāla (rupakam, misra-chapu,
    tintal...) -> (beats per bar for the band, what that means, said at the start)."""
    from . import indian

    t = text.strip().lower()
    if t in METERS:
        return METERS[t], t
    try:
        tala = indian.tala(t)
    except cfgmod.ConfigError:
        raise cfgmod.ConfigError(f"--time-sig '{text}': a time signature ({', '.join(METERS)}) "
                                 f"or a tāla (adi, rupakam, misra-chapu, khanda-chapu, tintal, "
                                 f"rupak, jhaptal, ektal, keherwa, dadra...)") from None
    beats, groups = tala.beats, "+".join(map(str, tala.groups))
    if tala.name == "rupakam":
        bar = 3                                      # 2 + 4, felt in 3
    elif 3 <= beats <= 7:
        bar = beats
    else:
        bar = next(n for n in (4, 3, 7, 5, 6) if beats % n == 0) if any(
            beats % n == 0 for n in (4, 3, 7, 5, 6)) else 4
    said = f"{tala.name}: {beats} beats ({groups})"
    if bar != beats:
        said += f", played as bars of {bar}"
    if bar == 5 and tala.groups[0] == 2:
        said += " (felt 3+2 for now: 2+3 comes with the tāla percussion)"
    return bar, said


def _chart_overrides(args) -> dict:
    h = {}
    key, mode = getattr(args, "key", None), getattr(args, "mode", None)
    if mode and not key:
        raise cfgmod.ConfigError("--mode needs --key too (the tonic, or the rāga's Sa): "
                                 "--key C --mode sahana")
    if key:                                          # --key C, --key "C sahana", + --mode
        words = key.split()
        if mode:
            h.update(model="modal", keys=f"{words[0]} {mode}")
        elif len(words) == 1 and "," not in key:     # a tonic alone: the mode heard from you
            h.update(model="modal", root=words[0], mode="auto")
        else:
            h.update(model="modal", keys=key)
    if getattr(args, "chart", None):
        h.update(model="chart", chart=args.chart)
    if getattr(args, "transpose", None) is not None:
        h["transpose"] = args.transpose
    out = {}
    if getattr(args, "time_sig", None):              # --time-sig 7/8, or a tāla
        out["song"] = {"count": meter_of(args.time_sig)[0]}
        if args.time_sig.strip().lower() not in METERS:
            from . import indian
            out["song"]["tala"] = indian.tala(args.time_sig).name
    if getattr(args, "tempo", None) is not None:
        h["chart_bpm"] = args.tempo
        out.setdefault("song", {})["tempo"] = args.tempo   # --tempo also beats a song file's
    if h:
        out["harmony"] = h
    return out


def add_chart_args(sp) -> None:
    sp.add_argument("--set", default=None, metavar="NAME",
                    help="play a set list (sets/NAME.toml): its songs in order, [ ] to move")
    sp.add_argument("--song", default=None, metavar="NAME",
                    help="load a song's setup (songs/NAME.toml): groove, harmony, drums, feel...")
    sp.add_argument("--chart", default=None, metavar="FILE",
                    help="play a chord chart (MusicXML, e.g. exported from iReal Pro)")
    sp.add_argument("--transpose", type=int, default=None, metavar="N",
                    help="transpose the chart N semitones (-11..11)")
    sp.add_argument("--tempo", type=float, default=None, metavar="BPM",
                    help="the tempo s counts in at (a song's or a chart's; a chart without it: a typical tempo for its style)")


PRESET_HELP = "layer a preset (presets/NAME.toml) under your config; see `accompanist params`"
KEYS = {" ": "panic", "p": "panic", "r": "resume", "t": "tap_tempo", "l": "lock_toggle",
        "c": "chord_toggle", "s": "song_start", "f": "finish", "b": "break",
        "]": "song_next", "right": "song_next", "[": "song_prev", "left": "song_prev"}
# Changing song while the band still sounds: fade every voice out on Expression over this
# long first (resent every EXPRESSION_STEP_S), then restore it for the next song.
SWITCH_FADE_S = 1.0
EXPRESSION_STEP_S = 0.05
FADE_CC = 11
PATCH_SETTLE_S = 0.15      # after a patch change, before the new song's levels go out
# accompanist levels: the pad's expression while it is measured (its normal level), and how
# long to wait for the DAW to finish writing its recording.
LEVELS_PAD_EXPRESSION = 0.8
KITMAP_STEP_S = 0.8        # kitmap: how long each note sounds
PRACTICE_FLUSH_S = 5.0     # practice: how often finished phrases are banked
LEVELS_WAIT_S = 3.0
# Keys that need a second press within this long while the band is playing (a slip of the
# finger mustn't end the set or change the song mid-song).
CONFIRM_S = 2.0
STAGE_EVERY_S = 0.1        # the stage screen's view of the band is refreshed this often


UI_MESSAGES: "collections.deque[str]" = collections.deque(maxlen=8)   # for the stage screen


def say(message) -> None:
    """Print an event line above the status line (which is redrawn after it), and show it
    on the stage screen."""
    if message:
        sys.stdout.write(f"\r\x1b[K{message}\n")
        sys.stdout.flush()
        UI_MESSAGES.append(str(message))


LAST_GOOD = ".last-good"                  # config.toml.last-good: the last config that started
ERROR_LOG = Path("logs") / "errors.log"


def load_for_run(args):
    """The config for `run`, never stopping a gig over a fixable mistake: a song that doesn't
    load is skipped (your config.toml alone), and a config.toml that doesn't load falls back to
    the last copy that started (config.toml.last-good). Each says so, loudly."""
    overrides = chart_overrides(args)
    path = Path(args.config)
    try:
        cfg = cfgmod.load(args.config, args.preset, overrides, song=args.song)
    except cfgmod.ConfigError as first:
        problem = str(first)
        if args.song:
            try:
                cfg = cfgmod.load(args.config, args.preset, overrides)
                print(f"\n*** The song '{args.song}' has a problem: {problem}\n"
                      f"*** Playing with your config.toml alone. (`accompanist check` to fix it.)\n")
                return cfg
            except cfgmod.ConfigError:
                pass
        good = path.with_name(path.name + LAST_GOOD)
        if not good.exists():
            raise
        try:
            cfg = cfgmod.load(str(good), args.preset, overrides, song=args.song)
        except cfgmod.ConfigError:
            try:
                cfg = cfgmod.load(str(good), args.preset, overrides)
            except cfgmod.ConfigError:
                raise first from None
        when = time.strftime("%d %b %H:%M", time.localtime(good.stat().st_mtime))
        print(f"\n*** {path} has a problem: {problem}\n"
              f"*** Using the last copy that worked ({good}, from {when}).\n")
        return cfg
    if path.exists():
        try:
            path.with_name(path.name + LAST_GOOD).write_text(path.read_text())
        except OSError:
            pass
    return cfg


class LiveGuard:
    """Keeps `run` going through an unexpected error (a bug): the error goes to
    logs/errors.log with its details, a short line on screen, and the band plays on."""

    def __init__(self, log: Path = ERROR_LOG) -> None:
        self.log, self.count, self._said = log, 0, float("-inf")
        self._logged: set[tuple[str, str]] = set()

    def report(self, e: BaseException) -> None:
        import traceback

        self.count += 1
        key = (type(e).__name__, str(e))
        if key in self._logged:
            return                                       # the same error again: counted only
        self._logged.add(key)
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}\n")
        except OSError:
            pass
        if time.monotonic() - self._said > 10:          # one line now and then, not a flood
            self._said = time.monotonic()
            say(f"(internal error: {type(e).__name__}: {e}; logged to {self.log}, carrying on)")

    def summary(self) -> None:
        if self.count:
            print(f"\n{self.count} internal error(s) during the run, logged to {self.log}: "
                  f"please send it over.")


def cmd_run(args) -> int:
    from .audio_io import AudioInput, WavWriter
    from .audio_notes import AudioFeed

    set_title, set_songs = (cfgmod.load_set(args.set) if args.set else (None, []))
    if set_songs:
        for name in set_songs:                       # all of them load, before the first note
            cfgmod.load(args.config, args.preset, song_overrides(args, name), song=name)
        args.song = set_songs[0]
    song_index = 0
    cfg = load_for_run(args)
    if args.record_audio and not any(i.is_audio for i in cfg.inputs):
        raise cfgmod.ConfigError("--record-audio needs an audio input in the config ([[inputs]] audio = ...)")
    q: queue.Queue = queue.Queue()
    missing: list = []
    in_ports = open_inputs(cfg, q, missing)
    for icfg, _ in missing:
        role = "controls" if icfg.role == "control" else "notes"
        print(f"warning: MIDI input '{icfg.name or icfg.port}' ('{icfg.port}', {role}) is not "
              f"plugged in: carrying on without it (plug it in and restart to use it)")
    port = open_output(cfg.output)
    out = SafeOutput(port)
    ctl = Controller(cfg, out)
    keys = KeyReader()
    rec = None
    record = args.record or ("auto" if cfg.output.record and not args.no_record else None)
    if record:
        rec = Recorder(auto_path(name=args.song) if record == "auto" else record, run_args(args))

    def heard(t, note, velocity, source):
        ctl.on_note(t, note, velocity)
        if rec:
            rec.note_on(t, note, velocity, source)

    audio_q: queue.Queue = queue.Queue()
    audios, feeds, wav = [], {}, None
    for icfg in cfg.inputs:
        if icfg.is_audio and icfg.role == "note_source":
            try:
                a = AudioInput(icfg, audio_q)
            except AudioError as e:
                print(f"warning: audio input '{icfg.name or icfg.audio}' isn't available "
                      f"({str(e).splitlines()[0]}): carrying on without it")
                continue
            audios.append(a)
            feeds[id(icfg)] = AudioFeed(cfg.audio, a.sample_rate, heard, icfg.name or icfg.audio)
            print(f"Listening to audio '{a.name}' input {icfg.audio_channel} as '{icfg.name or icfg.audio}' "
                  f"(gate {cfg.audio.gate_db:g} dB)")
            if args.record_audio and wav is None:
                wav = WavWriter(args.record_audio, a.sample_rate)
    for icfg in cfg.inputs:
        if not icfg.is_audio:
            print(f"Listening to MIDI '{icfg.port}' as '{icfg.name or icfg.port}'")
    body_q: queue.Queue = queue.Queue()
    bodies = []
    for icfg in cfg.inputs:
        if icfg.is_body:
            from .muse_io import MuseError, MuseInput
            try:
                bodies.append(MuseInput(icfg.muse, body_q, icfg.name or "muse"))
                print(f"Listening to the headband '{icfg.name or icfg.muse}' (heartbeat, "
                      f"gestures: nod = {cfg.body.nod or '-'}, tilt left twice = "
                      f"{cfg.body.tilt_left or '-'}, tilt right twice = {cfg.body.tilt_right or '-'})")
            except MuseError as e:
                print(f"warning: headband '{icfg.name or icfg.muse}': {e}: carrying on without it")
    if not in_ports and not audios:
        raise PortError("nothing to listen to: no MIDI input or audio input is available "
                        "(see the warnings above; `accompanist check` lists what's missing)")
    where = cfg.output.port or f"virtual source '{cfg.output.virtual_name}'"
    print(f"Playing to {where}." + (f" Preset: {cfg.preset}." if cfg.preset else ""))
    print(voices_summary(cfg))
    if getattr(args, "time_sig", None):
        print(f"Time: {meter_of(args.time_sig)[1]}")
    if getattr(args, "key", None):
        print(f"Key: {cfg.harmony.keys or cfg.harmony.root}")
    if cfgmod.is_indic({"song": {"tala": cfg.song.tala}, "harmony": {"keys": cfg.harmony.keys}}):
        print("An Indian piece: the percussion plays nearly throughout (styles/indic.toml; "
              "your own [percussion] settings win)")
    say_tala(ctl.engine)
    if set_songs:
        print(f"Set: {set_title}: " + ", ".join(f"{i + 1}. {s}" for i, s in enumerate(set_songs))
              + "   ([ ] or the arrows: previous / next song)")
    if rec:
        print(f"Recording your notes to {rec.path}")
    if wav:
        print(f"Recording the audio to {wav.path}")
    print("Keys: [space]/[p] = PANIC (silence + mute)   [r] = resume   [l] = lock / unlock tempo\n"
          "      [c] = hold / release chord   [t] = count off: 3 waltz, 4 four, 5 = 5/4, 6 = 6/8, 7 = 3+2+2"
          "\n      [s] = start the song (count in at its tempo; a chart from the top)"
          "   [f] = finish (a last chord on the next 1)\n"
          "      [b] = break (the band stops for a bar or two; you alone)   [q] = quit"
          + ("\n      [ / ] or left / right = previous / next song in the set" if set_songs else "")
          + "\n")
    stage = open_stage(cfg, args)
    set_cards = []                             # the reckoner for the set, on the stage screen
    if stage is not None and set_songs:
        from .reckoner import card
        plan = cfgmod.set_plan(args.set)
        set_cards = [card(cfgmod.load(args.config, args.preset, song_overrides(args, n), song=n), n,
                          plan.get(n, {}).get("style")) for n in set_songs]
    if select_patch(out, cfg):
        print(f"MainStage patch {cfg.song.patch} (channel {cfg.output.patch_channel})")
    apply_mix(out, cfg)
    if press_recorder(out, cfg):
        print(f"Pressed the recorder (cc {cfg.output.recorder_cc}, channel "
              f"{cfg.output.recorder_channel}): MainStage should be recording now.")
    last_print = 0.0
    confirm = None                             # (key, until): a second press is due
    fade, fade_sent = None, float("-inf")      # (song index, start, end): fading to change song
    takes_made: list[Path] = []                # this run's earlier songs' takes (a set)
    guard = LiveGuard()
    stage_sent = float("-inf")
    try:
        while True:
            now = time.monotonic()
            try:
                while True:
                    try:
                        t, icfg, msg = q.get_nowait()
                    except queue.Empty:
                        break
                    fired = (None, None)
                    try:
                        if msg.type == "note_on" and msg.velocity > 0:
                            if icfg.role == "note_source":
                                ctl.on_note(t, msg.note, msg.velocity)
                                if rec:
                                    rec.note_on(t, msg.note, msg.velocity, icfg.name or icfg.port)
                            else:                          # a control input: its notes are commands
                                fired = ctl.on_midi(t, "note", msg.note, msg.velocity)
                        elif msg.type == "control_change":
                            fired = ctl.on_midi(t, "cc", msg.control, msg.value)
                        elif msg.type == "program_change":
                            fired = ctl.on_midi(t, "pc", msg.program, 127)
                    except cfgmod.ConfigError as e:
                        fired = (None, str(e))
                    action, message = fired
                    if message:
                        say(message)
                        last_print = 0.0
                    if action and rec:
                        rec.action(t, action)
                while True:
                    try:
                        t, icfg, block = audio_q.get_nowait()
                    except queue.Empty:
                        break
                    if wav is not None and icfg is audios[0].icfg:
                        wav.write(block)
                    feeds[id(icfg)].process(block, t)
                while True:                        # the headband: heartbeats, gestures
                    try:
                        kind, t_body, value = body_q.get_nowait()
                    except queue.Empty:
                        break
                    if kind == "status":
                        say(value)
                        continue
                    action, message = ctl.on_body(t_body, kind, value)
                    if message:
                        say(message)
                        last_print = 0.0
                    if action and rec:
                        rec.action(t_body, action)
                key = keys.poll()
                playing = ctl.engine.clock.running and not ctl.engine.muted
                if key == "q" or (key in KEYS and KEYS[key] in ("song_next", "song_prev")):
                    if playing and not (confirm and confirm[0] == key and now < confirm[1]):
                        confirm = (key, now + CONFIRM_S)
                        say(f"playing: press {key} again to "
                            + ("quit" if key == "q" else "change song"))
                        key = None
                    else:
                        confirm = None
                if key == "q":
                    break
                if key in KEYS:
                    message = ctl.do(KEYS[key], now)
                    if message:
                        say(message)
                    if rec and KEYS[key] not in ("song_next", "song_prev"):
                        rec.action(now, KEYS[key])
                    last_print = 0.0               # show the new state at once
                if ctl.song_step:                  # a set list: another song
                    step, ctl.song_step = ctl.song_step, 0
                    if not set_songs:
                        say("no set list: start with --set NAME to move between songs")
                    elif not 0 <= song_index + step < len(set_songs):
                        say("that was the " + ("last" if step > 0 else "first") + " song of the set")
                    elif fade is None:
                        eng = ctl.engine
                        sounding = (eng.clock.running and not eng.muted) or eng._ending is not None
                        fade = (song_index + step, now, now + (SWITCH_FADE_S if sounding else 0.0))
                        if sounding:
                            say("fading out...")
                if fade is not None:               # fading out before the song changes
                    target, start, end = fade
                    if now < end:
                        if now - fade_sent >= EXPRESSION_STEP_S:
                            level = round(127 * (end - now) / (end - start))
                            for ch in band_channels(cfg):
                                out.control_change(ch - 1, FADE_CC, level)
                            fade_sent = now
                        time.sleep(0.005)
                        continue                   # the band holds still while it fades
                    fade, song_index = None, target
                    out.panic()
                    args.song = set_songs[song_index]
                    cfg = load_for_run(args)
                    ctl = Controller(cfg, out)
                    if select_patch(out, cfg):     # the new song's sounds first...
                        time.sleep(PATCH_SETTLE_S)
                    for ch in band_channels(cfg):  # ...then back to full, for them only: the
                        out.control_change(ch - 1, FADE_CC, 127)   # old patch's tails stay down
                    apply_mix(out, cfg)
                    if rec:
                        rec.close()
                        takes_made.append(rec.path)
                        rec = Recorder(auto_path(name=args.song), run_args(args))
                    s = cfg.song
                    say(f"Song {song_index + 1}/{len(set_songs)}: {s.title or args.song}"
                        + (f", {s.tempo:g} bpm" if s.tempo else "")
                        + (f" in {s.count}" if s.count else "") + ": s to count in")
                    last_print = 0.0
                if stage is not None:              # the stage screen's buttons and knobs
                    for kind, what, value in stage.take():
                        try:
                            if kind == "do":
                                say(ctl.do(what, now))
                                if rec and what not in ("song_next", "song_prev"):
                                    rec.action(now, what)
                            else:
                                ctl.set_param(what, value)
                        except cfgmod.ConfigError as e:
                            say(str(e))
                        last_print = 0.0
                ctl.tick(now)
                for t_held, held in ctl.take_holds():  # a switch held: its hold action
                    if rec:
                        rec.action(t_held, held)
                for message in ctl.take_events():     # e.g. a count-off completing
                    say(message)
                    last_print = 0.0
                if stage is not None and now - stage_sent >= STAGE_EVERY_S:
                    from .ui import stage_state
                    stage.publish(stage_state(ctl, now, {"title": set_title, "songs": set_songs,
                                                         "index": song_index, "cards": set_cards}
                                              if set_songs else None, list(UI_MESSAGES)))
                    stage_sent = now
                if now - last_print >= 0.25:
                    width = shutil.get_terminal_size((100, 20)).columns - 1
                    sys.stdout.write("\r\x1b[K" + format_status(ctl.get_state(now))[:width])
                    sys.stdout.flush()
                    last_print = now
            except Exception as e:           # a bug must not stop the band mid-set
                guard.report(e)
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        out.panic()  # never leave notes hanging, however we exit
        if press_recorder(out, cfg):
            print("\nPressed the recorder again: MainStage should have stopped recording.")
        if rec:
            rec.close()
            learn_takes(cfg, takes_made + [rec.path])
        keys.close()
        if stage is not None:
            stage.close()
        for p in in_ports:
            p.close()
        for a in audios:
            a.close()
        if wav is not None:
            wav.close()
        port.close()
        print("\nStopped. All notes off.")
    return 0


RUN_ARGS = ("song", "set", "key", "mode", "time_sig", "tempo", "preset", "chart", "transpose")


def run_args(args) -> dict:
    """The options a run was started with (for a take's header)."""
    return {k: getattr(args, k, None) for k in RUN_ARGS}


def say_tala(eng) -> None:
    """At the start: the tāla the percussion keeps, on which kit (or why it can't)."""
    if eng.tala_problem:
        print(f"warning: {eng.tala_problem}: the percussion plays its patterns instead")
    p = eng.tala_player
    if p is None:
        return
    kit = p.kit.name + (" (General MIDI stand-in: make your own with `accompanist kitmap "
                        "percussion --make mridangam` or `--make tabla`)" if p.kit.name == "gm-tabla" else "")
    print(f"Tāla: {p.cycle.tala}, {len(p.cycle.beats)} beats, on the {kit} kit"
          + (f"; strokes it has no key for: {', '.join(p.missing)}" if p.missing else ""))


def open_stage(cfg, args):
    """The stage screen (ui.py), if on: its address printed and opened. Never stops the band:
    if it can't start, a warning, and the keys still work."""
    if not cfg.ui.enabled or getattr(args, "no_ui", False):
        return None
    from .ui import StageServer

    try:
        stage = StageServer(cfg.ui.port, cfg.ui.lan)
    except OSError as e:
        print(f"warning: the stage screen couldn't start ({e}): carrying on with the keys")
        return None
    print(f"Stage screen: {stage.url}")
    if stage.lan_url:
        print(f"  on a phone or tablet (same Wi-Fi): {stage.lan_url}")
    if cfg.ui.open:
        stage.open_page_unless_watched()
    return stage


def cmd_reckoner(args) -> int:
    """How each song starts and finishes, before the show: a set, a song, or every song."""
    from .reckoner import card, format_card

    if args.set:
        title, songs = cfgmod.load_set(args.set)
        print(f"{title}: {len(songs)} song{'s' if len(songs) != 1 else ''}\n")
    else:
        songs = [args.song] if args.song else cfgmod.available_songs()
        title = None
    for i, name in enumerate(songs):
        entry = cfgmod.set_plan(args.set).get(name, {}) if args.set else {}
        cfg = cfgmod.load(args.config, overrides=cfgmod.plan_layers(entry) or None, song=name)
        print(format_card(card(cfg, name, entry.get("style")), i + 1 if title else None) + "\n")
    print("Choose a song's start and finish by ear, and keep them for the set:\n"
          "  accompanist rehearse --set NAME --song SONG\n"
          "Or in the song's file (songs/NAME.toml), for every set:\n"
          "  [start]  shape = \"count\" | \"intro\" | \"drums\" | \"you\" | \"drone\"   bars = 4\n"
          "  [ending] shape = \"chord\" | \"button\" | \"tag\" | \"ritardando\" | \"piano-tag\"\n"
          f"Styles (rehearse, or style = in a set's plan): {', '.join(cfgmod.available_styles())}\n"
          "Hear one, the band alone:  accompanist rehearse --song NAME "
          "[--start SHAPE] [--finish SHAPE]")
    return 0


REHEARSE_BARS = 4          # rehearse: bars the band plays between the start and the finish
REHEARSE_DRONE_S = 6.0     # rehearse: how long a drone start holds before the count


class Rehearsal:
    """rehearse, step by step (any clock, so it can be tested): the start, REHEARSE_BARS bars
    of the band alone (the home chord held, or the chart), then the finish, until it has
    rung out."""

    def __init__(self, ctl, cfg) -> None:
        self.ctl, self.cfg, self.eng = ctl, cfg, ctl.engine
        self.home = self.eng._home_chord()
        self.phase, self.drone_until, self.done_at = "begin", None, None
        if self.home is None and not self.eng.is_chart:     # no key in the song: C will do
            from .harmony import Voicing
            base = 12 * (cfg.pad.octave + 1)
            self.home = Voicing(0, 4, (base, base + 7, base + 12, base + 16), scheduled=True)
            self.phase = "begin-c"

    def step(self, now: float, say=lambda m: None) -> bool:
        """Advance to `now`. True when the rehearsal is over."""
        ctl, eng, cfg = self.ctl, self.eng, self.cfg
        if self.phase in ("begin", "begin-c"):
            keyless = self.phase == "begin-c"
            self.phase = "start"
            if keyless:
                say("(the song has no key: rehearsing in C)")
            if keyless and cfg.start.shape == "drone":
                eng.chord_held, eng.frozen, eng.droning = True, self.home, True
                ctl._started(now)
                say(f"drone: {self.home.label()} holds, in free time; s again counts the band in")
            else:
                say(ctl.do("song_start", now))
            if eng.droning:
                self.drone_until = now + REHEARSE_DRONE_S
        if self.drone_until is not None and now >= self.drone_until:
            self.drone_until = None
            say(ctl.do("song_start", now))
        if (not eng.is_chart and self.home is not None and not eng.chord_held
                and not eng.droning and eng.song_playing and not eng._drums_alone):
            eng.chord_held, eng.frozen = True, self.home     # no soloist: the home chord
        if self.phase == "start" and eng.song_playing and not eng.in_intro:
            self.phase = "middle"
            say(f"(the song: {REHEARSE_BARS} bars, then f)")
        if self.phase == "middle":
            bpb = cfg.song.count or cfg.pulse.beats_per_bar
            if eng.beat_count >= REHEARSE_BARS * bpb - 1:
                self.phase = "finish"
                say(ctl.do("finish", now))
        if self.phase == "finish" and eng.finished:
            self.done_at = self.done_at or now
            if now - self.done_at >= cfg.ending.ring_s + 1.0:
                return True
        ctl.tick(now)
        for message in ctl.take_events():
            say(message)
        return False


def sets_with(song: str) -> list[str]:
    out = []
    for name in cfgmod.available_sets():
        try:
            if song in cfgmod.load_set(name)[1]:
                out.append(name)
        except cfgmod.ConfigError:
            pass
    return out


def rehearse_menu(ch: dict, keys: str, kept_in: Optional[str]) -> str:
    from .reckoner import FINISH_SHAPES, START_SHAPES

    mark = lambda on: "*" if on else ""
    starts = "  ".join(f"{i + 1} {s}{mark(s == ch['start'])}" for i, s in enumerate(START_SHAPES))
    finishes = "  ".join(f"{chr(97 + i)} {f}{mark(f == ch['finish'])}"
                         for i, f in enumerate(FINISH_SHAPES[:-1]))
    styles = "  ".join(f"{s}{mark(s == (ch['style'] or 'as-written'))}"
                       for s in ["as-written"] + cfgmod.available_styles())
    t = ch["transpose"] or 0
    keep = f"k = keep it all for the set '{kept_in}'" if kept_in else "k = keep it for a set"
    return (f"\n  Starts:   {starts}   (+/- intro bars: {ch['bars']})\n"
            f"  Finishes: {finishes}\n"
            f"  Key:      < / > a semitone down / up (now {t:+d}: {keys})\n"
            f"  Style:    {styles}   (type its name)\n"
            f"  Enter = play again   e.g. 2c or 3d> = try that   {keep}   q = quit\n> ")


def cmd_rehearse(args) -> int:
    """Hear a song's start and finish, the band alone (holding the home chord, or following
    the chart), then try other starts, finishes, keys and styles from a menu and keep what
    you like in the set list."""
    from .reckoner import FINISH_SHAPES, START_SHAPES, card, format_card, harmony

    entry = cfgmod.set_plan(args.set).get(args.song, {}) if args.set else {}
    target = args.set or (lambda s: s[0] if len(s) == 1 else None)(sets_with(args.song))
    if not args.set and target:
        entry = cfgmod.set_plan(target).get(args.song, {})
    base = cfgmod.load(args.config, overrides=cfgmod.plan_layers(entry) or None, song=args.song)
    ch = {"start": args.start or base.start.shape, "finish": args.finish or base.ending.shape,
          "bars": args.bars or base.start.bars,
          "transpose": args.transpose if args.transpose is not None else base.song.transpose,
          "style": args.style or entry.get("style")}
    print(format_card(card(base, args.song)))
    out = SafeOutput(open_output(base.output))
    if select_patch(out, base):
        time.sleep(PATCH_SETTLE_S)
    apply_mix(out, base)
    try:
        while True:
            layers = cfgmod.plan_layers({**ch, "start": "count" if ch["start"] == "you" else ch["start"]})
            cfg = cfgmod.load(args.config, overrides=layers, song=args.song)
            print(f"\nPlaying: start {ch['start']}"
                  + (f" ({ch['bars']} bars)" if ch["start"] in ("intro", "drums") else "")
                  + (" (with a count: a 'you' start needs you)" if ch["start"] == "you" else "")
                  + f", four bars, finish {ch['finish']}; {harmony(cfg)}"
                  + (f", {ch['style']} style" if ch["style"] else "") + ".   (Ctrl-C stops it)")
            rehearsal = Rehearsal(Controller(cfg, out), cfg)
            try:
                while not rehearsal.step(time.monotonic(), say):
                    time.sleep(0.005)
            except KeyboardInterrupt:
                pass
            out.panic()
            if args.once:
                break
            try:
                answer = input(rehearse_menu(ch, harmony(cfg), target)).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if answer in ("q", "quit"):
                break
            if answer == "k":
                where = target
                if where is None:
                    options = sets_with(args.song)
                    if not options:
                        print(f"'{args.song}' isn't in any set yet: add it to one (sets/NAME.toml, "
                              f"songs = [...]) and keep it again")
                        continue
                    pick = input("  Which set? " + "  ".join(f"{i + 1} {s}" for i, s in
                                                            enumerate(options)) + "\n> ").strip()
                    if not pick.isdigit() or not 1 <= int(pick) <= len(options):
                        continue
                    where = target = options[int(pick) - 1]
                path = cfgmod.save_plan(
                    where, args.song, start=ch["start"], finish=ch["finish"],
                    bars=ch["bars"] if ch["start"] in ("intro", "drums") else None,
                    transpose=ch["transpose"] or None, style=ch["style"])
                print(f"Kept in {path}: {args.song}: start '{ch['start']}', finish "
                      f"'{ch['finish']}'" + (f", {ch['transpose']:+d} semitones" if ch["transpose"] else "")
                      + (f", {ch['style']} style" if ch["style"] else "") + ".")
                continue
            word = answer.replace("-", "")
            if word in ("aswritten", "written", "nostyle", "none"):
                ch["style"] = None
                continue
            if len(answer) > 2 and answer.isalpha() or "-" in answer.strip("-"):   # a style
                try:
                    name = cfgmod.resolve_name(answer, cfgmod.available_styles(), "style")
                except cfgmod.ConfigError as e:
                    print(f"  {e}")
                    continue
                if name in cfgmod.available_styles():
                    ch["style"] = name
                else:
                    print(f"  no style '{answer}': {', '.join(cfgmod.available_styles())}")
                continue
            for c in answer:
                if c.isdigit() and 1 <= int(c) <= len(START_SHAPES):
                    ch["start"] = START_SHAPES[int(c) - 1]
                elif "a" <= c < chr(97 + len(FINISH_SHAPES) - 1):
                    ch["finish"] = FINISH_SHAPES[ord(c) - 97]
                elif c == "+":
                    ch["bars"] = min(16, ch["bars"] + 1)
                elif c == "-":
                    ch["bars"] = max(1, ch["bars"] - 1)
                elif c in "<>":
                    t = (ch["transpose"] or 0) + (1 if c == ">" else -1)
                    ch["transpose"] = max(-11, min(11, t))
    finally:
        out.panic()
    return 0


def cmd_simulate(args) -> int:
    from . import simulate

    cfg = cfgmod.build(args.config, args.preset, chart_overrides(args), args.song)
    res = simulate.run(cfg, verbose=True)
    print("\nPad changes:")
    for t, what in res.log:
        print(f"  {clock(t)}  {what}")
    print(f"\nMIDI messages emitted: {len(res.port.sent)}; notes still sounding at end: {len(res.engine.out.sounding)}")
    return 0


def cmd_replay(args) -> int:
    from . import simulate

    from .recording import load_run

    cfg_path = args.config or ("config.toml" if Path("config.toml").exists() else None)
    for k, v in load_run(args.take).items():          # set up as the run was (unless you say)
        if k in RUN_ARGS and getattr(args, k, None) is None:
            setattr(args, k, v)
    cfg = cfgmod.build(cfg_path, args.preset, chart_overrides(args), args.song)
    # Times are the take's own clock (0 = first note or key press), as shown live in the status line.
    onsets = load_take(args.take)
    actions = load_actions(args.take)
    print(f"Replaying {args.take}: {len(onsets)} notes over {onsets[-1][0]:.1f}s "
          f"(config: {cfg_path or 'defaults'}{', preset: ' + cfg.preset if cfg.preset else ''})\n")
    res = simulate.run(cfg, verbose=True, onsets=onsets, actions=actions)
    active = [b for (t, b, c) in res.tempo_trace if onsets[0][0] <= t <= onsets[-1][0]]
    print("\nPad changes and groove lock:")
    for t, what in res.log:
        print(f"  {clock(t)}  {what}")
    if actions:
        print("Your key presses / controller actions (replayed): "
              + ", ".join(f"{a} at {clock(t)}" for t, a in actions))
    if active:
        print(f"\nTempo while you played: min {min(active):.1f}, max {max(active):.1f}, "
              f"final {active[-1]:.1f} bpm")
    m = re.search(r"(\d+(?:\.\d+)?)bpm", Path(args.take).stem)
    if m and "to" not in Path(args.take).stem:  # e.g. melody-90bpm.jsonl: the true tempo is known
        expected = float(m.group(1))
        start = onsets[0][0]
        late = [(t, b) for (t, b, _) in res.tempo_trace if start + 30 <= t <= onsets[-1][0]]
        if late:
            worst = max(abs(b / expected - 1) for _, b in late)
            print(f"Expected {expected:g} bpm (from the file name). From 30 s of playing on: "
                  f"worst error {worst:.1%} {'(OK, within 4%)' if worst <= 0.04 else '(outside 4%)'}")
    if res.engine.locked:
        print("Still LOCKED at the end: live, pad and pulse would keep playing until you "
              "unlock ([l], a controller, or panic).")
    if res.engine.chord_held:
        print(f"Chord still HELD at the end ({res.engine.frozen.label()}).")
    print(f"Notes still sounding at end: {len(res.engine.out.sounding)}")
    return 0


def cmd_params(args) -> int:
    if args.json:
        from . import patterns
        print(json.dumps({"params": registry.schema(), "actions": list(cfgmod.ACTIONS),
                          "presets": cfgmod.available_presets(), "songs": cfgmod.available_songs(),
                          "drum_patterns": patterns.available()}, indent=2))
        return 0
    group = None
    for p in registry.PARAMS:
        if p.deprecated or (args.primary and not p.primary):
            continue
        if p.group != group:
            group = p.group
            print(f"\n{group}")
        rng = f"{p.min:g}-{p.max:g}" if p.min is not None else (
            "|".join(map(str, p.choices)) if p.choices else p.type.__name__)
        flag = "" if p.live else "  (restart to change)"
        print(f"  {p.key:<26} {str(p.default):<12} {rng:<14} {p.help}{flag}")
    print(f"\nActions (keys, [controls] CCs): {', '.join(cfgmod.ACTIONS)}")
    print(f"Presets (--preset NAME): {', '.join(cfgmod.available_presets()) or '(none)'}")
    print(f"Songs (--song NAME): {', '.join(cfgmod.available_songs()) or '(none)'}")
    from . import patterns
    print(f"Drum patterns (drums.pattern): {', '.join(patterns.available())}")
    return 0


def home_note() -> Path:
    """Where the path of your accompanist folder is kept (looked up each time: HOME may move)."""
    return Path("~/.accompanist/home").expanduser()
PATH_ARGS = ("take", "paths", "record_audio", "record", "wav", "file")


def find_home(args) -> None:
    """Your accompanist folder is the one with your config.toml. Run from there and it is
    remembered; run from anywhere else (another folder, a new terminal) and the accompanist
    goes there first, so songs/, takes/ and the config are found. Paths you typed still mean
    what they meant where you typed them."""
    here = Path.cwd()
    if Path("config.toml").exists():
        try:
            note = home_note()
            if not note.exists() or note.read_text().strip() != str(here):
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(str(here))
        except OSError:
            pass
        return
    if getattr(args, "config", "config.toml") not in ("config.toml", None):
        return                                   # -c given: as typed
    try:
        home = Path(home_note().read_text().strip())
    except OSError:
        return
    if not (home / "config.toml").is_file() or home == here:
        return
    for name in PATH_ARGS:                       # what you typed, from where you typed it
        v = getattr(args, name, None)
        if isinstance(v, str) and v != "auto":
            setattr(args, name, str((here / Path(v).expanduser()).resolve()))
        elif isinstance(v, list):
            setattr(args, name, [str((here / Path(p).expanduser()).resolve()) for p in v])
    os.chdir(home)
    if args.cmd != "complete":
        print(f"(using your accompanist folder, {home})", file=sys.stderr)


ZSH_COMPLETION = r"""
# accompanist: Tab completes commands, options, and the names and choices an option takes
# (songs, sets, presets, styles, starts, finishes...). Everything is asked of the
# accompanist when you press Tab, so new commands and options never need a new shell.
# Load with:  eval "$(accompanist completion)"  (in ~/.zshrc)
_accompanist() {
  local -a values
  if (( CURRENT == 2 )); then
    compadd -- ${(f)"$(accompanist complete commands 2>/dev/null)"}
  elif [[ ${words[CURRENT]} == -* ]]; then
    compadd -- ${(f)"$(accompanist complete options ${words[2]} 2>/dev/null)"}
  else
    values=(${(f)"$(accompanist complete value ${words[2]} -- ${words[CURRENT-1]} 2>/dev/null)"})
    if [[ ${values[1]} == __files__ ]]; then
      _files
    elif (( ${#values} )); then
      compadd -- $values
    else
      _files
    fi
  fi
}
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit -i }
compdef _accompanist accompanist
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="accompanist", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list MIDI ports and audio inputs")
    for name, helptext in (("monitor", "show what the configured inputs hear (MIDI, and notes in audio)"),
                           ("run", "listen and accompany, live")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("-c", "--config", default="config.toml")
        if name == "monitor":
            sp.add_argument("--record-audio", default=None, metavar="FILE.wav",
                            help="save the (first) audio input to a WAV file")
            sp.add_argument("--input", default=None, metavar="NAME",
                            help="only this input (by its name in config.toml, e.g. steel)")
            sp.add_argument("--preset", default=None, help="e.g. voice: hear a voice or a slide "
                                                           "as held notes, not every glide")
        if name == "run":
            sp.add_argument("--preset", default=None, help=PRESET_HELP)
            sp.add_argument("--key", default=None, metavar="C",
                            help="the tonic (or the rāga's Sa): C, F#, Bb; with --mode, or in one: "
                                 "\"C sahana\"; several, comma-separated, to move between "
                                 "(\"F lydian, D minor\")")
            sp.add_argument("--mode", default=None, metavar="MODE",
                            help="a mode (major, minor, dorian, lydian, mixolydian, "
                                 "minor-pentatonic, blues...) or a rāga (sahana, kalyani, "
                                 "mohanam, bhairavi...): --key C --mode sahana")
            sp.add_argument("--time-sig", default=None, metavar="METER",
                            help="a time signature (3/4, 4/4, 5/4, 6/8, 7/8) or a tāla "
                                 "(adi, rupakam, misra-chapu, khanda-chapu, tintal, rupak, "
                                 "jhaptal...): the band's bars")
            add_chart_args(sp)
            sp.add_argument("--record-audio", default=None, metavar="FILE.wav",
                            help="also save the (first) audio input to a WAV file")
            sp.add_argument("--record", nargs="?", const="auto", default=None, metavar="FILE",
                            help="save your notes as a take (default: takes/take-<time>.jsonl, "
                                 "or takes/<song>-<time>.jsonl with --song)")
            sp.add_argument("--no-record", action="store_true",
                            help="don't record this run, even with [output] record = true")
            sp.add_argument("--no-ui", action="store_true",
                            help="no stage screen in the browser this time (ui.enabled)")
    sp = sub.add_parser("replay", help="run a recorded take through the engine offline (no hardware)")
    sp.add_argument("take", help="a take file made with `run --record`")
    sp.add_argument("-c", "--config", default=None, help="default: ./config.toml if present, else defaults")
    sp.add_argument("--preset", default=None, help=PRESET_HELP)
    add_chart_args(sp)
    sp = sub.add_parser("simulate", help="dry-run against a scripted performance (no hardware)")
    sp.add_argument("-c", "--config", default=None)
    sp.add_argument("--preset", default=None, help=PRESET_HELP)
    add_chart_args(sp)
    sp = sub.add_parser("library", help="your phrase library: add takes and recordings, or stats")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("action", choices=("add", "stats", "drums", "ragas"))
    sp.add_argument("paths", nargs="*", help="add: takes (.jsonl), recordings (.wav), or folders;"
                                             " drums: the Groove MIDI Dataset folder; ragas: the"
                                             " RagaDataset folder (Raga Recognition Dataset)")
    sp.add_argument("--tag", action="append", default=[],
                    help="label these phrases (e.g. --tag class --tag alap); repeatable")
    sp = sub.add_parser("completion", help="Tab completion for zsh: add "
                        "eval \"$(accompanist completion)\" to ~/.zshrc")
    sp = sub.add_parser("complete")             # (for the completion script: names, one a line)
    sp.add_argument("what", choices=("songs", "sets", "presets", "styles", "commands", "options",
                                     "value"))
    sp.add_argument("command", nargs="?", default=None)
    sp.add_argument("option", nargs="?", default=None)
    sp = sub.add_parser("reckoner", help="how each song starts and finishes (a set, a song, "
                                          "or all songs): settle it before the show")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("--set", default=None, help="a set list (sets/NAME.toml), in order")
    sp.add_argument("--song", default=None, help="one song")
    sp = sub.add_parser("rehearse", help="hear a song's start and finish, the band alone; try "
                                          "others and keep the pair you like in a set")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("--song", required=True, help="the song (songs/NAME.toml)")
    sp.add_argument("--set", default=None, help="the set whose plan to hear and keep it in")
    sp.add_argument("--bars", type=int, default=None, help="intro / drums start: how many bars")
    sp.add_argument("--transpose", type=int, default=None, metavar="N",
                    help="in another key: N semitones up (+) or down (-)")
    sp.add_argument("--style", default=None, help="a style pack (styles/NAME.toml): jazz-ballad, "
                                                  "swing, latin, pop, fusion")
    sp.add_argument("--once", action="store_true", help="play it once, no menu")
    sp.add_argument("--start", default=None, choices=("count", "intro", "drums", "you", "drone"),
                    help="try another start than the song's")
    sp.add_argument("--finish", default=None,
                    choices=("chord", "button", "tag", "ritardando", "piano-tag", "random"),
                    help="try another finish than the song's")
    sp = sub.add_parser("practice", help="listen while you practise: your phrases go into the "
                                         "library (notes only; speech left out)")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("--tag", action="append", default=[], help="label them (e.g. --tag class)")
    sp.add_argument("--preset", default=None, help="e.g. voice, when you sing")
    sp.add_argument("--sruti", default=None, metavar="NOTE",
                    help="the session's Sa, e.g. C or F# (else found from the singing)")
    sp = sub.add_parser("muse", help="connect to a Muse headband and show heartbeats and "
                                     "gestures live")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("address", nargs="?", default=None,
                    help="its address (from `OpenMuse find`; default: the one in config.toml)")
    sp.add_argument("--raw", default=None, metavar="FILE",
                    help="also save the raw data (OpenMuse's format), to replay and tune")
    sp = sub.add_parser("kitmap", help="play a voice's notes one by one, named, to find "
                                       "where a kit keeps its sounds")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("voice", choices=("pad", "bass", "drums", "percussion", "piano", "guitar"))
    sp.add_argument("--song", default=None, metavar="NAME", help="switch to this song's patch first")
    sp.add_argument("--low", type=int, default=35, help="first note (default 35)")
    sp.add_argument("--high", type=int, default=81, help="last note (default 81)")
    sp.add_argument("--make", default=None, metavar="NAME",
                    help="name each sound as it plays and save them as kits/NAME.toml "
                         "(mridangam, tabla, khanjira...: a percussion kit for the tālas)")
    sp = sub.add_parser("levels", help="line check: each voice alone, then you; sets each "
                                       "voice's level under you (written into the song)")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("--song", default=None, metavar="NAME", help="the song (its patch and sounds)")
    sp.add_argument("--file", default=None, metavar="WAV",
                    help="measure this recording of an earlier line check instead of playing")
    sp.add_argument("--write", action="store_true", help="write the trims without asking")
    sp = sub.add_parser("patch", help="send one program change (test MainStage patch switching)")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("number", type=int, help="the patch's Program Change number as MainStage "
                                             "shows it, 1-128")
    sp = sub.add_parser("recorder", help="press the recorder switch once (to map it in "
                                         "MainStage, or to test it)")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("--cc", type=int, default=119,
                    help="the CC to send if [output] recorder_cc isn't set yet (default 119)")
    sp = sub.add_parser("soundcheck", help="play a few notes on each voice's channel: is every "
                                           "instrument in MainStage/Logic making sound?")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("voice", nargs="?", default=None,
                    help="just one voice: pad, bass, drums, percussion, piano or guitar")
    sp.add_argument("--loop", action="store_true",
                    help="keep repeating until Ctrl-C, while you fix MainStage")
    sp = sub.add_parser("check", help="before a gig: does the config and every song load, "
                                      "is every device plugged in?")
    sp.add_argument("-c", "--config", default="config.toml")
    sp.add_argument("songs", nargs="*", help="songs to check (default: all of them)")
    sp.add_argument("--set", default=None, metavar="NAME", help="check a set list's songs")
    sp = sub.add_parser("learn", help="press each pedal/controller switch when asked: writes [controls]")
    sp.add_argument("-c", "--config", default="config.toml")
    sp = sub.add_parser("listen", help="run an audio recording (WAV) through the note detector, offline")
    sp.add_argument("audio", help="a WAV file, e.g. saved by `monitor --record-audio`")
    sp.add_argument("--channel", type=int, default=1, help="which channel of the file (default 1)")
    sp.add_argument("-c", "--config", default=None, help="default: ./config.toml if present, else defaults")
    sp.add_argument("--save-take", default=None, metavar="FILE.jsonl",
                    help="also save the notes heard as a take, for `replay`")
    sp = sub.add_parser("params", help="list every setting (with --json: the schema a UI is built from)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--primary", action="store_true",
                    help="only the few controls a simple interface shows (the feel knobs)")
    args = p.parse_args(argv)
    if args.cmd == "completion":
        print(ZSH_COMPLETION.strip())
        return 0
    if args.cmd == "complete":
        find_home(args)
        if args.what == "commands":
            names = [n for n in sub.choices if n not in ("complete", "completion")]
        elif args.what == "options":
            sp = sub.choices.get(args.command)
            names = [o for a in (sp._actions if sp else []) for o in a.option_strings
                     if o.startswith("--")]
        elif args.what == "value":               # what may follow this option
            sp = sub.choices.get(args.command)
            action = next((a for a in (sp._actions if sp else [])
                           if args.option in a.option_strings), None)
            def modes():
                from . import indian
                from .modal import MODES
                return list(MODES) + sorted(indian.ragas())      # every name and spelling

            def meters():
                from . import indian
                return list(METERS) + sorted(n for n, t in indian.talas().items() if n == t.name)

            named = {"song": cfgmod.available_songs, "set": cfgmod.available_sets,
                     "preset": cfgmod.available_presets, "style": cfgmod.available_styles,
                     "mode": modes, "time_sig": meters}
            if action is None and args.option == args.command:   # right after the command:
                names = [str(ch) for a in (sp._actions if sp else [])   # its first word's choices
                         if not a.option_strings and a.choices for ch in a.choices] or ["__files__"]
            elif action is None or action.nargs == 0:
                names = ["__files__"]            # not an option that takes a value
            elif action.choices:
                names = [str(c) for c in action.choices]
            elif action.dest in named:
                names = named[action.dest]()
            else:
                names = ["__files__"]
        else:
            names = {"songs": cfgmod.available_songs, "sets": cfgmod.available_sets,
                     "presets": cfgmod.available_presets,
                     "styles": cfgmod.available_styles}[args.what]()
        print("\n".join(names))
        return 0
    try:
        find_home(args)
        for name, names, what in (("song", cfgmod.available_songs, "song"),
                                  ("set", cfgmod.available_sets, "set"),
                                  ("preset", cfgmod.available_presets, "preset"),
                                  ("style", cfgmod.available_styles, "style")):
            v = getattr(args, name, None)              # part of a name is enough
            if isinstance(v, str) and v:
                setattr(args, name, cfgmod.resolve_name(v, names(), what))
        return {"devices": cmd_devices, "monitor": cmd_monitor, "run": cmd_run,
                "replay": cmd_replay, "simulate": cmd_simulate, "params": cmd_params,
                "listen": cmd_listen, "learn": cmd_learn,
                "check": cmd_check, "soundcheck": cmd_soundcheck,
                "recorder": cmd_recorder, "patch": cmd_patch,
                "levels": cmd_levels, "kitmap": cmd_kitmap, "library": cmd_library,
                "practice": cmd_practice, "muse": cmd_muse, "reckoner": cmd_reckoner,
                "rehearse": cmd_rehearse}[args.cmd](args)
    except (cfgmod.ConfigError, PortError, TakeError, AudioError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
