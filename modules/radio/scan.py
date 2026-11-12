"""Radio scanner.

Looks at every audio file in mogul_incoming/radio (and its subfolders),
works out what it is, and adds it to the monthly catalog JSON:

    mogul_json/radio/<show>/<yyyy>/<mm> - <Month name>.json

This step only READS media files. It never moves, renames or deletes
anything. Moving and sorting is the import step's job.

Duplicates: two files with the same xxh64 (same audio data) are the
same recording, so only the first one seen is added. Different
releases of the same episode (mp3 vs ogg, full show vs commercial
free, a best-of, ...) have different audio data, so each gets its
own entry.
"""

import calendar
import os
import shutil
import sys
import time

from mogul_core.progress import Progress
from mogul_core import VERSION, hashing, mediainfo, fingerprint, store

from . import naming

SCHEMA = "mogul.radio.month"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------- helpers

def safe_name(text):
    """Make a show name safe to use as a folder name."""
    return text.replace("/", "-").replace("\\", "-").replace("\0", "").strip()


def manifest_path(json_root, show, year, month):
    name = f"{month:02d} - {calendar.month_name[month]}.json"
    return os.path.join(json_root, "radio", safe_name(show), f"{year:04d}", name)


def new_manifest(entry):
    n = entry["naming"]
    stamp = store.now()
    return {
        "header": {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "media_type": "radio",
            "show": n["show"],
            "code": n["code"],
            "year": n["year"],
            "month": n["month"],
            "created": stamp,
            "updated": stamp,
            "file_count": 0,
            "mogul_version": VERSION,
        },
        "files": [],
    }


def find_files(folder, extensions):
    exts = {e.lower().lstrip(".") for e in extensions}
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if name.rsplit(".", 1)[-1].lower() in exts:
                found.append(os.path.join(dirpath, name))
    return found


def load_catalog(json_root):
    """Load every radio month file. Returns (manifests, index)
    where index maps xxh64 -> list of (manifest_path, entry)."""
    manifests = {}
    index = {}
    radio_root = os.path.join(json_root, "radio")
    if not os.path.isdir(radio_root):
        return manifests, index
    for dirpath, dirnames, filenames in os.walk(radio_root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
        for name in filenames:
            if not name.endswith(".json") or name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            try:
                data = store.load(path)
            except Exception as e:
                sys.exit(f"Could not read {path}: {e}\nFix or move that file, then scan again.")
            if (data.get("header") or {}).get("schema") != SCHEMA:
                continue
            manifests[path] = data
            for entry in data.get("files", []):
                xxh = ((entry.get("physical") or {}).get("hashes") or {}).get("xxh64")
                if xxh:
                    index.setdefault(xxh, []).append((path, entry))
    return manifests, index


def check_tools(config, use_fingerprint):
    problems = hashing.check_available(config.hashes)
    for tool, pkg in (("ffmpeg", "ffmpeg"), ("mediainfo", "mediainfo")):
        if shutil.which(tool) is None:
            problems.append(f"'{tool}' is not installed (sudo apt install {pkg})")
    if use_fingerprint and shutil.which("fpcalc") is None:
        problems.append("'fpcalc' is not installed (sudo apt install libchromaprint-tools), "
                        "or run with --no-fingerprint")
    if problems:
        sys.exit("Can't scan yet:\n  - " + "\n  - ".join(problems))


def describe(entry):
    n = entry["naming"]
    return f"{n['show']} {n['year']:04d}-{n['month']:02d}-{n['day']:02d} ({n['filename']})"


# ---------------------------------------------------------------- main

def run(args, config):
    radio_cfg = config.section("radio")
    shows = radio_cfg.get("shows", {})
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    use_fp = radio_cfg.get("fingerprint", True) and not args.no_fingerprint
    regex = naming.compile_pattern(radio_cfg.get("incoming_pattern", naming.DEFAULT_PATTERN))
    hash_names = [h for h in config.hashes if h != "xxh64"]

    incoming = os.path.abspath(os.path.expanduser(args.path)) if args.path \
        else os.path.join(config.incoming, "radio")
    json_root = config.json_store

    if not os.path.isdir(incoming):
        if args.path:
            sys.exit(f"Folder not found: {incoming}")
        os.makedirs(incoming, exist_ok=True)
        print(f"Created {incoming}\nPut radio files in there and run the scan again.")
        return 0

    check_tools(config, use_fp)

    files = find_files(incoming, extensions)
    print(f"MOGUL {VERSION} radio scan{' (DRY RUN, nothing will be written)' if args.dry_run else ''}")
    print(f"  incoming: {incoming}")
    print(f"  catalog:  {os.path.join(json_root, 'radio')}")
    print(f"  files:    {len(files)}")
    print()
    if not files:
        print("Nothing to scan.")
        return 0

    manifests, index = load_catalog(json_root)
    from . import quarantine   # imported here to avoid a circular import
    qindex = quarantine.quarantined_audio(json_root)
    report = {
        "scan": "radio",
        "started": store.now(),
        "finished": None,
        "dry_run": bool(args.dry_run),
        "incoming": incoming,
        "added": [],
        "already_cataloged": [],
        "duplicates": [],
        "problems": [],
        "warnings": [],
    }
    width = len(str(len(files)))
    t0 = time.time()

    progress = Progress(files)
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, incoming)
        tag = progress.tag(i)
        filename = os.path.basename(path)

        try:
            st = os.stat(path)
        except OSError as e:
            report["problems"].append({"file": rel, "problem": f"can't read file: {e}"})
            print(f"{tag} PROBLEM  {rel}: can't read file")
            continue

        # 1. what does the filename say?
        entry_naming, problems = naming.parse(filename, regex, shows)
        if problems:
            for p in problems:
                report["problems"].append({"file": rel, "problem": p})
            print(f"{tag} PROBLEM  {rel}: {'; '.join(problems)}")
            continue

        # 2. hashes
        try:
            audio = hashing.audio_hashes(path)
            xxh = audio["xxh64"]
            hashes = dict(audio)
            hashes.update(hashing.file_hashes(path, hash_names))
        except (hashing.HashError, OSError) as e:
            report["problems"].append({"file": rel, "problem": str(e)})
            print(f"{tag} PROBLEM  {rel}: {e}")
            continue

        # 2b. known bad audio that was quarantined?
        q = quarantine.is_quarantined(qindex, audio)
        if q:
            report["problems"].append({"file": rel, "problem": "QUARANTINED: same audio as a quarantined "
                                       f"recording ({q['naming']['filename']})"})
            print(f"{tag} QUARANTINED {rel}: same audio as quarantined {q['naming']['filename']}, skipped")
            continue

        # 3. already in the catalog?
        #    xxh64 finds candidates; audio_blake3 confirms them.
        matches, collisions = [], []
        for p, e in index.get(xxh, []):
            result = hashing.audio_match(e["physical"]["hashes"], audio)
            if result in ("confirmed", "unconfirmed"):
                matches.append((p, e))
            elif result == "collision":
                collisions.append((p, e))
        if collisions:
            report["warnings"].append({
                "file": rel,
                "warning": "COLLISION: same xxh64 as a cataloged file but different audio "
                           "(audio_blake3 differs). Added as its own entry; check it by hand.",
                "xxh64": xxh,
                "other": [e["naming"]["filename"] for _, e in collisions],
            })
            print(f"{tag} WARNING  {rel}: same xxh64 as {collisions[0][1]['naming']['filename']} "
                  f"but DIFFERENT audio (collision or faked file), check by hand")
        if matches:
            first_hash = hash_names[0] if hash_names else None
            same_file = any(
                e["naming"].get("original_filename") == filename
                and (first_hash is None
                     or e["physical"]["hashes"].get(first_hash) == hashes.get(first_hash))
                for _, e in matches
            )
            info = {
                "file": rel,
                "xxh64": xxh,
                "matches": [
                    {
                        "show": e["naming"]["show"],
                        "date": f"{e['naming']['year']:04d}-{e['naming']['month']:02d}-{e['naming']['day']:02d}",
                        "filename": e["naming"]["filename"],
                        "manifest": os.path.relpath(p, json_root),
                    }
                    for p, e in matches
                ],
            }
            if same_file:
                report["already_cataloged"].append(info)
                print(f"{tag} ALREADY  {rel}")
            else:
                my_date = (entry_naming["year"], entry_naming["month"], entry_naming["day"])
                other_dates = {(e["naming"]["year"], e["naming"]["month"], e["naming"]["day"])
                               for _, e in matches}
                info["date_conflict"] = any(d != my_date for d in other_dates)
                report["duplicates"].append(info)
                note = "  (DIFFERENT DATE in catalog!)" if info["date_conflict"] else ""
                print(f"{tag} DUPLICATE {rel} = {describe(matches[0][1])}{note}")
            continue

        # 4. technical details
        try:
            media = mediainfo.read(path)
        except mediainfo.MediaInfoError as e:
            report["problems"].append({"file": rel, "problem": str(e)})
            print(f"{tag} PROBLEM  {rel}: {e}")
            continue

        # 5. fingerprint digest
        fp = None
        if use_fp:
            try:
                fp = fingerprint.digest_entry(path)
            except fingerprint.FingerprintError as e:
                report["warnings"].append({"file": rel, "warning": f"no fingerprint: {e}"})
                print(f"{tag} WARNING  {rel}: no fingerprint ({e})")

        entry = {
            "naming": entry_naming,
            "physical": {
                "hashes": hashes,
                "size": st.st_size,
                "duration": media["duration"],
                "container": media["container"],
                "codec": media["codec"],
                "bitrate": media["bitrate"],
                "bitrate_mode": media["bitrate_mode"],
                "channels": media["channels"],
                "sample_rate": media["sample_rate"],
                "mtime": store.utc_from_timestamp(st.st_mtime),
                "added": store.now(),
            },
            "fingerprint": fp,
        }
        from .damaged import mark_damaged
        mark_damaged(entry, fp)

        # 6. add to the month file
        mpath = manifest_path(json_root, entry_naming["show"], entry_naming["year"], entry_naming["month"])
        manifest = manifests.get(mpath)
        if manifest is None:
            manifest = new_manifest(entry)
            manifests[mpath] = manifest
        manifest["files"].append(entry)
        manifest["files"].sort(key=lambda e: (e["naming"]["day"], e["naming"]["filename"].lower()))
        manifest["header"]["file_count"] = len(manifest["files"])
        manifest["header"]["updated"] = store.now()
        manifest["header"]["mogul_version"] = VERSION
        index.setdefault(xxh, []).append((mpath, entry))

        if not args.dry_run:
            store.save(mpath, manifest)  # saved after every file, so an interrupted scan keeps its progress

        report["added"].append({
            "file": rel,
            "xxh64": xxh,
            "manifest": os.path.relpath(mpath, json_root),
        })
        print(f"{tag} ADDED    {rel} -> {os.path.relpath(mpath, json_root)}")

    # ---------------------------------------------------------------- summary
    report["finished"] = store.now()
    elapsed = time.time() - t0
    print()
    print(f"Done in {progress.elapsed()}")
    print(f"  added:             {len(report['added'])}")
    print(f"  duplicates:        {len(report['duplicates'])}"
          + (f"  ({sum(1 for d in report['duplicates'] if d.get('date_conflict'))} with a different date)"
             if any(d.get('date_conflict') for d in report['duplicates']) else ""))
    print(f"  already cataloged: {len(report['already_cataloged'])}")
    print(f"  problems:          {len(report['problems'])}")
    if report["warnings"]:
        print(f"  warnings:          {len(report['warnings'])}")

    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    report_path = os.path.join(config.reports, f"radio-scan_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(report_path, report)
    print(f"\nReport: {report_path}")
    return 0
