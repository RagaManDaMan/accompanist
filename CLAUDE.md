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

## Not yet built (do not start without being asked)
Audio input path (voice, flute/sax, lap steel pitch tracking); raga-aware responder;
environmental-texture layer; licence choice (undecided).

## Known unknowns (unverified on real hardware)
Virtual MIDI port visibility in Logic; per-channel routing of pad vs pulse to separate tracks;
behaviour with a real EWI. Report findings back rather than assuming.
