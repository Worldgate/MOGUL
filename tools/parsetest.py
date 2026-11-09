#!/usr/bin/env python3
"""
parsetest.py - show how MOGUL reads radio filenames, using mogul.toml.

Usage:
  parsetest.py "O&A 1998-07-21.mp3" "O&A 2005-03-14_02 -- Some title.mp3" ...
  parsetest.py /path/to/folder/*.mp3

Prints the "naming" block for each file, the library name it would get,
and any problems found. Nothing is renamed or moved.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mogul_core.config import Config          # noqa: E402
from modules.radio import naming               # noqa: E402
from modules.radio.importer import target_folder, target_name  # noqa: E402


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    config = Config()
    radio = config.section("radio")
    regex = naming.compile_pattern(radio.get("incoming_pattern", naming.DEFAULT_PATTERN))
    shows = radio.get("shows", {})
    folder_t = radio.get("folder_template", "{show}/{yyyy}/{mm}")
    name_t = radio.get("filename_template", "{yyyy}-{mm}-{dd}_{code}_{HASH8}")
    print(f"using {config.path}\n")

    bad = 0
    for arg in args:
        name = os.path.basename(arg)
        entry_naming, problems = naming.parse(name, regex, shows)
        print(name)
        if entry_naming:
            print(json.dumps({"naming": entry_naming}, indent=2, ensure_ascii=False))
            if not problems:
                fake = {"naming": entry_naming, "physical": {"hashes": {"xxh64": "0123456789abcdef"}}}
                folder = target_folder("", folder_t, fake).lstrip("/")
                print(f"  library name: {os.path.join(folder, target_name(name_t, fake, 0))}"
                      "   (HASH8 shown as 01234567, the real one comes from the audio)")
        for p in problems:
            print(f"  PROBLEM: {p}")
        if problems:
            bad += 1
        print()
    print(f"{len(args)} files, {len(args) - bad} clean, {bad} with problems")


if __name__ == "__main__":
    main()
