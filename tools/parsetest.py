#!/usr/bin/env python3
"""
parsetest.py - show how MOGUL reads radio filenames, using mogul.toml.

Usage:
  parsetest.py "O&A 1998-07-21.mp3" "O&A 1998-06-31 -- 1st WNEW show.mp3" ...
  parsetest.py /path/to/folder/*.mp3

Prints the "naming" block for each file, plus any problems found.
Nothing is renamed or moved.
"""
import datetime
import json
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATHS = [
    os.path.join(os.getcwd(), "mogul.toml"),
    os.path.join(HERE, "..", "mogul.toml"),
    os.path.join(HERE, "mogul.toml"),
]

TOKENS = {
    "code": r"(?P<code>.+?)",
    "yyyy": r"(?P<yyyy>\d{4})",
    "mm": r"(?P<mm>\d{1,2})",
    "dd": r"(?P<dd>\d{1,2})",
    "notes": r"(?P<notes>.+?)",
}


def load_config():
    for p in CONFIG_PATHS:
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return tomllib.load(f), os.path.abspath(p)
    sys.exit("mogul.toml not found (looked in the current folder and next to this script)")


def pattern_to_regex(pattern):
    """Turn '{code} {yyyy}-{mm}-{dd}[ -- {notes}]' into a regex."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "{":
            j = pattern.index("}", i)
            name = pattern[i + 1:j]
            if name not in TOKENS:
                sys.exit(f"Unknown token {{{name}}} in incoming_pattern")
            out.append(TOKENS[name])
            i = j + 1
        elif c == "[":
            out.append("(?:")
            i += 1
        elif c == "]":
            out.append(")?")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + r"\.(?P<ext>[A-Za-z0-9]+)$")


def parse(filename, regex, shows):
    problems = []
    m = regex.match(filename)
    if not m:
        return None, ["filename doesn't match incoming_pattern"]

    code = m.group("code")
    year, month, day = int(m.group("yyyy")), int(m.group("mm")), int(m.group("dd"))
    notes = m.groupdict().get("notes")

    show = shows.get(code)
    if show is None:
        problems.append(f"unknown show code '{code}' (add it to [radio.shows] in mogul.toml)")

    try:
        datetime.date(year, month, day)
    except ValueError:
        problems.append(f"invalid date {year:04d}-{month:02d}-{day:02d}")

    naming = {
        "show": show,
        "code": code,
        "year": year,
        "month": month,
        "day": day,
        "notes": notes,
        "extension": m.group("ext").lower(),
        "filename": filename,
        "original_filename": filename,
    }
    return naming, problems


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    cfg, cfg_path = load_config()
    radio = cfg.get("radio", {})
    regex = pattern_to_regex(radio.get("incoming_pattern", "{code} {yyyy}-{mm}-{dd}[ -- {notes}]"))
    shows = radio.get("shows", {})
    print(f"using {cfg_path}\n")

    bad = 0
    for arg in args:
        name = os.path.basename(arg)
        naming, problems = parse(name, regex, shows)
        print(name)
        if naming:
            print(json.dumps({"naming": naming}, indent=2, ensure_ascii=False))
        for p in problems:
            print(f"  PROBLEM: {p}")
        if problems:
            bad += 1
        print()
    print(f"{len(args)} files, {len(args) - bad} clean, {bad} with problems")


if __name__ == "__main__":
    main()
