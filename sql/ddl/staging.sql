-- Fase 3 del layer analytics — DDL delle tabelle di staging.
--
-- Queste tabelle sono create e scritte da carica_bigquery.py con lo schema Python
-- qui sotto trascritto in SQL per documentazione/riproducibilità — carica_bigquery.py
-- resta la fonte eseguibile di verità sullo schema (bigquery.SchemaField), non
-- questo file: se divergono, fidati del codice.
--
-- Scrittura: WRITE_TRUNCATE per ogni tabella qui sotto ad ogni run (ogni fonte
-- locale è uno stato pieno e autoconsistente, non un log incrementale — vedi il
-- docstring di carica_bigquery.py). L'unica eccezione è _run_metadata (in fondo a
-- questo file), WRITE_APPEND deliberato.
--
-- Sostituisci `calciovich-video-analytics` col tuo progetto per rieseguire altrove.

CREATE SCHEMA IF NOT EXISTS `calciovich-video-analytics.calciovich_content`
OPTIONS (location = 'EU');

-- --------------------------------------------------------------------------
-- Dimensioni
-- --------------------------------------------------------------------------

-- Il calendario video dichiarato (da app/data.json, non un sottoprodotto di quali
-- piattaforme hanno ricevuto l'upload — vedi dimensional_model.py::build_dim_content()
-- per la regola di dedup e il perché).
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.dim_content` (
  content_key STRING NOT NULL,
  file        STRING,
  fonte       STRING,
  categoria   STRING,  -- canonical / gol-ai / long-form / personaggio / altro
  titolo      STRING   -- titolo umano leggibile (app/data.json), non il filename —
                        -- per Fase 4 (selettori/etichette nel report Looker Studio)
);

-- Statica, 3 righe.
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.dim_platform` (
  platform STRING NOT NULL  -- youtube / instagram / tiktok
);

-- Calendario standard, range determinato dalle date reali osservate nei fatti
-- (vedi carica_bigquery.py::build_all()).
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.dim_date` (
  date        DATE NOT NULL,
  year        INT64,
  month       INT64,
  day         INT64,
  day_of_week STRING,
  iso_week    INT64
);

-- --------------------------------------------------------------------------
-- Fatti — YouTube-only per le metriche di engagement (verificato: Instagram e
-- TikTok hanno solo log di pubblicazione, zero engagement raccolto)
-- --------------------------------------------------------------------------

-- Grain (video_id, snapshot_at) — staging 1:1 dal JSON sorgente, NESSUNA
-- aggregazione a monte. Un'eventuale dedup "un valore per bucket temporale" per un
-- mart Looker si fa qui sotto, a livello di vista, non nello staging.
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.fct_youtube_engagement_snapshot` (
  video_id    STRING,
  content_key STRING,
  snapshot_at TIMESTAMP NOT NULL,
  views       INT64,
  likes       INT64,
  comments    INT64
);

-- Grain (video_id). views_dayN, non views_24h: l'API bucket per giorno solare, non
-- per ore esatte dalla pubblicazione (verificato empiricamente in Fase 2).
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.fct_youtube_fixed_window` (
  video_id    STRING NOT NULL,
  content_key STRING,
  views_day1  INT64,
  views_day2  INT64,
  views_day7  INT64
);

-- Grain (content_key, platform). Solo record CONFIRMED presenti in dim_content
-- (foto/Stories Instagram fuori dal calendario video sono escluse esplicitamente,
-- con un conteggio loggato — vedi dimensional_model.py::build_fct_publish_event()).
-- CONFIRMED significa "la piattaforma ha accettato l'upload", NON "visibile
-- pubblicamente ora" — privacy è il valore grezzo della piattaforma al momento del
-- confirm(), mai un booleano derivato.
CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content.fct_publish_event` (
  content_key  STRING NOT NULL,
  platform     STRING NOT NULL,
  external_id  STRING,
  privacy      STRING,
  confirmed_at TIMESTAMP
);

-- --------------------------------------------------------------------------
-- _run_metadata — l'unica tabella WRITE_APPEND. Una riga per run RIUSCITO del
-- loader, scritta in un unico INSERT dopo che tutte le tabelle di staging sopra
-- sono state scritte con successo — mai una riga anticipata con completed_at NULL.
-- I consumatori leggono l'ultima riga per completed_at, senza dover filtrare NULL,
-- per sapere qual è l'ultimo punto nel tempo in cui le tabelle di staging erano
-- garantite coerenti fra loro.
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `calciovich-video-analytics.calciovich_content._run_metadata` (
  run_id         STRING NOT NULL,
  completed_at   TIMESTAMP NOT NULL,
  tables_written ARRAY<STRING>
);
