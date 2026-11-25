"""mogul.py radarr backup: keep Radarr's setup safe outside Radarr.

Two things, saved together in a dated folder under [movie]
radarr_backup_folder (default mogul_userdata/radarr/backup/):

  1. Radarr's OWN backup zip (database + settings). Radarr makes it
     (System > Backup), MOGUL downloads it. Restore it in Radarr:
     System > Backup > Restore. Inside Docker those zips normally sit in
     Radarr's /config folder, so if that volume is lost they go with it;
     this copy doesn't.
  2. export.json: a readable copy of what matters, read through the API:
     every movie (TMDB ID, path, root folder, monitored, quality
     profile, tags, file), tags, root folders, quality profiles, custom
     formats, naming and media management settings, import lists,
     the exclusion list (your blocklist), indexers, download clients,
     notifications... Enough to rebuild a fresh Radarr by hand or with
     a future `radarr restore` (not built yet). Radarr hides passwords
     and API keys in its answers, so those aren't in it.

Keeps the newest radarr_backup_keep (12) backups, removing older MOGUL
backup folders (only folders it made itself). 0 = keep them all.
`radarr update` runs this first (radarr_backup_before_update).
"""

import datetime
import os
import re
import shutil

from mogul_core import store

from .client import RadarrError, connect

STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")

# (name in export.json, API path(s) to try)
ENDPOINTS = [
    ("system_status", ["system/status"]),
    ("tags", ["tag"]),
    ("root_folders", ["rootfolder"]),
    ("quality_profiles", ["qualityprofile"]),
    ("quality_definitions", ["qualitydefinition"]),
    ("custom_formats", ["customformat"]),
    ("delay_profiles", ["delayprofile"]),
    ("release_profiles", ["releaseprofile"]),
    ("auto_tagging", ["autotagging"]),
    ("naming", ["config/naming"]),
    ("media_management", ["config/mediamanagement"]),
    ("indexer_settings", ["config/indexer"]),
    ("download_client_settings", ["config/downloadclient"]),
    ("ui_settings", ["config/ui"]),
    ("remote_path_mappings", ["remotepathmapping"]),
    ("import_lists", ["importlist"]),
    ("exclusions", ["exclusions", "importlistexclusion"]),
    ("indexers", ["indexer"]),
    ("download_clients", ["downloadclient"]),
    ("notifications", ["notification"]),
    ("metadata", ["metadata"]),
]
# Left out on purpose: config/host (holds Radarr's own API key and login).

MOVIE_KEYS = ["id", "tmdbId", "imdbId", "title", "year", "path", "rootFolderPath",
              "folderName", "qualityProfileId", "monitored", "minimumAvailability", "tags",
              "added", "hasFile", "sizeOnDisk"]


def slim_movie(m):
    out = {k: m.get(k) for k in MOVIE_KEYS if k in m}
    f = m.get("movieFile")
    if f:
        out["movieFile"] = {k: f.get(k) for k in ("relativePath", "size", "dateAdded",
                                                   "sceneName", "releaseGroup", "edition")
                            if k in f}
        q = (f.get("quality") or {}).get("quality") or {}
        out["movieFile"]["quality"] = q.get("name")
    return out


def get_first(radarr, paths):
    last = None
    for p in paths:
        try:
            data = radarr.get(p)
            if isinstance(data, dict) and "records" in data and isinstance(data["records"], list):
                data = data["records"]
            return data, None
        except RadarrError as e:
            last = str(e)
    return None, last


def make(config, ctx=None, no_zip=False, keep=None):
    """Make one backup. Returns the folder it went into."""
    mcfg = config.section("movie")
    ctx = ctx if ctx is not None else {}
    radarr = ctx.get("radarr") or connect(config)
    ctx["radarr"] = radarr
    base = config.resolve(mcfg.get("radarr_backup_folder", "mogul_userdata/radarr/backup"))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    folder = os.path.join(base, stamp)
    os.makedirs(folder, exist_ok=True)
    print(f"  backup folder: {folder}")

    # 1. Radarr's own zip
    zip_note = "skipped"
    if not no_zip and mcfg.get("radarr_backup_zip", True):
        try:
            print("  asking Radarr to make its backup zip ...")
            state = radarr.command("Backup")
            if state.get("status") != "completed":
                raise RadarrError(f"Radarr's backup ended as '{state.get('status')}'")
            backups = radarr.get("system/backup") or []
            if not backups:
                raise RadarrError("Radarr lists no backups")
            newest = max(backups, key=lambda b: b.get("time") or "")
            dest = os.path.join(folder, os.path.basename(newest.get("name") or "radarr_backup.zip"))
            radarr.download(newest["path"], dest)
            zip_note = f"{os.path.basename(dest)} ({os.path.getsize(dest) / 1e6:.1f} MB)"
            print(f"  zip: {zip_note}")
        except (RadarrError, OSError, KeyError) as e:
            zip_note = f"FAILED: {e}"
            print(f"  zip: couldn't get Radarr's backup zip ({e}); the export below still works")

    # 2. the export
    print("  reading Radarr's movies and settings ...")
    export = {"mogul": {"schema": "mogul.radarr.export", "schema_version": 1,
                        "made": store.now(), "radarr_url": radarr.url, "zip": zip_note},
              "errors": {}}
    movies, err = get_first(radarr, ["movie"])
    if err:
        raise SystemExit(f"Couldn't read Radarr's movie list: {err}")
    export["movies"] = [slim_movie(m) for m in movies or []]
    for name, paths in ENDPOINTS:
        data, err = get_first(radarr, paths)
        if err:
            export["errors"][name] = err
        else:
            export[name] = data
    store.save(os.path.join(folder, "export.json"), export)
    counts = {k: len(v) for k, v in export.items() if isinstance(v, list)}
    print("  export.json: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()))
    if export["errors"]:
        print("  (not available from this Radarr: " + ", ".join(export["errors"]) + ")")

    # 3. keep the newest N
    keep = keep if keep is not None else int(mcfg.get("radarr_backup_keep", 12))
    if keep > 0:
        mine = sorted(d for d in os.listdir(base)
                      if STAMP_RE.match(d) and os.path.isdir(os.path.join(base, d)))
        for old in mine[:-keep]:
            shutil.rmtree(os.path.join(base, old))
            print(f"  removed old backup {old} (keeping the newest {keep})")
    return folder


def run(args, config):
    print("MOGUL radarr backup")
    folder = make(config, no_zip=args.no_zip, keep=args.keep)
    print(f"\nSaved in {folder}")
    return 0
