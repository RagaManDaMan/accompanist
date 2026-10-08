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
                                       # ([output] record = true: every run, --no-record to skip)
accompanist replay takes/<file>        # run a take through the engine offline
```

If `pip install` fails building `python-rtmidi`, install Xcode command line tools
(`xcode-select --install`) and retry.

Keys while running: **space / p** = panic (silence and mute), **r** = resume,
**l** = lock / unlock the tempo, **c** = hold / release the chord, **t** = count off (see
below), **s** = chart: count in and play from the top, **f** = finish (a last chord on the next 1), **q** = quit. Every key press prints what it did. Keys are commands, not music: apart from
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

## Setting up a foot pedal or controller

Plug it in (USB), then:

```bash
accompanist monitor          # optional: see what it sends
accompanist learn            # press each switch when asked (twice), Enter to skip
```

`learn` asks for count-off, lock, chord hold, panic, resume and chart restart, then for an
expression pedal (which feel knob it should turn). It works out what each switch sends
(CC, program change or note; momentary or toggling), whether your pedal's bank switches
shift its numbers, adds the pedal as a `role = "control"` input, and writes `[controls]`
into `config.toml` (keeping `config.toml.bak`). No need to program the pedal itself.

**A small pedal: tap and hold.** With 4 switches, each can do two things: a quick tap, and a
hold (`pedal.hold_s`, 0.6 s). `learn` offers this layout:

| Switch | Tap | Hold |
|---|---|---|
| 1 | count off (3-7 taps) | start the song |
| 2 | lock / unlock the tempo | hold / release the chord |
| 3 | finish | break |
| 4 | panic / resume | |

In `[controls]` it reads `"cc:80" = { tap = "tap_tempo", hold = "song_start" }`. The switches
must send momentary CCs (a value on the press, 0 on the release) or notes: a program change
can't say how long it was held. Count-off taps count the moment the switch goes down (their
timing is the tempo); other taps act on the release.

## Settings, presets, controllers

- `accompanist params` lists every setting with its range and meaning;
  `accompanist params --json` prints the same as a schema (for building a UI).
- Settings are layered, later wins: built-in defaults < preset < `config.toml` < live
  changes (MIDI controllers now, a UI later).
- **Presets** are partial configs: `accompanist run --preset ambient`, or
  `preset = "ambient"` at the top of `config.toml`. Built in: `ambient` (slow wide pad,
  no pulse) and `modal-drone` (a steady drone that barely moves, quiet pulse). Put your
  own in `./presets/NAME.toml`; they may set anything except ports and inputs.
- **Pedals and controllers:** `[controls]` keys are a CC number (`64`), or `"cc:64"`,
  `"pc:0"` (program change) or `"note:36"`. A switch that toggles 127/0 on each press:
  `"cc:80" = { action = "lock_toggle", latching = true }`. A pedal whose bank switches shift
  its program numbers (e.g. Blackstar Live Logic: banks of 4): `program_bank = 4`, so a switch
  means the same in every bank. Give a pedal its own input with `role = "control"`: its
  notes are then commands, never music. `accompanist learn` writes all this for you.
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

## Songs: a setup per song

A set can't have every song sounding alike. A song file, `songs/NAME.toml`, holds what makes
one song itself: `[song]` title, `tempo` and `count` (meter: 3, 4, 5, 6, 7), plus any settings
(harmony model and key, a chart, drum pattern, which voices play, the feel knobs...).

```bash
accompanist run --song example-waltz        # then s (or a pedal: song_start) to count in
accompanist replay takes/misty-3.jsonl --song misty   # re-hear a take with a song's setup
```

Layering, later wins: defaults < preset < `config.toml` < song < command line. So
`config.toml` holds your rig and general taste; the song only what differs. A song can't set
ports, inputs or controls. A chart path in a song may be relative to the song file.

**A set list** puts songs in order for a gig, in `sets/NAME.toml`:

```toml
title = "Temple gig"
songs = ["waltz", "seven", "tamil-standard"]
```

`accompanist run --set NAME` loads and checks every song before the first note (as does
`accompanist check --set NAME` at soundcheck), starts on song 1, and **]** or the right arrow
moves to the next song, **[** or the left arrow back (or a pedal: `song_next`, `song_prev`).
Each song brings its own setup (and, with `[song] patch = N`, the patch numbered N in MainStage, sends its program change on
channel 16, `output.patch_channel`, so MainStage switches to the song's patch); then **s**
counts it in. One run plays the whole set, and each
song gets its own take file. While the band is playing, **q**, **[** and **]** need a second
press within 2 s, so a slip of the finger can't end the set or change the song mid-song; a
song change while the band still sounds (or the last chord still rings) fades every voice
out over a second first, on Expression (CC11), then restores it for the next song.

**s** (or `song_start`) counts the band in: one bar of clicks at the song's tempo and meter,
then drums and pulse come in with the tempo locked, and the pad once it has heard you. With a
chart, it plays from bar 1. See `src/accompanist/songs/` for two examples.

**b** (or `break`) is a break: on the next 1 the band hits (bass, kick, crash; `breaks.hit`)
and then stops for `breaks.bars` bars (2) so your line rings alone; the pad stays, recessed
(`breaks.pad_level`), and no one answers. The band comes back in on the 1 with a crash. **b**
again during a break ends it at the next 1.

**f** (or `finish`) ends it: the drums fill into the next 1 (`ending.fill`), where the band
plays one last chord, the key's tonic (else the chord of the moment): the pad, the bass's
root, a kick and a crash. It rings for `ending.ring_s` (4 s), fading out, and then everything
stops (FINISHED). **s** or a count-off starts again straight away; no resume needed.

How it ends is the song's choice, `[ending] shape`:
- `chord` (the default): as above, the last chord rings and fades;
- `button`: one short, tight hit on the last 1 (`ending.button_beats`);
- `tag`: the band plays `ending.tag_bars` (4) more bars, fills, then the last chord;
- `ritardando`: it slows over `ending.rit_bars` (2) bars to `ending.rit_to` (60%) of the
  tempo, into a held last chord;
- `piano-tag`: the band drops out for a bar of soft piano notes on the tonic chord ("plink,
  plink...") and comes back for the last chord ("...PLUNK");
- `random`: one of these each time.

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

## Keys and modes

A song can name its keys: `[harmony] keys = "F lydian, D minor, A minor"`. The band starts in
the first (home) from your first note and follows you between them, telling them apart by
the notes that differ (B natural, B-flat, G-sharp) and by where your lines rest (the tonic
and its triad). Modes: major, minor (with the raised 7th), ionian, dorian, phrygian,
lydian, mixolydian, aeolian, locrian, harmonic-minor, melodic-minor, bebop-major,
bebop-dominant, major-pentatonic, minor-pentatonic and blues. Without a palette the band
also hears the colour of the key by itself (`harmony.auto_modes`): the tonic and major or
minor from your playing, then whichever of the plain scale, dorian, mixolydian, lydian,
phrygian, harmonic minor, the pentatonics or the blues scale fits what you play best. One fixed key works too: `root = "F"`, `mode = "lydian"`. The ending lands on
the palette key you're in, or the one whose tonic you finished on.

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
you pause. On top of that the pad makes room (`dynamics.pad_space`, 0.7): it sits back
while anyone plays, you or the answer, and swells only once it has been quiet all round for
`dynamics.swell_after_s` (1.5 s), taking `dynamics.swell_s` (4 s) to reach full and
`dynamics.recede_s` (0.6 s) to sit back when someone plays again. The bass (pulse) velocity
follows your loudness too, but never ducks. Most Logic
instruments respond to CC11; if yours doesn't, set `expression_cc = 7` (volume).

The drums follow you harder (`drums.dynamics`, 0 = even): louder when you play louder, a
lift when you play busily and a drop back when you rest, softer off-beats, and phrases of
`drums.phrase_bars` bars (8) that build a little, may end in a short fill, and start the next
with a crash.

## Bass shapes

The bass plays the root on the 1 of each bar (sometimes an octave up) and whenever the chord
changes. Between, with `pulse.movement` above 0 (0.8), it plays shapes: fifths, thirds,
octaves, scale steps walking up or down, a step into the next bar, and now and then two
eighth notes stepping into the next beat, all in the key. Each shape repeats for
`pulse.shape_bars` bars (2) so it sounds like a line, not a dice roll, and the next one is
always a different shape, so a long chord doesn't loop one riff. Its rhythm changes with each shape
too (`pulse.rhythm`, 0.5): mostly a note on every beat, some stretches in half time (only the
strong beats, longer notes), some in double time (the octave on the "and"), and now and then a
triplet run into the next bar. Above `pulse.fast_bpm` (170) it leans to half time and plays no
double time or triplets. `pulse.movement = 0` is the old root on
every beat. Both follow the bass and drums feel knobs.

## Drums

`[drums] enabled = true` plays a General MIDI drum pattern on channel 10 (put a Logic
drum kit on a track that listens to channel 10). `drums.pattern` picks one of the
patterns (`accompanist params` lists them): `basic`, `halftime`, `soft`, `sparse`,
`waltz`, and `seven-322` (7 beats grouped 3+2+2). Drums follow the same beat as the bass,
start when it starts, follow your loudness, and stop on panic. `drums.swing` swings the
off-steps.

**Real drummers' grooves:** `accompanist library drums datasets/groove` builds a groove
library from the [Groove MIDI Dataset](https://magenta.withgoogle.com/datasets/groove)
(Google Magenta, CC BY 4.0: 13.6 hours of professional drummers; download and unzip
`groove-v1.0.0-midionly.zip`). A song with `[drums] style = "jazz"` (or `jazz/swing`,
`latin`, `latin/samba`, `funk`, `soul`, `rock`, `afrocuban`, `neworleans`, `reggae`,
`hiphop`...) then plays real grooves of that style near its tempo, bar after bar in the
drummer's own timing and dynamics, with a real fill at phrase ends and now and then
another groove. 4/4 only (the dataset is); other meters keep the patterns.

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

## Percussion

A second player, `[percussion] enabled = true`: latin hand percussion on its own channel
(`percussion.channel`, 11), so it can have its own kit (GM notes: congas, shaker, cowbell).
It is a seasoning, not a groove: it plays in occasional spells of `percussion.spell_bars`
bars (4), `percussion.presence` of them (0.3), likelier when you and the answers rest, and
softly (`percussion.velocity`, 55). It follows the meter: `latin` in 4 (a conga tumbao and
shaker), `latin-waltz` in 3, `latin-five`, `bembe` in 6/8 (the 12-pulse bell) and
`latin-seven` (3+2+2). A pitched percussion sound (bells, blocks, marimba) can play the
same rhythms on the chord's notes instead: `percussion.tuned = true`, around
`percussion.octave` (5). No fills, but now and then a **triplet figure** into the next
phrase (the last two beats of every 4 bars): the longer you've been soloing without a rest,
the likelier (`percussion.triplets`, reaching full after `percussion.triplet_build_s`, 30 s),
and past 70% of that it doubles up into eighth-note triplets. Its knob is
`percussion.feel`.

## Tāla percussion: your mridangam and tabla

With a tāla (`--time-sig misra-chapu`, `[song] tala = "adi"`, or `[percussion] tala`), the
percussion keeps the tāla instead of its latin patterns: the theka of a Hindustani tāla
(tintal, ektal, jhaptal, rupak, keherwa, dadra...) or a plain sarvalaghu for a Carnatic
one (adi, rupakam, the cāpus, any sūlādi tāla) in `percussion.nadai` (4 chatusram, 3
tisram, 5 khandam, 7 misram), the sam on the song's 1, accented, the vibhāg / anga starts
a little, khālī beats lighter. It follows your dynamics and steps forward in the gaps.

**Your instrument's keys.** Load your mridangam (or tabla) on the percussion channel and run

```
accompanist kitmap percussion --make mridangam
accompanist kitmap percussion --make tabla
```

Each key plays; type the stroke you hear (tha, dhi, thom, nam, dheem, chapu, ki...; na,
tin, tun, te, ge, ka, dha, dhin...), Enter to skip, `r` to hear it again, `q` to finish.
That writes `kits/mridangam.toml` (or `kits/tabla.toml`), and from then on a Carnatic tāla
plays your mridangam and a Hindustani one your tabla by itself (`percussion.kit = "auto"`;
or name any kit). A stroke the kit has no key for is played through its parts (Dha = Na +
Ge) or its nearest kin, so any theka plays on any kit. Until you make one, General MIDI
congas and bongos stand in.

**Mōrās that land on the sam.** The percussion speaks solkattu (`solkattu.py`): syllables
counted in pulses, four to a beat (`percussion.nadai`), and the mōrā, (statement) [gap]
(statement) [gap] (statement), 3s + 2g pulses, calculated to end exactly on the sam,
short statements taking a sounded gap of tām (D. P. Nelson's *Solkattu Manual*; the
phrase blocks and weights of the korvai-calculator and KorvaiSheets tools). Each syllable
is played as a stroke through `indian/solkattu.toml` (ta = tha, ka = ki, di = dhi, mi =
tom, tām = tam...: a starting point, per instrument, to correct). Uses:

- `[ending] shape = "mora"` (or `"tihai"`): `f` and the percussion plays a mōrā landing on
  the next sam (the kit drums rest so it speaks), where the whole band ends, its tām with
  the last chord. An Indian piece ends this way unless you choose otherwise.
- `percussion.moras`: now and then (this chance per cycle) a short mōrā marks the sam
  (`percussion.mora_beats` long), as a mridangist marks a song's sections (an Indian
  piece: 0.12).
- The sam itself is only lightly stressed (`percussion.sam_accent`): a tāla has no built-in
  accent; the stress comes from the phrases and the designs that land there.

**Your own cycle.** `[percussion] theka = "Thom.DhiKi Nam.DhiKi ..."`: one word per beat,
strokes inside a beat each starting with a capital, `.` a rest. The plain sarvalaghu is a
starting point, written to be corrected: `src/accompanist/indian/talas.toml`.

## Key, mode or rāga, and time, for one run

```
accompanist run --key C --mode sahana --time-sig misra-chapu --preset voice
accompanist run --key D --mode dorian --time-sig 7/8
```

- `--key`: the tonic (or the rāga's Sa): `C`, `F#`, `Bb`. Alone, the band hears the mode
  from you; `--key "F lydian, D minor"` gives a palette to move between.
- `--mode`: a mode (major, minor, dorian, lydian, mixolydian, minor-pentatonic, blues...)
  or a rāga (sahana, kalyani, mohanam, bhairavi...; the 72 melakartas and the janyas in
  `indian/ragas.toml`). A rāga gives the band its swaras as the key's scale, with Sa as the
  tonic: the right notes for the answers and the chords. (Its phrases and gamakas, and
  harmony that stays home on Sa, come with the rāga work later.)
- `--tempo`: the tempo `s` counts in at. Without one, `s` can't count; tap the beat with
  `t` instead (with `--time-sig`, any number of taps: they only give the tempo, and the
  band comes in on the next 1 in your meter).
- `--time-sig`: a time signature (3/4, 4/4, 5/4, 6/8, 7/8) or a tāla: misra-chapu and
  rupak in 7 (3+2+2), khanda-chapu and jhaptal in 5, tisra-chapu in 3, rupakam in 3, adi,
  tintal and keherwa in bars of 4, dadra in 6. It says how it took it when the band starts.
- A rāga (`--mode`, or a rāga in a key palette) or a tāla (`--time-sig`) makes it an Indian
  piece: the percussion (your mridangam or tabla) plays nearly throughout and steps
  forward in the gaps, instead of coming and going in spells (styles/indic.toml; your own
  `[percussion]` settings win).
- Tab completes all three. In a song file the same are `[harmony] keys = "C sahana"` and
  `[song] count = 7`.

## Typing less

- **Part of a name is enough:** `--song lady`, `--set fusion`, `--preset voice`, as long as
  only one name fits (else it lists the ones that do).
- **Tab completes** commands and the names after `--song`, `--set`, `--preset`, `--start`
  and `--finish` (zsh, the Mac's shell). Once:
  `echo 'eval "$(accompanist completion)"' >> ~/.zshrc`, then open a new terminal window.
- Run it from any folder: it finds your accompanist folder (the one with config.toml).

## How do we start this song? How do we finish?

Settle it before the show, song by song, as a band does in rehearsal. In the song's file:

```toml
[start]
shape = "intro"    # count | intro | drums | you | drone
bars = 4           # how long an intro or drums start lasts

[ending]
shape = "tag"      # chord | button | tag | ritardando | piano-tag | random
```

| Start | What happens |
|---|---|
| `count` (default) | `s`: a bar of clicks, then the band on the 1 (drums and bass first, the pad once it hears you) |
| `intro` | `s`: clicks, then the band plays `bars` bars on the home chord (or the chart's first chord), the drums filling you in; you come in on the next 1, the top of the song |
| `drums` | `s`: clicks, then the drums alone for `bars` bars; you come in with the band |
| `you` | no count: you start alone and the band comes in when it has your tempo |
| `drone` | `s`: the home chord holds in free time, for an ālāp; `s` again counts the band in |

- `accompanist reckoner --set fusion-1` prints the ready reckoner: each song's patch,
  meter, tempo and keys, its start and finish in words, and who plays. (`--song NAME` for
  one, nothing for every song.)
- `accompanist rehearse --song NAME` plays the song's start, four bars, and its finish, the
  band alone, so you can hear them. Then a menu: a number for another start, a letter for
  another finish (`2c` = intro and tag), `+`/`-` for the intro's length, Enter to hear it
  again, and **`k` keeps the pair in the set list**, as the song's plan for that gig:

  ```toml
  # sets/fusion-1.toml
  title = "Fusion set 1"
  songs = ["kann-pona-pokkile", "lady-sings-the-blues"]

  [plan.kann-pona-pokkile]
  start = "intro"
  bars = 4
  finish = "tag"
  ```

  The menu also moves the **key** (`<` `>` a semitone down or up: the chart, key palette and
  root move together) and tries a **style pack** (type its name: `jazz-ballad`, `swing`,
  `latin`, `pop`, `fusion`, or `as-written`), and `k` keeps those too (`transpose = 2`,
  `style = "latin"`). A style pack (styles/NAME.toml; add your own in ./styles) sets how
  the drums, bass, piano, percussion and pad play; the sounds stay the song's patch.
  A set's plan comes over the song file's own settings, so the same song can start one
  way, in one key and style, at one gig, and another way at the next. `--set NAME` says which set
  (needed only when the song is in more than one); `--start`/`--finish` begin with others.
- The stage screen shows this song's start and finish (change them there for this run) and
  the set's, song by song.

## The stage screen

While the band runs, a page opens in your browser: the song (and where you are in a set),
the tempo, key and chord, what you just played, and big buttons for what the keys do
(count in, break, finish, silence/resume, lock tempo, hold chord, tap tempo, previous and
next song). The keys work on the page too. Below them, each voice's feel, and a tap on its
number turns it on or off. Changes last for this run.

- Keep it in a small window beside your lyrics, or put it on a **phone or tablet** on the
  music stand: `[ui] lan = true` in config.toml prints a second address for your Wi-Fi.
  Each address carries a key made afresh each run, so nobody else on the venue's Wi-Fi can
  use it.
- `[ui] enabled = false` (or `run --no-ui`) for none; `ui.open = false` to not open it;
  `ui.port` if 8765 is taken (it tries the next ones itself). If it can't start, the band
  plays on and the keys still work.

## Walking bass

`[pulse] line = "walk"`: the bass walks, a note on every beat, as a jazz bassist does: the
root when a chord arrives; the beat before a new chord, an approach into its root (a half
step from below or above, a step of the scale, its fifth, or now and then an enclosure:
above, then below); between, chord tones and scale steps, never the same note twice,
heading for where the next root lies. With a chart it knows the next chord; else it heads
for the root on the next 1. `pulse.rhythm` adds swung "skips" (a ghosted eighth before
the beat). `line = "two"`: the two-feel, half notes, root then fifth (or an approach),
with pickups. `line = "shapes"` (default) keeps the bass shapes. The style packs `swing`
(walk) and `jazz-ballad` (two-feel) use them: `accompanist rehearse --song lady --style swing`.

## Piano

`[piano] enabled = true` (channel 5) answers you too, taking turns with the guitar: when your
phrase ends it takes some of the turns itself (`piano.share`, 0.35), and it fills some of
the gaps where the pad would otherwise swell (`piano.chance`, 0.5), so the gaps alternate
between a swell and an arpeggio. It plays a textbook arpeggio of the chord sounding (root,
third, fifth, seventh), strictly on the beat: eighths, triplets now and then, quarters above
`piano.fast_bpm`. It starts where your phrase ended and goes the way it went, about as long
as your phrase (at most `piano.max_beats`), and lands on the root. You playing again stops it
at once; on an ending it rolls the last chord. Its knob is `piano.feel`.

**Comping, jazz style.** `piano.comp` (0 to 1; 0, off, by default) has the piano comp
chords behind you while you play, the way a jazz pianist does: a different comping rhythm
each bar (the Charleston, 2 and 4, pushed off-beats, an anticipation into the next bar, a
held whole note...), never the same twice running, sparser when you play busily, and
quiet while it answers you. Higher `comp`: more of the rhythm's hits. Interludes comp the
same way. Voicings (`piano.voicing`): `rootless` jazz A/B voicings, without the root (the
bass has it): 3-5-7-9 and 7-9-3-5, with the 13 on dominants and the 6/9 on chords without
a seventh, colour tones only where they belong to the key, each chord moving as little as
possible from the last (so a ii-V-I moves by step); `close` plays the chord's own tones;
`auto` (default) uses rootless for seventh chords. They sit between `piano.comp_low` and
`piano.comp_high` (D3 to D5).


## Your phrase library

The guitar riffs on your phrases, and not only this run's: everything you play goes into a
library (`library.path`, ~/.accompanist/library: notes only, no audio), and the guitar
draws on it (`response.library`, half the time), moving a phrase to the key of the moment
and choosing the ones that fit the chord and resemble what you just played.

- Every `run` adds its takes when it ends (`library.learn`).
- `accompanist library add takes/ ~/Recordings/` adds takes and WAV recordings (folders
  too), each only once; `--tag class` labels them. `accompanist library stats` shows what's
  there.
- `accompanist library stats` also shows the rāga view of your practice and classes: for
  each session its Sa (found from where the phrases come home and dwell, or given with
  `practice --sruti F#`), the swaras sung, and the rāga they fit best among the 72
  melakartas and the janya and Hindustani rāgas in `indian/` (direction, ārohaṇa against
  avarohaṇa, breaks ties; rāgas with the same swaras are named together). No tags needed.
- `accompanist library ragas datasets/RagaDataset` learns how 70 rāgas (40 Carnatic, 30
  Hindustani) are actually sung, from the pitch tracks of 780 concert recordings in the
  Indian Art Music Raga Recognition Dataset (features), CompMusic, CC BY 4.0 (Gulati, Serrà,
  Ganguli, Şentürk & Serra; zenodo.org/records/7278506): how long each swara is dwelt on,
  and from which direction it is reached. `library stats` then adds, for each session, the
  nearest rāgas as sung in concert beside the reading from scales. On the dataset's own
  Carnatic recordings (each left out in turn) it names the rāga first about 4 times in 5;
  on noisy class recordings it is still a second opinion.
- `accompanist practice` listens while you practise or teach, and banks every musical
  phrase (no band plays). Speech is left out: short, unsteady, off-pitch syllables don't
  count as phrases. Free-time phrases (ālāp, ālāpana, cadenzas) are tagged `rubato`.

## Interludes

When you rest for a while (`interlude.after_beats`, 6 beats after the answers), the band
carries the music so you can take a proper break: the piano comps (chords on the beat, a
pattern for each meter, voice-led) and the guitar solos, stringing your own remembered
phrases together to fill its turn, taking
turns every `interlude.turn_bars` bars (4). The pad stays back while they play. Play again
and they stop at once. The status line shows INTERLUDE. `[interlude] enabled = false` turns it
off.

The guitar varies the rhythm of the phrases it plays back (`response.rhythm_variety`,
0.4): mostly as you played them, now and then in double time (not above 150 bpm), half time
(not below 70) or triplets. Answers are up to `response.max_notes` (8) long.

## Your body: a Muse headband

A Muse S Athena headband can be an input: your **heartbeat**, and **head gestures** as a
hands-free pedal. Install its Bluetooth support (`pip install bleak
https://github.com/DominiqueMakowski/OpenMuse/zipball/main`, or `pip install -e .[muse]`),
find its address (`OpenMuse find`), and add it:

```toml
[[inputs]]
name = "head"
muse = "1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D"   # yours, from `OpenMuse find`

[body]                      # the defaults:
nod = "break"
tilt_left = "song_start"    # tilt your head left twice: count in
tilt_right = "finish"       # ...right twice: finish
heart = true                # before a song, between songs and after the set: a soft kick
filler = true               # and the pad breathe with you, the pad moving slowly through
filler_beats = 8            # the home key's chords (8 heartbeats each) while you talk
pad_level = 0.55            # the pad's level then, ebbing and flowing slowly
ebb_s = 12                  # (one swell and fall every 12 s, falling 40% at its low point:
ebb_depth = 0.4             #  ebb_depth), with a swell on each heartbeat on top
```

`accompanist muse` shows what it hears, live. Gestures are recognised by how fast and how
far the head moves, and were tuned so that playing the sax never triggers one (lifting it
neither): a nod is one down-up; a tilt counts when you tilt twice to the same side. The
heart rate comes from the forehead's red light sensors and stays reliable while you play.
Brainwaves (EEG) aren't used yet: in tests they were swamped by mains hum and playing.

## Before (and during) a gig

`accompanist check` loads your config and every song, and looks for every device: run it at
soundcheck. `accompanist soundcheck` then plays a few notes on each voice's channel in turn
(pad, bass, drums, percussion, piano, guitar), naming each, so you hear that every instrument
in MainStage or Logic answers; `accompanist soundcheck percussion` plays just one. It lists each problem with what to do (`accompanist check waltz seven` checks
just those songs).

**Levels, like a line check:** `accompanist levels --song NAME` selects the song's patch,
starts the recorder, plays each voice alone for 3 seconds at its normal level, then asks
you to play for 6 seconds. It measures each part of the recording and works out a trim per
voice so it sits where you want it under your instrument (`[mix] target_pad_db = -12`,
`target_bass_db = -6`, ...), and writes the trims into the song file (`[mix] pad_db = ...`).
From then on the song sets its levels itself when it loads, as MIDI volume (CC7) on each
voice's channel, so no faders to chase. Re-run it after changing a patch's sounds. A voice
that made no sound is reported (check its strip); one whose instrument ignores CC7 won't
move, so set that one by hand. `--file` measures an earlier recording without playing.

**Recording in MainStage without remembering to:** set `[output] recorder_cc = 119` and map
that CC (channel 16, `output.recorder_channel`) to MainStage's Record action once; `run`
then presses it when it starts and again when it ends. `accompanist recorder` presses it
once, to teach MainStage the button or to test it. (A testing convenience; may go before
a public release.)

`run` doesn't stop a set over a fixable mistake:
- a pedal, keyboard or audio input that isn't plugged in: a warning, and it carries on
  without it (it stops only if there is nothing at all to listen to);
- a song file with a mistake: it plays with your config.toml alone, and says so;
- a config.toml with a mistake: it uses the last copy that started (`config.toml.last-good`,
  kept automatically), and says so;
- a bug while playing: logged to `logs/errors.log`, one line on screen, and the band plays on.
  Please send that file over.

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

Copyright (C) 2026 Ragavan Manian.

accompanist is free software: you can redistribute it and/or modify it under the terms of
the GNU General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. It is distributed in the hope that it
will be useful, but WITHOUT ANY WARRANTY; see [LICENSE](LICENSE) for the full terms.

Data used for learning is not part of this repository and keeps its own licence: the Weimar
Jazz Database (Jazzomat Research Project, ODbL 1.0) and the Nottingham Music Database
(GPL-3.0). Anything learnt from them that ships here is credited where it is used.
