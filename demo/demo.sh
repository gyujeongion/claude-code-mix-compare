#!/usr/bin/env bash
# Real demo: runs the bundled make_demo.py, which synthesizes two mixes differing only by
# a 3 kHz layer and runs the actual compare.py — so the band-localized delta is genuine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
say(){ printf '\033[1;36m$ %s\033[0m\n' "$1"; sleep .5; }

echo; printf '\033[1mgit diff — but for audio. What actually changed between two mixes?\033[0m\n'; sleep 1.2; echo
say 'python3 examples/make_demo.py    # v4 vs v5 — engineer says "brightened the vocal"'
sleep .4
cd "$ROOT" && python3 examples/make_demo.py 2>&1 | grep -v '^PASS'
sleep 1
printf '\033[1;32m→ change localized: the biggest jump is high-mid (2.5–5 kHz). The brightening, quantified.\033[0m\n'; sleep 2.5
