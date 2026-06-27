---
name: mix-compare
description: Diff two versions of a mix and explain what changed. When a mix engineer sends a revision (v4 → v5) and you need to know exactly what moved — per stem, per EQ band, in stereo width, low-end phase, reverb — this time-aligns the two files, phase-inverts to isolate the change, measures it objectively, and translates the numbers into feedback an engineer can act on. Use on "/mix-compare", "compare these two mixes", "what changed in v5", "what did they fix", "before/after mix", or two audio files + "what's different".
---

# /mix-compare — diff two versions of a mix, objectively

When you confirm a mix and a new bounce comes in (v4 → v5), this auto-aligns the two
versions and diffs them per stem, per EQ band, in stereo image, phase, and noise — then
translates the difference into a sentence you can hand straight back to the engineer.

> It's `git diff`, but for audio. No single tool does this combo (time-align + stem
> diff + phase + plain-language explanation): reference A/B meters compare to a
> reference track, single-file "AI feedback" tools look at one file, aligners only align.

## vs. single-file analysis

| | single-file analyzer | **mix-compare** |
|---|---|---|
| Target | one file | **two files (version vs version)** |
| Time alignment | — | ✓ cross-correlation |
| Phase-invert diff | — | ✓ isolates only the change |
| Stem separation | maybe | ✓ **both versions + per-stem delta** |
| Low-end phase corr | — | ✓ |
| High-end noise (flatness) | — | ✓ |
| Engineer-ready feedback | — | ✓ |

The point: a single-file tool answers "how is this mix?"; mix-compare answers "what changed
in this revision, and why does it sound that way?"

## Setup

```bash
pip install numpy scipy librosa soundfile   # required
pip install pyloudnorm                       # optional: LUFS / true-peak
pip install demucs                           # optional: per-stem diff (heavier, CPU)
```

Required deps give you alignment + phase diff + EQ + phase + noise. pyloudnorm adds
loudness; demucs adds the per-stem breakdown. Without demucs, pass `--skip-stems`.

## Trigger

1. `/mix-compare <old> <new>` — **previous version first, current version second**.
2. Two audio files + "compare the mixes", "what changed in v5", "what did they fix".
3. A revision arrives and you want it diffed against the prior bounce.

If two paths aren't given, ask for them in one line. **Order matters: first = old,
second = new (current). Every delta is `new - old`.**

## Workflow

### Phase 1 — Run the compare
```bash
python3 ~/.claude/skills/mix-compare/compare.py "<old>" "<new>" --pretty --out /tmp/mixcompare.json
```
Options:
- `--skip-stems` — skip stem separation (align + diff + full-mix + loudness + phase + noise, ~20s). Quick check, no demucs needed.
- `--diff-audio` — write phase-invert diff wavs (full mix + vocal stem). Play them and you hear *only* what changed.

With stems, demucs splits both files (~3–6 min on CPU). Drop `--skip-stems` only when you need the per-stem breakdown.

### Phase 2 — Read the JSON (delta-first)

> Accuracy depends on the inputs. On **two bounces of the same mix through the same chain**
> (the normal v4→v5 case), alignment + phase-invert isolate the real change well. On two
> **differently-mastered** files, non-linear bus processing (comp/limit/saturation) breaks
> clean cancellation — `phase_diff` and fine per-stem deltas get noisy; the full-mix EQ
> delta and loudness stay reliable. Say which case you're in before drawing strong
> conclusions.

Interpretation heuristics (rules of thumb, not lab constants):

- `per_stem.vocals.ms_width.delta > +0.3 dB` → vocal got wider — a common physical cause
  of a vocal feeling "detached / floating" (often a room-reverb side effect).
- `reverb_tail.delta` negative → reverb was added but it got *drier* anyway (e.g. backing
  vocals pulled down removed more space than the reverb added).
- `lowend_phase.*.phase_corr < 0.95` → low-end phase instability — a frequent cause of a
  mix that balances differently on different systems. Bass stem is usually ~0.99 (stable);
  if the `other` stem sits near 0.3, suspect pads / stereo reverb returns / layered synths
  with energy below ~120Hz → treat the low end as mono.
- `high_noise.*.flatness > 0.4` → genuinely noisy. ~0.001 means no noise (so a "harsh
  highs" worry is unfounded — read it as a question to rule out, not a problem to fix).
- `fullmix_octave_delta` → which bands were touched. Presence (1.3–2.6k) / high-mid
  (2.6–5k) coming down reads as "smoother / softer".

### Phase 3 — Translate to engineer-ready feedback

Turn numbers into language an engineer can act on:
1. One-line summary — the single biggest change in this revision.
2. Vocal change — level / width / space / EQ.
3. Problem + prescription (e.g. "`other` stem phase corr 0.27 below 120Hz → mono the
   low end on pads / reverb returns").
4. One follow-up question.

## Don't

- State a structure `label` (intro / drop / …) as fact — that's a dance-music-biased
  heuristic, not a measurement.
- Dump raw numbers ("M-S +0.49, corr 0.27, flatness 0.0003") — translate to musical language.
- **Lead with true peak / loudness.** Engineers often deliver intentionally limited, loud
  bounces so a non-technical client hears it "finished." Only raise loudness if the user
  brings it up first ("why does this sound quiet?"). At the confirm stage, a limited
  loudness is normal practice, not a defect.
- Indiscriminately praise — give critique when asked, with the numbers behind it.
- Confuse file order — **first = old, second = new**, delta = new - old.
- Silently fabricate a fallback when a stage fails — say it failed and proceed with the rest.

## Limits

- **Stem bleed**: htdemucs isn't perfect separation — vocal tails leak into `other`. Trust
  the old↔new *delta* over a stem's absolute number.
- **Phase-invert diff**: if the two versions differ in gain, the gain delta shows up in the
  diff. `ratio_percent` is an estimate of total change, not a clean isolation.
- **Low-end phase corr** is measured after stem separation, so separation artifacts can
  influence it — cross-check against the full-mix `lowend_phase`.
- **demucs on CPU**: two files = double the time. For a quick pass, `--skip-stems`.
