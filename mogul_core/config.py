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


class Config:
    def __init__(self, path=None):
        self.path = find_config(path)
        self.base = os.path.dirname(self.path)
        with open(self.path, "rb") as f:
            self.data = tomllib.load(f)
        self.core = self.data.get("core", {})

    def section(self, name):
        return self.data.get(name, {})

    def folder(self, key, default):
        """Resolve a [core] folder setting to an absolute path."""
        value = self.core.get(key, default)
        value = os.path.expanduser(value)
        if not os.path.isabs(value):
            value = os.path.join(self.base, value)
        return os.path.normpath(value)

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
