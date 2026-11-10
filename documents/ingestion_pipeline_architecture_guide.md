# Ingestion Pipeline Architecture Guide: Bulk Monolithic vs. Incremental Step-by-Step Ingestion

## Executive Summary

When constructing a **Graph RAG Knowledge Base** containing Abstract Syntax Trees (ASTs), Call Graphs, Endpoint Mappings, and Documentation Links in Neo4j, architectural design choices dictate system scalability, memory footprint, ingestion speed, and query availability.

This document answers the core ingestion design question:
> *Should all ASTs, Call Graphs, and Documentation Links be generated and saved in Neo4j together in a single bulk operation, or should processing occur incrementally in steps (per set of documents/code files)? What is the best approach?*

Furthermore, this guide provides concrete structural examples of how **ASTs**, **Call Graphs**, and **Documentation Links** are represented in memory (JSON) and inside Neo4j (Nodes, Relationships, and Cypher syntax).

---

## 1. Comparing Ingestion Architectural Approaches

### Approach A: Monolithic Bulk Ingestion (All-at-Once)

In a monolithic bulk ingestion pipeline:
1. **Full Parse Phase**: Parsers process 100% of codebase files into memory ASTs simultaneously.
2. **Full Call Graph Resolution**: A single global analyzer scans all ASTs to resolve method calls across the entire repo.
3. **Full Doc Linker Resolution**: Document matchers scan all Markdown/HTML files against all discovered symbols and endpoints.
4. **Single Bulk Database Insert**: All nodes (`File`, `Class`, `Method`, `Doc`, `Endpoint`) and relationships (`CONTAINS`, `HAS_METHOD`, `CALLS`, `DOCUMENTS`) are committed to Neo4j in a single transaction or session batch.

#### Pros
- **Global Resolution Simplicity**: Every symbol is available in memory when computing cross-file call graphs and documentation links, eliminating unresolved target pointers.
- **Straightforward Pipeline Code**: Requires no state tracking, placeholder management, or multi-pass database reconciliation logic.

#### Cons
- **Excessive Memory Usage**: Holding ASTs, full-repo call graphs, and documentation link matrices in RAM simultaneously causes OOM (Out Of Memory) crashes on large repositories.
- **High Latency To First Query**: Zero database nodes are queryable until 100% of parsing, graph building, and database insertion completes.
- **Fragile Fault Tolerance**: A parsing error, malformed doc string, or timeout in line 10,000 aborts or corrupts the entire ingestion process.
- **Unscalable for Delta/CI Updates**: Changing a single source file requires re-running the entire repository ingestion pipeline.

---

### Approach B: Pure Single-Document Ingestion (Step-by-Step Per File)

In a pure single-document/file ingestion pipeline:
1. Process **File A**: Parse AST $\rightarrow$ Insert AST nodes to Neo4j $\rightarrow$ Attempt to write Call edges and Doc links for File A immediately.
2. Repeat for **File B**, **File C**, and so forth.

#### Pros
- **Minimal Memory Overhead**: Only one file or document is held in memory at any given time.
- **Immediate Partial Availability**: Data for File A is queryable in Neo4j immediately after File A finishes.

#### Cons
- **Dangling & Unresolved References**: If File A calls a method defined in File Z (which hasn't been ingested yet), the `CALLS` relationship cannot be created immediately unless placeholder nodes or multi-pass passes are introduced.
- **Excessive Database Round-Trips**: High Neo4j transaction overhead when performing thousands of tiny single-item writes.

---

### Approach C: The Recommended / Best Approach — Hybrid Incremental Multi-Pass Ingestion

The **Best Approach** combines the performance and scalability of step-by-step processing with the integrity of global reference resolution through a **Two-Pass / Chunked Incremental Pipeline**.

```
[ Phase 1: Chunked Entity Ingestion ]
Code / Doc Files -> Split into Chunks (e.g., 50 files/chunk)
   └─> Parse ASTs & Extracted Metadata
   └─> Batch Upsert Nodes (:File, :Class, :Method, :Endpoint, :Doc) into Neo4j
   └─> Create Intra-File Hierarchy Relationships (:File)-[:CONTAINS]->(:Class)-[:HAS_METHOD]->(:Method)

[ Phase 2: Call Graph & Doc Link Resolution ]
Generate Call Edges & Doc Links against Symbol Table or Neo4j Index
   └─> Insert Cross-Entity Edges (:Method)-[:CALLS]->(:Method)
   └─> Insert Doc-to-Code Edges (:Doc)-[:DOCUMENTS]->(:Class|:Endpoint)

[ Phase 3: Differential / Delta Updates (CI/CD) ]
On git diff / commit:
   └─> Re-ingest ONLY modified/added files
   └─> Delete outdated local edges & re-link updated targets
```

#### Why This Is the Best Approach
1. **Scalability & Memory Safety**: Chunking files (e.g., 50–100 files per batch) caps peak RAM consumption while maintaining high batch-insert throughput.
2. **Fault Isolation**: Parsing failure in a corrupted file logs a non-fatal warning and skips that chunk without losing already-ingested code.
3. **High Query Availability**: Nodes become queryable progressively as chunks land in Neo4j.
4. **Seamless Delta Updates**: Supports continuous ingestion (re-indexing only modified files during PR checks or live developer sessions).

---

## 2. In-Depth Examples: ASTs, Call Graphs, and Docu Links

Below are concrete examples showing how each component is represented in:
1. **Source Input**
2. **In-Memory Intermediate Representation (JSON)**
3. **Neo4j Property Graph (Cypher Statements & Structural Model)**

---

### A. Abstract Syntax Trees (ASTs)

#### 1. Source Code Example (`services/order_service.py`)
```python
class OrderService:
    def checkout(self, order_id: str) -> bool:
        """Process order checkout."""
        return True

    def validate(self, order_id: str) -> bool:
        """Validate order data."""
        return True
```

#### 2. Intermediate JSON Representation (AST Generator)
```json
{
  "file_path": "services/order_service.py",
  "classes": [
    {
      "name": "OrderService",
      "methods": [
        {
          "name": "checkout",
          "args": ["self", "order_id"],
          "line": 2,
          "docstring": "Process order checkout."
        },
        {
          "name": "validate",
          "args": ["self", "order_id"],
          "line": 6,
          "docstring": "Validate order data."
        }
      ]
    }
  ],
  "functions": []
}
```

#### 3. Neo4j Cypher Representation
```cypher
// Create File Node
MERGE (f:File {path: 'services/order_service.py'})

// Create Class Node & Relationship
MERGE (c:Class {name: 'OrderService', file: 'services/order_service.py'})
MERGE (f)-[:CONTAINS]->(c)

// Create Method Nodes & Relationships
MERGE (m1:Method {name: 'checkout', file: 'services/order_service.py', class: 'OrderService', line: 2})
SET m1.docstring = 'Process order checkout.'
MERGE (c)-[:HAS_METHOD]->(m1)

MERGE (m2:Method {name: 'validate', file: 'services/order_service.py', class: 'OrderService', line: 6})
SET m2.docstring = 'Validate order data.'
MERGE (c)-[:HAS_METHOD]->(m2)
```

---

### B. Call Graphs

#### 1. Source Interaction (`OrderService` calls `PaymentGateway`)
```python
# inside services/order_service.py
class OrderService:
    def checkout(self, order_id: str) -> bool:
        gateway = PaymentGateway()
        return gateway.charge(order_id)
```

#### 2. Intermediate JSON Representation (Call Graph Generator)
```json
[
  {
    "caller_file": "services/order_service.py",
    "caller_class": "OrderService",
    "caller_method": "checkout",
    "callee_class": "PaymentGateway",
    "callee_method": "charge",
    "line": 4
  }
]
```

#### 3. Neo4j Cypher Representation
```cypher
// Link Caller Method to Callee Method
MATCH (caller:Method {name: 'checkout', class: 'OrderService', file: 'services/order_service.py'})
MATCH (callee:Method {name: 'charge', class: 'PaymentGateway'})
MERGE (caller)-[r:CALLS {line: 4}]->(callee)
```

*Note on Unresolved Symbols in Step-by-Step Processing:*
If `PaymentGateway.charge` is not yet ingested, a placeholder target node can be created:
```cypher
MERGE (callee:Method {name: 'charge', class: 'PaymentGateway', unresolved: true})
MERGE (caller)-[:CALLS {line: 4}]->(callee)
```
When `payment_gateway.py` is later ingested, the `unresolved: true` flag is replaced with actual metadata.

---

### C. Documentation Links (Docu Links)

#### 1. Documentation Source (`docs/checkout_guide.md`)
```markdown
# Checkout API Guide
The `OrderService` class provides payment processing capability. 
When calling `/api/v1/checkout`, it invokes `OrderService.checkout`.
```

#### 2. Intermediate JSON Representation (Doc Linker)
```json
[
  {
    "doc_file": "docs/checkout_guide.md",
    "target_type": "class",
    "target_name": "OrderService",
    "confidence": "high",
    "matched_mention": "OrderService"
  },
  {
    "doc_file": "docs/checkout_guide.md",
    "target_type": "endpoint",
    "target_name": "/api/v1/checkout",
    "confidence": "high",
    "matched_mention": "/api/v1/checkout"
  }
]
```

#### 3. Neo4j Cypher Representation
```cypher
// Create Doc Node
MERGE (d:Doc {path: 'docs/checkout_guide.md'})

// Link Doc to Class
MATCH (c:Class {name: 'OrderService'})
MERGE (d)-[:DOCUMENTS {confidence: 'high'}]->(c)

// Link Doc to Endpoint
MATCH (e:Endpoint {path: '/api/v1/checkout'})
MERGE (d)-[:EXPLAINS_ENDPOINT {confidence: 'high'}]->(e)
```

---

## 3. Recommended Ingestion Pipeline Matrix

| Feature / Metric | Monolithic Bulk Ingestion | Incremental Step-by-Step | **Recommended Hybrid Pipeline** |
| :--- | :--- | :--- | :--- |
| **Batch Size** | 100% of Repository | 1 File | **50–100 Files per Chunk** |
| **Memory Consumption** | High ($O(N)$ full codebase) | Minimal ($O(1)$ single file) | **Bounded ($O(B)$ batch size)** |
| **Initial Query Latency** | High (Wait for full job) | Lowest (Per file) | **Low (Per batch)** |
| **Cross-Reference Integrity** | Instant | Low (Requires placeholders) | **High (2-Pass Reconciliation)** |
| **CI/CD Delta Ingestion Support** | Non-existent | Complex | **Optimal (Re-ingest diff only)** |

---

## 4. Implementation Guidelines for the Engineering Team

1. **Implement 2-Pass Chunked Ingestion**:
   - Parse and write AST nodes (`File`, `Class`, `Method`, `Doc`, `Endpoint`) in chunks of 50 files.
   - Run a single light call graph and doc-linking pass across the populated Neo4j database.
2. **Use Cypher `MERGE` and Unresolved Stubs**:
   - Ensure relationships can gracefully target stub nodes if references span across un-ingested batches.
3. **Store Ingestion Checkpoints**:
   - Record file hash state in Neo4j (`(:File {path: '...', hash: 'a1b2c3...'})`) to bypass un-modified files during subsequent ingestion runs.
