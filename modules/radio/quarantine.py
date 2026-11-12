"""Radio quarantine.

Pulls bad recordings out of the library and the main catalog, so they can
be checked by hand without getting in the way.

For each selected recording:
  - the file is moved to the quarantine folder (same show/year/month
    layout as the library)
  - its catalog entry is moved out of the month file and into
    mogul_json/radio/_quarantine.json, with a note of when, why, where the
    file came from and went, and a damage report
  - the damage report shows how much of the file is empty (zero bytes) and
    where the biggest empty stretch is. An incomplete download usually
    shows up as a big empty stretch at the front.

What gets selected:
  default         every entry the fingerprint pass couldn't read
                  (status.fingerprint_failed), plus any marked bad with
                  `mogul.py radio damaged --bad NAME`
  --file NAME     specific library file(s) by name (repeatable)

Scan and import both check the quarantine list, so the same bad audio
won't be cataloged again if it turns up in another batch.

--restore NAME  puts a quarantined file and its entry back.
--list          shows what's in quarantine.
--dry-run       shows what would happen, changes nothing.

Nothing is ever deleted.
"""

import os

from mogul_core import VERSION, damage, hashing, store

from .importer import target_folder, safe_move, free_path
from .scan import load_catalog, find_files, new_manifest, manifest_path

SCHEMA = "mogul.radio.quarantine"
CHUNK = 4 * 1024 * 1024


# ---------------------------------------------------------------- the quarantine file

def quarantine_path(json_root):
    return os.path.join(json_root, "radio", "_quarantine.json")


def load_quarantine(json_root):
    path = quarantine_path(json_root)
    if os.path.isfile(path):
        data = store.load(path)
        if data.get("schema") == SCHEMA:
            return data
    return {"schema": SCHEMA, "schema_version": 1, "updated": None, "entries": []}


def quarantined_audio(json_root):
    """xxh64 -> quarantined entry. Used by scan and import to skip known-bad audio."""
    out = {}
    for e in load_quarantine(json_root).get("entries", []):
        xxh = ((e.get("physical") or {}).get("hashes") or {}).get("xxh64")
        if xxh:
            out.setdefault(xxh, []).append(e)
    return out


def is_quarantined(qindex, audio):
    """True if this audio matches a quarantined entry (xxh64, and audio_blake3
    when the entry has one)."""
    for e in qindex.get(audio["xxh64"], []):
        if hashing.audio_match(e["physical"]["hashes"], audio) in ("confirmed", "unconfirmed"):
            return e
    return None


# ---------------------------------------------------------------- damage report

damage_report = damage.empty_report
describe_damage = damage.describe_empty


# ---------------------------------------------------------------- finding files

def locate(entry, library, folder_t, waiting):
    """Where is this entry's file? Returns (path, 'library'|'incoming') or (None, None)."""
    size = entry["physical"].get("size")
    if (entry.get("status") or {}).get("imported"):
        path = os.path.join(target_folder(library, folder_t, entry), entry["naming"]["filename"])
        if os.path.isfile(path):
            return path, "library"
        return None, None
    path = waiting.get((entry["naming"].get("original_filename"), size))
    if path and os.path.isfile(path):
        return path, "incoming"
    return None, None


# ---------------------------------------------------------------- main

def run(args, config):
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    qroot = config.resolve(radio_cfg.get("quarantine", "mogul_quarantine/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    incoming = os.path.join(config.incoming, "radio")
    json_root = config.json_store

    if args.list:
        return list_quarantine(json_root)
    if args.restore:
        return restore(args, config, library, qroot, folder_t, incoming, json_root)

    manifests, _ = load_catalog(json_root)
    waiting = {}
    if os.path.isdir(incoming):
        for p in find_files(incoming, extensions):
            try:
                waiting.setdefault((os.path.basename(p), os.path.getsize(p)), p)
            except OSError:
                pass

    wanted = set(args.file or [])
    picked = []
    for mpath, manifest in manifests.items():
        for entry in manifest.get("files", []):
            name = entry["naming"]["filename"]
            if wanted:
                if name in wanted:
                    picked.append((mpath, entry, "picked by hand"))
            elif (entry.get("status") or {}).get("fingerprint_failed"):
                err = entry["status"]["fingerprint_failed"].get("error", "")
                picked.append((mpath, entry, f"fingerprint failed: {err}"))
            elif (((entry.get("status") or {}).get("damaged") or {}).get("checked") or {}).get("verdict") == "bad":
                picked.append((mpath, entry, "checked by hand: bad"))

    if wanted:
        found = {e["naming"]["filename"] for _, e, _ in picked}
        for name in sorted(wanted - found):
            print(f"NOT FOUND  {name} (not in the catalog; check the exact library filename)")

    print(f"MOGUL {VERSION} radio quarantine{' (DRY RUN, nothing will be moved)' if args.dry_run else ''}")
    print(f"  quarantine: {qroot}")
    print(f"  selected:   {len(picked)}")
    print()
    if not picked:
        print("Nothing to quarantine." if not wanted else "")
        return 0

    qdata = load_quarantine(json_root)
    claimed = set()
    moved = 0
    for mpath, entry, reason in picked:
        name = entry["naming"]["filename"]
        path, where = locate(entry, library, folder_t, waiting)
        if not path:
            print(f"NOT FOUND  {name}: file isn't where the catalog says (library or incoming)")
            continue
        damage = damage_report(path)
        rel_from = os.path.relpath(path, library if where == "library" else incoming)
        sub = os.path.relpath(target_folder("/", folder_t, entry), "/")
        dst = free_path(os.path.join(qroot, sub, os.path.basename(path)), claimed)
        claimed.add(dst)
        print(f"QUARANTINE {name}  [{describe_damage(damage)}]")
        print(f"           {reason}")
        if args.dry_run:
            continue
        try:
            safe_move(path, dst)
        except OSError as e:
            print(f"ERROR      {name}: {e}")
            continue

        manifest = manifests[mpath]
        manifest["files"] = [e for e in manifest["files"] if e is not entry]
        manifest["header"]["file_count"] = len(manifest["files"])
        manifest["header"]["updated"] = store.now()
        manifest["header"]["mogul_version"] = VERSION
        store.save(mpath, manifest)

        entry["quarantine"] = {
            "date": store.now(),
            "reason": reason,
            "was_in": where,
            "from": rel_from,
            "to": os.path.relpath(dst, qroot),
            "month_file": os.path.relpath(mpath, json_root),
            "damage": damage,
        }
        qdata["entries"].append(entry)
        qdata["updated"] = store.now()
        store.save(quarantine_path(json_root), qdata)
        moved += 1

    print()
    if args.dry_run:
        print(f"Would quarantine {len(picked)} file(s).")
    else:
        print(f"Quarantined {moved} file(s). Entries are in {os.path.relpath(quarantine_path(json_root), json_root)}")
        print("Check them by hand; put any good one back with:  mogul.py radio quarantine --restore <filename>")
    return 0


def list_quarantine(json_root):
    entries = load_quarantine(json_root).get("entries", [])
    if not entries:
        print("Quarantine is empty.")
        return 0
    for e in entries:
        q = e.get("quarantine", {})
        print(f"{e['naming']['filename']}  [{describe_damage(q['damage']) if q.get('damage') else '?'}]")
        print(f"    {q.get('reason', '')}  ({q.get('date', '')})")
    print(f"\n{len(entries)} in quarantine")
    return 0


def restore(args, config, library, qroot, folder_t, incoming, json_root):
    qdata = load_quarantine(json_root)
    manifests, _ = load_catalog(json_root)
    names = set(args.restore)
    if "all" in names:
        names = {e["naming"]["filename"] for e in qdata["entries"]}
    keep = []
    for e in qdata["entries"]:
        name = e["naming"]["filename"]
        if name not in names:
            keep.append(e)
            continue
        q = e.get("quarantine", {})
        src = os.path.join(qroot, q.get("to", ""))
        back_root = library if q.get("was_in") == "library" else incoming
        dst = os.path.join(back_root, q.get("from", name))
        if not os.path.isfile(src):
            print(f"MISSING    {name}: not in the quarantine folder ({src})")
            keep.append(e)
            continue
        if os.path.exists(dst):
            print(f"BLOCKED    {name}: something already exists at {dst}")
            keep.append(e)
            continue
        print(f"RESTORE    {name} -> {os.path.relpath(dst, back_root)}")
        if args.dry_run:
            keep.append(e)
            continue
        safe_move(src, dst)
        e.pop("quarantine", None)
        (e.get("status") or {}).pop("fingerprint_failed", None)
        n = e["naming"]
        mpath = manifest_path(json_root, n["show"], n["year"], n["month"])
        manifest = manifests.get(mpath) or new_manifest(e)
        manifest["files"].append(e)
        manifest["files"].sort(key=lambda x: (x["naming"]["day"], x["naming"]["filename"].lower()))
        manifest["header"]["file_count"] = len(manifest["files"])
        manifest["header"]["updated"] = store.now()
        store.save(mpath, manifest)
        manifests[mpath] = manifest
        names.discard(name)
    for name in sorted(names):
        if not any(x["naming"]["filename"] == name for x in keep):
            print(f"NOT FOUND  {name} (not in quarantine)")
    if not args.dry_run:
        qdata["entries"] = keep
        qdata["updated"] = store.now()
        store.save(quarantine_path(json_root), qdata)
    return 0
