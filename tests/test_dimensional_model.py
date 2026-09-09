"""Fase 3 del layer analytics — costruzione pure-Python del modello dimensionale.

WHY THIS EXISTS
----------------
Il grosso del rischio reale di Fase 3, trovato in 6 giri di revisione avversariale
su dati di produzione, vive in tre punti: (1) la regola di dedup di dim_content deve
distinguere un item di calendario vero da un backfill che punta allo stesso file
fisico, con una trappola concreta (un match a sottostringa fallirebbe esattamente
sull'unico caso reale che deve risolvere, perche' il testo del backfill CONTIENE la
stringa del vincitore); (2) fct_publish_event deve escludere esplicitamente, non in
silenzio, i contenuti fuori dal calendario video (foto/Stories Instagram); (3) la
mappa piattaforma->campo id deve avere un fallback per i record recuperati via
Registry.reconcile() dopo un crash, che scrivono external_id ma non il campo
piattaforma-specifico.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dimensional_model as dm  # noqa: E402


def item(file=None, stato="Pronto", fonte="", categoria="Pronto", titolo=None):
    return {"file": file, "stato": stato, "fonte": fonte, "categoria": categoria, "titolo": titolo}


def app_data(*items):
    return {"weeks": [{"items": list(items)}]}


# --- build_dim_content(): filtro stato -------------------------------------


def test_only_stato_pronto_items_produce_a_row():
    data = app_data(
        item(file="/output/a.mp4", stato="Pronto"),
        item(file="/output/b.mp4", stato="Da produrre"),
    )
    rows, dupes = dm.build_dim_content(data)
    assert [r["content_key"] for r in rows] == ["a"]
    assert dupes == 0


def test_a_pronto_item_with_no_file_is_skipped_not_a_crash():
    data = app_data(item(file=None, stato="Pronto"))
    rows, dupes = dm.build_dim_content(data)
    assert rows == []


# --- build_dim_content(): dedup, il caso reale ------------------------------


def test_dedup_the_real_settimana2_day1_case_prefix_match_not_substring():
    """Riprodotto esattamente dal caso reale trovato durante la revisione: due item
    puntano allo stesso file fisico. Il perdente ha una fonte che CONTIENE la
    stringa "ai-content-queue.json" nel proprio testo esplicativo — una regola a
    sottostringa matcherebbe entrambi e la scelta sarebbe indeterminata (dict,
    ultimo che vince per iterazione). La regola a prefisso deve scegliere sempre e
    solo il vincitore vero."""
    same_file = "/output/ai-clips/settimana2-day1-coast-to-coast-v2.tv.mp4"
    vincitore = item(
        file=same_file, stato="Pronto",
        fonte="output/ai-content-queue.json#settimana2-day1-coast-to-coast-v2",
    )
    backfill = item(
        file=same_file, stato="Pronto",
        fonte="output/youtube-uploads.json#settimana2-day1-cronaca-mondiale "
              "(nessun item corrispondente in ai-content-queue.json)",
    )
    for ordine in ([vincitore, backfill], [backfill, vincitore]):
        data = app_data(*ordine)
        rows, dupes = dm.build_dim_content(data)
        assert dupes == 1
        assert len(rows) == 1
        assert rows[0]["fonte"] == "output/ai-content-queue.json#settimana2-day1-coast-to-coast-v2"
        assert rows[0]["content_key"] == "settimana2-day1-coast-to-coast-v2.tv"


def test_dedup_falls_back_to_first_seen_when_neither_source_is_calendar_and_logs_it(capsys):
    same_file = "/output/x.mp4"
    data = app_data(
        item(file=same_file, stato="Pronto", fonte="output/manuale#uno"),
        item(file=same_file, stato="Pronto", fonte="output/manuale#due"),
    )
    rows, dupes = dm.build_dim_content(data)
    assert dupes == 1
    assert len(rows) == 1
    assert rows[0]["fonte"] == "output/manuale#uno"  # il primo visto
    assert "duplicato" in capsys.readouterr().out


def test_dedup_catches_different_files_that_collide_on_content_key():
    """Riprodotto dal dibattito di controllo: la dedup deve chiavare su
    content_key, non sul file path grezzo — due file fisicamente diversi il cui
    basename-senza-ultima-estensione collide (qui: .mp4 vs .mov) devono comunque
    essere deduplicati, non produrre due righe silenziose con lo stesso
    content_key."""
    vincitore = item(
        file="/output/dir1/short01.mp4", stato="Pronto",
        fonte="output/ai-content-queue.json#short01",
    )
    perdente = item(
        file="/output/dir2/short01.mov", stato="Pronto",
        fonte="output/manuale#backfill",
    )
    data = app_data(vincitore, perdente)
    rows, dupes = dm.build_dim_content(data)
    assert dupes == 1
    assert len(rows) == 1
    assert rows[0]["content_key"] == "short01"
    assert rows[0]["file"] == "/output/dir1/short01.mp4"


def test_dedup_logs_even_when_both_duplicates_are_from_the_calendar_source(capsys):
    """Caso trovato dal secondo giro di controllo: due candidati ENTRAMBI con
    fonte ai-content-queue.json che collidono sullo stesso content_key (due voci
    di calendario diverse, non un item + un backfill) — nessun criterio per
    scegliere, ma non deve restare silenzioso: e' un'ambiguita' vera nel
    calendario."""
    a = item(file="/output/dir1/short01.mp4", stato="Pronto",
             fonte="output/ai-content-queue.json#voce-a")
    b = item(file="/output/dir2/short01.mov", stato="Pronto",
             fonte="output/ai-content-queue.json#voce-b")
    rows, dupes = dm.build_dim_content(app_data(a, b))
    assert dupes == 1
    assert len(rows) == 1
    assert "ENTRAMBI da ai-content-queue.json" in capsys.readouterr().out


def test_no_duplicates_means_dupes_count_is_zero():
    data = app_data(
        item(file="/output/a.mp4", stato="Pronto"),
        item(file="/output/b.mp4", stato="Pronto"),
    )
    rows, dupes = dm.build_dim_content(data)
    assert dupes == 0
    assert len(rows) == 2


# --- build_dim_content(): content_key + categoria riusata -------------------


def test_content_key_strips_only_the_last_extension():
    data = app_data(item(file="/output/settimana2-day4-rovesciata-final.tv.mp4", stato="Pronto"))
    rows, _ = dm.build_dim_content(data)
    assert rows[0]["content_key"] == "settimana2-day4-rovesciata-final.tv"


def test_categoria_reuses_metriche_video_categoria_not_reimplemented():
    """short26 e' 'Personaggio: Esordio' nell'app_map, non canonical per il solo
    pattern del nome — se dim_content reimplementasse la classificazione invece di
    riusare categoria(), questo test lo scoprirebbe."""
    data = app_data(item(
        file="/output/short26-esordio.vert.mp4", stato="Pronto",
        categoria="Personaggio: Esordio",
    ))
    rows, _ = dm.build_dim_content(data)
    assert rows[0]["categoria"] == "personaggio"


# --- build_dim_content(): titolo (Fase 4, leggibilita' nel report Looker) --


def test_titolo_is_carried_from_app_data():
    data = app_data(item(file="/output/short01.mp4", stato="Pronto",
                          titolo="Il pallone che tornava da solo"))
    rows, _ = dm.build_dim_content(data)
    assert rows[0]["titolo"] == "Il pallone che tornava da solo"


def test_titolo_follows_the_dedup_winner_not_the_loser():
    """Il titolo deve viaggiare insieme al resto dei campi del vincitore della
    dedup, mai restare quello del candidato scartato — verificato in entrambi
    gli ordini di inserimento."""
    same_file = "/output/settimana2-day1-coast-to-coast-v2.tv.mp4"
    vincitore = item(file=same_file, stato="Pronto",
                      fonte="output/ai-content-queue.json#voce-vera",
                      titolo="TITOLO VINCITORE")
    perdente = item(file=same_file, stato="Pronto",
                     fonte="output/youtube-uploads.json#backfill "
                           "(nessun item corrispondente in ai-content-queue.json)",
                     titolo="TITOLO SCARTATO")
    for ordine in ([vincitore, perdente], [perdente, vincitore]):
        rows, dupes = dm.build_dim_content(app_data(*ordine))
        assert dupes == 1
        assert rows[0]["titolo"] == "TITOLO VINCITORE"


def test_titolo_falls_back_to_content_key_when_missing():
    """Mai un campo vuoto in un report pubblico: se titolo manca, il selettore
    deve comunque mostrare qualcosa di sensato invece di una cella vuota."""
    data = app_data(item(file="/output/short01.mp4", stato="Pronto", titolo=None))
    rows, _ = dm.build_dim_content(data)
    assert rows[0]["titolo"] == "short01"


# --- build_dim_platform() / build_dim_date() --------------------------------


def test_dim_platform_has_exactly_the_three_known_platforms():
    platforms = {r["platform"] for r in dm.build_dim_platform()}
    assert platforms == {"youtube", "instagram", "tiktok"}


def test_dim_date_covers_min_to_max_inclusive():
    from datetime import date
    rows = dm.build_dim_date([date(2026, 7, 5), date(2026, 7, 7)])
    assert [r["date"] for r in rows] == ["2026-07-05", "2026-07-06", "2026-07-07"]


def test_dim_date_empty_input_returns_no_rows():
    assert dm.build_dim_date([]) == []


# --- build_fct_youtube_engagement_snapshot() / fixed_window() --------------


def test_engagement_snapshot_is_staging_1_to_1_no_filtering():
    records = [{"video_id": "v1", "key": "short01.vert", "snapshot_at": "2026-09-08T21:33:51",
                "views": 10, "likes": 1, "comments": 0}]
    rows = dm.build_fct_youtube_engagement_snapshot(records)
    assert rows == [{"video_id": "v1", "content_key": "short01.vert",
                      "snapshot_at": "2026-09-08T21:33:51", "views": 10, "likes": 1, "comments": 0}]


def test_fixed_window_reads_the_dict_of_records_by_video_id():
    records = {"v1": {"video_id": "v1", "key": "short01.vert", "views_day7": 100}}
    rows = dm.build_fct_youtube_fixed_window(records)
    assert rows == [{"video_id": "v1", "content_key": "short01.vert",
                      "views_day1": None, "views_day2": None, "views_day7": 100}]


# --- build_fct_publish_event(): mappa id + scope ----------------------------


def test_id_field_fallback_per_platform():
    registry_data = {
        "youtube": {"short01.vert": {"videoId": "vidY"}},
        "instagram": {"short01.vert": {"mediaId": "medI"}},
        "tiktok": {"short01.vert": {"publishId": "pubT"}},
    }
    rows, excluded = dm.build_fct_publish_event(registry_data, {"short01.vert"})
    assert excluded == 0
    ids_by_platform = {r["platform"]: r["external_id"] for r in rows}
    assert ids_by_platform == {"youtube": "vidY", "instagram": "medI", "tiktok": "pubT"}


def test_id_field_falls_back_to_external_id_when_the_platform_field_is_missing():
    """Il caso reconcile(): id piattaforma-specifico assente, external_id presente."""
    registry_data = {"instagram": {"short01.vert": {"external_id": "recuperato123"}}}
    rows, _ = dm.build_fct_publish_event(registry_data, {"short01.vert"})
    assert rows[0]["external_id"] == "recuperato123"


def test_content_not_in_dim_content_is_excluded_and_counted():
    """Le foto standalone Instagram (fuori dal calendario video) non compaiono in
    dim_content — devono essere escluse, mai un'esclusione silenziosa."""
    registry_data = {"instagram": {
        "short01.vert": {"mediaId": "medI"},
        "diario-pallone-scucito.jpg": {"mediaId": "medFoto", "type": "photo"},
    }}
    rows, excluded = dm.build_fct_publish_event(registry_data, {"short01.vert"})
    assert [r["content_key"] for r in rows] == ["short01.vert"]
    assert excluded == 1


def test_privacy_is_carried_raw_never_derived_to_a_boolean():
    registry_data = {"tiktok": {"short01.vert": {"publishId": "p1", "privacy": "SELF_ONLY"}}}
    rows, _ = dm.build_fct_publish_event(registry_data, {"short01.vert"})
    assert rows[0]["privacy"] == "SELF_ONLY"
    assert "is_public" not in rows[0]
