# Esempio: modello dimensionale (Fase 3)

Dati fittizi con la stessa struttura di quelli reali di produzione (mai
pubblicati — vedi `.gitignore`), pensati per esercitare le due regole meno
ovvie di `dimensional_model.py`:

- **`app/data.json`** ha un duplicato deliberato: due item puntano allo stesso
  `file` (`settimana1-day1-rovesciata-demo.tv.mp4`), uno con `fonte` che
  referenzia `ai-content-queue.json` (vince) e uno di backfill (perde) — la
  stessa forma del caso reale trovato durante la revisione del piano.
- **`output/instagram-uploads.json`** ha una foto standalone
  (`diario-foto-esempio.jpg`) che non compare nel calendario video — deve
  restare esclusa da `fct_publish_event`, con un conteggio esplicito, non in
  silenzio.
- Un item con `stato: "Da produrre"` (nessun `file`) verifica che il filtro
  scarti i contenuti non ancora prodotti.

## Come provarlo

Nessuna credenziale GCP necessaria: `--dry-run` costruisce le righe senza mai
importare `google-cloud-bigquery` (vedi `dimensional_model.py`/
`carica_bigquery.py::build_all()`).

Da eseguire dalla root del repo:

```bash
python3 -c "
import carica_bigquery as cb
cb.OUTPUT = 'examples/output'
cb.APP_DATA = 'examples/app/data.json'
cb.STORICO = 'examples/output/metriche-video-storico.json'
cb.FINESTRE = 'examples/output/metriche-finestre-fisse.json'
cb.YT_UPLOADS = 'examples/output/youtube-uploads.json'
cb.IG_UPLOADS = 'examples/output/instagram-uploads.json'
cb.TK_UPLOADS = 'examples/output/tiktok-uploads.json'
tables, stats = cb.build_all()
print(f\"dim_content: {len(tables['dim_content'])} righe, {stats['dropped_content_dupes']} duplicati risolti\")
print(f\"fct_publish_event: {len(tables['fct_publish_event'])} righe, {stats['excluded_publish_events']} escluse\")
"
```

Atteso: `dim_content: 3 righe, 1 duplicati risolti` e
`fct_publish_event: 5 righe, 1 escluse`.

Nota: la prima esecuzione crea `examples/output/publish-state.db` (Registry
importa i JSON legacy in SQLite al primo apri — stesso comportamento di
produzione, vedi `upload_registry.py`). Quel file è generato, non versionato
(`.gitignore`) — cancellalo per ripartire da zero.
