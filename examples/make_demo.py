#!/usr/bin/env python3
"""
make_demo.py — reproducible smoke test for compare.py.

Generates two synthetic mixes that differ in a known way (B adds a 3 kHz layer and
widens it slightly), runs compare.py with --skip-stems, and prints the octave-band
delta so you can confirm the tool localizes the change to the right band.

    python3 examples/make_demo.py

Expected: the largest positive delta lands in the high-mid band (2.56–5.12 kHz), which
is where 3 kHz lives. Energy spills into neighbor bands (presence below, high above)
through the band-pass filter skirts — that's expected, the band-pass isn't a brick wall.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
HERE = Path(__file__).resolve().parent
COMPARE = HERE.parent / "skills" / "mix-compare" / "compare.py"


def main():
    t = np.linspace(0, 3, SR * 3, endpoint=False)
    # A: 440 Hz tone + a bit of 100 Hz, near-mono
    L = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 100 * t)
    R = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 100 * t + 0.1)
    # B: same, plus a 3 kHz layer, panned to widen it
    L2 = L + 0.1 * np.sin(2 * np.pi * 3000 * t)
    R2 = R + 0.1 * np.sin(2 * np.pi * 3000 * t + 0.5)

    with tempfile.TemporaryDirectory(prefix="mixcompare_demo_") as td:
        a, b = Path(td) / "mixA.wav", Path(td) / "mixB.wav"
        sf.write(a, np.stack([L, R], 1), SR)
        sf.write(b, np.stack([L2, R2], 1), SR)

        out = subprocess.run(
            [sys.executable, str(COMPARE), str(a), str(b), "--skip-stems"],
            capture_output=True, text=True, check=True,
        )
        d = json.loads(out.stdout)

    print(f"alignment:    {d['alignment']['note']}")
    print(f"phase_diff %: {d['phase_diff']['ratio_percent']}")
    print("octave-band delta (new - old):")
    bands = d["fullmix_octave_delta"]
    top = max(bands.items(), key=lambda kv: kv[1]["delta_db"])
    for name, info in bands.items():
        mark = "  <- largest" if name == top[0] else ""
        print(f"  {name:11} {info['hz']:>11} Hz   {info['delta_db']:+6.2f} dB{mark}")

    band = top[0]
    ok = band == "high-mid"
    print(f"\nLargest change in: {band} ({bands[band]['hz']} Hz)")
    print("PASS — 3 kHz localized to the high-mid band." if ok
          else f"NOTE — expected high-mid, got {band}.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
