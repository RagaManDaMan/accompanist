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
**l** = lock / unlock the tempo, **c** = hold / release the chord, **t** = count off (see
below), **s** = chart: count in and play from the top, **q** = quit. Every key press prints what it did. Keys are commands, not music: apart from
panic and a chart count-in, a key press never moves the beat.
On exit, and on Ctrl-C, it always sends All Notes Off.

## Four knobs: algorithmic to humanize

Each voice has one knob, `pad.feel`, `pulse.feel` (bass), `drums.feel`, `response.feel`,
from 0 (algorithmic: strict, even, on the grid, predictable) to 1 (humanize: varied,
breathing with you, a little loose). Each knob sets a group of detailed settings together;
at its default it reproduces the detailed defaults exactly. `accompanist params --primary`
shows just these (and each voice's on/off), which is all a simple interface needs. Put them
on a MIDI controller's knobs:

```toml
[controls]
21 = "pad.feel"
22 = "pulse.feel"
23 = "drums.feel"
24 = "response.feel"
```

A detailed setting you give in `config.toml` wins over its knob at start-up; turning the knob
live takes over everything it controls.

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
if confidence stays below `pulse.stop_confidence` for `pulse.stop_after_s` (8 s), or after
`pulse.idle_stop_s` of silence. A dip, or pressing **l** to unlock, never stops it.

## Playing with a chord chart

```bash
accompanist run --chart charts/Misty.musicxml --tempo 64   # --transpose -2 etc. changes key
```

Export a tune from iReal Pro as **MusicXML** (other MusicXML lead sheets work too; `.mxl`
as well) and pass it with `--chart`, or set `[harmony] model = "chart"` and
`chart = "charts/NAME.musicxml"`. The pad and bass then play the chart's chords, exactly on
the beat, following repeats and 1st/2nd endings and looping the form (segno/coda/D.C.
markings are reported and ignored for now).

- **Start the song:** press **s**: one bar of count-in clicks (side stick on the drums
  channel), then the band plays from bar 1 with the tempo locked, like pressing play in
  iReal Pro. The tempo is `--tempo N` (or `[harmony] chart_bpm`), else the tempo you have
  just been playing if it is clear, else a typical tempo for the chart's style (iReal Pro
  exports the style, e.g. Ballad = 60, but not the tempo). Or tap **t** four times: the taps
  are the count-in. Until you start, the band stays silent.
- **While it plays** the band never stops by itself: **l** only switches between keeping
  the tempo (locked) and following yours (unlocked). **s** counts in again from the top;
  panic stops the song. **c** holds a chord, as usual. The status line shows
  `bar 5/32 [A] beat 1`.
- **Your melody colours the chords:** play one of a chord's colour tones (9, #11, 13 on
  major chords; b9, 9, #9, #11, b13, 13 on dominants; 9, 11, 13 on minor chords) while it
  lasts and the pad adds it from the next beat until the chord changes (`Fmaj7` becomes
  `Fmaj7(#11)`). Notes that clash are ignored; the chart's root and quality never change.
  `harmony.melody_min_notes`, `melody_max_tensions`, or `melody_colors = false` to turn it off.
- `charts/` is ignored by git, like `takes/`: your charts stay on your machine.

## Counting off

Count the band in on **t**, like a bandleader: the taps set the tempo, and how many you tap
sets the meter:

| taps | groove | accents | drums (if yours doesn't fit) |
|---|---|---|---|
| 3 | waltz, 3/4 | 1 | `waltz` |
| 4 | four on the floor | 1 | `basic` |
| 5 | 5/4, 3+2 | 1, lighter 4 | `five` |
| 6 | 6/8 or up-tempo, 3+3 | 1, lighter 4 | `six-eight` |
| 7 | 3+2+2 | 1, lighter 4 and 6 | `seven-322` |

The count is over when the next tap does not come; the band then comes in on 1 (about 15% of
a beat late, `groove.count_wait`: it cannot know your count has ended any sooner) with the
tempo locked (**l** to let it follow you). Count again, any time, to change tempo and meter.
The counted meter stays until you count again; listening still follows your swing. With a
chart, one bar of taps is the count-in and the band comes in exactly on 1.

## Groove: meter, downbeat, feel

Once the beat is running, the accompanist listens for the **meter** (in 3 or in 4; a 2/4
march counts as "in 4"), **where 1 is**, and **straight vs swing** (and how much swing).
It judges the last `groove.window_beats` beats (24: six bars of 4 or eight of 3), commits to
what it hears, and only changes when a different groove keeps fitting clearly better
(`groove.switch_margin` for `groove.hold_s`). Swing is measured against where your own
on-beat notes fall, so a pulse sitting slightly ahead of you does not make straight eighths
look swung. Then:

- bass accents, the pad's bar changes and the drum cycle follow the bar it heard, with a
  firm accent on 1 (`groove.downbeat_accent`) once it is sure;
- the drums keep your pattern if it fits the meter, else switch (e.g. `waltz` in 3, `swing`
  when you swing in 4), and swing as much as you do (`groove.auto_drums`);
- the status line shows it, e.g. `3/4 swing` (a `?` while it is still unsure).

New patterns: `swing` (ride "spang-a-lang", hi-hat on 2 and 4) and `march` (2/4). Turn it
off with `[groove] auto = false` (then `pulse.beats_per_bar` and `drums.pattern` rule). With
a chart, the chart's meter rules.

## Groove library (experimental, not used live yet)

A library of signature grooves, learnt from open data, to recognise a groove from the last
bar or two of melody (`src/accompanist/grooves.py`). It is built on your machine, not
shipped (the datasets have their own licences):

```bash
mkdir -p datasets && cd datasets
curl -LO https://jazzomat.hfm-weimar.de/download/downloads/wjazzd.db        # Weimar Jazz Database (ODbL)
curl -L -o nottingham.zip https://github.com/jukedeck/nottingham-dataset/archive/refs/heads/master.zip
unzip -q nottingham.zip && cd ..                                             # Nottingham (GPL-3.0)
python tools/build_grooves.py      # -> grooves/library.json
python tools/eval_grooves.py       # how well it recognises tunes it never saw
```

Findings so far: on folk melodies it recognises jig, march and reel from one or two bars
(meter ~95-100%, 1 ~90%), far faster than listening alone; on jazz solos it is moderate.
On improvised sax over a counted groove it mostly fails (a solo line does not carry the
groove's signature; the band does), so counting off stays the way to set the groove.

## Call and response

`[response] enabled = true` adds a voice (channel 4, e.g. a guitar track) that answers you
with **your own phrases**. Every phrase you play is remembered (the last `response.memory`);
when you pause briefly after a phrase, it plays back an earlier one whose notes fit the chord
and key of the moment (`response.fit`), as you played it: your notes, your register, one note
at a time. When the beat is running it comes in on the next beat with the rhythm rounded to
eighth notes. If nothing fits yet, it echoes the phrase you just played.

Dials, shy to bold:
- `response.chance`: share of your pauses that get an answer.
- `response.gap_beats`: how long a pause it waits for.
- `response.yield_to_you` (0-1): when you play during an answer, 1 = it stops at once, 0 =
  it finishes over you, 0.5 = it keeps half of what is left, softer.
- `response.octave`: unset = your register; a number = a fixed register.
- `response.variety` (0-1): now and then play a variation of your last phrase instead.

Any of them can go on a knob with `[controls]`, e.g. `21 = "response.yield_to_you"`.

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

## Audio input (sax first)

The accompanist hears notes in audio from an interface (voice and sax are how most gigs
happen), and they play it exactly as a MIDI keyboard does: `accompanist run` with an audio
input follows your tempo, harmony and dynamics, or plays a chart with you. Check first that it
hears you (`monitor`), and set `[audio] gate_db` a few dB above your room's silence.

```toml
[[inputs]]
name = "sax"
audio = "Scarlett Solo"   # part of the audio interface's name (`accompanist devices`)
audio_channel = 1         # which input of the interface
```

```bash
accompanist run --record takes/gig-1.jsonl --record-audio takes/gig-1.wav   # play with it
accompanist monitor --record-audio takes/sax-1.wav   # live: notes as heard, a level meter
accompanist listen takes/sax-1.wav                   # offline: the same, from a recording
accompanist listen takes/sax-1.wav --save-take takes/sax-1.jsonl   # ...then `replay` it
```

It hears pitch with YIN, and a new note on a change of pitch (legato) or a fresh attack
on the same pitch (tonguing). Notes are reported at their real start, and a note whose attack briefly reads an octave off
(common on sax) is corrected rather than counted twice. Note names are concert pitch (a
tenor's written C is a concert Bb). The `[audio]` settings
(`accompanist params`) tune it: `gate_db` (what counts as silence; watch the meter),
`min_note_ms`, `attack_db`, `cents_tolerance`.

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
- **Pulse** (`beatclock.py`): free-runs at the estimated beat, easing into every tempo or
  phase correction (`pulse.max_tempo_step`, `pulse.max_nudge` per beat) so it never lurches. It starts on the phase your
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
