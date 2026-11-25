"""Movie module: the movie library (on abyss)."""

ACTIONS = {
    "tmdb": "Download each movie's TMDB info (credits, images, videos, release dates...) "
            "into the local TMDB copy",
    "pull": "Find movies by director, cast, studio, genre, title...; list them, or move them "
            "to another folder with --to",
    "count": "Count how many of your movies each actor, director, studio... is in (changes nothing)",
    "compare": "Compare your library with a streaming service's movie list (Netflix, Prime...)",
    "verify": "Read each movie's credits (OCR) to check it's really that movie; sort into folders "
              "by how sure (confirmed / identified / mismatch / weak / unknown)",
    "radarr": "Same as: mogul.py radarr add (movies from TMDB into Radarr by date, actor, "
              "director, studio, genre; junk filtered)",
}


def run(action, args, config):
    if action == "tmdb":
        from . import tmdb_fetch
        return tmdb_fetch.run(args, config)
    if action == "pull":
        from . import pull
        return pull.run(args, config)
    if action == "count":
        from . import count
        return count.run(args, config)
    if action == "compare":
        from . import compare
        return compare.run(args, config)
    if action == "verify":
        from . import verify
        return verify.run(args, config)
    if action == "radarr":
        from modules.radarr import add
        return add.run(args, config)
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
    elif action == "count":
        from .count import CHOICES
        parser.add_argument("--by", action="append", choices=CHOICES, metavar="LIST",
                            help="what to count (repeatable; default: cast, director, studio). "
                                 "Choices: " + ", ".join(CHOICES))
        parser.add_argument("--top", type=int, metavar="N",
                            help="for cast: only count the top N billed actors in each movie")
        parser.add_argument("--min", type=int, default=1, metavar="N",
                            help="leave out anyone in fewer than N of your movies (default 1)")
        parser.add_argument("--show", type=int, default=50, metavar="N",
                            help="how many of each list to print (default 50; 0 = all). "
                                 "The report files always have everything")
        parser.add_argument("--path", action="append", metavar="FOLDER",
                            help="count this folder instead of [movie] library (repeatable)")
    elif action == "compare":
        parser.add_argument("--service", metavar="NAME",
                            help='streaming service, e.g. netflix, "amazon prime video", hulu '
                                 "(see --list-services)")
        parser.add_argument("--list-services", action="store_true",
                            help="list the services TMDB knows for your country")
        parser.add_argument("--region", metavar="CC",
                            help="country code (default: [movie] watch_region, US)")
        parser.add_argument("--kind", default="flatrate",
                            choices=["flatrate", "free", "ads", "rent", "buy"],
                            help="flatrate = included with the subscription (default); "
                                 "free / ads / rent / buy")
        parser.add_argument("--show", type=int, default=25, metavar="N",
                            help="print the N most popular ones you don't have (default 25; 0 = none)")
        parser.add_argument("--jobs", type=int, default=4,
                            help="pages fetched at once (default 4)")
        parser.add_argument("--path", action="append", metavar="FOLDER",
                            help="compare this folder instead of [movie] library (repeatable)")
    elif action == "verify":
        parser.add_argument("--source", metavar="FOLDER",
                            help="folder (or one file) to check (default: [movie] verify_source)")
        parser.add_argument("--target", metavar="FOLDER",
                            help="where the result folders go (default: [movie] verify_target)")
        parser.add_argument("--dry-run", action="store_true",
                            help="check and show what would move; move and save nothing")
        parser.add_argument("--no-move", action="store_true",
                            help="check and save results, but leave every file where it is")
        parser.add_argument("--limit", type=int, metavar="N",
                            help="only check the first N files not checked yet (good for a test run)")
        parser.add_argument("--redo", action="store_true",
                            help="check files again even if they're in the results already")
        parser.add_argument("--report-only", action="store_true",
                            help="just rebuild the report page from the results")
        parser.add_argument("--ocr", choices=["auto", "easyocr", "tesseract"],
                            help="OCR engine (default: [movie] verify_ocr, auto = EasyOCR if installed)")
        parser.add_argument("--langs", metavar="LIST",
                            help="OCR languages, e.g. en,ja (default: [movie] verify_ocr_langs)")
    elif action == "radarr":
        from modules.radarr import add_arguments as radarr_args
        radarr_args("add", parser)
