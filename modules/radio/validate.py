"""mogul.py radio validate

par2 check + repair for downloads before they're scanned. Drop downloads
(with their .par2 files) into mogul_monitor/radio/, in subfolders or loose.
(The folder is `[core] monitor` in mogul.toml; later a watcher will check
it by itself.)

For every par2 set found there:
  OK / FIXED   the set's audio files move to mogul_incoming/radio/ (flat),
               then the set's par2 files (and par2's NAME.1 damaged copies)
               are deleted. A FIXED set is verified a second time first.
               If the set is an archive (NAME.part1.rar..., NAME.rar + .r00...,
               .7z, .7z.001..., .zip) it's extracted first; the audio inside
               moves to incoming, then the archive pieces are deleted. Only
               if extraction and every move worked; otherwise the archive
               and par2 files stay (EXTRACT FAILED). Needs unrar or 7-Zip for
               rar/7z (sudo apt install unrar); zip needs nothing.
  COVERED      a second set for the same files (e.g. "NAME (2).par2", a
               duplicate download): its par2 files are deleted too.
  CAN'T FIX    left exactly where it is, par2 files included (try MultiPar).
  ERROR        left where it is.
Audio files with no par2 set at all are listed as NO PAR2 and left alone.
If a file of that name is already in incoming, the new one gets a number
in its notes so scan can still read the name: "O&A 2008-01-01.mp3" ->
"O&A 2008-01-01 -- (2).mp3", "O&A 2008-01-01 -- CF64k.mp3" ->
"O&A 2008-01-01 -- CF64k (2).mp3". Nothing is overwritten; audio is never
deleted. (If it's really the same audio, scan calls it DUPLICATE.)

--dry-run only checks (no repair, no moves, no deletes).
Report: mogul_reports/radio-validate_<date>_<time>.txt
"""

import os
import time
from pathlib import Path

from mogul_core import archives as A
from mogul_core import par2 as P

def run(args, config):
    radio_cfg = config.section("radio")
    exts = {e.lower().lstrip(".") for e in radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])}
    src = Path(config.folder("monitor", "mogul_monitor")) / "radio"
    dest = Path(config.incoming) / "radio"
    src.mkdir(parents=True, exist_ok=True)

    par2 = P.find_program(getattr(args, "par2", None) or "par2")
    if not par2:
        raise SystemExit("par2 not found. Install it: sudo apt install par2")

    sets = P.find_sets(src)
    audio = sorted(p for p in src.rglob("*")
                   if p.is_file() and (p.suffix.lower().lstrip(".") in exts
                                       or A.is_first_volume(p.name)))
    if not sets and not audio:
        print(f"Nothing to validate in {src}")
        return
    print(f"{len(sets)} par2 set(s) in {src}"
          f"{'  (dry run: check only, nothing repaired, moved or deleted)' if args.dry_run else ''}\n")

    # Pass 1: check / repair every set. Nothing moves until all are done,
    # so a second set in the same folder still finds its files.
    results = []
    width = len(str(len(sets)))
    interrupted = False
    try:
        for i, set_file in enumerate(sets, 1):
            rel = set_file.relative_to(src)
            print(f"[{i:>{width}}/{len(sets)}] checking   {rel}", end="\r", flush=True)
            r = P.run_set(par2, set_file, verify_only=args.dry_run, extras=args.extras)
            r["path"], r["rel"] = set_file, str(rel)
            results.append(r)
            line = f"[{i:>{width}}/{len(sets)}] {r['status']:<10} {rel}"
            if r["detail"]:
                line += f"  ({r['detail']})"
            print(line + " " * 10)
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopped. Nothing gets moved or deleted; writing the report...")

    P.mark_covered(results)

    # Pass 2: move the good ones to incoming (extracting archives first),
    # then clean up their par2 files.
    moved, not_moved, cleaned = [], [], 0
    extracted = 0
    moved_here = set()          # (folder, name) already moved this run

    def move_one(src_file, shown):
        """Move one audio file to incoming. True if it moved."""
        target = free_name(dest, src_file.name)
        why = P.move_file(src_file, target)
        if why:
            not_moved.append((src_file, why))
            print(f"NOT MOVED  {shown}  ({why})")
            return False
        moved.append((src_file, target))
        renamed = "" if target.name == src_file.name else "  (name was taken: numbered)"
        print(f"MOVED      {shown}  ->  incoming/radio/{target.name}{renamed}")
        return True

    if args.dry_run:
        for r in results:
            groups = A.group_volumes(r["files"])
            if groups and r["status"] in ("OK", "REPAIRABLE"):
                r["detail"] = (r["detail"] + "; " if r["detail"] else "") + \
                    f"{len(groups)} archive(s) to extract on a real run"
    if not args.dry_run and not interrupted:
        print()
        for r in results:
            if r["status"] == "FIXED" and not P.recheck(par2, r["path"]):
                r["status"] = "ERROR"
                r["detail"] = "repaired, but the second check failed; left alone"
        for r in results:
            if r["status"] not in ("OK", "FIXED"):
                continue
            folder = r["path"].parent
            failed = False
            groups = A.group_volumes(r["files"])
            in_archives = {v for _, vols in groups for v in vols}

            # loose audio files of the set
            for name in sorted(r["files"]):
                if name in in_archives or Path(name).suffix.lower().lstrip(".") not in exts:
                    continue
                src_file = folder / name
                if not src_file.exists():
                    if (folder, name) not in moved_here:
                        failed = True
                        not_moved.append((src_file, "not there after the check"))
                    continue
                if move_one(src_file, str(src_file.relative_to(src))):
                    moved_here.add((folder, name))
                else:
                    failed = True

            # archives: extract, move the audio out, then delete the archive
            for first, vols in groups:
                if all((folder, v) in moved_here for v in vols):
                    continue          # already handled by another set
                missing = [v for v in vols if not (folder / v).exists()]
                if missing:
                    failed = True
                    not_moved.append((folder / first, f"archive piece missing: {missing[0]}"))
                    continue
                xdir = folder / f"_extracting_{first}"
                n = 2
                while xdir.exists():
                    xdir = folder / f"_extracting_{first}_{n}"
                    n += 1
                print(f"EXTRACT    {(folder / first).relative_to(src)}"
                      f"  ({len(vols)} piece(s))", flush=True)
                why = A.extract(folder / first, xdir)
                if why:
                    failed = True
                    r["status"] = "EXTRACT FAILED"
                    r["detail"] = f"{first}: {why}"
                    print(f"EXTRACT FAILED  {first}: {why}")
                    continue
                extracted += 1
                inside = sorted(p for p in xdir.rglob("*") if p.is_file())
                audio_inside = [p for p in inside
                                if p.suffix.lower().lstrip(".") in exts]
                ok = True
                for p in audio_inside:
                    if not move_one(p, f"{first} > {p.relative_to(xdir)}"):
                        ok = False
                if not audio_inside:
                    r["detail"] = (r["detail"] + "; " if r["detail"] else "") + \
                        f"{first}: no audio inside"
                P.remove_empty_folders(xdir)
                try:
                    xdir.rmdir()
                except OSError:      # non-audio files came out too (.nfo...)
                    keep = folder / f"{first} - other files"
                    n = 2
                    while keep.exists():
                        keep = folder / f"{first} - other files ({n})"
                        n += 1
                    xdir.rename(keep)
                    r["detail"] = (r["detail"] + "; " if r["detail"] else "") + \
                        f"other files from {first} kept in \"{keep.name}\""
                if ok:
                    for v in vols:
                        (folder / v).unlink()
                        moved_here.add((folder, v))
                else:
                    failed = True
            if r["status"] == "EXTRACT FAILED":
                continue
            if failed:
                r["detail"] = (r["detail"] + "; " if r["detail"] else "") + \
                    "something couldn't be moved, par2 files kept"
            else:
                r["cleaned"] = P.remove_set(r["path"], r["files"])
                cleaned += r["cleaned"]
        for r in results:
            if r["status"] == "COVERED" and r["path"].exists():
                r["cleaned"] = P.remove_set(r["path"], {})
                cleaned += r["cleaned"]
        P.remove_empty_folders(src)

    # Audio that no set covers
    covered = {(r["path"].parent, n) for r in results
               for n in list(r["files"]) + r.get("matched", [])}
    no_par2 = [p for p in audio
               if (p.parent, p.name) not in covered and p.exists()]

    counts = {s: sum(1 for r in results if r["status"] == s) for s in P.ORDER}
    summary = ", ".join(f"{counts[s]} {s}" for s in P.ORDER if counts[s]) or "no sets"
    print(f"\n{summary}")
    if not args.dry_run:
        print(f"{len(moved)} file(s) moved to {dest}"
              f"{f', {extracted} archive(s) extracted' if extracted else ''}"
              f", {cleaned} par2/backup file(s) removed")
    if no_par2:
        print(f"{len(no_par2)} audio/archive file(s) with no par2 set, left alone (see report)")
    report = write_report(config, src, dest, results, moved, not_moved, no_par2,
                          args.dry_run, interrupted, summary)
    print(f"Report: {report}")
    if moved:
        print("\nNext: python3 mogul.py radio scan")


def free_name(folder, name):
    """name in folder, or the first free numbered version of it. The number
    goes in the notes part (after " -- ") so the filename still matches the
    radio incoming pattern."""
    target = folder / name
    if not target.exists() and not target.with_name(name + ".part").exists():
        return target
    stem, ext = os.path.splitext(name)
    lead = f"{stem} (" if " -- " in stem else f"{stem} -- ("
    n = 2
    while True:
        target = folder / f"{lead}{n}){ext}"
        if not target.exists():
            return target
        n += 1


def write_report(config, src, dest, results, moved, not_moved, no_par2,
                 dry_run, interrupted, summary):
    os.makedirs(config.reports, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(config.reports,
                        f"radio-validate_{stamp}{'_dry-run' if dry_run else ''}.txt")
    out = [f"radio validate  {time.strftime('%Y-%m-%d %H:%M')}  "
           f"{'DRY RUN (check only)' if dry_run else 'check + repair + move'}",
           f"from: {src}", f"to:   {dest}", ""]
    if interrupted:
        out += ["STOPPED EARLY (Ctrl-C): nothing was moved or deleted.", ""]
    out += [f"{len(results)} set(s): {summary}", ""]
    for status in P.ORDER:
        group = [r for r in results if r["status"] == status]
        if not group:
            continue
        out.append(f"== {status} ({len(group)}) ==")
        for r in group:
            out.append(f"  {r['rel']}")
            if r["detail"]:
                out.append(f"      {r['detail']}")
            if status not in ("OK", "COVERED"):
                for name, state in sorted(r["files"].items()):
                    if state != "found":
                        out.append(f"      {name}: {state}")
            if r.get("cleaned"):
                out.append(f"      par2 cleaned up: {r['cleaned']} file(s) removed")
        out.append("")
    if moved:
        out.append(f"== MOVED to incoming ({len(moved)}) ==")
        out += [f"  {a.relative_to(src)}" + ("" if a.name == b.name else f"  ->  {b.name}")
                for a, b in moved]
        out.append("")
    if not_moved:
        out.append(f"== NOT MOVED ({len(not_moved)}) ==")
        out += [f"  {a.relative_to(src)}: {why}" for a, why in not_moved]
        out.append("")
    if no_par2:
        out.append(f"== NO PAR2 ({len(no_par2)}): not checked, left alone ==")
        out += [f"  {p.relative_to(src)}" for p in no_par2]
        out.append("")
    if dry_run:
        out.append("Dry run: REPAIRABLE sets would be FIXED on a real run; "
                   "OK/FIXED/COVERED files would move to incoming.")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return path
