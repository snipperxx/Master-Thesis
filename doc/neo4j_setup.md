# Neo4j setup (Phase-3 KG backend)

Two separate pieces — don't conflate them:

| Piece | What it is | Where it goes |
|-------|------------|---------------|
| `neo4j` **driver** | Python client (Bolt) | your conda/venv env |
| Neo4j **server** | Java database (Browser :7474, Bolt :7687) | Docker **or** Neo4j Desktop — *not* conda |

`conda install neo4j` only gets you the driver; it will not start a database.
That is why `localhost:7474` refused the connection.

---

## 1. Driver — into your conda env

```powershell
conda activate <your-env>
conda install -c conda-forge neo4j-python-driver   # provides `import neo4j`
# or simply:  pip install neo4j   (already pinned in requirements.txt)
```

## 2. Server — pick ONE (recommended: Docker, matches PROJECT_STATE)

### Option A — Docker  (needs Docker Desktop)
PowerShell, run from the repo root:

```powershell
docker run -d --name neo4j-thesis `
  -p 7474:7474 -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/test12345 `
  -e 'NEO4J_PLUGINS=["apoc"]' `
  -v "${PWD}\data\neo4j:/data" `
  neo4j:latest
```

- Pick your own password (>= 8 chars) in place of `test12345`.
- `NEO4J_PLUGINS=["apoc"]` auto-installs the version-matched APOC. APOC is
  **optional** — rules R1-R5 are plain Cypher; APOC is only needed if you later
  add live triggers. Drop that line to skip it.
- Browser opens at http://localhost:7474 (log in neo4j / your password).

### Option B — Neo4j Desktop  (GUI, no Docker)
Download from neo4j.com/download -> create a Local DBMS -> set a password ->
(optional) install APOC from the plugin tab -> **Start**. Browser is at :7474.

## 3. Load train-000000

Server up, then from the repo root in the env that has the driver:

```powershell
$env:NEO4J_URI="bolt://localhost:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="test12345"
python -m scripts.run_neo4j_export --in data/conflicts/train-000000.json --wipe
# v2 too:
python -m scripts.run_neo4j_export --in data/conflicts/train-000000__v2.json --wipe
```

Expected (v1): documents 1, annotators 3, entities 42, facts 89, spans 32,
aligned_edges 72.

## 4. Verify in Neo4j Browser (http://localhost:7474)

```cypher
// reified statement + its entities + provenance
MATCH (f:Fact)-[:SUBJECT]->(s:Entity), (f)-[:OBJECT]->(o:Entity)
RETURN f, s, o LIMIT 25;
```

Then paste `cypher/00_schema.cypher`, `cypher/10_rules_conflict.cypher`,
`cypher/20_guideline_patterns.cypher` to materialize conflict edges and run the
guideline-refinement queries.

> Sandbox note: the load must run on **your** machine — the assistant's sandbox
> cannot reach your local Bolt port. Once the server is up, ping me and I can
> drive the load + rule queries through Neo4j Browser via the Chrome extension.
