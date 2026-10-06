-- Migrazione del 06/10/2026: dimensione canale nelle fact di engagement.
-- Idempotente: si puo' rieseguire. Solo DDL (il progetto BigQuery e' senza DML, livello gratuito).
--
-- PERCHE' ALTER E NON CTAS: `CREATE OR REPLACE TABLE ... AS SELECT *` rende NULLABLE le colonne REQUIRED
-- (snapshot_at), e un load WRITE_APPEND con lo schema del loader (REQUIRED) fallirebbe; inoltre il replace apre una
-- finestra in cui una scrittura concorrente (il Mac o GitHub Actions) puo' andare persa. Con ALTER la tabella non
-- viene ricreata. Verificato: un load con lo schema VECCHIO (senza channel_key) continua a funzionare sulla tabella
-- con la colonna in piu', quindi gli scrittori non ancora aggiornati non si rompono durante il rollout.
--
-- Le righe storiche restano con channel_key NULL: tutte le viste leggono COALESCE(channel_key, 'gol-impossibili'),
-- ed e' un invariante dichiarato (non un default silenzioso): il loader segnala le righe NUOVE senza canale.

ALTER TABLE `calciovich-video-analytics.calciovich_content.fct_youtube_engagement_snapshot`
  ADD COLUMN IF NOT EXISTS channel_key STRING;

ALTER TABLE `calciovich-video-analytics.calciovich_content.fct_youtube_fixed_window`
  ADD COLUMN IF NOT EXISTS channel_key STRING;
