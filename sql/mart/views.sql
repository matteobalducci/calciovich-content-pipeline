-- Fase 3 del layer analytics — viste del mart (star schema), sopra lo staging di
-- sql/ddl/staging.sql. Viste, non tabelle materializzate, in v1: volume piccolo,
-- sempre fresche, costo trascurabile — vedi carica_bigquery.py per il perché.

-- --------------------------------------------------------------------------
-- mart_daily_engagement — un valore per (content_key, giorno), non uno per ogni
-- trigger del collector. fct_youtube_engagement_snapshot resta a grain
-- (video_id, snapshot_at) nello staging deliberatamente (vedi sql/ddl/staging.sql):
-- i cluster di snapshot ravvicinati osservati in sviluppo (riavvii del LaunchAgent,
-- non un regime stazionario a 6h) sono artefatti, non segnale — questa vista li
-- collassa prendendo il valore più alto del giorno (le view sono monotone non
-- decrescenti nel tempo per un dato video, quindi il massimo del giorno è anche
-- l'ultimo osservato).
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_daily_engagement` AS
SELECT
  video_id,
  content_key,
  DATE(snapshot_at) AS day,
  MAX(views) AS views,
  MAX(likes) AS likes,
  MAX(comments) AS comments
FROM `calciovich-video-analytics.calciovich_content.fct_youtube_engagement_snapshot`
GROUP BY video_id, content_key, day;

-- --------------------------------------------------------------------------
-- mart_video_performance — una riga per content_key con l'ultimo snapshot noto,
-- le finestre fisse (Fase 2, dove disponibili) e la categoria. YouTube-only,
-- dichiarato dal nome stesso delle colonne (nessun "platform" generico che
-- implicherebbe dati anche per Instagram/TikTok, che qui non esistono).
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_video_performance` AS
WITH ultimo_snapshot AS (
  SELECT
    video_id, content_key, views, likes, comments, snapshot_at,
    ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY snapshot_at DESC) AS rn
  FROM `calciovich-video-analytics.calciovich_content.fct_youtube_engagement_snapshot`
)
SELECT
  d.content_key,
  d.categoria,
  s.video_id,
  s.views          AS youtube_views_ultimo_snapshot,
  s.likes          AS youtube_likes_ultimo_snapshot,
  s.comments       AS youtube_comments_ultimo_snapshot,
  s.snapshot_at    AS youtube_ultimo_snapshot_at,
  w.views_day1,
  w.views_day2,
  w.views_day7
FROM `calciovich-video-analytics.calciovich_content.dim_content` d
LEFT JOIN ultimo_snapshot s ON s.content_key = d.content_key AND s.rn = 1
LEFT JOIN `calciovich-video-analytics.calciovich_content.fct_youtube_fixed_window` w
  ON w.content_key = d.content_key;

-- --------------------------------------------------------------------------
-- mart_publish_reach — per ogni contenuto del calendario, su quali piattaforme è
-- CONFIRMED e con che privacy grezza. CONFIRMED non implica visibilità pubblica
-- (vedi sql/ddl/staging.sql) — questa vista espone privacy così com'è, non deriva
-- un flag "pubblico".
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_publish_reach` AS
SELECT
  d.content_key,
  d.categoria,
  COUNTIF(p.platform = 'youtube')   AS su_youtube,
  COUNTIF(p.platform = 'instagram') AS su_instagram,
  COUNTIF(p.platform = 'tiktok')    AS su_tiktok,
  COUNT(DISTINCT p.platform)        AS n_piattaforme,
  ARRAY_AGG(STRUCT(p.platform, p.privacy, p.confirmed_at) ORDER BY p.platform) AS dettaglio_piattaforme
FROM `calciovich-video-analytics.calciovich_content.dim_content` d
LEFT JOIN `calciovich-video-analytics.calciovich_content.fct_publish_event` p
  ON p.content_key = d.content_key
GROUP BY d.content_key, d.categoria;
