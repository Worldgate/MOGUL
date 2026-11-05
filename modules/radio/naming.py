"""Read show, date and notes out of radio filenames.

The pattern comes from mogul.toml, e.g.
    {code} {yyyy}-{mm}-{dd}[ -- {notes}]
which matches
    O&A 1998-07-21.mp3
    O&A 1998-06-26 -- 1st WNEW show.mp3
"""

import datetime
import re

DEFAULT_PATTERN = "{code} {yyyy}-{mm}-{dd}[ -- {notes}]"

TOKENS = {
    "code": r"(?P<code>.+?)",
    "yyyy": r"(?P<yyyy>\d{4})",
    "mm": r"(?P<mm>\d{1,2})",
    "dd": r"(?P<dd>\d{1,2})",
    "notes": r"(?P<notes>.+?)",
}


def compile_pattern(pattern):
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "{":
            j = pattern.index("}", i)
            name = pattern[i + 1:j]
            if name not in TOKENS:
                raise SystemExit(f"Unknown token {{{name}}} in radio incoming_pattern in mogul.toml")
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
    """Return (naming dict or None, list of problems)."""
    m = regex.match(filename)
    if not m:
        return None, ["filename doesn't match the radio incoming_pattern"]

    code = m.group("code")
    year, month, day = int(m.group("yyyy")), int(m.group("mm")), int(m.group("dd"))
    notes = m.groupdict().get("notes")
    if notes is not None:
        notes = notes.strip() or None

    problems = []
    show = shows.get(code)
    if show is None:
        problems.append(f"unknown show code '{code}' (add it under [radio.shows] in mogul.toml)")
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
