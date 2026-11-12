"""Radio module: talk radio shows (Opie and Anthony, Howard Stern, ...)."""

ACTIONS = {
    "scan": "Catalog new files in mogul_incoming/radio into the JSON files (moves nothing)",
    "import": "Move cataloged files into the radio library, renamed and sorted; extra copies go to dupes",
    "verify": "Check the radio library against the catalog (moves nothing)",
    "fingerprint": "Add fingerprints to cataloged files that don't have one yet, several at a time",
    "quarantine": "Move bad recordings (default: failed fingerprints) and their entries out for checking",
    "damaged": "List recordings with damaged spots; mark them ok or bad after listening",
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
    if action == "fingerprint":
        from . import fingerprint_pass
        return fingerprint_pass.run(args, config)
    if action == "quarantine":
        from . import quarantine
        return quarantine.run(args, config)
    if action == "damaged":
        from . import damaged
        return damaged.run(args, config)
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
    elif action == "fingerprint":
        parser.add_argument("--dry-run", action="store_true",
                            help="list what would be fingerprinted, change nothing")
        parser.add_argument("--jobs", type=int, default=0,
                            help="how many files to work on at once (default: number of CPU cores minus one)")
        parser.add_argument("--retry-failed", action="store_true",
                            help="also try files that failed before")
    elif action == "quarantine":
        parser.add_argument("--dry-run", action="store_true",
                            help="show what would be quarantined, move nothing")
        parser.add_argument("--file", action="append", metavar="NAME",
                            help="quarantine this library file by name (repeatable); "
                                 "without it, every failed fingerprint is quarantined")
        parser.add_argument("--restore", action="append", metavar="NAME",
                            help="put a quarantined file and its entry back (repeatable); \"all\" puts everything back")
        parser.add_argument("--list", action="store_true", help="show what's in quarantine")
    elif action == "damaged":
        parser.add_argument("--ok", action="append", metavar="NAME",
                            help="mark this library file as checked: plays fine (repeatable)")
        parser.add_argument("--keep", action="append", metavar="NAME",
                            help="mark as playable but damaged: stays in the library, "
                                 "listed as wanting a cleaner copy (repeatable)")
        parser.add_argument("--wanted", action="store_true",
                            help="only list the 'keep' ones (want a better copy)")
        parser.add_argument("--bad", action="append", metavar="NAME",
                            help="mark this library file as checked: really broken; "
                                 "the next quarantine pulls it out (repeatable)")
        parser.add_argument("--unchecked", action="store_true",
                            help="only list the ones not checked yet")
