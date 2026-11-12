#!/usr/bin/env python3
"""Where in the audio is a file damaged?

    python3 tools/damagemap.py FILE.mp3 [FILE2.mp3 ...]

Uses mp3val to find every spot where the audio stream breaks, maps each to
a time in the show, groups spots close together into stretches, and shows
roughly how much audio is missing at each. Reads only; changes nothing.

  - a click, bloop or chirp: a damaged frame got played as noise
  - a jump: the junk replaced real audio, so a bit of the show is gone
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mogul_core.damage import analyze, hms, summary  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for path in sys.argv[1:]:
        r = analyze(path)
        print(path)
        print(f"  {summary(r)}  (found by {r['tool']})")
        for w in r["warnings"]:
            print(f"  also: {w}")
        if r["stretches"]:
            print()
            print(f"  {'where':<19} {'spots':>5}   audio likely missing")
            for s in r["stretches"]:
                where = hms(s["start"]) if s["start"] == s["end"] else f"{hms(s['start'])}-{hms(s['end'])}"
                print(f"  {where:<19} {s['count']:>5}   about {s['missing']} s")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
