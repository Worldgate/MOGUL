#!/usr/bin/env python3
"""
par2check.py - check (and fix) every par2 set under a folder, then report.

Walks the folder and all subfolders, finds every main .par2 file (skips the
.vol000+01.par2 style recovery volumes, par2 loads those by itself), and runs
par2 on each set from inside its own folder.

  python3 tools/par2check.py                 # current folder, repair what it can
  python3 tools/par2check.py /some/folder    # another folder
  python3 tools/par2check.py --verify-only   # check only, change nothing
  python3 tools/par2check.py --extras        # also look at other files in each
                                             # set's folder (finds renamed copies)
  python3 tools/par2check.py --cleanup       # remove the set's .par2 files once
                                             # it's OK or FIXED (and the NAME.1
                                             # damaged copies par2 left behind)

Results per set:
  OK          every file is correct
  FIXED       was damaged or missing, repaired (par2 keeps the damaged file as
              NAME.1 next to it; nothing is deleted)
  REPAIRABLE  damaged, enough recovery data to fix (only with --verify-only)
  CAN'T FIX   damaged and not enough recovery data (says how many blocks short)
  ERROR       par2 itself failed (bad par2 file, read error...)

--cleanup only touches sets that came out OK or FIXED. A FIXED set is
verified again first; if that second check doesn't pass, nothing is removed.
It deletes that set's own par2 files (NAME.par2 + NAME.volNN+NN.par2) and,
for repaired files, the NAME.1 copy par2 kept. Audio files are never touched.

Writes par2check_<date>_<time>.txt in the folder it checked. Ctrl-C stops it
and still writes the report for the sets done so far.

Needs par2 (par2cmdline): sudo apt install par2
"""
import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

VOL_RE = re.compile(r"\.vol\d+[+-]\d+\.par2$", re.IGNORECASE)
TARGET_RE = re.compile(
    r'Target: "(.+?)" - (found|damaged|missing|is a match for "(.+?)")'
    r"(?:\. Found (\d+) of (\d+) data blocks)?")
MATCH_RE = re.compile(r'File: "(.+?)" - is a match for "(.+?)"')
NEED_RE = re.compile(r"You need (\d+) more recovery blocks")

ORDER = ["CAN'T FIX", "ERROR", "REPAIRABLE", "FIXED", "OK"]


def find_sets(top):
    sets = [p for p in top.rglob("*")
            if p.is_file() and p.suffix.lower() == ".par2"
            and not VOL_RE.search(p.name)]
    return sorted(sets, key=lambda p: str(p).lower())


def run_set(par2, set_file, verify_only, extras):
    cmd = [par2, "v" if verify_only else "r", set_file.name]
    if extras:
        cmd += ["--"] + sorted(
            p.name for p in set_file.parent.iterdir()
            if p.is_file() and p.suffix.lower() != ".par2")
    try:
        proc = subprocess.run(cmd, cwd=set_file.parent,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return {"status": "ERROR", "files": {}, "detail": str(e)}
    text = proc.stdout.decode("utf-8", "replace")
    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]

    files = {}          # first report per file = how it was BEFORE any repair
    for line in lines:
        m = TARGET_RE.search(line)
        if not m or m.group(1) in files:
            continue
        name, what = m.group(1), m.group(2)
        if what == "damaged":
            files[name] = f"damaged ({m.group(4)} of {m.group(5)} blocks good)"
        elif what.startswith("is a match"):
            files[name] = f"found under another name: {m.group(3)}"
        else:
            files[name] = what

    for line in lines:   # extra files that turned out to be a renamed copy
        m = MATCH_RE.search(line)
        if m and files.get(m.group(2)) == "missing":
            files[m.group(2)] = (f"missing, found as \"{m.group(1)}\""
                                 + ("" if verify_only else " (renamed back)"))

    bad = any(v != "found" for v in files.values())
    need = next((m.group(1) for l in lines for m in [NEED_RE.search(l)] if m), None)
    code = proc.returncode
    detail = ""
    if code == 0:
        status = "FIXED" if bad else "OK"
    elif code == 1:
        status = "REPAIRABLE"
    elif code == 2:
        status = "CAN'T FIX"
        if need:
            detail = f"needs {need} more recovery blocks"
    else:
        status = "ERROR"
        detail = f"par2 exit code {code}: " + " | ".join(lines[-3:])
    return {"status": status, "files": files, "detail": detail}


def cleanup_set(par2, set_file, r):
    """Remove a finished set's par2 files (+ par2's NAME.1 backups).
    Returns how many files were removed, or None if the recheck failed."""
    folder = set_file.parent
    if r["status"] == "FIXED":
        recheck = subprocess.run([par2, "v", set_file.name], cwd=folder,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if recheck.returncode != 0:
            return None
    base = set_file.name[:-len(".par2")]
    vol = re.compile(re.escape(base) + r"\.vol\d+[+-]\d+\.par2$", re.IGNORECASE)
    doomed = [p for p in folder.iterdir() if p.is_file() and (
        p.name.lower() == set_file.name.lower() or vol.match(p.name))]
    for name, state in r["files"].items():
        if state != "found":
            for n in range(1, 10):
                backup = folder / f"{name}.{n}"
                if backup.is_file():
                    doomed.append(backup)
    for p in doomed:
        p.unlink()
    return len(doomed)


def write_report(top, results, verify_only, started, interrupted):
    stamp = started.strftime("%Y-%m-%d_%H%M%S")
    path = top / f"par2check_{stamp}.txt"
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ORDER}
    out = [f"par2check  {started:%Y-%m-%d %H:%M}  "
           f"{'verify only' if verify_only else 'verify + repair'}",
           f"folder: {top}", ""]
    if interrupted:
        out += ["STOPPED EARLY (Ctrl-C): only the sets below were checked.", ""]
    out.append(f"{len(results)} set(s): " + ", ".join(
        f"{counts[s]} {s}" for s in ORDER if counts[s]))
    out.append("")
    for status in ORDER:
        group = [r for r in results if r["status"] == status]
        if not group:
            continue
        out.append(f"== {status} ({len(group)}) ==")
        for r in group:
            out.append(f"  {r['set']}")
            if r["detail"]:
                out.append(f"      {r['detail']}")
            if r.get("cleaned"):
                out.append(f"      cleaned up: {r['cleaned']} par2/backup file(s) removed")
            if status != "OK":
                for name, state in sorted(r["files"].items()):
                    if state != "found":
                        out.append(f"      {name}: {state}")
        out.append("")
    if counts["FIXED"] and not any(r.get("cleaned") for r in results):
        out.append("Fixed files: par2 kept each damaged original as NAME.1 "
                   "next to the repaired file. Delete those yourself once happy.")
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

    par2 = shutil.which(args.par2)
    if not par2:
        sys.exit("par2 not found. Install it: sudo apt install par2")
    top = Path(args.folder).resolve()
    if not top.is_dir():
        sys.exit(f"Not a folder: {top}")

    sets = find_sets(top)
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
            r = run_set(par2, set_file, args.verify_only, args.extras)
            r["set"] = str(rel)
            if args.cleanup and r["status"] in ("OK", "FIXED"):
                n = cleanup_set(par2, set_file, r)
                if n is None:
                    r["status"] = "ERROR"
                    r["detail"] = "repaired, but the second check failed; nothing removed"
                else:
                    r["cleaned"] = n
            results.append(r)
            line = f"[{i:>{width}}/{len(sets)}] {r['status']:<10} {rel}"
            if r["detail"]:
                line += f"  ({r['detail']})"
            if r.get("cleaned"):
                line += f"  [cleaned up {r['cleaned']}]"
            print(line + " " * 10)
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopped. Writing the report for what's done...")

    report = write_report(top, results, args.verify_only, started, interrupted)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ORDER}
    print("\n" + ", ".join(f"{counts[s]} {s}" for s in ORDER if counts[s]))
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
