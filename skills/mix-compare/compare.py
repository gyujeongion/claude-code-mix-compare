#!/usr/bin/env python3
"""
compare.py — diff two versions of a mix and report exactly what changed.

Think `git diff`, but for audio. Give it the old and the new bounce of a mix; it
time-aligns them, phase-inverts to isolate only what moved, and quantifies the change
per band, per stem, in stereo width, low-end phase, and high-end noise — then (via the
companion SKILL.md) translates the numbers into feedback an engineer can act on.

No equivalent single tool exists: reference A/B meters compare against a reference
track, single-file "AI mastering feedback" tools look at one file. This compares two
*versions* of the same mix.

Pipeline:
  1. Time-align     — cross-correlation lag, shift `new` onto `old`
  2. Phase-invert   — new-old → only the change. RMS + % of original. (--diff-audio = wav)
  3. Full-mix EQ    — 10-band octave RMS delta + spectral centroid
  4. Loudness       — integrated LUFS / LRA / true-peak (needs pyloudnorm; optional)
  5. Stems          — htdemucs split (needs demucs; optional, --skip-stems to skip)
  6. Per-stem        — vocals/bass/drums/other RMS·peak·centroid·M-S width + vocal 7-band EQ
  7. Reverb tail    — post-onset 50–300ms vs pre-onset (dry/wet quantified)
  8. Low-end phase  — 0–300Hz per-band M/S width + phase correlation
  9. High noise     — 4kHz+ per-band spectral flatness

Dependencies: numpy, scipy, librosa, soundfile (required). pyloudnorm (optional, for
loudness). demucs (optional, for stems). Install:  pip install numpy scipy librosa
soundfile pyloudnorm demucs

Usage:
  python3 compare.py <old> <new> [--pretty] [--out result.json]
      [--skip-stems]   skip stem separation (align+diff+EQ+loudness+phase, ~20s)
      [--diff-audio]   write phase-invert diff wavs (full mix + vocal stem)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, sosfilt, correlate


# ───────────────────────── audio utils ─────────────────────────

def _load_stereo(path: str):
    y, sr = sf.read(path, always_2d=True)   # (samples, channels)
    return y, sr


def _to_mono(y):
    return y.mean(axis=1)


def _rms_db(x) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-10))


def _peak_db(x) -> float:
    return float(20 * np.log10(np.max(np.abs(x)) + 1e-10))


def _bpf(sig, sr, lo, hi):
    nyq = sr / 2
    sos = butter(4, [max(lo / nyq, 0.001), min(hi / nyq, 0.9999)], btype="band", output="sos")
    return sosfilt(sos, sig)


def _lpf(sig, sr, cutoff):
    sos = butter(4, cutoff / (sr / 2), btype="low", output="sos")
    return sosfilt(sos, sig)


def _band_rms(mono, sr, lo, hi) -> float:
    return _rms_db(_bpf(mono, sr, lo, hi))


def _spectral_centroid(mono, sr) -> float:
    sc = librosa.feature.spectral_centroid(y=mono.astype(np.float32), sr=sr)
    return float(np.mean(sc))


def _ms_width(stereo):
    """M/S width (dB): side_rms - mid_rms. Higher = wider."""
    if stereo.shape[1] < 2:
        return None
    mid = (stereo[:, 0] + stereo[:, 1]) / 2
    side = (stereo[:, 0] - stereo[:, 1]) / 2
    return round(_rms_db(side) - _rms_db(mid), 2)


def _phase_corr(stereo_band) -> float:
    """Phase correlation (-1..+1). +1 = mono, 0 = unrelated, -1 = out of phase."""
    L, R = stereo_band[:, 0], stereo_band[:, 1]
    if np.std(L) == 0 or np.std(R) == 0:
        return 1.0
    return round(float(np.corrcoef(L, R)[0, 1]), 4)


def _reverb_tail(mono, sr) -> dict:
    """Energy 50–300ms after each onset vs the 10ms before it. Higher = wetter."""
    onsets = librosa.onset.onset_detect(y=mono.astype(np.float32), sr=sr, units="samples")
    tails, pres = [], []
    for o in onsets[:30]:
        pre = mono[max(0, o - int(sr * 0.01)):o]
        tail = mono[o + int(sr * 0.05):o + int(sr * 0.30)]
        if len(pre) and len(tail):
            pres.append(_rms_db(pre))
            tails.append(_rms_db(tail))
    if not tails:
        return {"tail_vs_pre_diff": None}
    avg_pre, avg_tail = float(np.mean(pres)), float(np.mean(tails))
    return {
        "avg_pre_onset_db": round(avg_pre, 2),
        "avg_tail_50_300ms_db": round(avg_tail, 2),
        "tail_vs_pre_diff": round(avg_tail - avg_pre, 2),
    }


def _flatness(mono, sr, lo, hi) -> float:
    """Band spectral flatness (0 = pure tone, 1 = pure noise)."""
    band = _bpf(mono, sr, lo, hi)
    return float(np.mean(librosa.feature.spectral_flatness(y=band.astype(np.float32))))


# ───────────────────────── time alignment ─────────────────────────

def _align(old_mono, new_mono, sr, window_sec=5.0):
    """cross-correlation lag to shift `new` onto `old` (positive = new is later)."""
    n = min(len(old_mono), len(new_mono), int(sr * window_sec))
    corr = correlate(old_mono[:n], new_mono[:n], mode="full")
    return int(np.argmax(np.abs(corr))) - (n - 1)


def _apply_lag_stereo(stereo, lag, target_len):
    if lag > 0:
        out = np.pad(stereo, ((lag, 0), (0, 0)))
    elif lag < 0:
        out = stereo[-lag:]
    else:
        out = stereo
    return out[:target_len]


# ───────────────────────── band definitions ─────────────────────────

OCTAVE_BANDS = [
    (20, 40, "sub"), (40, 80, "sub-bass"), (80, 160, "bass"),
    (160, 320, "low-mid"), (320, 640, "mid"), (640, 1280, "upper-mid"),
    (1280, 2560, "presence"), (2560, 5120, "high-mid"),
    (5120, 10240, "high"), (10240, 20000, "air"),
]

VOCAL_BANDS = [
    (80, 200, "vocal-low"), (200, 400, "vocal-body"), (400, 800, "vocal-mid"),
    (800, 1600, "presence"), (1600, 3200, "air1"), (3200, 6400, "air2"),
    (6400, 10000, "top"),
]

LOWEND_BANDS = [
    (0, 60, "sub-0-60"), (0, 80, "sub-0-80"), (0, 120, "bass-0-120"),
    (0, 200, "low-0-200"), (120, 300, "low-mid-120-300"), (300, 600, "mid-300-600"),
]

HIGH_BANDS = [
    (4000, 8000, "high-mid-4-8k"), (8000, 12000, "high-8-12k"),
    (12000, 16000, "hi-12-16k"), (16000, 20000, "air-16-20k"), (8000, 20000, "all-8k+"),
]


# ───────────────────────── full-mix comparison ─────────────────────────

def _band_delta(old_mono, new_mono, sr, bands):
    out = {}
    for lo, hi, name in bands:
        b_old = _band_rms(old_mono, sr, lo, hi)
        b_new = _band_rms(new_mono, sr, lo, hi)
        d = round(b_new - b_old, 2)
        out[name] = {
            "hz": f"{lo}-{hi}", "old_db": round(b_old, 2), "new_db": round(b_new, 2),
            "delta_db": d,
            "direction": "up (new louder)" if d > 0.3 else "down (new quieter)" if d < -0.3 else "~equal",
        }
    return out


def _lowend_phase(stereo, sr):
    """Per low band M/S width + phase correlation. corr < 0.95 = phase suspect."""
    out = {}
    for lo, hi, name in LOWEND_BANDS:
        if lo == 0:
            band = np.stack([_lpf(stereo[:, 0], sr, hi), _lpf(stereo[:, 1], sr, hi)], axis=1)
        else:
            band = np.stack([_bpf(stereo[:, 0], sr, lo, hi), _bpf(stereo[:, 1], sr, lo, hi)], axis=1)
        mid = (band[:, 0] + band[:, 1]) / 2
        side = (band[:, 0] - band[:, 1]) / 2
        corr = _phase_corr(band)
        out[name] = {
            "hz": f"{lo}-{hi}",
            "width_db": round(_rms_db(side) - _rms_db(mid), 2),
            "phase_corr": corr,
            "flag": "phase suspect" if corr < 0.9 else "" if corr < 0.95 else "OK",
        }
    return out


def _high_noise(mono, sr):
    """Per high band spectral flatness. flatness > 0.4 = noisy."""
    out = {}
    for lo, hi, name in HIGH_BANDS:
        fl = _flatness(mono, sr, lo, hi)
        out[name] = {
            "hz": f"{lo}-{hi}", "rms_db": round(_band_rms(mono, sr, lo, hi), 2),
            "flatness": round(fl, 4),
            "flag": "noisy" if fl > 0.4 else "noisy?" if fl > 0.25 else "clean",
        }
    return out


# ───────────────────────── loudness (optional: pyloudnorm) ─────────────────────────

def _measure_loudness(path: str) -> dict:
    try:
        import pyloudnorm as pyln
    except ImportError:
        return {"_skipped": True, "reason": "pyloudnorm not installed (pip install pyloudnorm)"}
    try:
        data, rate = sf.read(path)
        meter = pyln.Meter(rate)
        lufs = float(meter.integrated_loudness(data))
        # 4x-oversampled true peak estimate
        mono = data.mean(axis=1) if data.ndim > 1 else data
        up = librosa.resample(mono.astype(np.float32), orig_sr=rate, target_sr=rate * 4)
        tp = float(20 * np.log10(np.max(np.abs(up)) + 1e-10))
        return {"integrated_lufs": round(lufs, 2), "true_peak_dbfs": round(tp, 2)}
    except Exception as e:   # noqa: BLE001
        return {"_failed": True, "message": str(e)}


# ───────────────────────── stems (optional: demucs) ─────────────────────────

def _demucs_available() -> bool:
    if shutil.which("demucs"):
        return True
    try:
        import demucs  # noqa: F401
        return True
    except ImportError:
        return False


def _separate_stems(path: str, outdir: Path) -> dict:
    """Run htdemucs locally. Returns {stems: {name: wav_path}} or {_failed}."""
    if not _demucs_available():
        return {"_failed": True, "error": "demucs not installed (pip install demucs)"}
    model = "htdemucs"
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-o", str(outdir), str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return {"_failed": True, "error": "demucs run failed", "message": e.stderr.decode()[-500:]}
    stem_dir = outdir / model / Path(path).stem
    stems = {}
    for name in ("vocals", "bass", "drums", "other"):
        f = stem_dir / f"{name}.wav"
        if f.exists():
            stems[name] = str(f)
    if not stems:
        return {"_failed": True, "error": "no stems produced", "looked_in": str(stem_dir)}
    return {"stems": stems, "where": str(stem_dir)}


def _compare_stem(name, old_path, new_path, want_diff=False):
    old, sr = _load_stereo(old_path)
    new, _ = _load_stereo(new_path)
    old_m, new_m = _to_mono(old), _to_mono(new)

    lag = _align(old_m, new_m, sr)
    new = _apply_lag_stereo(new, lag, len(old))
    new_m = _to_mono(new)
    n = min(len(old_m), len(new_m))
    old_m, new_m, old, new = old_m[:n], new_m[:n], old[:n], new[:n]

    res = {
        "lag_samples": int(lag),
        "lag_ms": round(lag / sr * 1000, 1),
        "rms": {"old": round(_rms_db(old_m), 2), "new": round(_rms_db(new_m), 2),
                "delta": round(_rms_db(new_m) - _rms_db(old_m), 2)},
        "peak": {"old": round(_peak_db(old_m), 2), "new": round(_peak_db(new_m), 2),
                 "delta": round(_peak_db(new_m) - _peak_db(old_m), 2)},
        "centroid_hz": {"old": round(_spectral_centroid(old_m, sr), 0),
                        "new": round(_spectral_centroid(new_m, sr), 0)},
        "ms_width": {"old": _ms_width(old), "new": _ms_width(new)},
    }
    w_old, w_new = res["ms_width"]["old"], res["ms_width"]["new"]
    if w_old is not None and w_new is not None:
        res["ms_width"]["delta"] = round(w_new - w_old, 2)

    if name == "vocals":
        res["vocal_band_delta"] = _band_delta(old_m, new_m, sr, VOCAL_BANDS)
        res["reverb_tail"] = {"old": _reverb_tail(old_m, sr), "new": _reverb_tail(new_m, sr)}
        rt_o = res["reverb_tail"]["old"].get("tail_vs_pre_diff")
        rt_n = res["reverb_tail"]["new"].get("tail_vs_pre_diff")
        if rt_o is not None and rt_n is not None:
            res["reverb_tail"]["delta"] = round(rt_n - rt_o, 3)
        if want_diff:
            diff = new_m - old_m
            dp = str(Path(tempfile.gettempdir()) / f"mixcompare_{name}_diff.wav")
            sf.write(dp, diff, sr)
            res["diff_audio"] = dp
            res["diff_rms_db"] = round(_rms_db(diff), 2)
    return res


# ───────────────────────── main ─────────────────────────

def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:   # noqa: BLE001
        return {"_failed": True, "message": str(e)}


def compare(old_path: str, new_path: str, *, skip_stems=False, diff_audio=False) -> dict:
    old_p, new_p = Path(old_path), Path(new_path)
    result: dict = {
        "mode": "mix-compare",
        "old": {"path": str(old_p), "name": old_p.name},
        "new": {"path": str(new_p), "name": new_p.name},
    }

    old_stereo, sr = _load_stereo(old_path)
    new_stereo, sr2 = _load_stereo(new_path)
    if sr != sr2:
        new_mono_rs = librosa.resample(_to_mono(new_stereo), orig_sr=sr2, target_sr=sr)
        new_stereo = np.stack([new_mono_rs, new_mono_rs], axis=1)
        result["_resampled_new"] = f"{sr2}->{sr}"

    old_mono, new_mono = _to_mono(old_stereo), _to_mono(new_stereo)

    # 1. time alignment
    lag = _align(old_mono, new_mono, sr)
    result["alignment"] = {
        "lag_samples": int(lag), "lag_ms": round(lag / sr * 1000, 1),
        "note": "new is later" if lag > 0 else "new is earlier" if lag < 0 else "aligned",
    }
    new_stereo_al = _apply_lag_stereo(new_stereo, lag, len(old_stereo))
    new_mono_al = _to_mono(new_stereo_al)
    n = min(len(old_mono), len(new_mono_al))
    old_mono, new_mono_al = old_mono[:n], new_mono_al[:n]
    old_stereo, new_stereo_al = old_stereo[:n], new_stereo_al[:n]

    # 2. phase-invert diff
    diff = new_mono_al - old_mono
    diff_rms = _rms_db(diff)
    old_rms = _rms_db(old_mono)
    result["phase_diff"] = {
        "diff_rms_db": round(diff_rms, 2),
        "old_rms_db": round(old_rms, 2),
        "ratio_vs_old_db": round(diff_rms - old_rms, 2),
        "ratio_percent": round(10 ** ((diff_rms - old_rms) / 20) * 100, 1),
    }
    if diff_audio:
        dL = new_stereo_al[:, 0] - old_stereo[:, 0]
        dR = new_stereo_al[:, 1] - old_stereo[:, 1]
        dp = str(Path(tempfile.gettempdir()) / "mixcompare_fullmix_diff.wav")
        sf.write(dp, np.stack([dL, dR], axis=1), sr)
        result["phase_diff"]["diff_audio"] = dp

    # 3. full-mix spectrum
    result["fullmix_octave_delta"] = _band_delta(old_mono, new_mono_al, sr, OCTAVE_BANDS)
    result["fullmix_centroid_hz"] = {
        "old": round(_spectral_centroid(old_mono, sr), 0),
        "new": round(_spectral_centroid(new_mono_al, sr), 0),
    }
    result["fullmix_ms_width"] = {"old": _ms_width(old_stereo), "new": _ms_width(new_stereo_al)}

    # low-end phase + high-end noise (both versions)
    result["lowend_phase"] = {"old": _lowend_phase(old_stereo, sr),
                              "new": _lowend_phase(new_stereo_al, sr)}
    result["high_noise"] = {"old": _high_noise(old_mono, sr),
                            "new": _high_noise(new_mono_al, sr)}

    # 4. loudness (optional)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_lo = ex.submit(_safe, _measure_loudness, old_path)
        f_ln = ex.submit(_safe, _measure_loudness, new_path)
        result["loudness"] = {"old": f_lo.result(), "new": f_ln.result()}

    # 5/6. stems (optional)
    if skip_stems:
        result["stems"] = {"_skipped": True}
    else:
        with tempfile.TemporaryDirectory(prefix="mixcompare_stems_") as td:
            tdp = Path(td)
            sep_old = _separate_stems(old_path, tdp / "old")
            sep_new = _separate_stems(new_path, tdp / "new")
            if sep_old.get("_failed") or sep_new.get("_failed"):
                result["stems"] = {"_failed": True, "old": sep_old, "new": sep_new,
                                   "hint": "install demucs, or pass --skip-stems"}
            else:
                per_stem = {}
                for stem in ("vocals", "bass", "drums", "other"):
                    op = sep_old["stems"].get(stem)
                    nps = sep_new["stems"].get(stem)
                    if op and nps:
                        per_stem[stem] = _safe(_compare_stem, stem, op, nps, want_diff=diff_audio)
                result["per_stem"] = per_stem

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff two versions of a mix.")
    ap.add_argument("old", help="path to the previous version")
    ap.add_argument("new", help="path to the current version")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--skip-stems", action="store_true", help="skip stem separation (no demucs)")
    ap.add_argument("--diff-audio", action="store_true", help="write phase-invert diff wavs")
    args = ap.parse_args()

    for p in (args.old, args.new):
        if not Path(p).exists():
            print(json.dumps({"_failed": True, "error": "FileNotFound", "path": p}))
            return 1

    res = compare(args.old, args.new, skip_stems=args.skip_stems, diff_audio=args.diff_audio)
    text = json.dumps(res, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Saved: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
