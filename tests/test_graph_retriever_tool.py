"""Unit tests for the graph_retrieve tool."""

import pytest
from tools.graph_retrieve import graph_retrieve, NAME, DESCRIPTION, PARAMETERS, register
from tools.registry import ToolRegistry


def test_graph_retrieve_tool_spec():
    assert NAME == "graph_retrieve"
    assert "Retrieve knowledge graph" in DESCRIPTION
    assert PARAMETERS["type"] == "object"
    assert "request" in PARAMETERS["properties"]


def test_graph_retrieve_string_query():
    res = graph_retrieve(request="User")
    assert isinstance(res, dict)
    assert "status" in res


def test_graph_retrieve_query_alias():
    res = graph_retrieve(query="User")
    assert isinstance(res, dict)
    assert "status" in res


def test_graph_retrieve_cypher_query():
    cypher = "MATCH (n:Entity) RETURN n LIMIT 10"
    res = graph_retrieve(cypher_query=cypher)
    assert isinstance(res, dict)
    assert res.get("query_type") == "cypher"


def test_graph_retrieve_dict_request():
    req_dict = {"type": "node", "label": "Concept", "property_key": "name", "property_value": "AI"}
    res = graph_retrieve(request=req_dict)
    assert isinstance(res, dict)


def test_graph_retrieve_with_label_and_depth():
    res = graph_retrieve(request="EntityName", node_label="Concept", max_depth=2)
    assert isinstance(res, dict)


def test_graph_retrieve_missing_args():
    res = graph_retrieve()
    assert res["status"] == "error"
    assert "Either 'request', 'query', or 'cypher_query' must be provided." in res["error"]


def test_graph_retrieve_register():
    registry = ToolRegistry()
    register(registry)
    assert "graph_retrieve" in registry
    spec = registry.get("graph_retrieve")
    assert spec is not None
    assert spec.name == "graph_retrieve"
