#!/bin/bash
# Rename Usenet-style O&A names into MOGUL's pattern:  O&A YYYY-MM-DD -- <rest>.ext
#   2008-10-01-O&A-CF64k.mp3        -> O&A 2008-10-01 -- CF64k.mp3
#   2008-10-30 Thu O&A CF64k.mp3    -> O&A 2008-10-30 -- CF64k.mp3
#   2008-10-30-O&A CF64K.mp3        -> O&A 2008-10-30 -- CF64K.mp3
# Add "-n" as the first argument to only show what would happen.
dry=0; [ "$1" = "-n" ] && dry=1
shopt -s nullglob
for f in *; do
  [ -f "$f" ] || continue
  if [[ "$f" =~ ^([0-9]{4}-[0-9]{2}-[0-9]{2})[-\ ]([A-Za-z]+\ )?O\&A[-\ ](.+)\.([A-Za-z0-9]+)$ ]]; then
    new="O&A ${BASH_REMATCH[1]} -- ${BASH_REMATCH[3]}.${BASH_REMATCH[4]}"
    if [ -e "$new" ]; then echo "SKIP (name taken): $f -> $new"; continue; fi
    echo "$f  ->  $new"
    [ $dry = 0 ] && mv -n -- "$f" "$new"
  fi
done
