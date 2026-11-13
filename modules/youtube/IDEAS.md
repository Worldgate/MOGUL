# MOGUL YouTube (placeholder)

Archiving whole YouTube channels, e.g. the White House channel.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py youtube ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py youtube <action>`, a catalog under
`mogul_json/youtube/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Downloading: yt-dlp, run per channel. Its "download archive" file
  remembers which videos are already done, so a re-run only grabs new
  uploads (a scheduled task could keep a channel up to date).
- YouTube's video ID (11 characters) is the natural episode key, like the
  air date is for radio; channel ID + upload date for sorting
  (`<channel>/<yyyy>/<mm>/`).
- Keep yt-dlp's `.info.json` (title, description, upload date, tags,
  duration) in the catalog: titles and descriptions get edited, and
  videos get deleted or made private. That's the main reason to archive.
- Same media-ID idea as radio: hash the video/audio packets, not the
  container, so a remuxed copy (mkv vs mp4) still matches.
- Note when a video disappears from the channel (deleted / private) and
  when its title or description changed since the last check.
- Channels to start with: the White House channel.

## Decisions

(none yet)
