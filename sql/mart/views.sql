-- Fase 3 del layer analytics — viste del mart (star schema), sopra lo staging di
-- sql/ddl/staging.sql. Viste, non tabelle materializzate, in v1: volume piccolo,
-- sempre fresche, costo trascurabile — vedi carica_bigquery.py per il perché.

-- --------------------------------------------------------------------------
-- v_engagement_canonico — UNA riga per (canale, video, snapshot_at). fct_youtube_engagement_snapshot e'
-- scritta in append da due processi indipendenti (il Mac e GitHub Actions): se partono insieme possono
-- scrivere la stessa riga due volte. Il progetto BigQuery e' senza DML (livello gratuito: niente
-- MERGE/UPDATE/DELETE), quindi i doppioni non si possono impedire ne' cancellare in scrittura: li elimina
-- questa vista, da cui leggono TUTTI i mart. `channel_key` NULL (righe storiche scritte prima dello split) =
-- canale originale.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.v_engagement_canonico` AS
SELECT
  video_id,
  content_key,
  snapshot_at,
  views,
  likes,
  comments,
  COALESCE(channel_key, 'gol-impossibili') AS channel_key
FROM `calciovich-video-analytics.calciovich_content.fct_youtube_engagement_snapshot`
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY COALESCE(channel_key, 'gol-impossibili'), video_id, snapshot_at
  ORDER BY views DESC NULLS LAST, likes DESC NULLS LAST, comments DESC NULLS LAST
) = 1;

-- --------------------------------------------------------------------------
-- mart_daily_engagement — un valore per (content_key, giorno), non uno per ogni
-- trigger del collector. fct_youtube_engagement_snapshot resta a grain
-- (canale, video_id, snapshot_at) nello staging deliberatamente (vedi sql/ddl/staging.sql):
-- i cluster di snapshot ravvicinati osservati in sviluppo (riavvii del LaunchAgent,
-- non un regime stazionario a 6h) sono artefatti, non segnale — questa vista li
-- collassa prendendo il valore più alto del giorno (le view sono monotone non
-- decrescenti nel tempo per un dato video, quindi il massimo del giorno è anche
-- l'ultimo osservato).
-- QUESTA VISTA E' DEL SOLO CANALE ORIGINALE (e' quella che il report Looker gia' pubblicato legge, a colonne
-- invariate): per il canale del libro e per il confronto fra canali vedi mart_daily_engagement_by_channel.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_daily_engagement` AS
SELECT
  e.video_id,
  e.content_key,
  d.titolo,
  DATE(e.snapshot_at) AS day,
  MAX(e.views) AS views,
  MAX(e.likes) AS likes,
  MAX(e.comments) AS comments
FROM `calciovich-video-analytics.calciovich_content.v_engagement_canonico` e
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_content` d
  ON d.content_key = e.content_key
WHERE e.channel_key = 'gol-impossibili'
GROUP BY e.video_id, e.content_key, d.titolo, day;

-- --------------------------------------------------------------------------
-- mart_video_performance — una riga per content_key con l'ultimo snapshot noto,
-- le finestre fisse (Fase 2, dove disponibili) e la categoria. YouTube-only,
-- dichiarato dal nome stesso delle colonne (nessun "platform" generico che
-- implicherebbe dati anche per Instagram/TikTok, che qui non esistono).
-- DEL SOLO CANALE ORIGINALE (stesso motivo di mart_daily_engagement): un contenuto ripubblicato sul canale
-- del libro avrebbe due video_id e il join per content_key lo raddoppierebbe. Il filtro sta DENTRO le CTE,
-- prima del join. Per entrambi i canali: mart_video_performance_by_channel.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_video_performance` AS
WITH ultimo_snapshot AS (
  SELECT
    video_id, content_key, views, likes, comments, snapshot_at,
    ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY snapshot_at DESC) AS rn
  FROM `calciovich-video-analytics.calciovich_content.v_engagement_canonico`
  WHERE channel_key = 'gol-impossibili'
),
finestre_originale AS (
  SELECT * FROM `calciovich-video-analytics.calciovich_content.fct_youtube_fixed_window`
  WHERE COALESCE(channel_key, 'gol-impossibili') = 'gol-impossibili'
)
SELECT
  d.content_key,
  d.titolo,
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
LEFT JOIN finestre_originale w
  ON w.content_key = d.content_key;

-- --------------------------------------------------------------------------
-- v_publish_event_canonico — UNA riga per (contenuto, piattaforma). Dallo split dei canali
-- (06/10/2026) un contenuto ripubblicato sul canale nuovo ha DUE righe YouTube in
-- fct_publish_event (una per canale): le viste che rispondono a "su quali piattaforme e'
-- questo contenuto" devono contarlo UNA volta, e restare identiche a prima per i contenuti
-- gia' esistenti. La riga canonica e' quella del canale ORIGINALE (`gol-impossibili`), poi la
-- piu' vecchia, poi per id. Non e' "la prima pubblicazione" in senso cronologico: confirmed_at e'
-- NULL nella maggior parte dei record storici, quindi non si puo' ordinare per tempo. E' una
-- regola di CANONICITA' dichiarata. Il dettaglio per canale sta in mart_publish_reach_by_channel.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.v_publish_event_canonico` AS
SELECT *
FROM `calciovich-video-analytics.calciovich_content.fct_publish_event`
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY content_key, platform
  ORDER BY IF(channel_key = 'gol-impossibili', 0, 1),
           confirmed_at ASC NULLS LAST,
           scheduled_publish_at ASC NULLS LAST,
           external_id
) = 1;

-- --------------------------------------------------------------------------
-- mart_publish_reach — per ogni contenuto del calendario, su quali piattaforme è
-- CONFIRMED e con che privacy grezza. CONFIRMED non implica visibilità pubblica
-- (vedi sql/ddl/staging.sql) — questa vista espone privacy così com'è, non deriva
-- un flag "pubblico". Costruita su v_publish_event_canonico: stessi numeri di prima anche dopo
-- le ripubblicazioni sul canale nuovo (su_youtube resta 0/1).
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
LEFT JOIN `calciovich-video-analytics.calciovich_content.v_publish_event_canonico` p
  ON p.content_key = d.content_key
GROUP BY d.content_key, d.categoria;

-- --------------------------------------------------------------------------
-- mart_publish_reach_detail — stesso contenuto di mart_publish_reach, ma a
-- grain piatto (content_key, platform) invece che un ARRAY<STRUCT> per
-- content_key. Aggiunta per Fase 4: il connettore nativo BigQuery di Looker
-- Studio non tratta campi REPEATED/RECORD come dimensioni/metriche utilizzabili
-- nel report builder standard — dettaglio_piattaforme in mart_publish_reach
-- resta corretto per un consumer SQL diretto, ma non e' collegabile a un
-- grafico Looker Studio cosi' com'e'. Questa vista e' la fonte per la pagina
-- "copertura multi-piattaforma" del report. Anch'essa su v_publish_event_canonico.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_publish_reach_detail` AS
SELECT
  d.content_key,
  d.titolo,
  d.categoria,
  p.platform,
  p.privacy,
  p.confirmed_at
FROM `calciovich-video-analytics.calciovich_content.dim_content` d
JOIN `calciovich-video-analytics.calciovich_content.v_publish_event_canonico` p
  ON p.content_key = d.content_key;

-- --------------------------------------------------------------------------
-- mart_publish_reach_by_channel — il dettaglio COMPLETO per (contenuto, piattaforma, canale), senza
-- ridurre: un contenuto ripubblicato sul canale nuovo compare due volte su YouTube. `privacy_alla_conferma`
-- e' la privacy al momento dell'upload (un upload programmato e' 'private' anche dopo essere diventato
-- pubblico), `scheduled_publish_at` e' il publishAt. Per Instagram/TikTok `channel_key` e' l'account del brand.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_publish_reach_by_channel` AS
SELECT
  d.content_key,
  d.titolo,
  d.categoria,
  p.platform,
  p.channel_key,
  c.channel_name,
  p.privacy AS privacy_alla_conferma,
  p.confirmed_at,
  p.scheduled_publish_at
FROM `calciovich-video-analytics.calciovich_content.dim_content` d
JOIN `calciovich-video-analytics.calciovich_content.fct_publish_event` p
  ON p.content_key = d.content_key
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_channel` c
  ON c.channel_key = p.channel_key AND p.platform = 'youtube';   -- dim_channel descrive i canali YouTube: per
                                                                  -- Instagram/TikTok channel_name resta NULL

-- --------------------------------------------------------------------------
-- mart_republication_lineage — i contenuti ripubblicati sul canale nuovo: video originale -> copia.
-- Serve a confrontare lo STESSO contenuto davanti a due pubblici senza sommarlo due volte. Le statistiche
-- di engagement del canale nuovo non sono ancora raccolte (fase successiva): oggi la vista dice cosa e'
-- stato ripubblicato e quando, non come e' andato.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_republication_lineage` AS
SELECT
  l.content_key,
  d.titolo,
  d.categoria,
  l.original_video_id,
  l.copy_video_id,
  l.scheduled_publish_at,
  l.copy_confirmed_at,
  l.reason
FROM `calciovich-video-analytics.calciovich_content.dim_content_lineage` l
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_content` d
  ON d.content_key = l.content_key;

-- --------------------------------------------------------------------------
-- mart_daily_engagement_by_channel — come mart_daily_engagement, ma per ENTRAMBI i canali, a grain
-- (channel_key, video_id, giorno). Un contenuto ripubblicato ha due righe (video originale e copia), mai
-- sommate. I video resi privati smettono di ricevere snapshot (il collector raccoglie solo i pubblici): la loro
-- serie si ferma all'ultimo valore, non e' un calo.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_daily_engagement_by_channel` AS
SELECT
  e.channel_key,
  c.channel_name,
  e.video_id,
  e.content_key,
  d.titolo,
  DATE(e.snapshot_at) AS day,
  MAX(e.views) AS views,
  MAX(e.likes) AS likes,
  MAX(e.comments) AS comments
FROM `calciovich-video-analytics.calciovich_content.v_engagement_canonico` e
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_content` d ON d.content_key = e.content_key
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_channel` c ON c.channel_key = e.channel_key
GROUP BY e.channel_key, c.channel_name, e.video_id, e.content_key, d.titolo, day;

-- --------------------------------------------------------------------------
-- mart_video_performance_by_channel — una riga per (canale, video_id) con l'ultimo snapshot noto. Le finestre
-- fisse (YouTube Analytics) esistono solo per il canale originale: per il canale del libro restano NULL finche'
-- non c'e' il consenso `yt-analytics.readonly` sul suo progetto. Il join con le finestre e' per (video_id,
-- canale), non per content_key.
-- --------------------------------------------------------------------------
CREATE OR REPLACE VIEW `calciovich-video-analytics.calciovich_content.mart_video_performance_by_channel` AS
WITH ultimo_snapshot AS (
  SELECT
    channel_key, video_id, content_key, views, likes, comments, snapshot_at,
    ROW_NUMBER() OVER (PARTITION BY channel_key, video_id ORDER BY snapshot_at DESC) AS rn
  FROM `calciovich-video-analytics.calciovich_content.v_engagement_canonico`
)
SELECT
  s.channel_key,
  c.channel_name,
  d.content_key,
  d.titolo,
  d.categoria,
  s.video_id,
  s.views          AS youtube_views_ultimo_snapshot,
  s.likes          AS youtube_likes_ultimo_snapshot,
  s.comments       AS youtube_comments_ultimo_snapshot,
  s.snapshot_at    AS youtube_ultimo_snapshot_at,
  w.views_day1,
  w.views_day2,
  w.views_day7
FROM ultimo_snapshot s
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_content` d ON d.content_key = s.content_key
LEFT JOIN `calciovich-video-analytics.calciovich_content.dim_channel` c ON c.channel_key = s.channel_key
LEFT JOIN `calciovich-video-analytics.calciovich_content.fct_youtube_fixed_window` w
  ON w.video_id = s.video_id AND COALESCE(w.channel_key, 'gol-impossibili') = s.channel_key
WHERE s.rn = 1;
