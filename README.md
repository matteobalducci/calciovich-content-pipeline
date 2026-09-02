# Calciovich Content Pipeline

Script Python che gestiscono, da soli, la produzione e pubblicazione giornaliera di
contenuti video su **YouTube, Instagram e TikTok** per un progetto transmediale
indipendente (un personaggio di fantasia, un romanzo, un canale social). Estratti dal
repository privato del progetto: qui c'è solo il codice, non la storia, i dati o le
credenziali.

Il sistema gira ogni giorno orchestrato da un agente AI (Claude Code) che legge lo stato
della coda contenuti, decide cosa produrre secondo una rotazione settimanale, genera il
video, lo passa da un controllo qualità visivo e lo pubblica su tutte e tre le
piattaforme — aggiornando registri, playlist e stato in automatico.

## Cosa fa la pipeline

**Generazione**
- `genera_video_ai.py` — clip video AI (Seedance via PiAPI) con volto del personaggio
  sempre coerente, usando immagini di riferimento canoniche (`omni_reference`) invece di
  lasciare al modello mano libera. Espande automaticamente i segnaposto del prompt
  (`{KIT}`, `{BROADCAST}`, `{SCENE_LOCK}`) dalle clausole canoniche, cosi' i prompt scritti
  a mano restano corti e non si gonfiano di boilerplate difensivo ad ogni fix.
- `genera_immagini.py` / `genera_immagini_free.py` / `genera_foto_ai.py` — illustrazioni e
  foto "d'archivio", su provider a pagamento (Seedream) o gratuito (Pollinations) a
  seconda della precisione di inquadratura richiesta.
- `genera_voci.py` / `genera_voci_free.py` — voci neurali (edge-tts, gratis) per il
  formato long-form audiolibro.
- `crea_audiolibro.py` / `make_video.py` — assemblano capitoli del testo, illustrazioni,
  voce e musica in video montati (Ken Burns, sottotitoli sincronizzati, badge episodio).
- `overlay_broadcast.py` / `overlay_motivational.py` — grafiche stile trasmissione TV
  (tabellino, nome giocatore, telecronaca sincronizzata) composte via PIL/ffmpeg, con
  margini di sicurezza testati contro l'interfaccia nativa di TikTok/Reels/Shorts (che
  copre la parte bassa del video in riproduzione reale).
- `genera_thumbnail.py` / `genera_certificato.py` — asset grafici secondari.

**Controllo qualita'**
- `qc_video.py` — foglio di contatto (contact sheet) di fotogrammi estratti a intervalli,
  per un controllo visivo rapido di coerenza volto/regia/brand prima della pubblicazione.
- `check_outliers.py` — confronta le view dell'ultimo video pubblicato di ogni formato
  con la mediana recente dello stesso formato (via YouTube Analytics API): segnala
  outlier (WIN >=5x, FAIL <=0.2x) senza aspettare una revisione periodica.

**Pubblicazione multi-piattaforma**
- `carica_youtube.py` — upload con `publishAt` programmato, tag/descrizione/categoria,
  gestione quota e retry.
- `carica_instagram.py` — Reel via Meta Graph API: upload su storage S3-compatibile
  (Cloudflare R2), creazione container, polling, pubblicazione, commento, story.
- `carica_tiktok.py` — Content Posting API, con fallback a bozza nell'inbox quando
  l'app non ha ancora superato l'audit della piattaforma.
- `gestisci_playlist.py` — crea e mantiene le playlist YouTube (per serie tematica e per
  ordine cronologico), incluso uno switch automatico tra due formati quando uno "supera"
  l'altro in copertura contenuti.
- `rispondi_commenti.py` / `leggi_commenti.py` — bozze di risposta ai commenti, coerenti
  con la voce del personaggio per piattaforma.
- `aggiorna_youtube_stats.py` — logger storico (iscritti/views/follower) via LaunchAgent,
  senza taglio di retention: il seme dati per analisi future.

**Orchestrazione**
- `stato_pipeline.py` / `coach.py` / `piano.py` — stato della coda, obiettivi e cadenza.
- `app_server.py` — piccolo server HTTP che serve un cruscotto locale (stato pipeline,
  percorso contenuti, azioni rapide) e ne rigenera i dati ad ogni avvio.

## Scelte tecniche degne di nota

- **Idempotenza sotto esecuzioni concorrenti**: i publisher deduplicano sia per nome file
  sia per un id stabile dell'item (sopravvive a un rename dopo un retry), e usano un
  file-lock per non sovrapporsi a un'altra esecuzione parallela dello stesso script.
- **Prompt "snelli"**: le clausole di regia/brand/kit non si incollano mai a mano nel
  prompt — vengono espanse da un template condiviso, cosi' restano identiche a se stesse
  e non driftano nel tempo con fix accumulati.
- **Costo zero come prima scelta**: la pipeline preferisce sempre il formato a costo
  zero disponibile (contenuto gia' pagato riciclato, provider gratuiti, pipeline locale)
  e usa la generazione AI a pagamento solo per il formato che *deve* essere sempre
  nuovo, con un tetto di spesa e un numero massimo di tentativi per singolo item.
- **Trigger leggero vs revisione periodica**: `check_outliers.py` copre il caso "un
  singolo video palesemente fuori scala" ogni giorno, senza sostituire un'analisi piu'
  ampia fatta a cadenza fissa.

## Stack

Python 3 · Google API Client (YouTube Data API v3, YouTube Analytics API) · Meta Graph
API · TikTok Content Posting API · Cloudflare R2 (S3-compatibile, via boto3) · PiAPI
(Seedance/Seedream) · Pollinations · edge-tts · Pillow · ffmpeg (via imageio-ffmpeg)

## Nota

Questo repository e' un estratto a scopo dimostrativo: gli script referenziano un layer
di configurazione/dati privato (credenziali, coda contenuti, calendario editoriale) non
incluso qui. Non e' pensato per essere eseguito standalone — mostra l'architettura e le
scelte di implementazione della pipeline di produzione reale.
