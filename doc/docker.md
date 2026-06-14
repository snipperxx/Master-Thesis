# Run with Docker (app + Neo4j; Ollama stays native)

`docker compose up` builds the Flask app image and runs it next to Neo4j.
**Ollama is NOT containerized** — keep it on the native Desktop app (it owns the
GPU); the container reaches it over `host.docker.internal:11434`.

## One-time
1. Install Docker Desktop.
2. *(only to run extraction from the container)* Make native Ollama listen beyond
   localhost: **Windows** `setx OLLAMA_HOST 0.0.0.0` (or System Settings ->
   Environment Variables) then quit Ollama from the tray and reopen it; **macOS/Linux**
   `export OLLAMA_HOST=0.0.0.0` before `ollama serve`. Skip it for just viewing the UI / Neo4j.
3. (optional) `cp .env.example .env` and set your own `NEO4J_PASSWORD`.

## Up
```powershell
docker compose up --build      # first build pulls torch + sentence-transformers (a few min)
```
- UI:            http://localhost:5000
- Neo4j Browser: http://localhost:7474   (neo4j / your NEO4J_PASSWORD)

The app waits for Neo4j's healthcheck before it starts.

## Load the KG

> Auto-synced: with `NEO4J_AUTOSYNC=1` (set in compose) the graph is pushed to Neo4j after
> every Phase-2 / pipeline run — you normally don't run this by hand. The command below is for a
> one-off / initial load, run **inside the app container** (it can reach the neo4j service):
```powershell
docker compose exec app python -m scripts.run_neo4j_export --in data/conflicts/train-000000.json --wipe
docker compose exec app python -m scripts.run_neo4j_export --in data/conflicts/train-000000__v2.json --wipe
```

## Wiring
| From | To       | Address |
|------|----------|---------|
| app  | Neo4j    | `bolt://neo4j:7687`  (NEO4J_URI) |
| app  | Ollama   | `http://host.docker.internal:11434`  (OLLAMA_URL) |
| you  | UI       | localhost:5000 |
| you  | Browser  | localhost:7474 |

`OLLAMA_URL` and `NEO4J_URI` fall back to `localhost` when unset, so running the
app natively in conda (`python -m ui.app`) still works unchanged — no code is
locked to Docker.

## Notes
- GPU: only native Ollama touches the GPU; app + Neo4j are CPU-only, so nothing
  competes for your 6 GB VRAM.
- The repo is bind-mounted (`.:/app`), so code edits apply on `docker compose
  restart app`; `data/` (facts, conflicts, hf_cache, neo4j) persists on the host.
- Phase-1 still needs models pulled in native Ollama (`ollama pull qwen3.5:4b`).

## Command-line pipeline (advanced)

Run these inside the container (`docker compose exec app <cmd>`) or in your Python env:

```powershell
# Phase-1 — extract facts with one model for one document
python -m src.extractor extract --model qwen3.5:4b --doc data/parsed/train-000000.json --guideline v1

# Phase-2 — align annotators + detect conflicts; writes data/conflicts/<doc>.json
# (auto-syncs to Neo4j when NEO4J_AUTOSYNC=1)
python -m scripts.run_phase2 --doc train-000000 --skip-layer2

# Tests
python -m pytest tests/
```
