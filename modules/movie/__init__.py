"""Movie module: the movie library (on abyss)."""

ACTIONS = {
    "tmdb": "Download each movie's TMDB info (credits, images, videos, release dates...) "
            "into the local TMDB copy",
}


def run(action, args, config):
    if action == "tmdb":
        from . import tmdb_fetch
        return tmdb_fetch.run(args, config)
    raise SystemExit(f"movie has no action '{action}'. Actions: {', '.join(ACTIONS)}")


def add_arguments(action, parser):
    if action == "tmdb":
        parser.add_argument("ids", nargs="*", metavar="TMDB_ID",
                            help="TMDB movie IDs to download, e.g. 550 603. Without any, the "
                                 "IDs are read from folder/file names like {tmdb-550}")
        parser.add_argument("--path", action="append", metavar="FOLDER",
                            help="read {tmdb-ID} from names in this folder and its subfolders "
                                 "(repeatable; default: [movie] library in mogul.toml)")
        parser.add_argument("--refresh", action="store_true",
                            help="download again even if already saved")
        parser.add_argument("--older-than", type=float, metavar="DAYS",
                            help="download again only if the saved copy is older than this")
        parser.add_argument("--jobs", type=int, default=4,
                            help="requests at once (default 4; the rate limit still applies)")
        parser.add_argument("--dry-run", action="store_true",
                            help="list what would be downloaded, download nothing")
