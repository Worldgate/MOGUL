"""Radio module: talk radio shows (Opie and Anthony, Howard Stern, ...)."""

ACTIONS = {
    "scan": "Catalog new files in mogul_incoming/radio into the JSON files (moves nothing)",
}


def run(action, args, config):
    if action == "scan":
        from . import scan
        return scan.run(args, config)
    raise SystemExit(f"radio has no action '{action}'. Actions: {', '.join(ACTIONS)}")


def add_arguments(action, parser):
    if action == "scan":
        parser.add_argument("--dry-run", action="store_true",
                            help="do everything except write the JSON files")
        parser.add_argument("--no-fingerprint", action="store_true",
                            help="skip the acoustic fingerprint (faster)")
        parser.add_argument("--path", help="scan this folder instead of mogul_incoming/radio")
