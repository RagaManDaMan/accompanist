# accompanist: project rules for Claude Code

A local, offline, listening MIDI accompanist for Logic Pro (and any DAW). It is meant
to be useful to other Logic users (the owner's students), not just one person's rig.

## Non-negotiables
1. **No device names in code.** Ports are matched by substring from `config.toml`. The role of
   each input (`note_source`, later `pitch_contour`, `voice`) is config, never a code branch.
2. **All MIDI goes out through `SafeOutput`** (`output.py`). It tracks what is sounding; that is
   what makes panic trustworthy. Never call a port's `send` directly from anywhere else.
3. **The engine is clock-agnostic.** It takes `now` as an argument. Do not call `time.*` inside
   `engine.py`, `tempo.py`, `beatclock.py`, `harmony.py` or `responders.py`. Live time enters
   only in `cli.py`.
4. **`pytest` must pass before any commit.** Commit after each green run, in small steps.
5. **Keep it generic.** Raga/scale logic will arrive later as a pluggable responder. Do not
   hard-wire it into the core.
6. Config errors must be readable messages (`ConfigError`), not tracebacks.
7. **Every tunable is declared once in a parameter registry** (key, type, min, max, step,
   choices, group, label, help, live). Config validation, CLI help and any future UI are
   generated from it. No magic numbers in engine code.
8. **All control goes through one Controller** (set_param, get_state, panic, resume, lock,
   unlock, tap_tempo). Keys, MIDI CCs and any future UI call it. No feature is CLI-only.
   get_state() returns a dict; the status line is only a formatter of it.
9. **Config layering:** defaults < preset (presets/*.toml) < config.toml < live overrides.
10. **Harmony is a plug-in interface** (observe, propose). Never hard-wire a style.

## Dev loop
- `pytest` (no hardware needed) and `accompanist simulate` for quick checks.
- **Tune against real playing:** `accompanist run --record` saves a take to `takes/`;
  `accompanist replay takes/<file>` runs it through the engine offline. Use this, not the
  scripted phrases, to judge tempo following and pad lag. The owner listens; you iterate on takes.
- When a take exposes a real failure, copy a short one to `tests/fixtures/` and add a test.

## Layout
`src/accompanist/`: `config` (schema/validation), `tempo` (onset-interval estimator),
`beatclock` (phase-locked pulse), `harmony` (pitch memory, voicing), `responders` (pad, pulse),
`engine` (glue), `output` (SafeOutput), `midi_io` (real ports), `recording` (takes),
`simulate` (virtual-time runner), `cli`.

## Audio input (started 2026-09-29, at the owner's request)
`audio_notes.py` (pure: YIN pitch + onsets, sax first) and `audio_io.py` (the only audio
hardware module). Step A (listening: `monitor`, `listen FILE.wav`) is hardware-tested on the owner's tenor sax;
step B feeds audio notes to the engine in `run` (AudioFeed). Build it one step at a time,
hardware-tested between steps. Tune against
recorded WAVs (`monitor --record-audio`, then `listen`), as with MIDI takes.

## Not yet built (do not start without being asked)
Voice and lap steel
(glides, gamakas: `pitch_contour`); call-and-response; raga-aware responder;
environmental-texture layer.

## Licence
GPL-3.0-or-later (LICENSE). All code stays open source. Datasets (Weimar Jazz DB, ODbL;
Nottingham, GPL-3.0) are downloaded locally and gitignored, never committed; credit anything
learnt from them where it is used.

## Known unknowns (unverified on real hardware)
Virtual MIDI port visibility in Logic; per-channel routing of pad vs pulse to separate tracks;
behaviour with a real EWI. Report findings back rather than assuming.
