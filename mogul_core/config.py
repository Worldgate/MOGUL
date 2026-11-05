"""Load mogul.toml and resolve folder paths."""

import os
import sys
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_config(explicit=None):
    candidates = [explicit] if explicit else [
        os.path.join(os.getcwd(), "mogul.toml"),
        os.path.join(ROOT, "mogul.toml"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    sys.exit("mogul.toml not found (looked in the current folder and the MOGUL folder)")


def merge(base, extra):
    """Merge settings from extra into base (nested sections merge too)."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    """Settings from mogul.toml, with mogul.local.toml (if present) layered
    on top. mogul.local.toml is for anything specific to this machine, like
    NAS paths. It's in .gitignore, so it never goes to GitHub."""

    def __init__(self, path=None):
        self.path = find_config(path)
        self.base = os.path.dirname(self.path)
        with open(self.path, "rb") as f:
            self.data = tomllib.load(f)
        local = os.path.join(self.base, "mogul.local.toml")
        self.local_path = local if os.path.isfile(local) and not path else None
        if self.local_path:
            with open(self.local_path, "rb") as f:
                merge(self.data, tomllib.load(f))
        self.core = self.data.get("core", {})

    def section(self, name):
        return self.data.get(name, {})

    def resolve(self, value):
        """Turn a path from the config into an absolute path.
        Relative paths are relative to the folder mogul.toml is in."""
        value = os.path.expanduser(value)
        if not os.path.isabs(value):
            value = os.path.join(self.base, value)
        return os.path.normpath(value)

    def folder(self, key, default):
        """Resolve a [core] folder setting to an absolute path."""
        return self.resolve(self.core.get(key, default))

    @property
    def incoming(self):
        return self.folder("incoming", "mogul_incoming")

    @property
    def json_store(self):
        return self.folder("json_store", "mogul_json")

    @property
    def reports(self):
        return self.folder("reports", "mogul_reports")

    @property
    def hashes(self):
        return list(self.core.get("hashes", ["blake3", "sha256", "sha512"]))
