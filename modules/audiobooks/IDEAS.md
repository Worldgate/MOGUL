# MOGUL Audiobooks (placeholder)

Audiobooks, single and multi-file.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py audiobooks ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py audiobooks <action>`, a catalog under
`mogul_json/audiobooks/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Multi-file sets; "unclean" sets where one part is encoded differently.
- Audio-only xxh64 IDs, same as radio. MKA as a "playable zip" (audio + chapters + attachments).

## Decisions

(none yet)
