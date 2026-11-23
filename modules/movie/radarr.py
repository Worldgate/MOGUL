"""mogul.py movie radarr: add a date range's new releases to Radarr, unmonitored.

What it does:
  1. asks TMDB "discover" for every movie with a release of the chosen
     types (theatrical, disc...) in your region between --from and --to,
  2. reads Radarr's library (and its exclusion list) and skips anything
     already there, in ANY root folder,
  3. downloads each remaining movie's release dates (kept in
     [movie] radarr_release_store so re-runs are fast) and works out what
     matched: theatrical, limited, disc, digital, or rerelease (an old
     movie back in theaters),
  4. adds them to Radarr in [movie] radarr_root_folder, UNMONITORED and
     with no search, so Radarr never grabs them on its own,
  5. tags them so you can browse by month in Radarr:
     tmdb, tmdb-2026-01, tmdb-disc ...

Report: mogul_reports/movie-radarr_<from>_<to>_<date>_<time>.csv
(every movie and what happened to it).

Never touches your movie files. --dry-run changes nothing in Radarr.

TMDB key: secrets.toml [tmdb] (same as movie tmdb).
Radarr key: secrets.toml [radarr] api_key, or the RADARR_API_KEY
environment variable (Radarr: Settings > General > API Key).
"""

import calendar
import concurrent.futures
import csv
import datetime
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from mogul_core import store, tmdb

from .compare import TMDB_RESULT_CAP, fetch_pages

TYPE_NAMES = {1: "premiere", 2: "limited", 3: "theatrical", 4: "digital", 5: "disc", 6: "tv"}
THEATRICAL = {1, 2, 3}


# ------------------------------------------------------------------ Radarr

class RadarrError(Exception):
    pass


def radarr_key(config):
    key = os.environ.get("RADARR_API_KEY")
    path = os.path.join(config.base, "secrets.toml")
    if not key and os.path.isfile(path):
        with open(path, "rb") as f:
            key = tomllib.load(f).get("radarr", {}).get("api_key") or None
    if not key:
        raise SystemExit(
            "No Radarr API key found.\n"
            f"Add it to {path}:\n\n"
            "  [radarr]\n"
            '  api_key = "your key here"\n\n'
            "(Radarr: Settings > General > API Key)\n"
            "or set the RADARR_API_KEY environment variable.")
    return key


class Radarr:
    def __init__(self, url, key, timeout=60):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def call(self, method, path, params=None, body=None):
        address = f"{self.url}/api/v3/{path.lstrip('/')}"
        if params:
            address += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Api-Key": self.key, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            try:
                req = urllib.request.Request(address, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                if e.code == 401:
                    raise SystemExit("Radarr refused the API key (401). Check secrets.toml [radarr].")
                if e.code >= 500 and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RadarrError(f"HTTP {e.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise SystemExit(f"Can't reach Radarr at {self.url}: {getattr(e, 'reason', e)}\n"
                                 "Check [movie] radarr_url (mogul.local.toml).")

    def get(self, path, params=None):
        return self.call("GET", path, params)

    def post(self, path, body):
        return self.call("POST", path, body=body)

    def movie_ids(self):
        return {m.get("tmdbId") for m in self.get("movie") or []}

    def exclusion_ids(self):
        for path in ("exclusions", "importlistexclusion"):
            try:
                data = self.get(path)
            except RadarrError:
                continue
            if isinstance(data, dict):
                data = data.get("records", [])
            return {x.get("tmdbId") for x in data or []}
        print("  (couldn't read Radarr's exclusion list; going on without it)")
        return set()

    def quality_profile_id(self, wanted):
        profiles = self.get("qualityprofile") or []
        for p in profiles:
            if str(p.get("id")) == str(wanted) or p.get("name", "").lower() == str(wanted).lower():
                return p["id"]
        names = ", ".join(f"'{p.get('name')}'" for p in profiles)
        raise SystemExit(f"Radarr has no quality profile '{wanted}'. "
                         f"Set [movie] radarr_quality_profile to one of: {names}")

    def root_folder(self, path):
        roots = [r.get("path", "").rstrip("/") for r in self.get("rootfolder") or []]
        if path.rstrip("/") not in roots:
            raise SystemExit(f"'{path}' isn't a root folder in Radarr.\n"
                             "Add it in Radarr: Settings > Media Management > Root Folders.\n"
                             f"Radarr's root folders: {', '.join(roots) or '(none)'}")
        return path.rstrip("/")

    def tag_ids(self, labels):
        have = {t["label"].lower(): t["id"] for t in self.get("tag") or []}
        for label in labels:
            if label not in have:
                have[label] = self.post("tag", {"label": label})["id"]
        return {label: have[label] for label in labels}

    def lookup(self, tmdb_id):
        data = self.get("movie/lookup/tmdb", {"tmdbId": tmdb_id})
        if isinstance(data, list):
            data = data[0] if data else None
        return data


# ------------------------------------------------------------------ helpers

def parse_bound(text, end):
    """2026 / 2026-01 / 2026-01-15 -> a date (start or end of that span)."""
    s = text.strip()
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        return datetime.date(y, 12, 31) if end else datetime.date(y, 1, 1)
    if re.fullmatch(r"\d{4}-\d{1,2}", s):
        y, m = map(int, s.split("-"))
        return datetime.date(y, m, calendar.monthrange(y, m)[1] if end else 1)
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"Can't read the date '{text}'. Use 2026, 2026-01 or 2026-01-15.")


def day(text):
    try:
        return datetime.date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def tag_label(text):
    """Radarr tags: lowercase letters, numbers and dashes only."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", str(text).lower())).strip("-")


def months(start, end):
    """Split a date range into whole-month pieces (clipped to the range)."""
    cur = start
    while cur <= end:
        last = datetime.date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        yield cur, min(last, end)
        cur = last + datetime.timedelta(days=1)


def discover(client, params, start, end, jobs):
    """Every movie with a matching release in the range. Over TMDB's
    10,000-result cap, it goes month by month."""
    p = dict(params, **{"release_date.gte": start.isoformat(), "release_date.lte": end.isoformat()})
    first = client.get("discover/movie", dict(p, page=1))
    total = first.get("total_results", 0)
    if total <= TMDB_RESULT_CAP:
        return fetch_pages(client, p, first, jobs), []
    pieces = list(months(start, end))
    if len(pieces) == 1:
        return fetch_pages(client, p, first, jobs), [
            f"{start:%Y-%m}: {total} movies, only the first {TMDB_RESULT_CAP} listed "
            "(narrow the range or raise radarr_min_votes)"]
    print(f"  {total} movies: more than TMDB lists in one go, fetching month by month ...")
    found, notes = {}, []
    for a, b in pieces:
        got, n = discover(client, params, a, b, jobs)
        notes += n
        for m in got:
            found[m["id"]] = m
    return list(found.values()), notes


def matched(rd, region, types, start, end):
    out = []
    for country in (rd or {}).get("results", []):
        if country.get("iso_3166_1") != region:
            continue
        for r in country.get("release_dates", []):
            d = day(r.get("release_date"))
            if r.get("type") in types and d and start <= d <= end:
                out.append({"type": r["type"], "date": d, "note": (r.get("note") or "").strip()})
    return sorted(out, key=lambda x: (x["date"], x["type"]))


def first_release(movie, rd):
    """Earliest theatrical release anywhere (to spot re-releases)."""
    dates = [d for d in [day(movie.get("release_date"))] if d]
    for country in (rd or {}).get("results", []):
        for r in country.get("release_dates", []):
            d = day(r.get("release_date"))
            if r.get("type") in THEATRICAL and d:
                dates.append(d)
    return min(dates) if dates else None


def kind(match, first, rerelease_days):
    if match["type"] in THEATRICAL and first and (match["date"] - first).days > rerelease_days:
        return "rerelease"
    return TYPE_NAMES.get(match["type"], f"type{match['type']}")


class ReleaseStore:
    """One file per movie: <store>/<id>.json, re-downloaded when older
    than max_age days (release dates get added over time)."""

    def __init__(self, folder, max_age_days, refresh):
        self.folder = folder
        self.max_age = datetime.timedelta(days=float(max_age_days))
        self.refresh = refresh

    def path(self, tid):
        return os.path.join(self.folder, f"{tid}.json")

    def get(self, tid):
        if self.refresh:
            return None
        try:
            data = store.load(self.path(tid))
            fetched = datetime.datetime.fromisoformat(data["mogul"]["fetched"])
        except (OSError, ValueError, KeyError):
            return None
        if datetime.datetime.now().astimezone() - fetched > self.max_age:
            return None
        return data["tmdb"]

    def put(self, tid, rd):
        store.save(self.path(tid), {"mogul": {"schema": "mogul.tmdb.release_dates",
                                              "tmdb_id": tid, "fetched": store.now()},
                                    "tmdb": rd})


# ------------------------------------------------------------------ run

def run(args, config):
    try:
        return _run(args, config)
    except tmdb.BadKey as e:
        raise SystemExit(f"TMDB refused the API key: {e}. Check secrets.toml [tmdb].")


def _run(args, config):
    mcfg = config.section("movie")
    start = parse_bound(args.start, end=False)
    end = parse_bound(args.end or args.start, end=True)
    if end < start:
        raise SystemExit("--to is before --from")
    types = ([int(t) for t in args.types.split(",") if t.strip()] if args.types
             else [int(t) for t in mcfg.get("radarr_release_types", [3, 5])])
    region = (args.region or mcfg.get("radarr_region") or mcfg.get("watch_region", "US")).upper()
    rerelease_days = int(mcfg.get("radarr_rerelease_days", 365))
    skip_rereleases = bool(mcfg.get("radarr_skip_rereleases", False))
    min_votes = int(args.min_votes if args.min_votes is not None else mcfg.get("radarr_min_votes", 0))
    monitored = bool(mcfg.get("radarr_monitored", False))

    print(f"MOGUL movie radarr: {start} to {end}, {region}, "
          f"{', '.join(TYPE_NAMES.get(t, str(t)) for t in types)}"
          + ("  (DRY RUN: nothing is added)" if args.dry_run else ""))

    # Radarr first: bad key / wrong folder should stop us before the TMDB work.
    url = mcfg.get("radarr_url", "http://localhost:7878")
    radarr = Radarr(url, radarr_key(config))
    root = mcfg.get("radarr_root_folder", "")
    if not root:
        raise SystemExit("Set [movie] radarr_root_folder (in mogul.local.toml), "
                         'e.g. radarr_root_folder = "/qnap/movies-tmdb"')
    root = radarr.root_folder(root)
    profile = radarr.quality_profile_id(mcfg.get("radarr_quality_profile", "Any"))
    print(f"  Radarr {url}: reading the library ...")
    have = radarr.movie_ids()
    excluded = radarr.exclusion_ids() if mcfg.get("radarr_respect_exclusions", True) else set()
    print(f"  {len(have)} movies in Radarr, {len(excluded)} on its exclusion list")

    client = tmdb.Client(config, per_second=float(mcfg.get("tmdb_requests_per_second", 20)))
    params = {"region": region, "language": mcfg.get("tmdb_language", "en-US"),
              "with_release_type": "|".join(str(t) for t in types),
              "include_adult": "false", "include_video": "false",
              "sort_by": "primary_release_date.asc"}
    if min_votes > 0:
        params["vote_count.gte"] = min_votes
    print("  asking TMDB ...")
    movies, notes = discover(client, params, start, end, args.jobs)
    print(f"  TMDB: {len(movies)} movies")

    rows = []

    def row(m, status, **extra):
        r = {"status": status, "tmdb_id": m["id"], "title": m.get("title") or m.get("original_title"),
             "year": (m.get("release_date") or "")[:4], "kinds": "", "matched": "", "notes": "",
             "tags": "", "votes": m.get("vote_count", 0),
             "tmdb_url": f"https://www.themoviedb.org/movie/{m['id']}"}
        r.update(extra)
        rows.append(r)
        return r

    todo = []
    for m in movies:
        if m["id"] in have:
            row(m, "IN RADARR")
        elif m["id"] in excluded:
            row(m, "EXCLUDED")
        else:
            todo.append(m)

    # Release dates (stored, so a re-run of the same months is quick)
    rstore = ReleaseStore(config.resolve(mcfg.get("radarr_release_store",
                                                  "mogul_cache/tmdb/release_dates")),
                          mcfg.get("radarr_release_max_age_days", 7), args.refresh)
    dates, failed = {}, {}
    need = []
    for m in todo:
        rd = rstore.get(m["id"])
        if rd is None:
            need.append(m["id"])
        else:
            dates[m["id"]] = rd
    if need:
        print(f"  release dates: {len(dates)} stored, downloading {len(need)} ...")

        def fetch(tid):
            try:
                rd = client.get(f"movie/{tid}/release_dates")
                rstore.put(tid, rd)
                return tid, rd, None
            except tmdb.BadKey:
                raise
            except tmdb.TMDBError as e:
                return tid, None, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for n, (tid, rd, err) in enumerate(pool.map(fetch, need), 1):
                if err:
                    failed[tid] = err
                else:
                    dates[tid] = rd
                if n % 25 == 0 or n == len(need):
                    print(f"\r    {n}/{len(need)}", end="", flush=True)
        print()

    prefix = tag_label(mcfg.get("radarr_tag_prefix", "tmdb"))
    use_tags = bool(mcfg.get("radarr_tags", True))
    adds = []
    for m in todo:
        tid = m["id"]
        if tid in failed:
            row(m, "ERROR", notes=f"release dates: {failed[tid]}")
            continue
        rd = dates.get(tid)
        hits = matched(rd, region, types, start, end)
        if not hits:
            row(m, "NO MATCH", notes=f"TMDB listed it, but no {region} release of these types in range")
            continue
        first = first_release(m, rd)
        kinds = list(dict.fromkeys(kind(h, first, rerelease_days) for h in hits))
        info = {"kinds": " ".join(kinds),
                "matched": " ".join(f"{h['date']}:{TYPE_NAMES.get(h['type'])}" for h in hits),
                "notes": " | ".join(sorted({h["note"] for h in hits if h["note"]}))}
        if skip_rereleases and kinds == ["rerelease"]:
            row(m, "RERELEASE", **info)
            continue
        labels = []
        if use_tags:
            labels += [tag_label(t) for t in mcfg.get("radarr_tag_base", ["tmdb"])]
            if mcfg.get("radarr_tag_month", True):
                labels += [f"{prefix}-{h['date']:%Y-%m}" for h in hits]
            if mcfg.get("radarr_tag_kind", True):
                labels += [f"{prefix}-{k}" for k in kinds]
            labels = list(dict.fromkeys(x for x in labels if x))
        r = row(m, "WOULD ADD" if args.dry_run else "ADD", tags=" ".join(labels), **info)
        adds.append((r, labels))

    # Add to Radarr
    if adds and not args.dry_run:
        print(f"  adding {len(adds)} to Radarr ({root}, unmonitored, no search) ...")
        tag_map = radarr.tag_ids(sorted({x for _, labels in adds for x in labels}))
        for n, (r, labels) in enumerate(adds, 1):
            try:
                movie = radarr.lookup(r["tmdb_id"])
                if not movie:
                    r["status"], r["notes"] = "ERROR", "Radarr's lookup found nothing"
                    continue
                if movie.get("id"):
                    r["status"] = "IN RADARR"
                    continue
                movie.update({
                    "qualityProfileId": profile,
                    "rootFolderPath": root,
                    "monitored": monitored,
                    "minimumAvailability": mcfg.get("radarr_minimum_availability", "released"),
                    "tags": [tag_map[x] for x in labels],
                    "addOptions": {"searchForMovie": bool(mcfg.get("radarr_search", False)),
                                   "monitor": "movieOnly" if monitored else "none"},
                })
                movie.pop("path", None)
                movie.pop("folder", None)
                radarr.post("movie", movie)
                r["status"] = "ADDED"
            except RadarrError as e:
                if "already been added" in str(e) or "MovieExists" in str(e):
                    r["status"] = "IN RADARR"
                else:
                    r["status"], r["notes"] = "ERROR", str(e)
            if n % 10 == 0 or n == len(adds):
                print(f"\r    {n}/{len(adds)}", end="", flush=True)
        print()

    # Report
    os.makedirs(config.reports, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    report = os.path.join(config.reports,
                          f"movie-radarr_{start}_{end}_{stamp}{'_dryrun' if args.dry_run else ''}.csv")
    order = {"ADDED": 0, "WOULD ADD": 0, "ERROR": 1, "NO MATCH": 2, "RERELEASE": 3,
             "EXCLUDED": 4, "IN RADARR": 5}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["matched"], r["title"] or ""))
    fields = ["status", "tmdb_id", "title", "year", "kinds", "matched", "notes", "tags",
              "votes", "tmdb_url"]
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print()
    for status in sorted(counts, key=lambda s: order.get(s, 9)):
        print(f"  {status:<10} {counts[status]:>6}")
    for n in notes:
        print(f"  note: {n}")
    errors = [r for r in rows if r["status"] == "ERROR"]
    for r in errors[:10]:
        print(f"  ERROR {r['title']} {{tmdb-{r['tmdb_id']}}}: {r['notes']}")
    print(f"\nReport: {report}")
    return 1 if errors else 0
