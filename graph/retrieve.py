"""
Graph Database Retriever Module.

Retrieves nodes, relationships, and context from Neo4j DB for a given request.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from graph.dbconn import Neo4jConnection

logger = logging.getLogger(__name__)


def _format_records(res: Any) -> List[Dict[str, Any]]:
    """Helper to format Neo4j query result records into standard Python dictionaries."""
    if res is None:
        return []

    records = getattr(res, "records", None)
    if records is None:
        if isinstance(res, (tuple, list)) and len(res) > 0:
            records = res[0]
        elif hasattr(res, "__iter__") and not isinstance(res, (str, bytes, dict)):
            records = res
        else:
            records = []

    formatted = []
    if records:
        for record in records:
            if hasattr(record, "data"):
                try:
                    formatted.append(record.data())
                    continue
                except Exception:
                    pass
            if isinstance(record, dict):
                formatted.append(record)
            elif hasattr(record, "__getitem__"):
                try:
                    formatted.append(dict(record))
                except Exception:
                    pass
    return formatted


class GraphRetriever:
    """
    Retriever class to fetch context and database records from Neo4j based on requests.
    """

    def __init__(self, connection: Optional[Neo4jConnection] = None) -> None:
        """
        Initialize GraphRetriever with an optional Neo4jConnection instance.
        """
        self.conn = connection or Neo4jConnection()

    def execute_cypher(
        self, query: str, parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute Cypher query safely and return list of dictionary results.
        """
        try:
            res = self.conn.execute_query(query, parameters=parameters)
            return _format_records(res)
        except Exception as e:
            logger.error("Error executing Cypher query: %s", e)
            return []

    def get_endpoint_context(self, path_or_id: str) -> Dict[str, Any]:
        """
        Retrieve context for an endpoint: endpoint info, handler method, call hierarchy, and docs.
        """
        cypher = """
        MATCH (e:Endpoint)
        WHERE e.path CONTAINS $term OR e.id CONTAINS $term
        OPTIONAL MATCH (e)-[:HANDLED_BY]->(m:Method)
        OPTIONAL MATCH (m)-[:CALLS*1..2]->(callee:Method)
        OPTIONAL MATCH (d:Document)-[:DOCUMENTS]->(e)
        OPTIONAL MATCH (d2:Document)-[:DOCUMENTS]->(m)
        RETURN e AS endpoint,
               m AS handler,
               collect(DISTINCT callee) AS called_methods,
               collect(DISTINCT d) + collect(DISTINCT d2) AS documentation
        LIMIT 10
        """
        results = self.execute_cypher(cypher, {"term": path_or_id})
        return {"type": "endpoint", "query_term": path_or_id, "results": results}

    def get_class_context(self, class_name: str) -> Dict[str, Any]:
        """
        Retrieve context for a class: class info, defining file, methods, and docs.
        """
        cypher = """
        MATCH (c:Class)
        WHERE c.name CONTAINS $term OR c.full_name CONTAINS $term
        OPTIONAL MATCH (f:File)-[:DEFINES]->(c)
        OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Method)
        OPTIONAL MATCH (d:Document)-[:DOCUMENTS]->(c)
        RETURN c AS class,
               f AS file,
               collect(DISTINCT m) AS methods,
               collect(DISTINCT d) AS documentation
        LIMIT 10
        """
        results = self.execute_cypher(cypher, {"term": class_name})
        return {"type": "class", "query_term": class_name, "results": results}

    def get_method_context(self, method_name: str) -> Dict[str, Any]:
        """
        Retrieve context for a method: method info, parent class, callers, callees, handling endpoints, and docs.
        """
        cypher = """
        MATCH (m:Method)
        WHERE m.name CONTAINS $term OR m.full_name CONTAINS $term
        OPTIONAL MATCH (c:Class)-[:HAS_METHOD]->(m)
        OPTIONAL MATCH (caller:Method)-[:CALLS]->(m)
        OPTIONAL MATCH (m)-[:CALLS]->(callee:Method)
        OPTIONAL MATCH (e:Endpoint)-[:HANDLED_BY]->(m)
        OPTIONAL MATCH (d:Document)-[:DOCUMENTS]->(m)
        RETURN m AS method,
               c AS class,
               collect(DISTINCT caller) AS callers,
               collect(DISTINCT callee) AS callees,
               collect(DISTINCT e) AS endpoints,
               collect(DISTINCT d) AS documentation
        LIMIT 10
        """
        results = self.execute_cypher(cypher, {"term": method_name})
        return {"type": "method", "query_term": method_name, "results": results}

    def get_file_context(self, file_path: str) -> Dict[str, Any]:
        """
        Retrieve context for a file: file info, defined classes, AST nodes, and docs.
        """
        cypher = """
        MATCH (f:File)
        WHERE f.path CONTAINS $term
        OPTIONAL MATCH (f)-[:DEFINES]->(c:Class)
        OPTIONAL MATCH (f)-[:CONTAINS_AST]->(ast:ASTNode)
        OPTIONAL MATCH (d:Document)-[:DOCUMENTS]->(f)
        RETURN f AS file,
               collect(DISTINCT c) AS classes,
               collect(DISTINCT ast) AS ast_nodes,
               collect(DISTINCT d) AS documentation
        LIMIT 10
        """
        results = self.execute_cypher(cypher, {"term": file_path})
        return {"type": "file", "query_term": file_path, "results": results}

    def get_doc_context(self, doc_term: str) -> Dict[str, Any]:
        """
        Retrieve context for documents: document info and target nodes documented.
        """
        cypher = """
        MATCH (d:Document)
        WHERE d.path CONTAINS $term OR d.name CONTAINS $term
        OPTIONAL MATCH (d)-[:DOCUMENTS]->(target)
        RETURN d AS document,
               collect(DISTINCT target) AS documented_targets
        LIMIT 10
        """
        results = self.execute_cypher(cypher, {"term": doc_term})
        return {"type": "doc", "query_term": doc_term, "results": results}

    def search_nodes(
        self, keyword: str, labels: Optional[List[str]] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        General search across nodes by keyword.
        """
        cypher = """
        MATCH (n)
        WHERE n.name CONTAINS $kw
           OR n.path CONTAINS $kw
           OR n.id CONTAINS $kw
           OR n.full_name CONTAINS $kw
           OR n.text CONTAINS $kw
        RETURN labels(n) AS labels, properties(n) AS properties
        LIMIT $limit
        """
        return self.execute_cypher(cypher, {"kw": keyword, "limit": limit})

    def retrieve(self, request: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Main retrieval method that processes a string or dictionary request and retrieves corresponding output from Neo4j.

        Args:
            request (Union[str, Dict[str, Any]]): Request string or dictionary specifying request type / params.

        Returns:
            Dict[str, Any]: Dict containing status, request, result_type, and retrieved data.
        """
        try:
            if isinstance(request, dict):
                req_type = str(request.get("type", "")).lower()

                if req_type == "cypher" or "cypher" in request:
                    query = request.get("query") or request.get("cypher") or ""
                    params = request.get("parameters") or request.get("params") or {}
                    res = self.execute_cypher(query, params)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "cypher",
                        "data": res,
                    }

                if req_type in ("endpoint", "route") or "endpoint" in request or "path" in request:
                    term = request.get("path") or request.get("endpoint") or request.get("term") or request.get("name") or ""
                    res = self.get_endpoint_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "endpoint",
                        "data": res,
                    }

                if req_type == "class" or "class" in request:
                    term = request.get("class") or request.get("name") or request.get("term") or ""
                    res = self.get_class_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "class",
                        "data": res,
                    }

                if req_type in ("method", "function") or "method" in request:
                    term = request.get("method") or request.get("name") or request.get("term") or ""
                    res = self.get_method_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "method",
                        "data": res,
                    }

                if req_type == "file" or "file" in request:
                    term = request.get("file") or request.get("path") or request.get("term") or ""
                    res = self.get_file_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "file",
                        "data": res,
                    }

                if req_type in ("doc", "document") or "doc" in request:
                    term = request.get("doc") or request.get("name") or request.get("path") or request.get("term") or ""
                    res = self.get_doc_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "doc",
                        "data": res,
                    }

                if req_type == "search" or "search" in request or "keyword" in request:
                    kw = request.get("keyword") or request.get("search") or request.get("query") or ""
                    res = self.search_nodes(kw)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "search",
                        "data": res,
                    }

                # Fallback if dictionary has "query" or "request" field
                if "query" in request or "request" in request:
                    sub_req = request.get("query") or request.get("request")
                    return self.retrieve(sub_req)

            elif isinstance(request, str):
                req_str = request.strip()
                if not req_str:
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "empty",
                        "data": [],
                    }

                # Check if raw Cypher query
                upper_req = req_str.upper()
                if any(upper_req.startswith(kw) for kw in ("MATCH", "OPTIONAL MATCH", "WITH", "RETURN", "CALL", "CREATE", "MERGE")):
                    res = self.execute_cypher(req_str)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "cypher",
                        "data": res,
                    }

                # Keyword-based heuristics on string request
                req_lower = req_str.lower()
                if "endpoint" in req_lower or "route" in req_lower or req_str.startswith("/"):
                    term = req_str.replace("endpoint", "").replace("route", "").replace("get", "").replace("post", "").strip()
                    if not term:
                        term = req_str
                    res = self.get_endpoint_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "endpoint",
                        "data": res,
                    }

                if "class" in req_lower:
                    term = req_str.replace("class", "").strip()
                    res = self.get_class_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "class",
                        "data": res,
                    }

                if "method" in req_lower or "function" in req_lower or req_str.endswith("()"):
                    term = req_str.replace("method", "").replace("function", "").replace("()", "").strip()
                    res = self.get_method_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "method",
                        "data": res,
                    }

                if "file" in req_lower or req_str.endswith((".py", ".java", ".js", ".ts", ".go")):
                    term = req_str.replace("file", "").strip()
                    res = self.get_file_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "file",
                        "data": res,
                    }

                if "doc" in req_lower or "document" in req_lower or req_str.endswith((".md", ".txt")):
                    term = req_str.replace("document", "").replace("doc", "").strip()
                    res = self.get_doc_context(term)
                    return {
                        "status": "success",
                        "request": request,
                        "result_type": "doc",
                        "data": res,
                    }

                # Default fallback search across nodes
                res = self.search_nodes(req_str)
                return {
                    "status": "success",
                    "request": request,
                    "result_type": "search",
                    "data": res,
                }

            return {
                "status": "error",
                "request": request,
                "reason": f"Unsupported request format: {type(request)}",
                "data": [],
            }

        except Exception as e:
            logger.exception("Failed to retrieve graph data for request: %s", request)
            return {
                "status": "error",
                "request": request,
                "error": str(e),
                "data": [],
            }


def retrieve_from_graph(
    request: Union[str, Dict[str, Any]], connection: Optional[Neo4jConnection] = None
) -> Dict[str, Any]:
    """
    Convenience function to retrieve output from graph database for a request.

    Args:
        request (Union[str, Dict[str, Any]]): The request query string or dictionary.
        connection (Optional[Neo4jConnection]): Optional custom Neo4j connection.

    Returns:
        Dict[str, Any]: Dictionary response containing graph retrieval output.
    """
    retriever = GraphRetriever(connection=connection)
    return retriever.retrieve(request)
