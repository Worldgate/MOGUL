"""Finding movies in the movie library folders.

Layout: <root>/<decade>/<Title (YYYY) {tmdb-ID}>/<movie file>, but any depth
works: the first folder whose NAME has a TMDB ID is one movie ("unit"),
and everything inside it belongs to that movie. A video file with the ID
in its own name, sitting in a folder without one, is a unit by itself.
"""

import os
import re
import sys

ID_RE = re.compile(r"[\[{(]tmdb(?:id)?[-=_ ](\d+)[\]})]", re.IGNORECASE)


def roots(config, paths=None):
    """The library folders: --path ones if given, else [movie] library."""
    if paths:
        folders = paths
    else:
        lib = config.section("movie").get("library") or []
        folders = [lib] if isinstance(lib, str) else list(lib)
    if not folders:
        sys.exit("No movie folders: give --path FOLDER or set [movie] library in mogul.local.toml.")
    out = []
    for f in folders:
        f = config.resolve(f)
        if not os.path.isdir(f):
            sys.exit(f"Folder not found: {f}")
        out.append(f)
    return out


def find_units(folders):
    """Return a list of (tmdb_id, root, path) for every movie folder (or
    loose file) with a TMDB ID in its name. Doesn't look inside a movie
    folder once it's found."""
    units = []
    for root in folders:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            keep = []
            for d in dirnames:
                m = ID_RE.search(d)
                if m:
                    units.append((int(m.group(1)), root, os.path.join(dirpath, d)))
                else:
                    keep.append(d)
            dirnames[:] = keep
            for name in sorted(filenames):
                m = ID_RE.search(name)
                if m:
                    units.append((int(m.group(1)), root, os.path.join(dirpath, name)))
    return units
