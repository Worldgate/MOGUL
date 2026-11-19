"""Movie module: the movie library (on abyss)."""

ACTIONS = {
    "tmdb": "Download each movie's TMDB info (credits, images, videos, release dates...) "
            "into the local TMDB copy",
    "pull": "Find movies by director, cast, studio, genre, title...; list them, or move them "
            "to another folder with --to",
}


def run(action, args, config):
    if action == "tmdb":
        from . import tmdb_fetch
        return tmdb_fetch.run(args, config)
    if action == "pull":
        from . import pull
        return pull.run(args, config)
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
    elif action == "pull":
        w = "repeatable; * = wildcard"
        parser.add_argument("--director", action="append", metavar="NAME", help=f"directed by ({w})")
        parser.add_argument("--cast", action="append", metavar="NAME", help=f"in the cast ({w})")
        parser.add_argument("--top", type=int, metavar="N",
                            help="with --cast: only the top N billed actors count")
        parser.add_argument("--crew", action="append", metavar="NAME",
                            help=f"any crew job: writer, composer, DP... ({w})")
        parser.add_argument("--studio", action="append", metavar="NAME",
                            help=f"production company ({w})")
        parser.add_argument("--genre", action="append", metavar="NAME",
                            help=f"TMDB genre, e.g. Documentary ({w})")
        parser.add_argument("--title", action="append", metavar="TEXT",
                            help=f"title or original title ({w}), e.g. \"*making of*\"")
        parser.add_argument("--no-studio", action="store_true",
                            help="only movies with no production company listed on TMDB")
        parser.add_argument("--max-runtime", type=int, metavar="MINUTES",
                            help="only movies this long or shorter (by TMDB's runtime)")
        parser.add_argument("--max-votes", type=int, metavar="N",
                            help="only movies with N or fewer votes on TMDB (obscure stuff)")
        parser.add_argument("--to", metavar="FOLDER",
                            help="move matching movies here (keeps the decade folders). "
                                 "Without it, nothing moves: matches are just listed")
        parser.add_argument("--path", action="append", metavar="FOLDER",
                            help="look in this folder instead of [movie] library (repeatable)")
        parser.add_argument("--dry-run", action="store_true",
                            help="with --to: show what would move, move nothing")
