"""mogul.py movie verify: check each movie file by reading its credits, and
sort it into a folder by how sure that check is.

Engine: modules/movie/credits.py (Mavrick's credits_id.py, adapted).
For every video file under the SOURCE folder:
  - the {tmdb-ID} in its folder/file name (or the filename title) is the
    guess; the credits have to agree with it,
  - frames from the end credits (then the opening) are OCR'd and the names
    matched against that movie's cast and crew,
  - the file (with its whole movie folder) moves into TARGET/<result>/:

      confirmed   credits back up the name                    (sure)
      identified  name was no help, credits found the movie   (sure)
      mismatch    credits say it's a DIFFERENT movie          (sure it's wrong)
      weak        a few names, or fits several movies         (check by hand)
      unknown     no credits found / nothing matched          (check by hand)

    Sure ones are renamed to what the credits say:
      TARGET/confirmed/1990-1999/Fight Club (1999) {tmdb-550}/Fight Club (1999) Bluray-1080p.mkv
    weak / unknown keep their names and their path under SOURCE.

Never overwrites (that file is reported and left in place). Results go to a
resumable results file (one line per file), so a stopped run carries on
where it left off; the readable report is rebuilt after every run.

TMDB: the claimed movie's cast comes from the files saved by `movie tmdb`
(no request needed); anything else (title searches, the blind name search)
uses the API with the key in secrets.toml, cached locally.
"""

import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from mogul_core import store, tmdb as tmdbapi

STATUSES = ["confirmed", "identified", "mismatch", "weak", "unknown"]
SURE = {"confirmed", "identified", "mismatch"}


class TMDBAdapter:
    """What the credits engine needs from TMDB: saved movie files first,
    then the API (answers cached in a small sqlite file)."""

    def __init__(self, config, store_dir, cache_path):
        self.config = config
        self.store_dir = store_dir
        self.client = None
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        self.db = sqlite3.connect(cache_path)
        self.db.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT)")
        self.saved_hits = 0
        self.api_calls = 0

    def _api(self):
        if self.client is None:
            per = float(self.config.section("movie").get("tmdb_requests_per_second", 20))
            self.client = tmdbapi.Client(self.config, per_second=per)
        return self.client

    def get(self, path, **params):
        key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        row = self.db.execute("SELECT v FROM c WHERE k=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        self.api_calls += 1
        try:
            data = self._api().get(path.lstrip("/"), params)
        except tmdbapi.NotFound:
            data = None
        self.db.execute("INSERT OR REPLACE INTO c VALUES (?,?)", (key, json.dumps(data)))
        self.db.commit()
        return data

    def movie(self, mid):
        try:
            data = store.load(os.path.join(self.store_dir, f"{int(mid)}.json")).get("tmdb")
            if data and "credits" in data:
                self.saved_hits += 1
                return data
        except (OSError, ValueError):
            pass
        return self.get(f"/movie/{mid}", append_to_response="credits")

    def search_movie(self, title, year=None):
        res = self.get("/search/movie", query=title, include_adult="false",
                       **({"year": year} if year else {}))
        out = (res or {}).get("results", [])
        if not out and year:
            res = self.get("/search/movie", query=title, include_adult="false")
            out = (res or {}).get("results", [])
        return out

    def find_imdb(self, imdb_id):
        res = self.get(f"/find/{imdb_id}", external_source="imdb_id")
        hits = (res or {}).get("movie_results", [])
        return hits[0]["id"] if hits else None

    def person_aliases(self, pid):
        res = self.get(f"/person/{pid}")
        return (res or {}).get("also_known_as", []) if res else []

    def search_person(self, name):
        res = self.get("/search/person", query=name, include_adult="false")
        return (res or {}).get("results", [])

    def person_movies(self, pid):
        res = self.get(f"/person/{pid}/movie_credits")
        if not res:
            return set()
        return {m["id"] for m in res.get("cast", []) + res.get("crew", [])}


def run(args, config):
    from . import credits  # needs OpenCV; only loaded for this command

    mcfg = config.section("movie")
    credits.configure(mcfg)

    source = args.source or mcfg.get("verify_source")
    target = args.target or mcfg.get("verify_target")
    results = config.resolve(mcfg.get("verify_results", "mogul_cache/movie-verify_results.jsonl"))
    report_html = os.path.join(config.reports, "movie-verify_report.html")
    report_csv = os.path.join(config.reports, "movie-verify_report.csv")
    os.makedirs(config.reports, exist_ok=True)

    if args.report_only:
        c = credits.write_reports(results, report_html, report_csv)
        print(f"Report: {report_html}  ({', '.join(f'{v} {k}' for k, v in c.items())})")
        return 0

    if not source:
        sys.exit("No source folder: set verify_source in mogul.local.toml [movie] or use --source.")
    source = config.resolve(source)
    if not os.path.exists(source):
        sys.exit(f"Source not found: {source}")
    moving = not args.no_move
    if moving and not target:
        sys.exit("No target folder: set verify_target in mogul.local.toml [movie], use --target, "
                 "or --no-move to only check.")
    target = config.resolve(target) if target else None
    if moving and Path(target).resolve() == Path(source).resolve():
        sys.exit("Source and target are the same folder.")

    folders = dict(zip(STATUSES, STATUSES))
    folders.update(mcfg.get("verify_folders", {}))

    def destination(status):
        sub = folders.get(status)
        return os.path.join(target, sub) if sub else None

    store_dir = config.resolve(mcfg.get("tmdb_store", "mogul_cache/tmdb/movie"))
    cache = config.resolve(mcfg.get("verify_cache", "mogul_cache/tmdb/verify_cache.sqlite"))
    tmdb = TMDBAdapter(config, store_dir, cache)
    langs = (args.langs or mcfg.get("verify_ocr_langs", "en")).split(",")
    ocr = credits.OCR(args.ocr or mcfg.get("verify_ocr", "auto"), langs)

    dry = args.dry_run
    print(f"MOGUL movie verify{' (DRY RUN, nothing moves)' if dry else ''}"
          f"{' (check only, nothing moves)' if not moving else ''}")
    print(f"  source:  {source}")
    if moving:
        print(f"  target:  {target}/{{{','.join(s for s in STATUSES if folders.get(s))}}}")
    print(f"  OCR:     {ocr.name} ({','.join(langs)})")

    skip = [target] if moving else []
    files = list(credits.iter_files(source, skip))
    done = set() if args.redo else {r["file"] for r in credits.load_results(results)}
    todo = [f for f in files if str(f) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"  files:   {len(files)}   already checked: {len(files) - len([f for f in files if str(f) not in done])}"
          f"   this run: {len(todo)}")
    print()

    tally = Counter()
    start = time.time()
    os.makedirs(os.path.dirname(results), exist_ok=True)
    with open(results, "a", encoding="utf-8") as fh:
        for i, f in enumerate(todo, 1):
            t0 = time.time()
            try:
                r = credits.identify(f, tmdb=tmdb, ocr=ocr)
            except KeyboardInterrupt:
                print("\nStopped. Run the same command again to carry on.")
                break
            except Exception as e:
                r = {"file": str(f), "status": "unknown", "error": repr(e)}

            dest = destination(r["status"]) if moving else None
            if dest:
                try:
                    match = r.get("match") if r["status"] in SURE else None
                    new = credits.move_verified(f, source, dest, match, dry)
                    if Path(new).resolve() != Path(f).resolve():
                        r["moved_to"] = ("(dry run) " if dry else "") + new
                except Exception as e:
                    r["move_error"] = repr(e)

            r["seconds"] = round(time.time() - t0, 1)
            r["checked"] = store.now()
            if not dry:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
            tally[r["status"]] += 1
            m = r.get("match") or {}
            label = f"{m.get('title')} ({m.get('year')})" if m else "-"
            moved = ""
            if r.get("moved_to"):
                moved = "  -> would move" if dry else "  -> moved"
                newname = Path(r["moved_to"].replace("(dry run) ", "")).name
                if newname != f.name:
                    moved += f" as {newname}"
            if r.get("move_error"):
                moved = f"  !! not moved: {r['move_error']}"
            elapsed = time.time() - start
            eta = (len(todo) - i) * elapsed / i
            print(f"[{i}/{len(todo)} ~{int(eta // 60)}m left] {r['status'].upper():<10} "
                  f"{label:<40} score={r.get('score', 0):<5} {f.name}{moved}")

    print()
    print("This run: " + (", ".join(f"{v} {k}" for k, v in tally.most_common()) or "nothing new"))
    print(f"TMDB: {tmdb.saved_hits} from saved files, {tmdb.api_calls} API requests")
    if dry:
        print("(dry run: nothing saved to the results file)")
    c = credits.write_reports(results, report_html, report_csv)
    print(f"Report: {report_html}")
    print(f"        {report_csv}")
    print("All checked so far: " + ", ".join(f"{v} {k}" for k, v in c.items()))
    return 0
