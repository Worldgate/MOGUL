#!/usr/bin/env python3
"""Every copy of one air date, side by side.

    python3 tools/sameday.py 2008-09-15
    python3 tools/sameday.py 2008-09-15 --config test.toml

Lists every cataloged recording of that date (library, incoming and
quarantine) with format, bitrate, length, notes and what `radio check`
found, grouped by show, then how alike each pair within a show sounds
(fingerprint digest similarity): 95% and up = same recording. Across
shows it only speaks up when two recordings filed under different shows
sound the same (95%+), i.e. a re-encoded copy with the wrong show code.
--all lists every cross-show pair too.

Handy for picking the best copy, and for spotting a copy that's shorter
than the others (audio missing). Reads only; changes nothing.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mogul_core import damage, fingerprint, store   # noqa: E402
from mogul_core.config import Config                 # noqa: E402
from modules.radio.quarantine import load_quarantine  # noqa: E402
from modules.radio.scan import load_catalog          # noqa: E402


def mmss(seconds):
    if not seconds:
        return "?"
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="Every copy of one air date, side by side.")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--all", action="store_true",
                    help="also list every cross-show comparison, not just suspicious ones")
    ap.add_argument("--config", help="use this mogul.toml instead of the default one")
    args = ap.parse_args()
    try:
        y, m, d = (int(x) for x in args.date.split("-"))
    except ValueError:
        sys.exit("Date must look like 2008-09-15")

    config = Config(args.config)
    manifests, _ = load_catalog(config.json_store)
    found = []
    for manifest in manifests.values():
        for e in manifest.get("files", []):
            n = e["naming"]
            if (n["year"], n["month"], n["day"]) == (y, m, d):
                status = e.get("status") or {}
                where = "library" if status.get("imported") else "incoming"
                found.append((e, where, status.get("check"),
                              ((status.get("damaged") or {}).get("checked") or {}).get("verdict")))
    for e in load_quarantine(config.json_store).get("entries", []):
        n = e["naming"]
        if (n["year"], n["month"], n["day"]) == (y, m, d):
            found.append((e, "QUARANTINE", (e.get("quarantine") or {}).get("check"), None))

    if not found:
        print(f"Nothing cataloged for {args.date}.")
        return 0
    found.sort(key=lambda r: (r[0]["naming"]["show"], -(r[0]["physical"].get("duration") or 0)))

    def length(r):
        return (r[2] or {}).get("real_duration") or r[0]["physical"].get("duration") or 0

    # group by show; numbers run on across groups so pairs can be named
    shows = {}
    for i, r in enumerate(found, 1):
        shows.setdefault(r[0]["naming"]["show"], []).append((i, r))

    print(f"{args.date}: {len(found)} recording(s) of {len(shows)} show(s)\n")
    for show, rows in shows.items():
        print(f"== {show} ({len(rows)}) ==\n")
        longest = max(length(r) for _, r in rows)
        for i, (e, where, check, verdict) in rows:
            n, p = e["naming"], e["physical"]
            kbps = f"{round((p.get('bitrate') or 0) / 1000)}k" if p.get("bitrate") else "?"
            ch = {1: "mono", 2: "stereo"}.get(p.get("channels"), "")
            # the real length (from `radio check`) beats the header's claim
            dur = length((e, where, check, verdict))
            if (check or {}).get("real_duration"):
                real = " (real length)"
            elif (p.get("codec") or "").lower() == "mp3":
                real = " (from the header; run radio check for the real length)"
            else:
                real = ""
            short = f"  ({mmss(longest - dur)} shorter than the longest)" if longest - dur >= 5 else ""
            print(f"{i}. {n['filename']}   [{where}]")
            print(f"   {(p.get('codec') or '?')} {kbps} {ch}, {mmss(dur)}{real}{short}")
            if n.get("notes"):
                print(f"   notes: {n['notes']}")
            if check:
                print(f"   check: {damage.summary(check)}" + (f"   [{verdict}]" if verdict else ""))
            else:
                print("   check: not checked yet (mogul.py radio check)")
            print()

        dg = [(i, (r[0].get("fingerprint") or {}).get("digest")) for i, r in rows]
        dg = [(i, d) for i, d in dg if d]
        if len(dg) >= 2:
            print("   How alike they sound (fingerprint digest):")
            for x in range(len(dg)):
                for y_ in range(x + 1, len(dg)):
                    (ia, da), (ib, db) = dg[x], dg[y_]
                    sim = fingerprint.similarity(da, db)
                    label = ("same recording" if sim >= 95 else
                             "unsure" if sim >= 90 else
                             "different recording (or the same show cut differently)")
                    print(f"   {ia} vs {ib}: {sim:.1f}%  {label}")
            print()

    # across shows: only speak up when two "different shows" sound the same
    # (a re-encoded copy filed under the wrong show code)
    if len(shows) > 1:
        names = list(shows)
        hits = []
        for x in range(len(names)):
            for y_ in range(x + 1, len(names)):
                for ia, ra in shows[names[x]]:
                    for ib, rb in shows[names[y_]]:
                        da = (ra[0].get("fingerprint") or {}).get("digest")
                        db = (rb[0].get("fingerprint") or {}).get("digest")
                        if da and db:
                            sim = fingerprint.similarity(da, db)
                            if sim >= 95 or args.all:
                                hits.append((ia, ib, sim))
        if hits:
            print("Across shows:")
            for ia, ib, sim in hits:
                warn = "  <- SAME RECORDING under two show names? one may be misnamed" if sim >= 95 else ""
                print(f"   {ia} vs {ib}: {sim:.1f}%{warn}")
        else:
            print("Across shows: nothing sounds alike (as it should be).")
        print()

    print("(Compared minute by minute from the start: a copy with a piece missing,")
    print(" or with commercials cut, stops lining up and scores like a different")
    print(" recording; two different shows score ~83%.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
