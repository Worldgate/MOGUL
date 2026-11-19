"""mogul.py movie tmdb: download each movie's TMDB info and keep a copy.

One request per movie: /movie/<id> with append_to_response, so the basic
details plus every extra (credits, images, ...) come back in one go. The
answer is saved as-is (nothing thrown away) in

    <[movie] tmdb_store>/<id>.json          default mogul_cache/tmdb/movie/550.json

wrapped like this:

    {"mogul": {"schema": "mogul.tmdb.movie", "tmdb_id": 550,
               "fetched": "...", "request": "<address, no key>", "append": [...]},
     "tmdb":  { ...exactly what TMDB sent... }}

This is a copy of TMDB's data, not the catalog: it can always be
downloaded again. Already-saved movies are skipped unless --refresh or
--older-than DAYS.

Where the IDs come from: TMDB IDs on the command line, or names like
"Fight Club (1999) {tmdb-550}" (also [tmdbid-550], {tmdb=550}) found in
the folders given with --path or [movie] library. Read-only on the movie
folders: nothing there is touched.
"""

import concurrent.futures
import datetime
import json
import os
import re
import sys
import threading
import time

from mogul_core import store, tmdb
from mogul_core.progress import fmt_duration

ID_RE = re.compile(r"[\[{(]tmdb(?:id)?[-=_ ](\d+)[\]})]", re.IGNORECASE)

DEFAULT_APPEND = ["credits", "images", "videos", "release_dates", "external_ids",
                  "keywords", "alternative_titles", "translations"]
DEFAULT_VIDEO_EXT = ["mkv", "mp4", "m4v", "avi", "mov", "wmv", "mpg", "mpeg", "ts",
                     "m2ts", "vob", "iso", "webm", "flv", "divx", "ogm"]


def ids_from_folders(folders, video_ext):
    """Walk the folders. Returns ({id: first name it was seen in},
    [video files with no TMDB ID in their name or any folder above them])."""
    found, missing = {}, []
    exts = {"." + e.lower().lstrip(".") for e in video_ext}
    for root in folders:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            sys.exit(f"Folder not found: {root}")
        print(f"  reading names in {root} ...")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            rel_dir = os.path.relpath(dirpath, root)
            dir_ids = [] if rel_dir == "." else ID_RE.findall(rel_dir)
            for d in dirnames:
                for m in ID_RE.findall(d):
                    found.setdefault(int(m), os.path.join(dirpath, d))
            for name in sorted(filenames):
                file_ids = ID_RE.findall(name)
                for m in file_ids:
                    found.setdefault(int(m), os.path.join(dirpath, name))
                if not file_ids and not dir_ids and os.path.splitext(name)[1].lower() in exts:
                    missing.append(os.path.join(dirpath, name))
    return found, missing


def saved_age_days(path):
    """Days since the saved copy was fetched, or None if there isn't one."""
    try:
        fetched = store.load(path)["mogul"]["fetched"]
        then = datetime.datetime.fromisoformat(fetched)
        return (datetime.datetime.now().astimezone() - then).total_seconds() / 86400
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def run(args, config):
    mcfg = config.section("movie")
    out_dir = config.resolve(mcfg.get("tmdb_store", "mogul_cache/tmdb/movie"))
    append = list(mcfg.get("tmdb_append", DEFAULT_APPEND))
    language = mcfg.get("tmdb_language", "en-US")
    image_langs = mcfg.get("tmdb_image_languages", "en,null")
    video_langs = mcfg.get("tmdb_video_languages", "en,null")
    per_second = float(mcfg.get("tmdb_requests_per_second", 20))
    video_ext = mcfg.get("extensions", DEFAULT_VIDEO_EXT)

    if len(append) > 20:
        sys.exit("TMDB allows at most 20 items in tmdb_append.")

    print(f"MOGUL movie tmdb{' (DRY RUN, nothing will be downloaded)' if args.dry_run else ''}")
    print(f"  saving to: {out_dir}")
    print(f"  includes:  {', '.join(append)}")

    # 1. which movies
    sources = {}
    missing = []
    for raw in args.ids:
        if not raw.isdigit():
            sys.exit(f"Not a TMDB ID: {raw} (numbers only, e.g. 550)")
        sources.setdefault(int(raw), "command line")
    if not args.ids:
        lib = mcfg.get("library") or []
        folders = args.path or ([lib] if isinstance(lib, str) else list(lib))
        if not folders:
            sys.exit("Give TMDB IDs (mogul.py movie tmdb 550 603), or --path FOLDER, "
                     "or set [movie] library in mogul.local.toml.")
        found, missing = ids_from_folders([config.resolve(f) for f in folders], video_ext)
        sources.update(found)
    print(f"  movies:    {len(sources)}"
          + (f"  ({len(missing)} video file(s) with no {{tmdb-ID}} in the name)" if missing else ""))

    # 2. which ones need downloading
    todo, skipped = [], []
    for tid in sorted(sources):
        path = os.path.join(out_dir, f"{tid}.json")
        if args.refresh or not os.path.exists(path):
            todo.append(tid)
            continue
        if args.older_than is not None:
            age = saved_age_days(path)
            if age is None or age > args.older_than:
                todo.append(tid)
                continue
        skipped.append(tid)
    print(f"  to fetch:  {len(todo)}   already saved: {len(skipped)}")
    print()

    report = {"action": "movie tmdb", "started": store.now(), "dry_run": args.dry_run,
              "append": append, "saved": [], "already_saved": len(skipped),
              "not_found": [], "errors": [], "no_tmdb_id": missing}

    if args.dry_run:
        for tid in todo:
            print(f"  would fetch {tid}   ({sources[tid]})")
    elif todo:
        client = tmdb.Client(config, per_second=per_second)
        params = {"append_to_response": ",".join(append), "language": language,
                  "include_image_language": image_langs,
                  "include_video_language": video_langs}
        lock = threading.Lock()
        done = [0]
        start = time.time()
        width = len(str(len(todo)))
        stop = threading.Event()

        def fetch(tid):
            if stop.is_set():
                return tid, "stopped", None
            try:
                data = client.get(f"movie/{tid}", params)
            except tmdb.NotFound as e:
                return tid, "not_found", str(e)
            except tmdb.BadKey as e:
                stop.set()
                return tid, "bad_key", str(e)
            except tmdb.TMDBError as e:
                return tid, "error", str(e)
            store.save(os.path.join(out_dir, f"{tid}.json"), {
                "mogul": {"schema": "mogul.tmdb.movie", "schema_version": 1,
                          "tmdb_id": tid, "fetched": store.now(),
                          "request": client.url(f"movie/{tid}", params, with_key=False),
                          "append": append},
                "tmdb": data,
            })
            return tid, "saved", data

        def label():
            n = done[0]
            elapsed = time.time() - start
            eta = (f"~{fmt_duration((len(todo) - n) * elapsed / n)} left"
                   if n and elapsed >= 1 else "estimating...")
            return f"[{n:>{width}}/{len(todo)}  {eta:>15}]"

        jobs = max(1, args.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            for tid, result, info in pool.map(fetch, todo):
                if result == "stopped":
                    continue
                with lock:
                    done[0] += 1
                    tag = label()
                if result == "saved":
                    year = (info.get("release_date") or "")[:4]
                    print(f"{tag} SAVED     {tid}  {info.get('title', '?')}"
                          + (f" ({year})" if year else ""))
                    report["saved"].append(tid)
                elif result == "not_found":
                    print(f"{tag} NOT FOUND {tid}  ({sources[tid]})")
                    report["not_found"].append({"tmdb_id": tid, "where": sources[tid],
                                                "message": info})
                elif result == "bad_key":
                    print(f"\nTMDB refused the API key: {info}\n"
                          "Check [tmdb] api_key in secrets.toml.")
                    report["errors"].append({"tmdb_id": tid, "error": info})
                else:
                    print(f"{tag} ERROR     {tid}  {info}")
                    report["errors"].append({"tmdb_id": tid, "where": sources[tid],
                                             "error": info})

    # 3. report
    report["finished"] = store.now()
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    rpath = os.path.join(config.reports,
                         f"movie-tmdb_{stamp}{'_dry-run' if args.dry_run else ''}.json")
    store.save(rpath, report)

    print()
    print(f"Saved: {len(report['saved'])}   already saved: {len(skipped)}   "
          f"not found: {len(report['not_found'])}   errors: {len(report['errors'])}")
    if missing:
        print(f"{len(missing)} video file(s) have no {{tmdb-ID}} in their name or folder "
              "(listed in the report).")
    print(f"Report: {rpath}")
    return 1 if report["errors"] else 0
