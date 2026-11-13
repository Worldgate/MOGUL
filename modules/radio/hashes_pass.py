"""Radio hashes pass.

Adds any whole-file hashes from `hashes` in mogul.toml that a catalog
entry doesn't have yet. For example md5 and sha1 (the two Internet
Archive publishes for every file), added to the list after most of the
library was already scanned. New scans take every listed hash anyway;
this fills in the older entries, the same way the fingerprint pass
fills in missing fingerprints.

Files are found by the catalog, not by searching:
  - imported entries: in the library, at <folder_template>/<filename>
  - entries not imported yet: in mogul_incoming/radio, by original filename
A file is only used if its size matches the catalog, AND one hash the
entry already has (blake3 when there is one) still matches the file, read
in the same pass. So new hashes can never be attached to a file that has
changed since it was scanned (e.g. retagged); those are listed as CHANGED
(`radio verify --update` brings them up to date first).

Moves nothing. Disk-bound, so it runs a few files at once (--jobs,
default 4). The catalog is saved after every finished file.
"""

import concurrent.futures as cf
import os
import time

from mogul_core import VERSION, hashing, store
from mogul_core.progress import Progress

from .importer import target_folder
from .scan import find_files, load_catalog


def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    incoming = os.path.join(config.incoming, "radio")
    wanted = list(config.hashes)
    jobs = args.jobs or 4

    problems = hashing.check_available(wanted)
    if problems:
        raise SystemExit("\n".join(problems))

    manifests, _ = load_catalog(config.json_store)

    waiting = {}
    if os.path.isdir(incoming):
        for path in find_files(incoming, extensions):
            try:
                waiting.setdefault((os.path.basename(path), os.path.getsize(path)), path)
            except OSError:
                pass

    todo, not_found, complete = [], [], 0
    for mpath, manifest in manifests.items():
        for entry in manifest.get("files", []):
            have = entry["physical"].get("hashes") or {}
            missing = [h for h in wanted if not have.get(h)]
            if not missing:
                complete += 1
                continue
            # one whole-file hash the entry already has, to prove it's the same file
            proof = next((h for h in ("blake3", "sha256", "sha512", "sha1", "md5")
                          if have.get(h) and h in hashing.SUPPORTED), None)
            name = entry["naming"]["filename"]
            size = entry["physical"].get("size")
            if (entry.get("status") or {}).get("imported"):
                path = os.path.join(target_folder(library, folder_t, entry), name)
            else:
                path = waiting.get((entry["naming"].get("original_filename"), size))
            if not path or not os.path.isfile(path) or os.path.getsize(path) != size:
                not_found.append(name)
                continue
            todo.append((path, mpath, entry, missing, proof))

    print(f"MOGUL {VERSION} radio hashes{' (DRY RUN, catalog not changed)' if args.dry_run else ''}")
    print(f"  hashes:    {', '.join(wanted)} (from mogul.toml)")
    print(f"  to do:     {len(todo)}   already complete: {complete}")
    print(f"  jobs:      {jobs} at a time")
    if not_found:
        print(f"  not found: {len(not_found)} (see report)")
    print()

    report = {"hashes": "radio", "started": store.now(), "finished": None,
              "dry_run": bool(args.dry_run), "wanted": wanted,
              "done": [], "changed": [], "failed": [], "not_found": not_found}

    if args.dry_run:
        for path, _, entry, missing, _ in todo:
            print(f"  would add {', '.join(missing)} to {entry['naming']['filename']}")
        todo = []

    progress = Progress([t[0] for t in todo])
    done_bytes = 0

    def work(item):
        path, _, _, missing, proof = item
        names = missing + ([proof] if proof else [])
        try:
            return item, hashing.file_hashes(path, names), None
        except (OSError, ValueError) as e:
            return item, None, str(e)

    try:
        with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(work, item) for item in todo]
            for n, fut in enumerate(cf.as_completed(futures), 1):
                (path, mpath, entry, missing, proof), got, error = fut.result()
                done_bytes += entry["physical"].get("size") or 0
                tag = progress.label(n, done_bytes)
                name = entry["naming"]["filename"]
                have = entry["physical"].setdefault("hashes", {})
                if got is None:
                    report["failed"].append({"file": name, "error": error})
                    print(f"{tag} FAILED    {name}: {error}")
                    continue
                if proof and got[proof] != have[proof]:
                    report["changed"].append(name)
                    print(f"{tag} CHANGED   {name}: file isn't the one scanned ({proof} differs); "
                          "run radio verify --update first")
                    continue
                for h in missing:
                    have[h] = got[h]
                report["done"].append(name)
                print(f"{tag} ADDED     {name}  ({', '.join(missing)})")
                manifests[mpath]["header"]["updated"] = store.now()
                manifests[mpath]["header"]["mogul_version"] = VERSION
                store.save(mpath, manifests[mpath])
    except KeyboardInterrupt:
        print("\nStopping... files already finished are saved.")
        raise

    report["finished"] = store.now()
    print()
    print(f"Done in {progress.elapsed()}")
    print(f"  hashes added:  {len(report['done'])}")
    if report["changed"]:
        print(f"  changed:       {len(report['changed'])}  (run: mogul.py radio verify --update)")
    if report["failed"]:
        print(f"  failed:        {len(report['failed'])}")
    if not_found:
        print(f"  not found:     {len(not_found)}")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = os.path.join(config.reports, f"radio-hashes_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(path, report)
    print(f"\nReport: {path}")
    return 0
