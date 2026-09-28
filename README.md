# accompanist (V0, working title)

A small, local, offline accompanist. It listens to you play, follows your tempo as it
breathes, remembers what you've been playing, and answers a beat or two later on a
soft pad and a tempo-locked pulse. It sends MIDI to Logic Pro (or any DAW), so the
sounds are whatever instruments you load.

Nothing is tied to particular gear: ports are matched by name, and the role of each
input is a setting. Status: **alpha, MIDI input only.**

## Quickstart (macOS)

```bash
cd accompanist
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                 # no hardware needed
accompanist simulate                   # watch it follow a scripted performance
cp config.example.toml config.toml
accompanist devices                    # find your MIDI input's name, put it in config.toml
accompanist monitor                    # confirm your instrument's notes arrive
accompanist run                        # add --record to save your playing as a take
accompanist replay takes/<file>        # run a take through the engine offline
```

If `pip install` fails building `python-rtmidi`, install Xcode command line tools
(`xcode-select --install`) and retry.

Keys while running: **space / p** = panic (silence and mute), **r** = resume, **q** = quit.
On exit, and on Ctrl-C, it always sends All Notes Off.

## Tuning against your own playing

`accompanist run --record` saves every note you play to `takes/take-<time>.jsonl`.
`accompanist replay takes/<file>` feeds it through the engine offline and prints the
tempo it inferred and when the pad changed, so you can change `config.toml` and rerun
in seconds without playing again. Takes are gitignored by default; copy a short useful
one into `tests/fixtures/` to keep it as a regression test.

## Logic Pro setup

1. Run `accompanist run`. It creates a virtual MIDI source called "Accompanist"
   (or use `port = "IAC Driver Bus 1"` in `[output]` after enabling the IAC bus in
   Audio MIDI Setup).
2. Create a software-instrument track (a pad patch), record-enable it. Play a note on
   your controller to check that it reaches the engine, and see the `heard` field change.
3. To hear the pulse on its own instrument, use a second track. Pad is on channel 1,
   pulse on channel 2. *Logic's per-channel routing across tracks needs checking on
   your setup* (I haven't verified it here). Simplest first test: set `[pulse] enabled = false`
   and use one track.

## How it works

```
 MIDI in ──► TempoEstimator ──► BeatClock ──► PulseResponder ─┐
        └──► PitchClassTracker ─► choose_voicing ─► PadResponder ─┴─► SafeOutput ─► Logic
```

- **Tempo** (`tempo.py`): each interval between your onsets is snapped to the nearest
  of 1/4, 1/2, 1, 2, 4 beats and nudges the estimate. Intervals that fit nothing are
  ignored (they lower confidence instead of corrupting the estimate). No fixed grid.
- **Pulse** (`beatclock.py`): free-runs at the estimated beat; long-interval onsets pull its
  phase toward you (a small phase-locked loop).
- **Harmony** (`harmony.py`): a decaying pitch-class memory picks a root; the third is
  only added if you've played it, otherwise you get an open root-fifth-octave voicing.
- **Lag on purpose** (`responders.py`): a harmonic change must persist `lag_beats` before
  the pad follows, and changes are rate-limited. This is the "recall can be delayed" behaviour.
- **Safety** (`output.py`): every note goes through one place that knows what is
  sounding, so panic can silence exactly that, plus CC 123/120 on all channels.

## Known limits

- **Beat octave is a prior.** A steady stream of notes is ambiguous (quarters at 60 or
  eighths at 120). `initial_bpm` and `min/max_bpm` decide.
- **No downbeat/meter detection.** The bar accent counts from when the pulse starts.
- **Pitch memory is Western-pitch-class based** (12 classes, no microtones). For a
  piece with a fixed tonic, set `harmony.root`.
- **MIDI input only.** Voice, flute/sax and lap steel need the audio path (below).
- Virtual-port and Logic-routing behaviour is untested on real hardware as of this version.

## Roadmap

1. Real-hardware shakedown of V0 (EWI in, Logic out); tune defaults from that.
2. Audio input path: one pitch/onset tracker per channel (`voice`, `pitch_contour`
   for continuously gliding pitch such as lap steel), feeding the same engine.
3. Pluggable responders (a raga/scale-aware module is just another responder).
4. Environmental-texture layer, pre-assigned per section.

## Licence

Not chosen yet.
