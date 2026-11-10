"""
Neo4j Inserter module.
Inserts parsed code files, AST nodes, classes, methods, call graphs, endpoint routes,
and documentation links into a Neo4j graph database.
Supports chunked entity ingestion, hash tracking, and multi-pass resolution.
"""

import logging
from typing import Any, Dict, List, Optional
from graph.dbconn import Neo4jConnection

logger = logging.getLogger(__name__)


def _extract_records(result: Any) -> List[Any]:
    """Extract list of Record objects/dicts from execute_query result (EagerResult, tuple, or list)."""
    if result is None:
        return []
    if hasattr(result, "records") and getattr(result, "records") is not None:
        return getattr(result, "records")
    if isinstance(result, (list, tuple)):
        if len(result) > 0 and isinstance(result[0], (list, tuple)) and hasattr(result, "summary"):
            return result[0]
        return list(result)
    return []


def _get_first_count(result: Any) -> int:
    """Helper to extract integer count value from first record of execute_query result."""
    records = _extract_records(result)
    if not records:
        return 0
    first = records[0]
    if hasattr(first, "get"):
        val = first.get("count")
        if val is not None:
            return int(val)
    if hasattr(first, "__getitem__"):
        try:
            val = first["count"]
            if val is not None:
                return int(val)
        except (KeyError, IndexError, TypeError):
            pass
        try:
            val = first[0]
            if val is not None:
                return int(val)
        except (IndexError, TypeError):
            pass
    if hasattr(first, "count"):
        val = getattr(first, "count")
        if val is not None:
            return int(val)
    return 0


class Neo4jInserter:
    """Inserts AST, call graph, endpoint, and documentation data into Neo4j."""

    def __init__(self, connection: Optional[Neo4jConnection] = None):
        self.conn = connection or Neo4jConnection()

    def get_stored_file_hashes(self) -> Dict[str, str]:
        """
        Fetch stored content hashes for all File and Document nodes in Neo4j.
        Returns a dict mapping file path to hash digest.
        """
        if not self.conn or not getattr(self.conn, "driver", None):
            return {}

        try:
            query = """
            MATCH (n)
            WHERE (n:File OR n:Document OR n:Doc) AND n.hash IS NOT NULL
            RETURN n.path AS path, n.hash AS hash
            """
            result = self.conn.execute_query(query)
            hashes = {}
            records = _extract_records(result)
            for record in records:
                path = None
                h = None
                if hasattr(record, "get"):
                    path = record.get("path")
                    h = record.get("hash")
                elif hasattr(record, "__getitem__"):
                    try:
                        path = record["path"]
                        h = record["hash"]
                    except Exception:
                        pass
                if not path and hasattr(record, "path"):
                    path = getattr(record, "path", None)
                if not h and hasattr(record, "hash"):
                    h = getattr(record, "hash", None)

                if path and h:
                    hashes[path] = h
            return hashes
        except Exception as e:
            logger.warning(f"Error fetching stored file hashes from Neo4j: {e}")
            return {}

    def get_record_counts(self) -> Dict[str, int]:
        """
        Query Neo4j database directly to count actual records (nodes, relationships, and total records).
        """
        if not self.conn or not getattr(self.conn, "driver", None):
            return {"nodes": 0, "relationships": 0, "total_records": 0, "total": 0}

        try:
            nodes_res = self.conn.execute_query("MATCH (n) RETURN count(n) AS count")
            nodes = _get_first_count(nodes_res)

            rels_res = self.conn.execute_query("MATCH ()-[r]->() RETURN count(r) AS count")
            rels = _get_first_count(rels_res)

            total = nodes + rels
            return {
                "nodes": nodes,
                "relationships": rels,
                "total_records": total,
                "total": total,
            }
        except Exception as e:
            logger.warning(f"Error querying Neo4j record counts: {e}")
            return {"nodes": 0, "relationships": 0, "total_records": 0, "total": 0}

    def insert_entities_chunk(
        self,
        ast_outputs: List[Dict[str, Any]],
        doc_files: List[Dict[str, Any]],
        endpoints: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, int]:
        """
        Phase 1: Batch/chunk insert of entities and intra-file relationships.
        Inserts Files, ASTNodes, Classes, Methods, Endpoints, and Documents in batch.
        """
        if not self.conn or not getattr(self.conn, "driver", None):
            logger.warning("No active Neo4j connection. Skipping chunk insertion.")
            return {"files": 0, "ast_nodes": 0, "classes": 0, "methods": 0, "endpoints": 0, "documents": 0}

        counts = {
            "files": 0,
            "ast_nodes": 0,
            "classes": 0,
            "methods": 0,
            "endpoints": 0,
            "documents": 0,
        }

        # 1. Insert AST File, Class, Method, ASTNode structure
        for ast in ast_outputs:
            file_path = ast.get("file_path", "")
            if not file_path:
                continue

            language = ast.get("language", "")
            package = ast.get("package", "")
            file_hash = ast.get("hash", "")

            # Create/Update File node
            self.conn.execute_query(
                """
                MERGE (f:File {path: $path})
                SET f.language = $language, f.package = $package, f.hash = $hash
                """,
                {"path": file_path, "language": language, "package": package, "hash": file_hash},
            )
            counts["files"] += 1

            # Insert Classes
            classes = ast.get("classes", [])
            for cls in classes:
                cls_name = cls.get("name", "")
                if not cls_name:
                    continue
                cls_id = f"{file_path}::{cls_name}"
                self.conn.execute_query(
                    """
                    MATCH (f:File {path: $file_path})
                    MERGE (c:Class {id: $cls_id})
                    SET c.name = $cls_name, c.package = $package
                    MERGE (f)-[:CONTAINS_CLASS]->(c)
                    """,
                    {
                        "file_path": file_path,
                        "cls_id": cls_id,
                        "cls_name": cls_name,
                        "package": cls.get("package", package),
                    },
                )
                counts["classes"] += 1

            # Insert Methods
            methods = ast.get("methods", [])
            for mtd in methods:
                mtd_name = mtd.get("name", "")
                if not mtd_name:
                    continue
                mtd_id = f"{file_path}::{mtd_name}"
                parent_cls = mtd.get("class", "")
                signature = mtd.get("signature", "")

                if parent_cls:
                    cls_id = f"{file_path}::{parent_cls}"
                    self.conn.execute_query(
                        """
                        MATCH (c:Class {id: $cls_id})
                        MERGE (m:Method {id: $mtd_id})
                        SET m.name = $mtd_name, m.signature = $signature
                        MERGE (c)-[:HAS_METHOD]->(m)
                        """,
                        {
                            "cls_id": cls_id,
                            "mtd_id": mtd_id,
                            "mtd_name": mtd_name,
                            "signature": signature,
                        },
                    )
                else:
                    self.conn.execute_query(
                        """
                        MATCH (f:File {path: $file_path})
                        MERGE (m:Method {id: $mtd_id})
                        SET m.name = $mtd_name, m.signature = $signature
                        MERGE (f)-[:HAS_METHOD]->(m)
                        """,
                        {
                            "file_path": file_path,
                            "mtd_id": mtd_id,
                            "mtd_name": mtd_name,
                            "signature": signature,
                        },
                    )
                counts["methods"] += 1

            # Insert AST Nodes (generic syntax elements)
            ast_nodes = ast.get("ast_nodes", [])
            for node in ast_nodes:
                node_type = node.get("type", "ASTNode")
                node_name = node.get("name", "")
                node_id = f"{file_path}::{node_type}::{node_name}"
                self.conn.execute_query(
                    """
                    MATCH (f:File {path: $file_path})
                    MERGE (a:ASTNode {id: $node_id})
                    SET a.type = $node_type, a.name = $node_name
                    MERGE (f)-[:HAS_AST_NODE]->(a)
                    """,
                    {
                        "file_path": file_path,
                        "node_id": node_id,
                        "node_type": node_type,
                        "node_name": node_name,
                    },
                )
                counts["ast_nodes"] += 1

        # 2. Insert Endpoints
        if endpoints:
            for ep in endpoints:
                ep_id = f"{ep.get('http_method', 'GET')}::{ep.get('path', '')}"
                handler = ep.get("handler", "")
                ep_file = ep.get("file_path", "")
                self.conn.execute_query(
                    """
                    MERGE (e:Endpoint {id: $ep_id})
                    SET e.http_method = $method, e.path = $path, e.handler = $handler
                    """,
                    {
                        "ep_id": ep_id,
                        "method": ep.get("http_method", "GET"),
                        "path": ep.get("path", ""),
                        "handler": handler,
                    },
                )
                if ep_file:
                    self.conn.execute_query(
                        """
                        MATCH (f:File {path: $file_path}), (e:Endpoint {id: $ep_id})
                        MERGE (f)-[:DEFINES_ENDPOINT]->(e)
                        """,
                        {"file_path": ep_file, "ep_id": ep_id},
                    )
                counts["endpoints"] += 1

        # 3. Insert Document Nodes
        for doc in doc_files:
            doc_path = doc.get("path", "")
            doc_hash = doc.get("hash", "")
            self.conn.execute_query(
                """
                MERGE (d:Document {path: $path})
                SET d.name = $name, d.file_type = $file_type, d.hash = $hash
                """,
                {
                    "path": doc_path,
                    "name": doc.get("name", ""),
                    "file_type": doc.get("file_type", ""),
                    "hash": doc_hash,
                },
            )
            counts["documents"] += 1

        return counts

    def insert_call_graph(self, call_graph: Dict[str, Any]) -> int:
        """
        Phase 2: Insert inter-file and method call graph relationships.
        Creates CALLS relationships between methods/functions.
        """
        if not self.conn or not getattr(self.conn, "driver", None):
            logger.warning("No active Neo4j connection. Skipping call graph insertion.")
            return 0

        edges = call_graph.get("edges", [])
        inserted_count = 0

        for edge in edges:
            caller = edge.get("caller", "")
            callee = edge.get("callee", "")
            caller_file = edge.get("caller_file", "")
            callee_file = edge.get("callee_file", "")

            if not caller or not callee:
                continue

            caller_id = f"{caller_file}::{caller}" if caller_file else caller
            callee_id = f"{callee_file}::{callee}" if callee_file else callee

            # Link methods/functions via CALLS
            self.conn.execute_query(
                """
                MERGE (c1:Method {id: $caller_id})
                MERGE (c2:Method {id: $callee_id})
                MERGE (c1)-[r:CALLS]->(c2)
                """,
                {"caller_id": caller_id, "callee_id": callee_id},
            )
            inserted_count += 1

        return inserted_count

    def insert_doc_links(self, doc_links: Dict[str, Any]) -> int:
        """
        Phase 3: Insert documentation link relationships.
        Connects Document nodes to File, Class, Method, or Endpoint nodes.
        """
        if not self.conn or not getattr(self.conn, "driver", None):
            logger.warning("No active Neo4j connection. Skipping document link insertion.")
            return 0

        links = doc_links.get("links", [])
        inserted_count = 0

        for link in links:
            doc_path = link.get("doc_path", "")
            target_type = link.get("target_type", "")  # file, class, method, endpoint
            target_id = link.get("target_id", "")
            rel_type = link.get("relationship", "REFERENCES")

            if not doc_path or not target_id:
                continue

            if target_type.lower() == "file":
                self.conn.execute_query(
                    f"""
                    MATCH (d:Document {{path: $doc_path}})
                    MATCH (f:File {{path: $target_id}})
                    MERGE (d)-[:{rel_type}]->(f)
                    """,
                    {"doc_path": doc_path, "target_id": target_id},
                )
            elif target_type.lower() == "class":
                self.conn.execute_query(
                    f"""
                    MATCH (d:Document {{path: $doc_path}})
                    MATCH (c:Class {{id: $target_id}})
                    MERGE (d)-[:{rel_type}]->(c)
                    """,
                    {"doc_path": doc_path, "target_id": target_id},
                )
            elif target_type.lower() == "method":
                self.conn.execute_query(
                    f"""
                    MATCH (d:Document {{path: $doc_path}})
                    MATCH (m:Method {{id: $target_id}})
                    MERGE (d)-[:{rel_type}]->(m)
                    """,
                    {"doc_path": doc_path, "target_id": target_id},
                )
            elif target_type.lower() == "endpoint":
                self.conn.execute_query(
                    f"""
                    MATCH (d:Document {{path: $doc_path}})
                    MATCH (e:Endpoint {{id: $target_id}})
                    MERGE (d)-[:{rel_type}]->(e)
                    """,
                    {"doc_path": doc_path, "target_id": target_id},
                )

            inserted_count += 1

        return inserted_count
