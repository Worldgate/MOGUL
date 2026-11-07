"""Radio fingerprint pass.

Adds the acoustic fingerprint digest to every catalog entry that doesn't
have one yet, e.g. files scanned with --no-fingerprint. Works on several
files at once (--jobs), since fingerprinting is CPU work.

Files are found by the catalog, not by searching:
  - imported entries: in the library, at <folder_template>/<filename>
  - entries not imported yet: in mogul_incoming/radio, by original filename
A file is only fingerprinted if its size matches the catalog, so the wrong
file can't get attached to an entry.

If fpcalc can't read a file (usually damaged audio), that's recorded on
the entry under status.fingerprint_failed, and it isn't retried on later
runs unless you use --retry-failed.

Moves nothing. The catalog is saved after every finished file.
"""

import concurrent.futures as cf
import os
import shutil
import sys

from mogul_core import VERSION, fingerprint, store
from mogul_core.progress import Progress

from .importer import target_folder
from .scan import load_catalog, find_files


def default_jobs():
    return max(1, (os.cpu_count() or 2) - 1)


def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    incoming = os.path.join(config.incoming, "radio")
    jobs = args.jobs or default_jobs()

    if shutil.which("fpcalc") is None:
        sys.exit("'fpcalc' is not installed (sudo apt install libchromaprint-tools)")

    manifests, index = load_catalog(config.json_store)

    # files waiting in incoming, by (name, size), for entries not imported yet
    waiting = {}
    if os.path.isdir(incoming):
        for path in find_files(incoming, extensions):
            try:
                waiting.setdefault((os.path.basename(path), os.path.getsize(path)), path)
            except OSError:
                pass

    todo, not_found, failed_before = [], [], 0
    for mpath, manifest in manifests.items():
        for entry in manifest.get("files", []):
            if entry.get("fingerprint"):
                continue
            status = entry.get("status") or {}
            if status.get("fingerprint_failed") and not args.retry_failed:
                failed_before += 1
                continue
            size = entry["physical"].get("size")
            if status.get("imported"):
                path = os.path.join(target_folder(library, folder_t, entry), entry["naming"]["filename"])
            else:
                path = waiting.get((entry["naming"].get("original_filename"), size))
            if not path or not os.path.isfile(path):
                not_found.append(entry["naming"]["filename"])
                continue
            if os.path.getsize(path) != size:
                not_found.append(entry["naming"]["filename"] + " (size doesn't match the catalog)")
                continue
            todo.append((path, mpath, entry))

    print(f"MOGUL {VERSION} radio fingerprint{' (DRY RUN, catalog not changed)' if args.dry_run else ''}")
    print(f"  library:  {library}")
    print(f"  to do:    {len(todo)}")
    print(f"  jobs:     {jobs} at a time")
    if failed_before:
        print(f"  skipping: {failed_before} that failed before (use --retry-failed to try again)")
    if not_found:
        print(f"  not found: {len(not_found)} (see report)")
    print()

    report = {
        "fingerprint": "radio",
        "started": store.now(),
        "finished": None,
        "dry_run": bool(args.dry_run),
        "done": [], "failed": [], "not_found": not_found,
    }

    if args.dry_run:
        for path, _, _ in todo:
            where = (os.path.relpath(path, library) if path.startswith(library + os.sep)
                     else "incoming/" + os.path.relpath(path, incoming))
            print(f"  would fingerprint {where}")
        todo = []

    progress = Progress([p for p, _, _ in todo])
    done_bytes = 0

    def work(item):
        path, _, _ = item
        try:
            return item, fingerprint.digest_entry(path), None
        except fingerprint.FingerprintError as e:
            return item, None, str(e)

    try:
        with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(work, item) for item in todo]
            for n, fut in enumerate(cf.as_completed(futures), 1):
                (path, mpath, entry), fp, error = fut.result()
                done_bytes += entry["physical"].get("size") or 0
                tag = progress.label(n, done_bytes)
                name = entry["naming"]["filename"]
                stamp = store.now()
                if fp:
                    entry["fingerprint"] = fp
                    (entry.get("status") or {}).pop("fingerprint_failed", None)
                    report["done"].append(name)
                    print(f"{tag} DONE      {name}  ({fp['digest_minutes']} min)")
                else:
                    entry.setdefault("status", {})["fingerprint_failed"] = {"date": stamp, "error": error}
                    report["failed"].append({"file": name, "error": error})
                    print(f"{tag} FAILED    {name}: {error}")
                manifests[mpath]["header"]["updated"] = stamp
                manifests[mpath]["header"]["mogul_version"] = VERSION
                store.save(mpath, manifests[mpath])
    except KeyboardInterrupt:
        print("\nStopping... files already finished are saved.")
        raise

    report["finished"] = store.now()
    print()
    print(f"Done in {progress.elapsed()}")
    print(f"  fingerprinted: {len(report['done'])}")
    if report["failed"]:
        print(f"  failed:        {len(report['failed'])}  (probably damaged audio, recorded on the entry)")
    if not_found:
        print(f"  not found:     {len(not_found)}")

    import time
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(config.reports, f"radio-fingerprint_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(path, report)
    print(f"\nReport: {path}")
    return 0
