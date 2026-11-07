"""Radio verify.

Checks the radio library against the catalog. Every file in the library
is re-hashed and matched to its catalog entry by its audio hashes (never
by its name). Moves, renames and deletes nothing.

Per file:
  OK         audio and whole-file hashes match the catalog, and the file
             is where the catalog says it should be
  FILLED     older entry that was missing audio_blake3: now filled in
  FOUND      file is in the library but its entry wasn't marked imported:
             now marked, with the file's current name
  CHANGED    same audio, but the whole file differs from when it was
             scanned (tags edited since). Use --update to record the new
             whole-file hashes, size and date
  MISPLACED  the right file, but not in the folder/name the catalog expects
  EXTRA      a second library file with the same audio as one already seen
  COLLISION  same xxh64 as a catalog entry but different audio
  UNKNOWN    not in the catalog at all
  BAD        the audio can't be read

After the files, every entry marked imported whose file wasn't found is
listed as MISSING.
"""

import os
import sys
import time

from mogul_core.progress import Progress
from mogul_core import VERSION, hashing, store

from .importer import target_folder
from .scan import load_catalog, find_files, check_tools


def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    hash_names = [h for h in config.hashes if h != "xxh64"]
    first_hash = hash_names[0] if hash_names else None
    json_root = config.json_store

    if not os.path.isdir(library):
        sys.exit(f"Library folder not found: {library}")
    check_tools(config, use_fingerprint=False)

    files = find_files(library, extensions)
    print(f"MOGUL {VERSION} radio verify{' (DRY RUN, catalog not changed)' if args.dry_run else ''}")
    print(f"  library: {library}")
    print(f"  files:   {len(files)}")
    print()

    manifests, index = load_catalog(json_root)
    report = {
        "verify": "radio",
        "started": store.now(),
        "finished": None,
        "dry_run": bool(args.dry_run),
        "library": library,
        "results": {k: [] for k in ("ok", "filled", "found", "changed", "updated",
                                     "misplaced", "extra", "collision", "unknown",
                                     "bad", "missing")},
    }
    R = report["results"]
    seen = {}            # id(entry) -> library path
    dirty = set()        # manifest paths that changed
    width = len(str(max(len(files), 1)))
    t0 = time.time()

    progress = Progress(files)
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, library)
        tag = progress.tag(i)
        name = os.path.basename(path)

        try:
            audio = hashing.audio_hashes(path)
        except (hashing.HashError, OSError) as e:
            R["bad"].append({"file": rel, "problem": str(e)})
            print(f"{tag} BAD        {rel}: {e}")
            continue

        matches, collisions = [], []
        for p, e in index.get(audio["xxh64"], []):
            result = hashing.audio_match(e["physical"]["hashes"], audio)
            if result in ("confirmed", "unconfirmed"):
                matches.append((p, e))
            elif result == "collision":
                collisions.append((p, e))

        if not matches:
            if collisions:
                R["collision"].append({"file": rel, "xxh64": audio["xxh64"],
                                       "other": [e["naming"]["filename"] for _, e in collisions]})
                print(f"{tag} COLLISION  {rel}: same xxh64 as a catalog entry but DIFFERENT audio")
            else:
                R["unknown"].append({"file": rel, "xxh64": audio["xxh64"]})
                print(f"{tag} UNKNOWN    {rel}: not in the catalog")
            continue

        # pick the entry: prefer one whose recorded name is this file's name
        mpath, entry = next(((p, e) for p, e in matches if e["naming"].get("filename") == name),
                            matches[0])

        if id(entry) in seen:
            R["extra"].append({"file": rel, "same_as": os.path.relpath(seen[id(entry)], library)})
            print(f"{tag} EXTRA      {rel}: same audio as {os.path.relpath(seen[id(entry)], library)}")
            continue
        seen[id(entry)] = path

        notes = []
        hashes = entry["physical"]["hashes"]
        status = entry.get("status") or {}

        # another entry has the same xxh64 but different audio: always flag it
        if collisions:
            R["collision"].append({"file": rel, "xxh64": audio["xxh64"],
                                   "other": [e["naming"]["filename"] for _, e in collisions]})
            notes.append("COLLISION")

        # fill in audio_blake3 for older entries
        if not hashes.get("audio_blake3"):
            hashes["audio_blake3"] = audio["audio_blake3"]
            dirty.add(mpath)
            R["filled"].append({"file": rel})
            notes.append("FILLED")

        # entry not marked as imported yet (file got here some other way).
        # Only adopt it if it sits in the right folder for that entry's date.
        expected_folder = target_folder(library, folder_t, entry)
        if not status.get("imported"):
            if os.path.normpath(os.path.dirname(path)) == os.path.normpath(expected_folder):
                entry.setdefault("status", {})["imported"] = store.now()
                entry["naming"]["filename"] = name
                dirty.add(mpath)
                R["found"].append({"file": rel})
                notes.append("FOUND")
            else:
                R["misplaced"].append({"file": rel, "expected": os.path.relpath(expected_folder, library) + "/",
                                       "note": "entry not marked imported; not adopted because it's in the wrong folder"})
                notes.append("MISPLACED")
                print(f"{tag} {notes[0]:<10} {rel}" + (f"  [{', '.join(notes[1:])}]" if len(notes) > 1 else "")
                      + f"\n{'':>{len(tag)}}            catalog says this audio belongs in: "
                        f"{os.path.relpath(expected_folder, library)}/ (left alone)")
                continue

        # whole-file check
        if first_hash:
            now_hash = hashing.file_hashes(path, [first_hash])[first_hash]
            if hashes.get(first_hash) and now_hash != hashes[first_hash]:
                if args.update:
                    st = os.stat(path)
                    hashes.update(hashing.file_hashes(path, hash_names))
                    entry["physical"]["size"] = st.st_size
                    entry["physical"]["mtime"] = store.utc_from_timestamp(st.st_mtime)
                    dirty.add(mpath)
                    R["updated"].append({"file": rel})
                    notes.append("UPDATED")
                else:
                    R["changed"].append({"file": rel})
                    notes.append("CHANGED")

        # location check
        expected = os.path.join(target_folder(library, folder_t, entry), entry["naming"]["filename"])
        detail = ""
        if os.path.normpath(expected) != os.path.normpath(path):
            R["misplaced"].append({"file": rel, "expected": os.path.relpath(expected, library)})
            notes.append("MISPLACED")
            detail = f"\n{'':>{len(tag)}}            expected at: {os.path.relpath(expected, library)}"

        if notes:
            extra = f"  [{', '.join(notes[1:])}]" if len(notes) > 1 else ""
            print(f"{tag} {notes[0]:<10} {rel}{extra}{detail}")
        else:
            R["ok"].append({"file": rel})
            print(f"{tag} OK         {rel}")

    # entries marked imported whose file wasn't found
    for mpath, manifest in manifests.items():
        for e in manifest.get("files", []):
            if (e.get("status") or {}).get("imported") and id(e) not in seen:
                where = os.path.relpath(
                    os.path.join(target_folder(library, folder_t, e), e["naming"]["filename"]), library)
                R["missing"].append({"expected": where, "xxh64": e["physical"]["hashes"]["xxh64"]})
                print(f"MISSING    {where}")

    if dirty and not args.dry_run:
        stamp = store.now()
        for mpath in dirty:
            manifests[mpath]["header"]["updated"] = stamp
            manifests[mpath]["header"]["mogul_version"] = VERSION
            store.save(mpath, manifests[mpath])

    report["finished"] = store.now()
    print()
    print(f"Done in {progress.elapsed()}")
    labels = [("ok", "ok"), ("filled", "audio_blake3 filled in"), ("found", "found, marked imported"),
              ("changed", "changed since scan (use --update)"), ("updated", "updated"),
              ("misplaced", "misplaced"), ("extra", "extra copies"), ("collision", "COLLISIONS"),
              ("unknown", "not in catalog"), ("bad", "unreadable"), ("missing", "MISSING")]
    for key, label in labels:
        if R[key] or key == "ok":
            print(f"  {label + ':':<36} {len(R[key])}")
    if dirty:
        print(f"\n{'Would update' if args.dry_run else 'Updated'} {len(dirty)} catalog file(s).")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    report_path = os.path.join(config.reports,
                               f"radio-verify_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(report_path, report)
    print(f"\nReport: {report_path}")
    return 0
