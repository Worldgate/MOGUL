#!/usr/bin/env python3
"""Fill in the length (and bitrate) for catalog entries that have none.

    python3 tools/filllength.py            fill them in
    python3 tools/filllength.py --dry-run  show what it would fill, save nothing

Raw .aac files (ADTS streams) have no header saying how long they are, so
mediainfo gives no duration or bitrate and the catalog got `null` for
both. Scan now works the length out by itself (ffprobe adds up the audio
packets), but entries scanned before that need this once.

Finds each file the same way the fingerprint pass does: imported entries
in the library, the rest in mogul_incoming/radio by original filename,
and only if the size matches the catalog. Saves after every month file.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mogul_core import VERSION, mediainfo, store       # noqa: E402
from mogul_core.config import Config                   # noqa: E402
from modules.radio.importer import target_folder       # noqa: E402
from modules.radio.scan import find_files, load_catalog  # noqa: E402


def hms(t):
    t = int(round(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="Fill in missing lengths (raw .aac files etc).")
    ap.add_argument("--dry-run", action="store_true", help="show what it would fill, save nothing")
    ap.add_argument("--config", help="use this mogul.toml instead of the default one")
    args = ap.parse_args()

    config = Config(args.config)
    radio_cfg = config.section("radio")
    extensions = radio_cfg.get("extensions", ["mp3", "ogg", "m4a"])
    library = config.resolve(radio_cfg.get("library", "mogul_library/radio"))
    folder_t = radio_cfg.get("folder_template", "{show}/{yyyy}/{mm}")
    incoming = os.path.join(config.incoming, "radio")

    manifests, _ = load_catalog(config.json_store)
    waiting = {}
    if os.path.isdir(incoming):
        for path in find_files(incoming, extensions):
            try:
                waiting.setdefault((os.path.basename(path), os.path.getsize(path)), path)
            except OSError:
                pass

    filled, missing, failed = 0, [], []
    for mpath, manifest in manifests.items():
        changed = False
        for entry in manifest.get("files", []):
            p = entry["physical"]
            if p.get("duration"):
                continue
            name = entry["naming"]["filename"]
            status = entry.get("status") or {}
            if status.get("imported"):
                path = os.path.join(target_folder(library, folder_t, entry), name)
            else:
                path = waiting.get((entry["naming"].get("original_filename"), p.get("size")))
            if not path or not os.path.isfile(path) or os.path.getsize(path) != p.get("size"):
                missing.append(name)
                continue
            length = mediainfo.packet_length(path)
            if not length:
                failed.append(name)
                print(f"FAILED  {name}: couldn't count its audio packets")
                continue
            rate = p.get("bitrate") or int(p["size"] * 8 / length)
            print(f"FILLED  {name}: {hms(length)}, {round(rate / 1000)}k")
            filled += 1
            if not args.dry_run:
                p["duration"] = length
                if not p.get("bitrate"):
                    p["bitrate"] = rate
                changed = True
        if changed:
            manifest["header"]["updated"] = store.now()
            manifest["header"]["mogul_version"] = VERSION
            store.save(mpath, manifest)

    print(f"\n{'would fill' if args.dry_run else 'filled'}: {filled}"
          f"   file not found: {len(missing)}   couldn't read: {len(failed)}")
    for name in missing:
        print(f"  not found: {name}")
    if args.dry_run:
        print("DRY RUN: nothing saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
