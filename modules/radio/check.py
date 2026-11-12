"""Radio check (mogul.py radio check).

Reads every file all the way through looking for damage, whether or not
it fingerprinted fine:
  - MP3s: mp3val finds every spot where the audio stream breaks; each one
    is mapped to a time in the show and grouped into stretches
  - other formats: decoder errors from ffmpeg
  - every file: how much is empty (zero bytes) and the biggest empty gap

What gets checked:
  default        every cataloged file that hasn't been checked yet
                 (library, or incoming if not imported yet)
  --quarantine   the files in quarantine instead
  --recheck      check again even if checked before

Results are saved on each entry as status.check, so a long run can be
stopped and picked up again. A file with damaged spots also gets
status.damaged, so it shows up in `mogul.py radio damaged` for
--ok / --keep / --bad. Quarantined entries get quarantine.check.

Writes two reports to mogul_reports/:
  radio-check_<date>_<time>.txt   the one to read: every damaged file,
                                  worst first, one line each, plus files
                                  that can't be read and the counts
  radio-check_<date>_<time>.json  the same with every stretch listed

Moves nothing. --dry-run checks and reports but writes nothing to the
catalog. MP3s need mp3val (sudo apt install mp3val).
"""

import concurrent.futures as cf
import os
import shutil
import sys
import time

from mogul_core import VERSION, damage, store
from mogul_core.progress import Progress

from .importer import target_folder
from .quarantine import load_quarantine, quarantine_path
from .scan import load_catalog, find_files


def default_jobs():
    # reading files is mostly disk work; a few at once is plenty
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def compact(result):
    """What gets stored on the entry (stretches stay in the JSON report)."""
    keep = {"date": store.now(), "tool": result["tool"], "spots": result["spots"]}
    if not result["readable"]:
        keep["readable"] = False
        keep["error"] = result["error"]
    if result["spots"] and result["first"] is not None:
        keep.update({"stretches": len(result["stretches"]), "first": result["first"],
                     "last": result["last"], "missing_seconds": result["missing_seconds"]})
    for k in ("real_duration", "header_missing_seconds"):
        if result.get(k):
            keep[k] = result[k]
    if result["warnings"]:
        keep["warnings"] = result["warnings"]
    if "empty_percent" in result:
        keep["empty_percent"] = result["empty_percent"]
    if result.get("biggest_gap"):
        keep["biggest_gap"] = result["biggest_gap"]
    return keep


def mark_from_check(entry, check):
    """status.damaged from a check result (keeps any verdict already given)."""
    if not check.get("spots") and check.get("readable", True):
        return
    status = entry.setdefault("status", {})
    old = status.get("damaged") or {}
    status["damaged"] = {
        "date": check["date"],
        "decode_errors": check.get("spots", 0),
        "found_by": "check",
        "checked": old.get("checked"),
    }


def severity(check):
    """Sort key: unreadable first, then most audio missing, then most spots."""
    return (check.get("readable", True), -(check.get("missing_seconds") or 0), -(check.get("spots") or 0))


def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    qroot = config.resolve(radio_cfg.get("quarantine", "mogul_quarantine/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    incoming = os.path.join(config.incoming, "radio")
    json_root = config.json_store
    jobs = args.jobs or default_jobs()

    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        sys.exit("'ffmpeg' / 'ffprobe' are not installed")
    no_mp3val = shutil.which("mp3val") is None

    todo, not_found, skipped = [], [], 0
    manifests = {}
    qdata = None

    if args.quarantine:
        qdata = load_quarantine(json_root)
        for entry in qdata["entries"]:
            q = entry.get("quarantine") or {}
            if q.get("check") and not args.recheck:
                skipped += 1
                continue
            path = os.path.join(qroot, q.get("to", ""))
            if os.path.isfile(path):
                todo.append((path, None, entry))
            else:
                not_found.append(entry["naming"]["filename"])
        where_label = qroot
    else:
        manifests, _ = load_catalog(json_root)
        waiting = {}
        if os.path.isdir(incoming):
            for p in find_files(incoming, extensions):
                try:
                    waiting.setdefault((os.path.basename(p), os.path.getsize(p)), p)
                except OSError:
                    pass
        for mpath, manifest in manifests.items():
            for entry in manifest.get("files", []):
                status = entry.get("status") or {}
                if status.get("check") and not args.recheck:
                    skipped += 1
                    continue
                size = entry["physical"].get("size")
                if status.get("imported"):
                    path = os.path.join(target_folder(library, folder_t, entry), entry["naming"]["filename"])
                else:
                    path = waiting.get((entry["naming"].get("original_filename"), size))
                if not path or not os.path.isfile(path):
                    not_found.append(entry["naming"]["filename"])
                    continue
                todo.append((path, mpath, entry))
        where_label = library

    print(f"MOGUL {VERSION} radio check{' (DRY RUN, catalog not changed)' if args.dry_run else ''}")
    print(f"  checking: {'quarantine' if args.quarantine else 'library'} ({where_label})")
    print(f"  to do:    {len(todo)}")
    print(f"  jobs:     {jobs} at a time")
    if skipped:
        print(f"  skipping: {skipped} checked before (use --recheck to check again)")
    if not_found:
        print(f"  not found: {len(not_found)} (see report)")
    if no_mp3val:
        print("  NOTE: mp3val isn't installed, so MP3s only get a decoder error count with no times")
        print("        (sudo apt install mp3val)")
    print()

    report = {
        "check": "radio",
        "target": "quarantine" if args.quarantine else "library",
        "started": store.now(),
        "finished": None,
        "dry_run": bool(args.dry_run),
        "files": [],
        "not_found": not_found,
    }

    progress = Progress([p for p, _, _ in todo])
    done_bytes = 0

    def work(item):
        path = item[0]
        try:
            return item, damage.analyze(path)
        except OSError as e:
            return item, {"tool": None, "readable": False, "error": str(e), "spots": 0,
                          "stretches": [], "first": None, "last": None,
                          "missing_seconds": 0.0, "warnings": []}

    try:
        with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(work, item) for item in todo]
            for n, fut in enumerate(cf.as_completed(futures), 1):
                (path, mpath, entry), result = fut.result()
                done_bytes += entry["physical"].get("size") or 0
                tag = progress.label(n, done_bytes)
                name = entry["naming"]["filename"]
                check = compact(result)
                if not result["readable"]:
                    word = "UNREADABLE"
                elif result["spots"] or result.get("biggest_gap") or (result.get("header_missing_seconds") or 0) >= 1:
                    word = "DAMAGED"
                else:
                    word = "CLEAN"
                print(f"{tag} {word:<10} {name}" + ("" if word == "CLEAN" else f"  {damage.summary(result)}"))
                report["files"].append({"file": name, "result": word, **result})

                if args.dry_run:
                    continue
                if args.quarantine:
                    entry.setdefault("quarantine", {})["check"] = check
                    qdata["updated"] = store.now()
                    store.save(quarantine_path(json_root), qdata)
                else:
                    entry.setdefault("status", {})["check"] = check
                    mark_from_check(entry, check)
                    manifests[mpath]["header"]["updated"] = store.now()
                    manifests[mpath]["header"]["mogul_version"] = VERSION
                    store.save(mpath, manifests[mpath])
    except KeyboardInterrupt:
        print("\nStopping... files already checked are saved.")
        raise

    report["finished"] = store.now()
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    suffix = "_dry-run" if args.dry_run else ""
    os.makedirs(config.reports, exist_ok=True)
    json_path = os.path.join(config.reports, f"radio-check_{stamp}{suffix}.json")
    txt_path = os.path.join(config.reports, f"radio-check_{stamp}{suffix}.txt")
    store.save(json_path, report)

    # The readable report covers everything checked so far, not just this
    # run, so it's always the full to-do list.
    rows = []
    if args.quarantine:
        source = qdata["entries"]
        for entry in source:
            c = (entry.get("quarantine") or {}).get("check")
            if c:
                rows.append((entry, c, "quarantined"))
    else:
        for manifest in manifests.values():
            for entry in manifest.get("files", []):
                c = (entry.get("status") or {}).get("check")
                if c:
                    verdict = (((entry.get("status") or {}).get("damaged") or {}).get("checked") or {}).get("verdict")
                    rows.append((entry, c, verdict or "not judged"))
    if args.dry_run:
        # nothing was saved on entries; use this run's results
        rows = [(None, compact(f), "dry run") for f in report["files"]]
        names = [f["file"] for f in report["files"]]
    else:
        names = [e["naming"]["filename"] for e, _, _ in rows]

    lines = [
        f"MOGUL radio check report - {store.now()}",
        f"{'Quarantine' if args.quarantine else 'Library'}: {where_label}",
        "",
    ]
    bad = sorted([(n, c, v) for n, (e, c, v) in zip(names, rows) if not c.get("readable", True)],
                 key=lambda r: r[0])
    damaged = sorted([(n, c, v) for n, (e, c, v) in zip(names, rows)
                      if c.get("readable", True) and (c.get("spots") or c.get("biggest_gap")
                                                      or (c.get("header_missing_seconds") or 0) >= 1)],
                     key=lambda r: (severity(r[1]), r[0]))
    clean = len(rows) - len(bad) - len(damaged)
    lines.append(f"{len(rows)} checked: {clean} clean, {len(damaged)} damaged, {len(bad)} can't be read")
    if not_found:
        lines.append(f"{len(not_found)} cataloged file(s) not found (listed at the end)")
    lines.append("")
    width = max([len(n) for n, _, _ in damaged + bad] + [20])
    if bad:
        lines.append("CAN'T BE READ")
        for n, c, v in bad:
            lines.append(f"  {n:<{width}}  {damage.summary(c)}   [{v}]")
        lines.append("")
    if damaged:
        lines.append("DAMAGED (worst first)")
        for n, c, v in damaged:
            lines.append(f"  {n:<{width}}  {damage.summary(c)}   [{v}]")
            if c.get("warnings"):
                lines.append(f"  {'':<{width}}  also: " + "; ".join(c["warnings"]))
        lines.append("")
    if not_found:
        lines.append("NOT FOUND")
        for n in not_found:
            lines.append(f"  {n}")
        lines.append("")
    lines.append("Where exactly:  python3 tools/damagemap.py FILE")
    if not args.quarantine:
        lines.append("After listening: mogul.py radio damaged --ok / --keep / --bad NAME")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print()
    print(f"Done in {progress.elapsed()}")
    print(f"  {lines[3]}")
    print(f"\nReport to read: {txt_path}")
    print(f"Full details:   {json_path}")
    return 0
