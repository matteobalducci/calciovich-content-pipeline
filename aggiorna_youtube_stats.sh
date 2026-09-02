#!/bin/bash
# aggiorna_youtube_stats.sh — wrapper per com.calciovich.youtubestats.plist.
# Lanciato da launchd invocando /bin/bash (non python3 direttamente): stesso
# schema di auto-upload.sh, che ha accesso a Desktop mentre python3 invocato
# come ProgramArguments[0] no (bug macOS TCC scoperto il 21/08 — il symlink
# di Xcode CLT non è nemmeno selezionabile in Full Disk Access). Bypassa il
# problema invece di dipendere da un permesso che il Finder non fa concedere.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
/usr/bin/python3 aggiorna_youtube_stats.py
