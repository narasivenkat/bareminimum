# File-Based Graph Storage vs. Neo4j: Options, Trade-offs, and Recommendations

## Executive Summary

Today, `bareminimum` builds its code-knowledge graph inside a **Neo4j** database. The
ingestion pipeline ([graph/ingester.py](ingestion_pipeline_architecture_guide.md)) parses
source files into ASTs, call graphs, endpoint mappings, and documentation links, then the
inserter ([graph/neo4j_inserter.py](../graph/neo4j_inserter.py)) writes them as **nodes**
(`File`, `Class`, `Method`, `Endpoint`, `Document`, `ASTNode`) and **relationships**
(`DEFINES`, `HAS_METHOD`, `CALLS`, `HANDLED_BY`, `DOCUMENTS`, `CONTAINS_AST`). Retrieval
([graph/retrieve.py](../graph/retrieve.py)) runs Cypher queries against that database.

This document answers:
> *Is it possible to store the graph as a file (or a few files) instead of in Neo4j?
> What are the pros and cons?*

**Short answer:** Yes — it is entirely possible, and for a "bare minimum" harness it can be
attractive. But the right choice depends on **graph size**, **query complexity**, and whether
you need **multi-hop traversals** like the current `CALLS*1..2` queries. Below are the viable
file-based options, each weighed against what this codebase actually does.

---

## 1. What the Graph Must Support (Requirements Baseline)

Before comparing storage formats, it helps to pin down what the current Neo4j usage actually
requires. From [graph/retrieve.py](../graph/retrieve.py), the graph must support:

| Capability | Example in code | Difficulty to replicate in files |
| --- | --- | --- |
| Node lookup by property | `MATCH (c:Class) WHERE c.name CONTAINS $term` | Easy |
| Substring / fuzzy matching | `WHERE f.path CONTAINS $term` | Easy |
| 1-hop relationship traversal | `(f:File)-[:DEFINES]->(c:Class)` | Easy |
| Multi-hop / variable-length traversal | `(m)-[:CALLS*1..2]->(callee:Method)` | **Hard** |
| Reverse traversal (callers) | `(caller:Method)-[:CALLS]->(m)` | Medium |
| Collecting related nodes | `collect(DISTINCT callee)` | Medium |
| Per-repo isolation | database named after repo | Easy |
| Incremental updates via hashes | `get_stored_file_hashes()` | Medium |

The hardest capability to reproduce with plain files is **variable-length traversal**
(`CALLS*1..2` and deeper). Keep that in mind when reading the options below.

---

## 2. File-Based Storage Options

### Option A: Plain JSON / JSONL Graph File(s)

Store the whole graph as one (or a few) JSON documents: an array of nodes and an array of
edges, mirroring the in-memory structures the pipeline already builds.

```json
{
  "nodes": [
    { "id": "File:graph/retrieve.py", "label": "File", "path": "graph/retrieve.py", "hash": "a1b2c3" },
    { "id": "Class:GraphRetriever",   "label": "Class", "name": "GraphRetriever" },
    { "id": "Method:execute_cypher",  "label": "Method", "name": "execute_cypher" }
  ],
  "edges": [
    { "from": "File:graph/retrieve.py", "type": "DEFINES",    "to": "Class:GraphRetriever" },
    { "from": "Class:GraphRetriever",   "type": "HAS_METHOD", "to": "Method:execute_cypher" }
  ]
}
```

Typical layout: one file per repo (`<repo>.graph.json`), or split by concern
(`nodes.jsonl`, `edges.jsonl`, `docs.jsonl`).

**Pros**
- **Zero infrastructure**: No database server, no `NEO4J_USERID`/`NEO4J_PASSWORD`, no
  `bolt://localhost:7687` connection. Removes a whole class of setup/auth failures.
- **Human-readable & diffable**: Fits the "bare minimum" ethos; the graph can live in Git
  next to the code and be code-reviewed.
- **Trivially portable**: Copy a file to share or cache a graph.
- **Simple incremental writes**: The existing hash-tracking logic
  (`get_stored_file_hashes`) maps cleanly onto a JSON `hash` field.

**Cons**
- **No query engine**: You must load the file into memory and implement traversal yourself.
  Multi-hop queries like `CALLS*1..2` become hand-written graph walks (BFS/DFS).
- **Whole-file load**: JSON is read all-or-nothing; a large repo graph must be fully loaded
  into RAM to answer even one query (the same OOM risk the ingestion guide warns about).
- **No concurrency / locking**: Two writers can corrupt the file; Neo4j handles transactions.
- **No indexes**: Property lookups become O(n) scans unless you build in-memory indexes.

**Best when:** small-to-medium repos, single process, graph comfortably fits in memory.

---

### Option B: Embedded Graph Database File (Kùzu / embedded engine)

Use an **embedded** graph database that stores everything in a local file/directory but still
speaks a query language. [Kùzu](https://kuzudb.com/) is the closest drop-in: it is embeddable
(like SQLite), file-based, and supports **Cypher** — including variable-length traversals.

**Pros**
- **Keeps Cypher**: Queries like `(m)-[:CALLS*1..2]->(callee)` work with minimal rewrites,
  so [graph/retrieve.py](../graph/retrieve.py) changes stay small.
- **No server process**: The database is a local folder; no daemon, ports, or credentials.
- **Real indexes & out-of-core execution**: Scales far past what hand-rolled JSON can do
  without loading everything into RAM.
- **Transactional**: Safe concurrent reads, atomic writes.

**Cons**
- **New dependency**: Adds a library (and a compiled engine) to `requirements.txt`.
- **Not purely "a file you can read"**: It's a binary store/directory, not human-diffable.
- **Cypher dialect differences**: Not 100% identical to Neo4j; some queries need tweaks.
- **Less ubiquitous**: Smaller ecosystem/community than Neo4j.

**Best when:** you want to drop the Neo4j server but keep rich graph queries and scale.

---

### Option C: SQLite (Relational Tables Emulating a Graph)

Store nodes and edges in a single-file SQLite database: a `nodes` table and an `edges` table,
with indexes on labels, names, and endpoints.

```sql
CREATE TABLE nodes (id TEXT PRIMARY KEY, label TEXT, name TEXT, path TEXT, hash TEXT);
CREATE TABLE edges (src TEXT, type TEXT, dst TEXT);
CREATE INDEX idx_nodes_label ON nodes(label);
CREATE INDEX idx_edges_src   ON edges(src, type);
```

**Pros**
- **Battery-included**: Python ships `sqlite3` in the stdlib — no new dependency.
- **One file, indexed, transactional, concurrent-read-safe.**
- **Fast property lookups** via SQL indexes; `CONTAINS` becomes `LIKE '%term%'`.
- **Mature and ubiquitous.**

**Cons**
- **No native traversal**: Multi-hop `CALLS*1..2` must be expressed as recursive CTEs
  (`WITH RECURSIVE ...`) or multiple self-joins — more verbose and easy to get wrong.
- **Impedance mismatch**: Graph semantics bolted onto tables; collecting neighbor sets takes
  extra joins/grouping.

**Best when:** you want zero new dependencies, mostly shallow (1–2 hop) queries, and SQL is
acceptable.

---

### Option D: Pickle / GraphML / GEXF via a graph library (e.g. NetworkX)

Build the graph with an in-memory graph library and serialize it to a file (`pickle`,
`GraphML`, or `GEXF`).

**Pros**
- **Rich traversal API**: BFS, DFS, shortest paths, ancestors/descendants come for free —
  directly covers the `CALLS*1..2` style needs in Python.
- **Compact serialization**; GraphML/GEXF are interoperable with graph tools.

**Cons**
- **Full load into memory** to query (same RAM ceiling as JSON).
- **`pickle` is unsafe to load from untrusted sources** (arbitrary code execution) — never
  load a pickle you didn't produce.
- **New dependency** (NetworkX or similar).

**Best when:** you want programmatic graph algorithms in-process and the graph fits in RAM.

---

## 3. Side-by-Side Comparison

| Dimension | Neo4j (current) | JSON/JSONL (A) | Kùzu embedded (B) | SQLite (C) | NetworkX file (D) |
| --- | --- | --- | --- | --- | --- |
| Separate server/daemon | **Yes** | No | No | No | No |
| New dependency | neo4j driver | None | Yes | None (stdlib) | Yes |
| Human-readable file | No | **Yes** | No | No | GraphML: partly |
| Keeps Cypher queries | **Yes** | No | Mostly | No (SQL) | No (Python) |
| Multi-hop traversal | **Native** | Manual code | **Native** | Recursive CTE | **Native API** |
| Scales beyond RAM | **Yes** | No | **Yes** | **Yes** | No |
| Indexed property lookup | **Yes** | Build yourself | **Yes** | **Yes** | Partial |
| Transactions/concurrency | **Yes** | No | Yes | Reads yes | No |
| Setup / ops burden | **Highest** | Lowest | Low | Low | Low |
| Portability (copy to share) | Low | **Highest** | High | High | High |

---

## 4. Impact on This Codebase

Switching from Neo4j to files touches three modules:

1. **[graph/dbconn.py](../graph/dbconn.py)** — the `Neo4jConnection`, credential handling,
   and per-repo database creation would be replaced by a file path (e.g. `./<repo>.graph.json`
   or `./<repo>.kuzu/`). Much of the env-var/auth plumbing disappears.
2. **[graph/neo4j_inserter.py](../graph/neo4j_inserter.py)** — `Neo4jInserter` would become a
   file writer. The hash-tracking contract (`get_stored_file_hashes`) is preserved as a
   `hash` field on each `File`/`Document` node, so **incremental ingestion still works**.
3. **[graph/retrieve.py](../graph/retrieve.py)** — the biggest rewrite. Each Cypher method
   (`get_endpoint_context`, `get_method_context`, `get_file_context`, …) must be reimplemented:
   - With **Kùzu (B)**: near-identical Cypher — least work.
   - With **JSON (A)** or **NetworkX (D)**: hand-written graph walks for `CALLS*1..2`.
   - With **SQLite (C)**: SQL joins + recursive CTEs for deep traversals.

The ingestion architecture itself (bulk vs. incremental, described in
[ingestion_pipeline_architecture_guide.md](ingestion_pipeline_architecture_guide.md)) is
**orthogonal** — it works the same regardless of the storage backend.

---

## 5. Recommendations

| Scenario | Recommended storage |
| --- | --- |
| Smallest footprint, small repos, demos, offline/portable | **Option A — JSON/JSONL** |
| Want to drop the Neo4j server but keep Cypher & scale | **Option B — Kùzu embedded** |
| No new dependencies, mostly shallow queries | **Option C — SQLite** |
| Need in-process graph algorithms, graph fits in RAM | **Option D — NetworkX** |
| Large graphs, deep multi-hop queries, multi-user, CI at scale | **Stay on Neo4j** |

**Pragmatic path for `bareminimum`:** If the goal is to eliminate the Neo4j server dependency
while preserving the existing query shape, **Kùzu (Option B)** is the lowest-friction swap
because it keeps Cypher and multi-hop traversals. If the goal is maximum simplicity and the
graphs are small, **JSON (Option A)** best matches the "bare minimum" philosophy — at the cost
of writing your own traversal logic for the `CALLS*1..2` queries.

---

## 6. Key Takeaways

- **Yes, the graph can be stored as a file (or a few files).** Several formats work: JSON,
  embedded graph DB, SQLite, or a serialized graph-library format.
- The **decisive factor** is whether you need native **multi-hop traversal**; the current
  code relies on `CALLS*1..2`, which is cheap in Neo4j/Kùzu but manual in JSON/SQLite.
- File storage **removes server/auth/ops overhead and improves portability**, but gives up
  Neo4j's **query engine, indexing, out-of-core scaling, and transactions**.
- The **ingestion pipeline and incremental hash tracking are backend-agnostic**, so only the
  connection, insert, and retrieve layers need to change.
