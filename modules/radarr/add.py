"""mogul.py radarr add (same as: mogul.py movie radarr): add movies from TMDB to Radarr, unmonitored,
without the junk.

Three ways to pick movies (they can be mixed):

  By date      --from 2026-01 [--to 2026-03]
               every movie with a release of the chosen types (theatrical,
               disc...) in your region in that range, like before.
  By people    --cast "Keanu Reeves"   --director "John Carpenter"
               --crew NAME (any job)
               everything that person made. With --from/--to, only movies
               that first came out in that range.
  By studio /  --studio "Warner Bros*"  --genre Horror
  genre        (with --from: within the dates; alone: all of it)

  Same option twice = either (--cast A --cast B: A or B).
  Different options = all of them (--cast A --studio B: both).
  --also 12345 [67890]: add these TMDB IDs no matter what the filters say.

Then the junk filter (settings radarr_* in mogul.toml, --no-junk-filter
turns it off), so a year of TMDB doesn't fill Radarr with 2-minute
shorts and school projects:
  - shorter than radarr_min_runtime (40 min), or no runtime on TMDB
  - no production company on TMDB at all
  - fewer than radarr_min_votes votes
  - Documentary (radarr_skip_genres; --include-docs keeps them)
  - with --cast: billed lower than --top (10), uncredited cameos, and
    "appears as himself" in a documentary / concert / TV special. Playing
    himself in a real movie (Keanu in Always Be My Maybe) is kept and noted.
  - with no --from: movies that aren't out yet

Skipped (never added): anything already in Radarr (any root folder), on
Radarr's exclusion list (your blocklist: delete a movie in Radarr with
"add exclusion" and it never comes back), or already in your movie
folders ([movie] library) with a {tmdb-ID} but not in Radarr (ON DISK).

Added to [movie] radarr_root_folder (or --root), UNMONITORED and with no
search unless --monitor / --search, and tagged so the batch is easy to
find in Radarr: tmdb, tmdb-2026-01, tmdb-disc, tmdb-cast-keanu-reeves...

Prints every movie grouped by what happened (ADD / SKIPPED with the
reason / ON DISK / IN RADARR...). Report with everything:
mogul_reports/movie-radarr_<what>_<date>_<time>.csv

Never touches your movie files. --dry-run changes nothing in Radarr.

TMDB key: secrets.toml [tmdb] (same as movie tmdb).
Radarr key: secrets.toml [radarr] api_key, or the RADARR_API_KEY
environment variable (Radarr: Settings > General > API Key).
"""

import calendar
import concurrent.futures
import csv
import datetime
import fnmatch
import os
import re
import time

from mogul_core import store, tmdb
from modules.movie import library
from modules.movie.compare import TMDB_RESULT_CAP, discover_all, fetch_pages
from modules.movie.pull import fold

from .client import Radarr, RadarrError, radarr_key

TYPE_NAMES = {1: "premiere", 2: "limited", 3: "theatrical", 4: "digital", 5: "disc", 6: "tv"}
THEATRICAL = {1, 2, 3}
SELF_RE = re.compile(r"^\s*(self|himself|herself|themselves|themself)\b", re.IGNORECASE)


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
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", fold(str(text)))).strip("-")


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


class DetailStore:
    """Each movie's TMDB details (runtime, studios, genres, credits,
    release dates), slimmed down, one file per movie:
    <store>/<id>.json. Re-downloaded when older than max_age days."""

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

    def put(self, tid, details):
        store.save(self.path(tid), {"mogul": {"schema": "mogul.tmdb.radarr_details",
                                              "tmdb_id": tid, "fetched": store.now()},
                                    "tmdb": details})


def slim(d):
    """Keep only what the filters need (full credits are big)."""
    credits = d.get("credits") or {}
    out = {k: d.get(k) for k in ("id", "title", "original_title", "release_date", "runtime",
                                 "vote_count", "status")}
    out["genres"] = [{"id": g.get("id"), "name": g.get("name")} for g in d.get("genres") or []]
    out["production_companies"] = [{"id": c.get("id"), "name": c.get("name")}
                                   for c in d.get("production_companies") or []]
    out["release_dates"] = d.get("release_dates") or {}
    out["credits"] = {
        "cast": [{"id": c.get("id"), "name": c.get("name"), "character": c.get("character") or "",
                  "order": c.get("order")} for c in credits.get("cast") or []],
        "crew": [{"id": c.get("id"), "name": c.get("name"), "job": c.get("job")}
                 for c in credits.get("crew") or []],
    }
    return out


# ------------------------------------------------------------------ names -> TMDB IDs

def resolve_person(client, text):
    """'Keanu Reeves' or '6384' -> (id, name). Several people with the same
    name: takes the far more famous one, otherwise lists them and stops."""
    t = text.strip()
    if t.isdigit():
        try:
            p = client.get(f"person/{t}")
        except tmdb.NotFound:
            raise SystemExit(f"TMDB has no person with ID {t}.")
        return int(t), p.get("name") or t
    results = client.get("search/person", {"query": t, "include_adult": "false"}).get("results", [])

    def known(p):
        titles = [k.get("title") or k.get("name") for k in p.get("known_for") or []]
        return ", ".join(x for x in titles if x)[:70]

    exact = [p for p in results if fold(p.get("name")) == fold(t)]
    if len(exact) == 1:
        return exact[0]["id"], exact[0]["name"]
    if len(exact) > 1:
        exact.sort(key=lambda p: p.get("popularity") or 0, reverse=True)
        a, b = exact[0].get("popularity") or 0, exact[1].get("popularity") or 0
        if a >= 5 * max(b, 0.1):
            return exact[0]["id"], exact[0]["name"]
        lines = "\n".join(f"  {p['id']:>8}  {p['name']}  ({known(p)})" for p in exact[:10])
        raise SystemExit(f"More than one '{t}' on TMDB. Use the number instead of the name:\n{lines}")
    if not results:
        raise SystemExit(f"TMDB has nobody called '{t}'.")
    lines = "\n".join(f"  {p['id']:>8}  {p['name']}  ({known(p)})" for p in results[:8])
    raise SystemExit(f"Nobody called exactly '{t}' on TMDB. Did you mean one of these? "
                     f"(use the name or the number)\n{lines}")


def resolve_studios(client, patterns):
    """Studio names (wildcards allowed) or IDs -> {id: name}. One big studio
    is many TMDB companies ("Warner Bros. Pictures", "Warner Bros.
    Animation"...), so "Warner Bros*" picks up all of them."""
    out = {}
    for pat in patterns:
        p = pat.strip()
        if p.isdigit():
            try:
                c = client.get(f"company/{p}")
            except tmdb.NotFound:
                raise SystemExit(f"TMDB has no company with ID {p}.")
            out[int(p)] = c.get("name") or p
            continue
        query = re.sub(r"\s+", " ", p.replace("*", " ")).strip()
        found, page, pages = [], 1, 1
        while page <= min(pages, 10):
            data = client.get("search/company", {"query": query, "page": page})
            found += data.get("results", [])
            pages = data.get("total_pages", 1)
            page += 1
        want = fold(p)
        hits = [c for c in found if fnmatch.fnmatchcase(fold(c.get("name")), want)]
        if not hits:
            near = "\n".join(f"  {c['id']:>8}  {c['name']}" for c in found[:15])
            raise SystemExit(f"No studio named '{p}' on TMDB."
                             + (f" Close ones (use * for 'starts with', or the number):\n{near}"
                                if near else ""))
        for c in hits:
            out[c["id"]] = c["name"]
    return out


def resolve_genres(client, names, language):
    """Genre names -> {id: name}. Unknown name: lists TMDB's genres."""
    gl = client.get("genre/movie/list", {"language": language}).get("genres", [])
    by_name = {fold(g["name"]): g for g in gl}
    out = {}
    for n in names:
        g = by_name.get(fold(n))
        if not g and str(n).isdigit():
            g = next((x for x in gl if x["id"] == int(n)), None)
        if not g:
            raise SystemExit(f"TMDB has no movie genre '{n}'. Genres: "
                             + ", ".join(x["name"] for x in gl))
        out[g["id"]] = g["name"]
    return out


def library_ids(config, mcfg):
    """TMDB IDs of movies already in your movie folders ({tmdb-ID} names)."""
    lib = mcfg.get("library") or []
    folders = [lib] if isinstance(lib, str) else list(lib)
    usable = []
    for f in folders:
        p = config.resolve(f)
        if os.path.isdir(p):
            usable.append(p)
        else:
            print(f"  (movie folder not found, not checked: {p})")
    if not usable:
        return set(), 0
    return {tid for tid, _, _ in library.find_units(usable)}, len(usable)


# ------------------------------------------------------------------ run

DEFAULTS = dict(start=None, end=None, cast=None, director=None, crew=None, studio=None,
                genre=None, also=None, top=None, min_runtime=None, min_votes=None,
                include_docs=False, no_junk_filter=False, types=None, region=None, root=None,
                profile=None, monitor=False, search=False, no_library_check=False, show=50,
                refresh=False, jobs=4, dry_run=False)


def run(args, config, ctx=None):
    """ctx: a dict shared between several runs (radarr update), so Radarr's
    library and your folders are read once. Returns 1 if anything errored."""
    for k, v in DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)
    try:
        return _run(args, config, ctx if ctx is not None else {})
    except tmdb.BadKey as e:
        raise SystemExit(f"TMDB refused the API key: {e}. Check secrets.toml [tmdb].")


def _run(args, config, ctx):
    mcfg = config.section("movie")
    also = []
    for chunk in args.also or []:
        for x in re.split(r"[,\s]+", chunk):
            if x.strip():
                if not x.strip().isdigit():
                    raise SystemExit(f"--also takes TMDB movie numbers, not '{x}'")
                also.append(int(x))
    people_given = bool(args.cast or args.director or args.crew)
    if not (args.start or people_given or args.studio or args.genre or also):
        raise SystemExit("Say what to add: --from DATE, and/or --cast / --director / --crew / "
                         "--studio / --genre, or --also TMDB_ID (see --help).")
    if args.end and not args.start:
        raise SystemExit("--to needs a --from")

    dated = bool(args.start)
    start = parse_bound(args.start, end=False) if dated else None
    end = parse_bound(args.end or args.start, end=True) if dated else None
    if dated and end < start:
        raise SystemExit("--to is before --from")
    types = ([int(t) for t in args.types.split(",") if t.strip()] if args.types
             else [int(t) for t in mcfg.get("radarr_release_types", [3, 5])])
    region = (args.region or mcfg.get("radarr_region") or mcfg.get("watch_region", "US")).upper()
    language = mcfg.get("tmdb_language", "en-US")
    rerelease_days = int(mcfg.get("radarr_rerelease_days", 365))
    skip_rereleases = bool(mcfg.get("radarr_skip_rereleases", False))

    # junk filter settings
    junk = not args.no_junk_filter
    min_votes = int(args.min_votes if args.min_votes is not None else mcfg.get("radarr_min_votes", 0))
    min_runtime = int(args.min_runtime if args.min_runtime is not None
                      else mcfg.get("radarr_min_runtime", 40))
    skip_no_runtime = bool(mcfg.get("radarr_skip_no_runtime", True))
    require_studio = bool(mcfg.get("radarr_require_studio", True))
    skip_genre_names = [] if args.include_docs else list(mcfg.get("radarr_skip_genres", ["Documentary"]))
    self_genre_names = list(mcfg.get("radarr_self_skip_genres", ["Documentary", "Music", "TV Movie"]))
    top = args.top if args.top is not None else int(mcfg.get("radarr_cast_top", 10))
    skip_uncredited = bool(mcfg.get("radarr_skip_uncredited", True))
    released_only = bool(mcfg.get("radarr_released_only", True))
    if not junk:
        min_votes, min_runtime, skip_no_runtime, require_studio = 0, 0, False, False
        skip_genre_names, top, skip_uncredited = [], 0, False

    monitored = bool(args.monitor or args.search or mcfg.get("radarr_monitored", False))
    search = bool(args.search or mcfg.get("radarr_search", False))

    what = []
    if dated:
        what.append(f"{start} to {end}")
    for label, vals in (("cast", args.cast), ("director", args.director), ("crew", args.crew),
                        ("studio", args.studio), ("genre", args.genre)):
        if vals:
            what.append(f"{label}: {' or '.join(vals)}")
    if also:
        what.append(f"also: {', '.join(map(str, also))}")
    print("MOGUL radarr add: " + "; ".join(what)
          + ("  (DRY RUN: nothing is added)" if args.dry_run else ""))
    if dated and not people_given:
        print(f"  releases: {region}, {', '.join(TYPE_NAMES.get(t, str(t)) for t in types)}")
    if junk:
        bits = [f"under {min_runtime} min" if min_runtime else "",
                "no runtime" if skip_no_runtime else "",
                "no studio" if require_studio else "",
                f"under {min_votes} votes" if min_votes else "",
                ", ".join(skip_genre_names).lower(),
                f"billed below #{top}" if (top and args.cast) else "",
                "uncredited" if (skip_uncredited and args.cast) else ""]
        print("  junk filter: skipping " + ", ".join(b for b in bits if b))
    else:
        print("  junk filter: OFF")

    # Radarr first: bad key / wrong folder should stop us before the TMDB work.
    url = mcfg.get("radarr_url", "http://localhost:7878")
    if "radarr" not in ctx:
        ctx["radarr"] = Radarr(url, radarr_key(config))
    radarr = ctx["radarr"]
    root = args.root or mcfg.get("radarr_root_folder", "")
    if not root:
        raise SystemExit("Set [movie] radarr_root_folder (in mogul.local.toml), "
                         'e.g. radarr_root_folder = "/qnap/movies-tmdb", or use --root')
    root = radarr.root_folder(root)
    profile = radarr.quality_profile_id(args.profile or mcfg.get("radarr_quality_profile", "Any"))
    if "have" not in ctx:
        print(f"  Radarr {url}: reading the library ...")
        ctx["have"] = radarr.movie_ids()
        ctx["excluded"] = (radarr.exclusion_ids() if mcfg.get("radarr_respect_exclusions", True)
                           else set())
        print(f"  {len(ctx['have'])} movies in Radarr, {len(ctx['excluded'])} on its exclusion list")
    have, excluded = ctx["have"], ctx["excluded"]
    if "on_disk" not in ctx:
        ctx["on_disk"] = set()
        if mcfg.get("radarr_check_library", True) and not args.no_library_check:
            ctx["on_disk"], nroots = library_ids(config, mcfg)
            if nroots:
                print(f"  {len(ctx['on_disk'])} movies in your movie folders")
    on_disk = ctx["on_disk"]

    if "tmdb" not in ctx:
        ctx["tmdb"] = tmdb.Client(config, per_second=float(mcfg.get("tmdb_requests_per_second", 20)))
    client = ctx["tmdb"]

    # names -> TMDB IDs
    people = {}
    for label, names in (("cast", args.cast), ("director", args.director), ("crew", args.crew)):
        if names:
            people[label] = dict(resolve_person(client, n) for n in names)
            print(f"  {label}: " + ", ".join(f"{v} ({k})" for k, v in people[label].items()))
    studios = resolve_studios(client, args.studio) if args.studio else {}
    if studios:
        print(f"  studios ({len(studios)}): "
              + ", ".join(f"{v} ({k})" for k, v in sorted(studios.items(), key=lambda x: x[1]))[:600])
    genres = resolve_genres(client, args.genre, language) if args.genre else {}
    skip_genres = resolve_genres(client, skip_genre_names, language) if skip_genre_names else {}
    self_genres = resolve_genres(client, self_genre_names, language) if self_genre_names else {}

    # ---- candidates
    notes = []
    if people:
        print("  asking TMDB for their movies ...")
        per_kind = []
        info = {}
        for label, persons in people.items():
            ids = set()
            for pid in persons:
                pc = client.get(f"person/{pid}/movie_credits", {"language": language})
                entries = pc.get("cast", []) if label == "cast" else [
                    c for c in pc.get("crew", []) if label == "crew" or c.get("job") == "Director"]
                for e in entries:
                    ids.add(e["id"])
                    info.setdefault(e["id"], e)
            per_kind.append(ids)
        keep = set.intersection(*per_kind) if per_kind else set()
        movies = [info[i] for i in keep]
        if dated:
            inside = [m for m in movies if (lambda d: d is None or start <= d <= end)(day(m.get("release_date")))]
            if len(inside) < len(movies):
                print(f"  {len(movies) - len(inside)} of their movies came out outside {start} to {end}")
            movies = inside
    elif dated or studios or genres:
        params = {"language": language, "include_adult": "false", "include_video": "false",
                  "sort_by": "primary_release_date.asc"}
        if studios:
            params["with_companies"] = "|".join(str(i) for i in studios)
        if genres:
            params["with_genres"] = "|".join(str(i) for i in genres)
        # let TMDB drop the obvious junk before we download anything
        if min_votes > 0:
            params["vote_count.gte"] = min_votes
        if min_runtime and skip_no_runtime:
            params["with_runtime.gte"] = min_runtime
        if skip_genres:
            params["without_genres"] = ",".join(str(i) for i in skip_genres)
        print("  asking TMDB ...")
        if dated:
            params.update({"region": region, "with_release_type": "|".join(str(t) for t in types)})
            movies, notes = discover(client, params, start, end, args.jobs)
        else:
            movies, _, notes = discover_all(client, params, args.jobs)
    else:
        movies = []
    print(f"  TMDB: {len(movies)} movies" + (" (+ --also)" if also else ""))
    known = {m["id"] for m in movies}
    for tid in also:
        if tid not in known:
            movies.append({"id": tid, "title": f"TMDB {tid}"})

    # ---- report rows
    rows = []

    def row(m, status, **extra):
        r = {"status": status, "tmdb_id": m["id"], "title": m.get("title") or m.get("original_title"),
             "year": (m.get("release_date") or "")[:4], "why": "", "role": "", "runtime": "",
             "votes": m.get("vote_count", ""), "studios": "", "genres": "", "kinds": "",
             "matched": "", "release_notes": "", "tags": "",
             "tmdb_url": f"https://www.themoviedb.org/movie/{m['id']}"}
        r.update(extra)
        rows.append(r)
        return r

    todo = []
    for m in movies:
        if m["id"] in have:
            row(m, "IN RADARR")
        elif m["id"] in on_disk:
            row(m, "ON DISK", why="in your movie folders, not in Radarr")
        elif m["id"] in excluded and m["id"] not in also:
            row(m, "EXCLUDED", why="on Radarr's exclusion list")
        else:
            todo.append(m)

    # ---- details (stored, so a re-run is quick)
    dstore = DetailStore(config.resolve(mcfg.get("radarr_details_store",
                                                 "mogul_cache/tmdb/radarr_details")),
                         mcfg.get("radarr_details_max_age_days",
                                  mcfg.get("radarr_release_max_age_days", 7)), args.refresh)
    details, failed = {}, {}
    need = []
    for m in todo:
        d = dstore.get(m["id"])
        if d is None:
            need.append(m["id"])
        else:
            details[m["id"]] = d
    if need:
        print(f"  movie details: {len(details)} stored, downloading {len(need)} ...")

        def fetch(tid):
            try:
                d = slim(client.get(f"movie/{tid}", {"language": language,
                                                     "append_to_response": "credits,release_dates"}))
                dstore.put(tid, d)
                return tid, d, None
            except tmdb.BadKey:
                raise
            except tmdb.NotFound:
                return tid, None, "not found on TMDB"
            except tmdb.TMDBError as e:
                return tid, None, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for n, (tid, d, err) in enumerate(pool.map(fetch, need), 1):
                if err:
                    failed[tid] = err
                else:
                    details[tid] = d
                if n % 25 == 0 or n == len(need):
                    print(f"\r    {n}/{len(need)}", end="", flush=True)
        print()

    # ---- decide
    prefix = tag_label(mcfg.get("radarr_tag_prefix", "tmdb"))
    use_tags = bool(mcfg.get("radarr_tags", True))
    filter_tags = []
    if use_tags and mcfg.get("radarr_tag_filters", True):
        for label, persons in people.items():
            filter_tags += [f"{prefix}-{label}-{tag_label(n)}" for n in persons.values()]
        filter_tags += [f"{prefix}-studio-{tag_label(s.replace('*', ''))}" for s in args.studio or []]
        filter_tags += [f"{prefix}-genre-{tag_label(g)}" for g in genres.values()]
    today = datetime.date.today()
    adds = []
    for m in todo:
        tid = m["id"]
        if tid in failed:
            row(m, "ERROR", why=f"TMDB details: {failed[tid]}")
            continue
        d = details[tid]
        m = dict(m, **{k: d.get(k) for k in ("title", "original_title", "release_date", "vote_count")
                       if d.get(k) is not None})
        gnames = {g["id"]: g["name"] for g in d.get("genres") or []}
        snames = {c["id"]: c["name"] for c in d.get("production_companies") or []}
        runtime = d.get("runtime") or 0
        info = {"runtime": runtime or "", "studios": " | ".join(snames.values()),
                "genres": ", ".join(gnames.values())}
        forced = tid in also
        why, role_notes = [], []

        # -- what was asked for
        hits, kinds = [], []
        if dated and not people:
            hits = matched(d.get("release_dates"), region, types, start, end)
            if not hits and not forced:
                row(m, "NO MATCH", why=f"no {region} release of these types in range", **info)
                continue
            first = first_release(d, d.get("release_dates"))
            kinds = list(dict.fromkeys(kind(h, first, rerelease_days) for h in hits))
            info.update({"kinds": " ".join(kinds),
                         "matched": " ".join(f"{h['date']}:{TYPE_NAMES.get(h['type'])}" for h in hits),
                         "release_notes": " | ".join(sorted({h["note"] for h in hits if h["note"]}))})
            if skip_rereleases and kinds == ["rerelease"] and not forced:
                row(m, "RERELEASE", **info)
                continue
        if not forced:
            fail = None
            if dated and people:
                rd = day(d.get("release_date"))
                if not rd or not (start <= rd <= end):
                    fail = f"came out {d.get('release_date') or '(no date)'}, outside {start} to {end}"
            if not fail and studios and not (set(snames) & set(studios)):
                fail = "not by " + " / ".join(args.studio)
            if not fail and genres and not (set(gnames) & set(genres)):
                fail = "not " + " / ".join(genres.values())
            if fail:
                row(m, "NO MATCH", why=fail, **info)
                continue

        # -- the people's part in it
        self_in_special = False
        if "cast" in people:
            mine = [c for c in d["credits"]["cast"] if c.get("id") in people["cast"]]
            best = None
            for c in sorted(mine, key=lambda c: c.get("order") if c.get("order") is not None else 999):
                problem = None
                ch = c.get("character") or ""
                if skip_uncredited and "uncredited" in ch.lower():
                    problem = "uncredited cameo"
                elif top and c.get("order") is not None and c["order"] >= top:
                    problem = f"billed #{c['order'] + 1}" + (f" ({ch})" if ch else "")
                elif SELF_RE.match(ch) and set(gnames) & set(self_genres):
                    problem = f"appears as {'herself' if 'her' in ch.lower() else 'himself'} in a " \
                              + "/".join(gnames[g] for g in gnames if g in self_genres).lower()
                    self_in_special = True
                if not problem:
                    best = c
                    break
                why.append(problem)
            if best:
                why, self_in_special = [], False
                ch = best.get("character") or ""
                role_notes.append(f"{best['name']}: {ch or 'cast'}"
                                  + (f", billed #{best['order'] + 1}" if best.get("order") is not None else ""))
                if SELF_RE.match(ch):
                    role_notes.append("plays himself" if "her" not in ch.lower() else "plays herself")
            elif not mine:
                why.append("not in this movie's credits on TMDB")
        for label, job in (("director", "Director"), ("crew", None)):
            if label in people:
                jobs = [f"{c['name']}: {c['job']}" for c in d["credits"]["crew"]
                        if c.get("id") in people[label] and (job is None or c.get("job") == job)]
                if jobs:
                    role_notes.append("; ".join(dict.fromkeys(jobs)))
                else:
                    why.append(f"not credited as {label} on TMDB")

        # -- junk
        if released_only and junk and not dated:
            rd = day(d.get("release_date"))
            if (d.get("status") and d["status"] != "Released") or not rd or rd > today:
                why.append(f"not out yet ({d.get('status') or 'no date'}"
                           + (f", {rd}" if rd else "") + ")")
        if not runtime and skip_no_runtime:
            why.append("no runtime on TMDB")
        elif runtime and runtime < min_runtime:
            why.append(f"short ({runtime} min)")
        if require_studio and not snames:
            why.append("no studio listed")
        if min_votes and (d.get("vote_count") or 0) < min_votes:
            why.append(f"{d.get('vote_count') or 0} votes")
        bad_genres = [gnames[g] for g in gnames if g in skip_genres]
        if bad_genres and not self_in_special:
            why.append(", ".join(bad_genres).lower())

        info["role"] = "; ".join(role_notes)
        if why and not forced:
            row(m, "SKIPPED", why="; ".join(dict.fromkeys(why)), **info)
            continue
        if forced:
            note = "--also" + (f" (would have skipped: {'; '.join(why)})" if why else "")
            if tid in excluded:
                note += "; on Radarr's exclusion list"
            info["role"] = "; ".join(x for x in [info["role"], note] if x)

        labels = []
        if use_tags:
            labels += [tag_label(t) for t in mcfg.get("radarr_tag_base", ["tmdb"])]
            if hits and mcfg.get("radarr_tag_month", True):
                labels += [f"{prefix}-{h['date']:%Y-%m}" for h in hits]
            if kinds and mcfg.get("radarr_tag_kind", True):
                labels += [f"{prefix}-{k}" for k in kinds]
            labels += filter_tags
            if forced:
                labels.append(f"{prefix}-also")
            labels = list(dict.fromkeys(x for x in labels if x))
        r = row(m, "WOULD ADD" if args.dry_run else "ADD", tags=" ".join(labels), **info)
        adds.append((r, labels))

    # ---- add to Radarr
    if adds and not args.dry_run:
        how = ("monitored, searching" if search else "monitored, no search") if monitored \
            else "unmonitored, no search"
        print(f"  adding {len(adds)} to Radarr ({root}, {how}) ...")
        tag_map = radarr.tag_ids(sorted({x for _, labels in adds for x in labels}))
        for n, (r, labels) in enumerate(adds, 1):
            try:
                movie = radarr.lookup(r["tmdb_id"])
                if not movie:
                    r["status"], r["why"] = "ERROR", "Radarr's lookup found nothing"
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
                    "addOptions": {"searchForMovie": search,
                                   "monitor": "movieOnly" if monitored else "none"},
                })
                movie.pop("path", None)
                movie.pop("folder", None)
                radarr.post("movie", movie)
                r["status"] = "ADDED"
                have.add(r["tmdb_id"])
            except RadarrError as e:
                if "already been added" in str(e) or "MovieExists" in str(e):
                    r["status"] = "IN RADARR"
                elif "UNIQUE constraint failed: MovieMetadata.TmdbId" in str(e):
                    # Radarr already holds info for this TMDB ID without the movie
                    # being in its library (left over from a deleted movie, or a
                    # collection) and its database refuses a second copy.
                    r["status"], r["why"] = "SKIPPED", (
                        "Radarr refused it: its database already has info for this TMDB ID "
                        "(leftover or collection entry). Try adding it in Radarr by hand")
                else:
                    r["status"], r["why"] = "ERROR", str(e)
            if n % 10 == 0 or n == len(adds):
                print(f"\r    {n}/{len(adds)}", end="", flush=True)
        print()

    # ---- report
    os.makedirs(config.reports, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    name_bits = []
    if dated:
        name_bits.append(f"{start}_{end}")
    for label, persons in people.items():
        name_bits += [f"{label}-{tag_label(n)}" for n in persons.values()]
    if args.studio:
        name_bits += [f"studio-{tag_label(s.replace('*', ''))}" for s in args.studio]
    if genres:
        name_bits += [f"genre-{tag_label(g)}" for g in genres.values()]
    if not name_bits:
        name_bits.append("also")
    report = os.path.join(config.reports,
                          f"movie-radarr_{'_'.join(name_bits)[:120]}_{stamp}"
                          f"{'_dryrun' if args.dry_run else ''}.csv")
    order = {"ADDED": 0, "ADD": 0, "WOULD ADD": 0, "ERROR": 1, "SKIPPED": 2, "ON DISK": 3,
             "EXCLUDED": 4, "IN RADARR": 5, "RERELEASE": 6, "NO MATCH": 7}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["year"] or "9999", r["title"] or ""))
    fields = ["status", "tmdb_id", "title", "year", "why", "role", "runtime", "votes", "studios",
              "genres", "kinds", "matched", "release_notes", "tags", "tmdb_url"]
    with open(report, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ---- the list on screen, grouped
    show = args.show
    groups = {}
    for r in rows:
        groups.setdefault(r["status"], []).append(r)
    heads = [("ADDED", "ADDED"), ("ADD", "TO ADD"), ("WOULD ADD", "WOULD ADD"), ("ERROR", "ERROR"),
             ("SKIPPED", "SKIPPED"), ("ON DISK", "ALREADY ON DISK (not in Radarr)"),
             ("EXCLUDED", "BLOCKED (Radarr exclusion list)"), ("IN RADARR", "ALREADY IN RADARR"),
             ("RERELEASE", "RERELEASE (not added)")]
    print()
    for status, head in heads:
        g = groups.get(status)
        if not g:
            continue
        print(f"{head} ({len(g)})")
        limit = len(g) if (show == 0 or status == "ERROR") else show
        for r in g[:limit]:
            name = f"{r['title']}" + (f" ({r['year']})" if r["year"] else "")
            extra = r["why"] or r["role"]
            print((f"  {name[:48]:<49} {{tmdb-{r['tmdb_id']}}}".ljust(66)
                   + (f"  {extra}" if extra else "")).rstrip())
        if len(g) > limit:
            print(f"  ... and {len(g) - limit} more (all in the report; --show 0 = list all)")
        print()
    if groups.get("NO MATCH"):
        print(f"NOT MATCHING the filters: {len(groups['NO MATCH'])} (listed in the report)\n")
    for n in notes:
        print(f"  note: {n}")
    if groups.get("SKIPPED"):
        print("A skipped one you want anyway: run again with --also <tmdb number>.")
    print(f"Report: {report}")
    ctx["last"] = {"counts": {k: len(v) for k, v in groups.items()}, "report": report,
                   "people": {label: dict(p) for label, p in people.items()}}
    return 1 if groups.get("ERROR") else 0
