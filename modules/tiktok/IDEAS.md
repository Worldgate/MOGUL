# MOGUL TikTok (placeholder)

TikTok harvesting (personal use).

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py tiktok ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py tiktok <action>`, a catalog under
`mogul_json/tiktok/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Being built in another session; this is where it moves when it's "good enough". If it lives in its own repo, `git subtree add` brings it in here as a folder with its history kept.

## Decisions

(none yet)
