# Visual Analytics for Atomic Fact Annotation

A tool for studying how an annotation **guideline** affects fact annotation. Several annotators (AI models or people) read the same document and break it into small facts; the tool lines those facts up, shows where the annotators disagree as a graph, and uses those disagreements to improve the guideline.

## Run it

The easy way is **Docker Desktop** plus one command. Open a terminal in the project folder and run:

```bash
docker compose up --build
```

The first run takes a few minutes (it downloads and builds everything). Then open **http://localhost:5000** in your browser — the demo document shows up with its facts and a colour-coded graph. Nothing else to install.

To look at the database directly, open **http://localhost:7474**. It asks you to log in: username `neo4j`, password `test12345`.

**Without Docker (Python 3.11):**

```bash
pip install -r requirements.txt
python -m scripts.select_short_docs --n 50   # downloads the demo documents
python -m ui.app                             # then open http://localhost:5000
```

## What it does

1. **Read** — each annotator turns a document into short facts. Every fact is a simple **Subject – Predicate – Object** statement (e.g. *"the Council – decided – that a deficit existed"*), linked back to the exact sentence it came from.
2. **Compare** — the tool matches facts from different annotators that are about the same thing and labels each pair: *agree*, *redundant*, *too coarse / too fine*, or *contradictory*.
3. **Show** — every fact becomes a graph you can click through, coloured by the kind of disagreement, shown next to the source text and a facts table.
4. **Improve** — the disagreements point to where the guideline is unclear, so you can revise it and check whether the new version produces fewer conflicts.

## Limitations

- **The guideline isn't completely free.** It requires every fact to be written as Subject – Predicate – Object *and* tagged with where it appears in the text. This keeps the rest of the tool simple, but you can't use a guideline that asks for facts in a very different shape.
- **Subject – Predicate – Object can't capture everything.** It handles most facts — dates, conditions, and negation are attached as extra details — but it can't really express logic like *"if X then Y"*, *"X or Y"*, or *"all / some X"*. The tool flags those cases instead of forcing them in, and those flags are themselves useful hints for improving the guideline.

## Run your own extraction (optional)

The demo data is already included, so you only need this to re-run the AI extraction yourself. It uses [Ollama](https://ollama.com) to run the models locally.

**If you run the tool in Docker, do this one-time setup so the container can reach Ollama:**

1. Open a terminal and run:

   ```bash
   setx OLLAMA_HOST 0.0.0.0
   ```

2. Quit Ollama from the system tray, then start it again.

   *(macOS / Linux: run `export OLLAMA_HOST=0.0.0.0` before starting Ollama. If you run the tool with Python instead of Docker, you can skip this step.)*

Then download the models:

```bash
ollama pull qwen3.5:4b
ollama pull gemma3:4b
ollama pull phi4-mini
```

Now you can start extraction from the web UI — open the **Background runs** tab at the bottom of the page. (On a 6 GB GPU the models can't all run at once, so each document takes a few minutes.) The command-line steps are in [`doc/docker.md`](doc/docker.md).


Contact: yanzilong5@gmail.com
