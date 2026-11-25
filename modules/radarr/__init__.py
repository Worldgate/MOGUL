"""Radarr module: everything that talks to Radarr.

  mogul.py radarr add      movies from TMDB into Radarr (dates, actor, director,
                           studio, genre), junk filtered, unmonitored
  mogul.py radarr watch    keep a list of actors / directors / studios to follow
  mogul.py radarr update   check everyone on the watch list, add their new movies
  mogul.py radarr backup   save Radarr's own backup zip + a readable export

Settings: [movie] radarr_* in mogul.toml (address and root folder in
mogul.local.toml). Key: secrets.toml [radarr] api_key.
Your lists and backups: mogul_userdata/radarr/ (not in git; back it up).
"""

ACTIONS = {
    "add": "Add movies from TMDB to Radarr by date range, actor, director, studio or genre, "
           "minus the junk (shorts, no-studio, docs, cameos) and what you already have",
    "watch": "The watch list: actors, directors, studios to check for new movies "
             "(watch add / watch list / watch remove)",
    "update": "Check everyone on the watch list and add their new movies "
              "(backs Radarr up first). Meant to run monthly",
    "backup": "Save Radarr's own backup zip plus a readable export of its movies and settings "
              "into mogul_userdata/radarr/backup/",
}


def run(action, args, config):
    if action == "add":
        from . import add
        return add.run(args, config)
    if action in ("watch", "update"):
        from . import watch
        return watch.run(action, args, config)
    if action == "backup":
        from . import backup
        return backup.run(args, config)
    raise SystemExit(f"radarr has no action '{action}'. Actions: {', '.join(ACTIONS)}")


def add_arguments(action, parser):
    if action == "add":
            w = "repeatable; same option twice = either"
            parser.add_argument("--from", dest="start", metavar="DATE",
                                help="start of a date range: 2026, 2026-01 or 2026-01-15. Alone: that "
                                     "range's releases (see --types). With --cast/--director/--crew: "
                                     "only their movies that first came out in the range")
            parser.add_argument("--to", dest="end", metavar="DATE",
                                help="end of the range (same forms; default: same as --from, "
                                     "so --from 2026-01 alone = all of January)")
            parser.add_argument("--cast", action="append", metavar="NAME",
                                help=f'actor, e.g. "Keanu Reeves" or a TMDB person number ({w})')
            parser.add_argument("--director", action="append", metavar="NAME",
                                help=f"director, name or TMDB person number ({w})")
            parser.add_argument("--crew", action="append", metavar="NAME",
                                help=f"anyone in the crew, any job (writer, composer...) ({w})")
            parser.add_argument("--studio", action="append", metavar="NAME",
                                help=f'studio name, * = wildcard ("Warner Bros*" = every Warner '
                                     f"company on TMDB), or a TMDB company number ({w})")
            parser.add_argument("--genre", action="append", metavar="NAME",
                                help=f"TMDB genre, e.g. Horror ({w})")
            parser.add_argument("--also", action="append", metavar="TMDB_ID",
                                help="add these TMDB movie numbers no matter what the filters say "
                                     "(e.g. one from the SKIPPED list); several = 123,456")
            parser.add_argument("--top", type=int, metavar="N",
                                help="with --cast: skip movies where they're billed lower than #N "
                                     "(default: [movie] radarr_cast_top, 10; 0 = any billing)")
            parser.add_argument("--min-runtime", type=int, metavar="MIN",
                                help="skip movies shorter than this (default: [movie] radarr_min_runtime, 40)")
            parser.add_argument("--min-votes", type=int, metavar="N",
                                help="skip movies with fewer TMDB votes (default: [movie] radarr_min_votes)")
            parser.add_argument("--include-docs", action="store_true",
                                help="don't skip documentaries (\"appears as himself\" ones still are)")
            parser.add_argument("--no-junk-filter", action="store_true",
                                help="turn off every junk rule (runtime, studio, votes, genres, billing)")
            parser.add_argument("--types", metavar="LIST",
                                help="date mode: release types, e.g. 5 or 3,5 (default: [movie] "
                                     "radarr_release_types). 1 premiere, 2 limited theatrical, "
                                     "3 theatrical, 4 digital, 5 physical (DVD/Blu-ray), 6 TV")
            parser.add_argument("--region", metavar="CC",
                                help="date mode: country whose release dates count (default: [movie] "
                                     "radarr_region, else watch_region)")
            parser.add_argument("--root", metavar="FOLDER",
                                help="Radarr root folder to add to, as Radarr sees it "
                                     "(default: [movie] radarr_root_folder)")
            parser.add_argument("--profile", metavar="NAME",
                                help="Radarr quality profile (default: [movie] radarr_quality_profile)")
            parser.add_argument("--monitor", action="store_true",
                                help="add them MONITORED (default: unmonitored, Radarr leaves them alone)")
            parser.add_argument("--search", action="store_true",
                                help="monitored AND search right away (Radarr starts downloading)")
            parser.add_argument("--no-library-check", action="store_true",
                                help="don't look in [movie] library for movies you already have on disk")
            parser.add_argument("--show", type=int, default=50, metavar="N",
                                help="how many to print per group (default 50; 0 = all). "
                                     "The report always has everything")
            parser.add_argument("--refresh", action="store_true",
                                help="download movie details again even if stored recently")
            parser.add_argument("--jobs", type=int, default=4,
                                help="TMDB requests at once (default 4; the rate limit still applies)")
            parser.add_argument("--dry-run", action="store_true",
                                help="show what would be added; add nothing to Radarr")
    elif action == "watch":
        parser.add_argument("op", choices=["add", "list", "remove"],
                            help="add someone, list the watch list, or remove someone")
        parser.add_argument("name", nargs="?", metavar="NAME",
                            help="with remove: the name (or TMDB number) to take off the list")
        parser.add_argument("--cast", metavar="NAME", help='actor to follow, e.g. "Keanu Reeves"')
        parser.add_argument("--director", metavar="NAME", help="director to follow")
        parser.add_argument("--crew", metavar="NAME", help="crew member to follow (any job)")
        parser.add_argument("--studio", metavar="NAME",
                            help='studio to follow, * = wildcard ("A24", "Warner Bros*")')
        parser.add_argument("--top", type=int, metavar="N",
                            help="this actor only: billing cut-off (default radarr_cast_top)")
        parser.add_argument("--monitor", action="store_true",
                            help="add this one's new movies MONITORED (Radarr will download them)")
        parser.add_argument("--root", metavar="FOLDER", help="this one's Radarr root folder")
        parser.add_argument("--profile", metavar="NAME", help="this one's quality profile")
        parser.add_argument("--note", metavar="TEXT", help="your own note, kept in the list")
    elif action == "update":
        parser.add_argument("--only", metavar="NAME",
                            help="just this entry of the watch list (name or part of it)")
        parser.add_argument("--no-backup", action="store_true",
                            help="don't back Radarr up first (default: radarr_backup_before_update)")
        parser.add_argument("--zip", action="store_true",
                            help="also make Radarr's own (slow) backup zip first "
                                 "(default: radarr_update_zip, off; the quick export is always made)")
        parser.add_argument("--show", type=int, default=10, metavar="N",
                            help="how many to print per group for each entry (default 10; 0 = all)")
        parser.add_argument("--refresh", action="store_true",
                            help="download movie details again even if stored recently")
        parser.add_argument("--jobs", type=int, default=4, help="TMDB requests at once (default 4)")
        parser.add_argument("--dry-run", action="store_true",
                            help="show what would be added; add nothing, change nothing")
    elif action == "backup":
        parser.add_argument("--no-zip", action="store_true",
                            help="skip Radarr's own backup zip, just save the export")
        parser.add_argument("--keep", type=int, metavar="N",
                            help="keep the newest N backups (default: radarr_backup_keep; 0 = all)")
