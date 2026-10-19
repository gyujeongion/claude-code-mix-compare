# Mix Compare

**Compare two versions of a mix and hear exactly what changed.** Drop in the old and new
bounce. It shows what moved per stem, per EQ band, in stereo width, low-end phase and
reverb, and turns the numbers into feedback an engineer can act on.

*Think `git diff`, but for audio.* A [Claude Code](https://claude.com/claude-code) skill
+ a self-contained Python tool.

![mix-compare demo](demo/mix-compare.gif)

> **Read this first — where it's exact and where it's an estimate.** This is at its best
> on **two bounces of the same mix through the same chain** (the normal revision case:
> v4 → v5, mastering not yet changed). There, after time-alignment the phase-invert
> cancels cleanly and the diff really is *just the change*. It is **weaker on two
> differently-mastered files** — bus compression, limiting, and saturation are
> non-linear, so the cancellation leaves residue and `phase_diff` reads high even when
> little "musically" changed. The full-mix EQ delta and loudness stay meaningful in that
> case; the phase-diff % and fine per-stem deltas don't. See [Limits](#limits) — the docs
> say so instead of pretending the math is clean on a finished master.

---

## The gap it fills

When a mix engineer sends a revision, "what actually changed between v4 and v5?" is
hard to answer. Your ears adapt, gain differences fool you, and a change buried
in one stem is invisible in the full mix. The existing tools don't cover it:

- **Reference A/B meters** compare your mix to a *reference track*, not to its own previous version.
- **Single-file "AI feedback"** tools analyze one file in isolation.
- **Audio aligners** align two files but don't diff them.

mix-compare combines what none of them do: **time-align the two versions →
phase-invert to isolate only what moved → measure it per stem and per band → explain it
in plain language.**

```
$ python3 compare.py mix_v4.wav mix_v5.wav --skip-stems --pretty
# → alignment lag, phase-diff %, 10-band EQ delta, stereo width,
#   low-end phase correlation, high-end noise, loudness
```

Add `--diff-audio` and it writes a wav of *just the change*. Play it and you hear what the engineer touched.

## What it measures

- **Time alignment**: cross-correlation lag, so the two versions line up sample-accurate.
- **Phase-invert diff**: `new - old`, the isolated change, as RMS and % of the original.
- **Full-mix EQ delta**: 10 octave bands, which got louder/quieter, plus spectral centroid.
- **Per-stem diff** (optional, demucs): vocals / bass / drums / other: level, peak,
  stereo width, and a 7-band vocal EQ + reverb-tail (dry/wet) breakdown.
- **Low-end phase**: per-band M/S width + phase correlation (catches the mono-compatibility
  problems that make a mix fall apart on different systems).
- **High-end noise**: per-band spectral flatness (tone vs. noise).
- **Loudness** (optional, pyloudnorm): integrated LUFS + true-peak.

The companion [SKILL.md](skills/mix-compare/SKILL.md) carries field-tested interpretation
heuristics (e.g. *vocal M/S width up >0.3dB → "floating vocal"; low-end phase corr <0.95 →
mono-compatibility risk*) so the agent writes notes an engineer can use instead of dumping numbers. They're rules of thumb that have held up in practice, not lab-proven
constants. Treat them as a starting read, not a verdict.

### Verified to run (reproducible)

`examples/make_demo.py` builds two synthetic mixes — B adds a 3 kHz layer and widens it —
runs the tool, and checks the change lands in the right band. Run it yourself:

```
$ python3 examples/make_demo.py
alignment:    aligned
phase_diff %: 26.9
octave-band delta (new - old):
  presence      1280-2560 Hz   +31.52 dB
  high-mid      2560-5120 Hz   +57.91 dB  <- largest
  high         5120-10240 Hz   +33.58 dB
  air         10240-20000 Hz   +23.23 dB
Largest change in: high-mid (2560-5120 Hz)
PASS — 3 kHz localized to the high-mid band.
```

The 3 kHz layer shows up largest in **high-mid (2.56–5.12 kHz)** (where 3 kHz actually
lives) and spills into the neighbor bands through the band-pass filter skirts (the
filters aren't brick walls). That spill is exactly why the SKILL reads the *pattern*
across bands, not a single band in isolation.

## Limits

Know these before you trust a number:

- **Non-linear mastering breaks clean cancellation.** If the two files went through
  different bus compression / limiting / saturation, `new - old` won't cancel to silence;
  the diff carries residue of the whole mix and `phase_diff %` overstates the change. Use
  mix-compare on **same-chain revisions**; for differently-mastered files, lean on the
  full-mix EQ delta and loudness, and discount the phase-diff % and fine per-stem deltas.
- **Stem separation is approximate and reactive.** htdemucs isn't clean separation, and a
  small balance change can shift *how* it separates — which can manufacture a per-stem EQ
  delta that isn't really in the mix. Trust the **direction of the old↔new delta**, not a
  stem's absolute number, and cross-check per-stem findings against the full-mix EQ delta.
- **Gain differences leak into the diff.** If v5 is simply louder, that gain shows up as
  "change." `ratio_percent` is an estimate of *total* change, not a clean isolation.
- **Low-end phase corr** is measured post-separation, so separation artifacts can color it
  — cross-check against the full-mix `lowend_phase`.
- **demucs on CPU** doubles the time for two files. Quick pass: `--skip-stems`.

In short: a sharp, fast read of a same-chain revision, and a rough but useful one of
anything else.

## Install

```bash
git clone https://github.com/<you>/claude-code-mix-compare.git
cp -r claude-code-mix-compare/skills/mix-compare ~/.claude/skills/mix-compare

pip install numpy scipy librosa soundfile   # required
pip install pyloudnorm                       # optional: loudness
pip install demucs                           # optional: per-stem diff
```

The required deps cover alignment + phase diff + EQ + phase + noise. Stems and
loudness are optional add-ons; without demucs, run with `--skip-stems`.

## Usage

Standalone:

```bash
python3 ~/.claude/skills/mix-compare/compare.py old.wav new.wav --pretty
python3 ~/.claude/skills/mix-compare/compare.py old.wav new.wav --diff-audio   # hear the change
```

In Claude Code: `/mix-compare old.wav new.wav`, or just give it two files and ask "what
changed in v5". **Order matters: first = old, second = new; every delta is `new - old`.**

## License

MIT, see [LICENSE](LICENSE). Not affiliated with Anthropic.
