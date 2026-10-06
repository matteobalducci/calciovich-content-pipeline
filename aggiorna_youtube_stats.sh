#!/bin/bash
# aggiorna_youtube_stats.sh — wrapper per com.calciovich.youtubestats.plist.
# Lanciato da launchd invocando /bin/bash (non python3 direttamente): stesso
# schema di auto-upload.sh, che ha accesso a Desktop mentre python3 invocato
# come ProgramArguments[0] no (bug macOS TCC scoperto il 21/08 — il symlink
# di Xcode CLT non è nemmeno selezionabile in Full Disk Access). Bypassa il
# problema invece di dipendere da un permesso che il Finder non fa concedere.
#
# Ogni step si autoprotegge (uno che fallisce NON ferma gli altri: e' voluto), ma l'exit code finale NON deve
# mentire: se un solo step e' fallito (es. un canale non raccolto) il wrapper esce con errore.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
rc=0
/usr/bin/python3 aggiorna_youtube_stats.py || rc=1
/usr/bin/python3 raccogli_metriche_video.py || rc=1
/usr/bin/python3 raccogli_finestre_fisse.py || rc=1
/usr/bin/python3 carica_bigquery.py || rc=1
exit $rc
