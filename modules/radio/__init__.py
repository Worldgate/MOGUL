"""Radio module: talk radio shows (Opie and Anthony, Howard Stern, ...)."""

ACTIONS = {
    "scan": "Catalog new files in mogul_incoming/radio into the JSON files (moves nothing)",
    "import": "Move cataloged files into the radio library, renamed and sorted; extra copies go to dupes",
    "verify": "Check the radio library against the catalog (moves nothing)",
}


def run(action, args, config):
    if action == "scan":
        from . import scan
        return scan.run(args, config)
    if action == "import":
        from . import importer
        return importer.run(args, config)
    if action == "verify":
        from . import verify
        return verify.run(args, config)
    raise SystemExit(f"radio has no action '{action}'. Actions: {', '.join(ACTIONS)}")


def add_arguments(action, parser):
    if action == "scan":
        parser.add_argument("--dry-run", action="store_true",
                            help="do everything except write the JSON files")
        parser.add_argument("--no-fingerprint", action="store_true",
                            help="skip the acoustic fingerprint (faster)")
        parser.add_argument("--path", help="scan this folder instead of mogul_incoming/radio")
    elif action == "import":
        parser.add_argument("--dry-run", action="store_true",
                            help="show what would be moved, move nothing")
        parser.add_argument("--path", help="import from this folder instead of mogul_incoming/radio")
    elif action == "verify":
        parser.add_argument("--dry-run", action="store_true",
                            help="check only, don't write anything to the catalog")
        parser.add_argument("--update", action="store_true",
                            help="for files whose tags changed since the scan, record the new "
                                 "whole-file hashes, size and date")
