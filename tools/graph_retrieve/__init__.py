"""
graph_retrieve tool subpackage.
"""

from typing import Any, Dict, Optional, Union

from graph.retrieve import retrieve_from_graph

NAME = "graph_retrieve"
DESCRIPTION = (
    "Retrieve knowledge graph data, node information, endpoints, classes, "
    "methods, call graphs, documents, or execute Cypher queries on the Neo4j graph database."
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "request": {
            "type": ["string", "object"],
            "description": (
                "Retrieval request or query. Can be a string (search term, entity name, "
                "or Cypher query) or a dictionary specifying query parameters."
            ),
        },
        "node_label": {
            "type": "string",
            "description": "Optional node label filter (e.g., 'Concept', 'Entity', 'Document', 'Code').",
        },
        "cypher_query": {
            "type": "string",
            "description": "Optional explicit Cypher query string to execute against the Neo4j graph.",
        },
        "max_depth": {
            "type": "integer",
            "description": "Optional depth for graph neighborhood retrieval (default is 1).",
        },
    },
    "required": [],
}


def graph_retrieve(
    request: Optional[Union[str, Dict[str, Any]]] = None,
    query: Optional[Union[str, Dict[str, Any]]] = None,
    node_label: Optional[str] = None,
    cypher_query: Optional[str] = None,
    max_depth: Optional[int] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute graph retrieval query against the Neo4j knowledge graph database."""
    req = request if request is not None else query

    if cypher_query:
        query_input: Union[str, Dict[str, Any]] = {"type": "cypher", "cypher": cypher_query}
    elif isinstance(req, dict):
        query_input = dict(req)
        if node_label and "label" not in query_input:
            query_input["label"] = node_label
        if max_depth is not None and "max_depth" not in query_input:
            query_input["max_depth"] = max_depth
    elif isinstance(req, str):
        if node_label or max_depth is not None:
            query_input = {"request": req}
            if node_label:
                query_input["label"] = node_label
            if max_depth is not None:
                query_input["max_depth"] = max_depth
        else:
            query_input = req
    elif req is None:
        return {
            "status": "error",
            "error": "Either 'request', 'query', or 'cypher_query' must be provided.",
        }
    else:
        query_input = str(req)

    return retrieve_from_graph(query_input)


def register(registry) -> None:
    """Register the graph_retrieve tool with the tool registry."""
    registry.register(
        NAME,
        graph_retrieve,
        description=DESCRIPTION,
        parameters=PARAMETERS,
    )
