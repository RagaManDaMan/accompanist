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

Keys while running: **space / p** = panic (silence and mute), **r** = resume,
**l** = lock / unlock the tempo, **c** = hold / release the chord, **t** = tap tempo
(4 taps set the tempo and its octave; with a chart they are the count-in), **s** = chart
from the top, **q** = quit. Every key press prints what it did. Keys are commands, not music: apart from
panic and a chart count-in, a key press never moves the beat.
On exit, and on Ctrl-C, it always sends All Notes Off.

## Settings, presets, controllers

- `accompanist params` lists every setting with its range and meaning;
  `accompanist params --json` prints the same as a schema (for building a UI).
- Settings are layered, later wins: built-in defaults < preset < `config.toml` < live
  changes (MIDI controllers now, a UI later).
- **Presets** are partial configs: `accompanist run --preset ambient`, or
  `preset = "ambient"` at the top of `config.toml`. Built in: `ambient` (slow wide pad,
  no pulse) and `modal-drone` (a steady drone that barely moves, quiet pulse). Put your
  own in `./presets/NAME.toml`; they may set anything except ports and inputs.
- **[controls]** maps MIDI controllers to actions (`panic`, `resume`, `lock`, `unlock`,
  `chord_hold`, `chord_release`, `chord_toggle`, `tap_tempo`) or to
  any live setting (`7 = "pad.velocity"`). See `config.example.toml`.

## Tempo lock and chord hold

Two separate locks, so the beat can stay put while the harmony keeps moving:

- **Tempo lock** (**l** toggles; `lock` / `unlock` / `lock_toggle` controllers; automatic once the tempo has been clear,
  confidence >= `lock.confidence`, for `lock.after_s`, unless `[lock] auto = false`): pad and
  pulse keep going through silence, and the tempo follows you only slowly
  (`lock.tempo_rate`), never jumping to another tempo or octave. Chords still follow you.
  Only **l**, an `unlock` / `lock_toggle` controller or panic ends it; stopping playing never does.
- **Chord hold** (**c** toggles; `chord_hold` / `chord_release` / `chord_toggle` controllers):
  the pad (and the pulse's note) stay on the current chord, whatever you play, until you
  release it or panic.

The status line shows `LOCKED` and `CHORD HELD`.

Unlocked, the pulse starts once confidence reaches `pulse.min_confidence` and stops only
if it falls below `pulse.stop_confidence` (or after `pulse.idle_stop_s` of silence).

## Playing with a chord chart

```bash
accompanist run --chart charts/Misty.musicxml          # add --transpose -2 etc. to change key
```

Export a tune from iReal Pro as **MusicXML** (other MusicXML lead sheets work too; `.mxl`
as well) and pass it with `--chart`, or set `[harmony] model = "chart"` and
`chart = "charts/NAME.musicxml"`. The pad and bass then play the chart's chords, exactly on
the beat, following repeats and 1st/2nd endings and looping the form (segno/coda/D.C.
markings are reported and ignored for now).

- **Count in:** tap **t** four times ("1 2 3 4"): the chart starts at bar 1 on the next beat,
  and the tempo locks so the band keeps going before you play. Or just start playing: the
  chart starts when the beat is found. **s** restarts from the top on the next beat.
- Your playing sets and steers the tempo (unless locked); the pad still varies its voicings;
  **c** holds a chord, as usual. The status line shows `bar 5/32 [A] beat 1`.
- `charts/` is ignored by git, like `takes/`: your charts stay on your machine.

## Dynamics

The pad's level rides on MIDI Expression (CC11, `pad.expression_cc`) so a held chord can
swell and fade: it follows how loudly you play (`dynamics.follow`) and steps back while you
play busily (`dynamics.duck`, never below `dynamics.pad_floor`), coming forward again when
you pause. The bass (pulse) velocity follows your loudness too, but never ducks. Most Logic
instruments respond to CC11; if yours doesn't, set `expression_cc = 7` (volume).

## Drums

`[drums] enabled = true` plays a General MIDI drum pattern on channel 10 (put a Logic
drum kit on a track that listens to channel 10). `drums.pattern` picks one of the
patterns (`accompanist params` lists them): `basic`, `halftime`, `soft`, `sparse`,
`waltz`, and `seven-322` (7 beats grouped 3+2+2). Drums follow the same beat as the bass,
start when it starts, follow your loudness, and stop on panic. `drums.swing` swings the
off-steps.

A pattern is a small text file, so new grooves need no code. Put your own in
`./patterns/NAME.toml`:

```toml
description = "7 beats grouped 3+2+2"
beats = 7                 # the cycle: any length
steps_per_beat = 2        # subdivisions of each beat
[hits]                    # one step per character: X accent, x hit, g ghost, . rest
kick   = "X. .. .. | x. .. | x. .."
shaker = "xg xg xg | xg xg | xg xg"
```

The cycle restarts when the beat starts (there is no downbeat detection yet).

## Tuning against your own playing

`accompanist run --record` saves every note you play to `takes/take-<time>.jsonl`.
`accompanist replay takes/<file>` feeds it through the engine offline and prints the
tempo it inferred and when the pad changed, so you can change `config.toml` and rerun
in seconds without playing again. If the file name contains the true tempo (e.g.
`takes/melody-90bpm.jsonl`), replay also reports how far off the estimate was from
30 s of playing on. Takes are gitignored by default; copy a short useful
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

- **Tempo** (`tempo.py`): about once a second, the last `window_s` (30 s) of onsets
  (older ones weighted down, half-life `halflife_s`) are smoothed and autocorrelated.
  Each candidate bpm is scored on how well the playing repeats at 1, 2, 3 and 4 beats,
  times a gentle prior toward `prior_bpm`. The result is smoothed, and a different
  tempo peak must clearly win for a few seconds before the estimate jumps to it.
  Silence holds the tempo. No fixed grid, and no need to start near the right tempo.
- **Pulse** (`beatclock.py`): free-runs at the estimated beat. It starts on the phase your
  recent notes fit best (most notes fall on beats), and is realigned if it is clearly off;
  onsets within `hint_window` (15%) of a predicted beat pull its phase toward you (a small
  phase-locked loop). While locked, the tempo is refined by fitting a beat grid to your notes.
- **Harmony** (`harmony.py`, `modal.py`): a plug-in (`observe(onset)`, `propose(now) -> voicing`),
  chosen by `harmony.model`:
  - `drone`: a decaying pitch-class memory picks a root; the third is only added if you've
    played it, otherwise an open root-fifth-octave voicing.
  - `modal`: chords from a key. `harmony.root` is the tonic and `harmony.mode` is `major`,
    `minor` (with the raised 7th), `chromatic` (any chord) or `auto` (major/minor detected
    from your playing). Each moment it picks the chord of that key (5, triads, sus2/4, dim,
    aug, add9, 7ths, m7b5, dim7) that best fits the last few seconds you played;
    `harmony.color` (0-1) sets how readily it goes past plain triads. Mode, root and colour
    can change while running (a `[controls]` knob, later a UI).
- **Control** (`controller.py`): keys, MIDI controllers and any future UI go through one
  Controller (`set_param`, `get_state`, `panic`, `resume`, `tap_tempo`). The status line
  is just a formatting of `get_state()`.
- **Lag on purpose** (`responders.py`): a harmonic change must persist `lag_beats` before
  the pad follows, and changes are rate-limited. This is the "recall can be delayed" behaviour.
- **Safety** (`output.py`): every note goes through one place that knows what is
  sounding, so panic can silence exactly that, plus CC 123/120 on all channels.

## Known limits

- **Beat octave is a prior.** A steady stream of notes is ambiguous (quarters at 60 or
  eighths at 120). `prior_bpm` decides: with the default 80, a melody at 120 bpm is read
  as 60 (half time), and a sudden doubling of your tempo looks like "more eighth notes".
  If you play fast pieces, tell it so:

  ```toml
  [tempo]
  prior_bpm = 110    # a 120 bpm melody now reads as 120, not 60
  ```

  The crossover sits near the geometric middle of the two readings (60 vs 120 flips
  around prior_bpm 85). `accompanist replay takes/melody-120bpm.jsonl` shows which you get.
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
