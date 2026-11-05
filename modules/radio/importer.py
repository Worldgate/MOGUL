"""Radio importer.

Moves cataloged files out of mogul_incoming/radio into the radio
library, renamed and sorted by the templates in mogul.toml:

    <library>/<show>/<yyyy>/<mm>/<yyyy>-<mm>-<dd>_<code>_<HASH8>.<ext>

Run `mogul.py radio scan` first: only files that are in the catalog get
imported. For each file in the incoming folder:

  - IMPORT     it's the file the catalog entry was made from, and that
               entry isn't in the library yet -> move and rename it
  - DUPLICATE  same audio as something already in the library (or
               already imported this run) -> move to the dupes folder
  - SKIP       not in the catalog (not scanned yet, or had a problem in
               the scan) -> left where it is

Nothing is ever deleted. After a file is moved, its catalog entry is
updated with the new filename and an "imported" time.
"""

import errno
import os
import shutil
import sys
import time

from mogul_core import VERSION, hashing, store

from . import naming
from .scan import load_catalog, find_files, check_tools

BAD_CHARS = '/\\<>:"|?*\0'


# ---------------------------------------------------------------- naming

def clean(text):
    """Make text safe for a file or folder name (also on Windows/SMB shares)."""
    out = "".join("-" if c in BAD_CHARS else c for c in str(text))
    return out.strip().rstrip(".")


def render(template, entry, hash_slice):
    n = entry["naming"]
    import calendar
    values = {
        "show": n["show"],
        "code": n["code"],
        "yyyy": f"{n['year']:04d}",
        "mm": f"{n['month']:02d}",
        "dd": f"{n['day']:02d}",
        "month_name": calendar.month_name[n["month"]],
        "hash8": hash_slice.lower(),
        "HASH8": hash_slice.upper(),
    }
    try:
        text = template.format(**values)
    except KeyError as e:
        sys.exit(f"Unknown token {{{e.args[0]}}} in a radio template in mogul.toml")
    return text


def target_folder(library, folder_template, entry):
    parts = [clean(p) for p in render(folder_template, entry, "").split("/") if p.strip()]
    return os.path.join(library, *parts)


def target_name(filename_template, entry, offset):
    xxh = entry["physical"]["hashes"]["xxh64"]
    piece = xxh[offset:offset + 8]
    return clean(render(filename_template, entry, piece)) + "." + entry["naming"]["extension"]


# ---------------------------------------------------------------- moving

def same_content(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
    except OSError:
        return False
    return hashing.whole_file_xxh64(a) == hashing.whole_file_xxh64(b)


def safe_move(src, dst):
    """Move a file. On the same drive this is an instant rename. Across
    drives (e.g. to the NAS) it copies to a temp file, checks the copy
    matches, renames it into place, then removes the original."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = os.path.join(os.path.dirname(dst), "." + os.path.basename(dst) + ".part")
    try:
        shutil.copy2(src, tmp)
        if not same_content(src, tmp):
            raise OSError(f"copy check failed for {dst}")
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.unlink(src)


def free_dupe_path(dupes_root, rel):
    """Where to put a duplicate. Keeps its original name and subfolder;
    adds (2), (3), ... if that spot is taken."""
    dst = os.path.join(dupes_root, rel)
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def remove_empty_folders(root):
    """Remove empty subfolders under root (never root itself)."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            os.rmdir(dirpath)  # only succeeds if empty
        except OSError:
            pass


# ---------------------------------------------------------------- main

def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    dupes_root = config.resolve(radio_cfg.get("dupes", "mogul_dupes/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    name_t = radio_cfg.get("filename_template", "{yyyy}-{mm}-{dd}_{code}_{HASH8}")
    incoming = os.path.abspath(os.path.expanduser(args.path)) if args.path \
        else os.path.join(config.incoming, "radio")
    json_root = config.json_store

    if not os.path.isdir(incoming):
        sys.exit(f"Folder not found: {incoming}")
    check_tools(config, use_fingerprint=False)

    files = find_files(incoming, extensions)
    print(f"MOGUL {VERSION} radio import{' (DRY RUN, nothing will be moved)' if args.dry_run else ''}")
    print(f"  incoming: {incoming}")
    print(f"  library:  {library}")
    print(f"  dupes:    {dupes_root}")
    print(f"  files:    {len(files)}")
    print()
    if not files:
        print("Nothing to import.")
        return 0

    manifests, index = load_catalog(json_root)
    if not index:
        sys.exit("The radio catalog is empty. Run `mogul.py radio scan` first.")

    report = {
        "import": "radio",
        "started": store.now(),
        "finished": None,
        "dry_run": bool(args.dry_run),
        "incoming": incoming,
        "library": library,
        "dupes": dupes_root,
        "imported": [],
        "duplicates": [],
        "skipped": [],
        "errors": [],
    }
    claimed = set()        # target paths used this run (matters for dry runs)
    done_this_run = {}     # xxh64 -> library filename imported this run
    pending_dupes = []     # copies to handle once their original is imported
    incoming_ids = set()
    for p in files:
        try:
            incoming_ids.add((os.path.basename(p), os.path.getsize(p)))
        except OSError:
            pass
    width = len(str(len(files)))
    t0 = time.time()

    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, incoming)
        tag = f"[{i:>{width}}/{len(files)}]"
        filename = os.path.basename(path)

        try:
            audio = hashing.audio_hashes(path)
            xxh = audio["xxh64"]
        except (hashing.HashError, OSError) as e:
            report["skipped"].append({"file": rel, "reason": f"can't read audio: {e}"})
            print(f"{tag} SKIP      {rel}: can't read audio")
            continue

        # xxh64 finds candidates; audio_blake3 confirms them.
        matches, collisions = [], []
        for p, e in index.get(xxh, []):
            result = hashing.audio_match(e["physical"]["hashes"], audio)
            if result in ("confirmed", "unconfirmed"):
                matches.append((p, e))
            elif result == "collision":
                collisions.append((p, e))
        if collisions and not matches:
            report["skipped"].append({
                "file": rel,
                "reason": "COLLISION: same xxh64 as a cataloged file but different audio "
                          "(audio_blake3 differs). Not moved; check it by hand.",
                "xxh64": xxh,
            })
            print(f"{tag} SKIP      {rel}: same xxh64 but DIFFERENT audio (collision or faked file), not moved")
            continue

        if not matches:
            report["skipped"].append({"file": rel, "reason": "not in the catalog (scan it first)"})
            print(f"{tag} SKIP      {rel}: not in the catalog (run scan first)")
            continue

        # Which catalog entry is this file? Prefer the one made from this exact file.
        size = os.path.getsize(path)
        mpath, entry = matches[0]
        for p, e in matches:
            if e["naming"].get("original_filename") == filename and e["physical"].get("size") == size:
                mpath, entry = p, e
                break
        is_source = (entry["naming"].get("original_filename") == filename
                     and entry["physical"].get("size") == size)
        already_in_library = bool((entry.get("status") or {}).get("imported")) or xxh in done_this_run

        # older entries made before audio_blake3 existed: fill it in now
        if is_source and not entry["physical"]["hashes"].get("audio_blake3"):
            entry["physical"]["hashes"]["audio_blake3"] = audio["audio_blake3"]

        # Same audio as a catalog entry, but not the exact file the entry was
        # made from, and that entry isn't in the library yet. If the original
        # is also here it will be imported and this one becomes a dupe later in
        # the run; if it's gone, leave this copy alone and ask for a rescan.
        if not already_in_library and not is_source:
            src_name = entry["naming"].get("original_filename")
            src_size = entry["physical"].get("size")
            if (src_name, src_size) in incoming_ids:
                pending_dupes.append((path, rel, tag, xxh, entry))
                print(f"{tag} WAIT      {rel}: same audio as {src_name}, handled after it")
                continue
            report["skipped"].append({
                "file": rel,
                "reason": f"same audio as cataloged '{src_name}', which isn't here; "
                          "scan this copy or bring the original back",
            })
            print(f"{tag} SKIP      {rel}: catalog entry was made from '{src_name}', which isn't here")
            continue

        # ------------------------------------------------ duplicate copy
        if already_in_library:
            dst = free_dupe_path(dupes_root, rel)
            while dst in claimed:
                base, ext = os.path.splitext(dst)
                dst = f"{base}_{len(claimed)}{ext}"
            claimed.add(dst)
            where = done_this_run.get(xxh) or entry["naming"].get("filename")
            try:
                if not args.dry_run:
                    safe_move(path, dst)
            except OSError as e:
                report["errors"].append({"file": rel, "error": str(e)})
                print(f"{tag} ERROR     {rel}: {e}")
                continue
            report["duplicates"].append({
                "from": rel, "to": os.path.relpath(dst, dupes_root),
                "xxh64": xxh, "same_as": where,
            })
            print(f"{tag} DUPLICATE {rel} -> dupes/{os.path.relpath(dst, dupes_root)}")
            continue

        # ------------------------------------------------ import
        folder = target_folder(library, folder_t, entry)
        dst = None
        for offset in range(0, 9):  # 0..8 gives every 8-character run of the 16
            candidate = os.path.join(folder, target_name(name_t, entry, offset))
            if candidate in claimed:
                continue
            if os.path.exists(candidate):
                if same_content(path, candidate):
                    dst = "SAME"   # this exact file is already there
                    break
                continue           # a different file has this name, try next 8
            dst = candidate
            break
        if dst is None:
            report["errors"].append({"file": rel, "error": "no free filename (all hash slices taken)"})
            print(f"{tag} ERROR     {rel}: no free filename")
            continue

        stamp = store.now()
        if dst == "SAME":
            # Already in the library (e.g. an earlier import was interrupted
            # before the catalog was saved). Treat the incoming copy as a dupe.
            lib_name = os.path.basename(candidate)
            ddst = free_dupe_path(dupes_root, rel)
            claimed.add(ddst)
            try:
                if not args.dry_run:
                    safe_move(path, ddst)
            except OSError as e:
                report["errors"].append({"file": rel, "error": str(e)})
                print(f"{tag} ERROR     {rel}: {e}")
                continue
            entry["naming"]["filename"] = lib_name
            entry.setdefault("status", {})["imported"] = stamp
            done_this_run[xxh] = lib_name
            report["duplicates"].append({
                "from": rel, "to": os.path.relpath(ddst, dupes_root),
                "xxh64": xxh, "same_as": lib_name,
            })
            print(f"{tag} DUPLICATE {rel} -> dupes/ (already in library as {lib_name})")
        else:
            claimed.add(dst)
            try:
                if not args.dry_run:
                    safe_move(path, dst)
            except OSError as e:
                report["errors"].append({"file": rel, "error": str(e)})
                print(f"{tag} ERROR     {rel}: {e}")
                continue
            new_name = os.path.basename(dst)
            entry["naming"]["filename"] = new_name
            entry.setdefault("status", {})["imported"] = stamp
            done_this_run[xxh] = new_name
            report["imported"].append({
                "from": rel, "to": os.path.relpath(dst, library), "xxh64": xxh,
            })
            print(f"{tag} IMPORT    {rel} -> {os.path.relpath(dst, library)}")

        # save the catalog change right away
        manifest = manifests[mpath]
        manifest["header"]["updated"] = stamp
        manifest["header"]["mogul_version"] = VERSION
        if not args.dry_run:
            store.save(mpath, manifest)

    # copies whose original was imported earlier in this run
    for path, rel, tag, xxh, entry in pending_dupes:
        if xxh not in done_this_run:
            report["skipped"].append({"file": rel, "reason": "its original wasn't imported, left in place"})
            print(f"{tag} SKIP      {rel}: its original wasn't imported")
            continue
        dst = free_dupe_path(dupes_root, rel)
        while dst in claimed:
            base, ext = os.path.splitext(dst)
            dst = f"{base}_{len(claimed)}{ext}"
        claimed.add(dst)
        try:
            if not args.dry_run:
                safe_move(path, dst)
        except OSError as e:
            report["errors"].append({"file": rel, "error": str(e)})
            print(f"{tag} ERROR     {rel}: {e}")
            continue
        report["duplicates"].append({
            "from": rel, "to": os.path.relpath(dst, dupes_root),
            "xxh64": xxh, "same_as": done_this_run[xxh],
        })
        print(f"{tag} DUPLICATE {rel} -> dupes/{os.path.relpath(dst, dupes_root)}")

    if not args.dry_run:
        remove_empty_folders(incoming)

    # ---------------------------------------------------------------- summary
    report["finished"] = store.now()
    print()
    print(f"Done in {time.time() - t0:.0f}s")
    print(f"  imported:   {len(report['imported'])}")
    print(f"  duplicates: {len(report['duplicates'])}  (moved to the dupes folder)")
    print(f"  skipped:    {len(report['skipped'])}  (left in incoming)")
    if report["errors"]:
        print(f"  errors:     {len(report['errors'])}")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    report_path = os.path.join(config.reports,
                               f"radio-import_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(report_path, report)
    print(f"\nReport: {report_path}")
    return 0
