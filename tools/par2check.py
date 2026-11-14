#!/usr/bin/env python3
"""
par2check.py - check (and fix) every par2 set under ANY folder, then report.
(For the MOGUL flow, `mogul.py radio validate` does the same on
mogul_validate/radio and moves the good files to incoming.)

  python3 tools/par2check.py                 # current folder, repair what it can
  python3 tools/par2check.py /some/folder    # another folder
  python3 tools/par2check.py --verify-only   # check only, change nothing
  python3 tools/par2check.py --extras        # also look at other files in each
                                             # set's folder (finds renamed copies)
  python3 tools/par2check.py --cleanup       # remove a set's .par2 files once
                                             # it's OK or FIXED (and the NAME.1
                                             # damaged copies par2 left behind)

Results per set: OK, FIXED, REPAIRABLE (only with --verify-only), CAN'T FIX,
COVERED (a second set for files another set in the folder found good, e.g. a
duplicate "NAME (2).par2"), ERROR. Details in mogul_core/par2.py.

--cleanup only touches OK, FIXED and COVERED sets; a FIXED set is verified
again first. Audio files are never touched. Moves nothing.

Writes par2check_<date>_<time>.txt in the folder it checked. Ctrl-C stops it
and still writes the report (cleanup is skipped then).

Needs par2 (par2cmdline): sudo apt install par2
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mogul_core import par2 as P     # noqa: E402


def write_report(top, results, verify_only, started, interrupted):
    path = top / f"par2check_{started:%Y-%m-%d_%H%M%S}.txt"
    counts = {s: sum(1 for r in results if r["status"] == s) for s in P.ORDER}
    out = [f"par2check  {started:%Y-%m-%d %H:%M}  "
           f"{'verify only' if verify_only else 'verify + repair'}",
           f"folder: {top}", ""]
    if interrupted:
        out += ["STOPPED EARLY (Ctrl-C): only the sets below were checked.", ""]
    out.append(f"{len(results)} set(s): " + ", ".join(
        f"{counts[s]} {s}" for s in P.ORDER if counts[s]))
    out.append("")
    for status in P.ORDER:
        group = [r for r in results if r["status"] == status]
        if not group:
            continue
        out.append(f"== {status} ({len(group)}) ==")
        for r in group:
            out.append(f"  {r['rel']}")
            if r["detail"]:
                out.append(f"      {r['detail']}")
            if r.get("cleaned"):
                out.append(f"      cleaned up: {r['cleaned']} par2/backup file(s) removed")
            if status not in ("OK", "COVERED"):
                for name, state in sorted(r["files"].items()):
                    if state != "found":
                        out.append(f"      {name}: {state}")
        out.append("")
    if counts["FIXED"] and not any(r.get("cleaned") for r in results):
        out.append("Fixed files: par2 kept each damaged original as NAME.1 "
                   "next to the repaired file. Delete those yourself once happy "
                   "(or run with --cleanup).")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(
        description="Check (and repair) every par2 set under a folder.")
    ap.add_argument("folder", nargs="?", default=".",
                    help="folder to search (default: current folder)")
    ap.add_argument("--verify-only", action="store_true",
                    help="only check, don't repair anything")
    ap.add_argument("--extras", action="store_true",
                    help="also scan the other files in each set's folder "
                         "(finds renamed copies; slower)")
    ap.add_argument("--cleanup", action="store_true",
                    help="remove a set's par2 files (and par2's NAME.1 backups) "
                         "once it's OK or FIXED")
    ap.add_argument("--par2", default="par2", help="par2 program to use")
    args = ap.parse_args()

    par2 = P.find_program(args.par2)
    if not par2:
        sys.exit("par2 not found. Install it: sudo apt install par2")
    top = Path(args.folder).resolve()
    if not top.is_dir():
        sys.exit(f"Not a folder: {top}")

    sets = P.find_sets(top)
    if not sets:
        sys.exit(f"No .par2 sets found under {top}")
    print(f"{len(sets)} par2 set(s) under {top}"
          f"{'  (verify only)' if args.verify_only else ''}\n")

    started = datetime.datetime.now()
    results = []
    interrupted = False
    width = len(str(len(sets)))
    try:
        for i, set_file in enumerate(sets, 1):
            rel = set_file.relative_to(top)
            print(f"[{i:>{width}}/{len(sets)}] checking  {rel}", end="\r", flush=True)
            r = P.run_set(par2, set_file, args.verify_only, args.extras)
            r["path"], r["rel"] = set_file, str(rel)
            results.append(r)
            line = f"[{i:>{width}}/{len(sets)}] {r['status']:<10} {rel}"
            if r["detail"]:
                line += f"  ({r['detail']})"
            print(line + " " * 10)
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopped. Writing the report for what's done...")

    P.mark_covered(results)
    if args.cleanup and not interrupted:
        for r in results:
            if r["status"] == "FIXED" and not P.recheck(par2, r["path"]):
                r["status"] = "ERROR"
                r["detail"] = "repaired, but the second check failed; nothing removed"
            if r["status"] in P.GOOD and r["path"].exists():
                r["cleaned"] = P.remove_set(r["path"], r["files"]
                                            if r["status"] != "COVERED" else {})
                print(f"cleaned up {r['cleaned']:>3}  {r['rel']}")

    report = write_report(top, results, args.verify_only, started, interrupted)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in P.ORDER}
    print("\n" + ", ".join(f"{counts[s]} {s}" for s in P.ORDER if counts[s]))
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
