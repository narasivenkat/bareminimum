import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from graph.ingester import CodeContextIngester, ingest_context


def test_scan_directory_delegation():
    ingester = CodeContextIngester(context_dir=".")
    mock_scan = MagicMock(return_value=([{"path": "a.py"}], [{"path": "b.md"}]))
    ingester.file_scanner.scan_directory = mock_scan

    code_files, doc_files = ingester.scan_directory("/some/dir")

    mock_scan.assert_called_once_with("/some/dir")
    assert code_files == [{"path": "a.py"}]
    assert doc_files == [{"path": "b.md"}]


def test_ingest_without_db_conn(tmp_path):
    context_dir = str(tmp_path / "test_repo")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "main.py", "full_path": "main.py", "content": "def main(): pass", "hash": "h1"},
    ]
    doc_files = [
        {"path": "README.md", "full_path": "README.md", "name": "README.md", "file_type": "md", "content": "# Readme", "hash": "h2"},
    ]

    ingester = CodeContextIngester(context_dir=context_dir)

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value={"file_path": "main.py"}), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value={"edges": []}), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": []}):

        res = ingester.ingest()

    assert res["status"] == "success"
    assert res["counts"]["code_files"] == 1
    assert res["counts"]["doc_files"] == 1
    assert res["counts"]["ast_files"] == 1
    assert res["db_result"] is None


def test_ingest_creates_graph_json_files(tmp_path):
    context_dir = str(tmp_path / "test_graph_repo")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "main.py", "full_path": "main.py", "content": "def main(): pass", "hash": "h1"},
    ]
    doc_files = []

    ast_mock_data = {"file_path": "main.py", "functions": ["main"], "hash": "h1"}
    call_graph_mock_data = {"nodes": ["main"], "edges": [{"caller": "main", "callee": "helper"}]}

    ingester = CodeContextIngester(context_dir=context_dir)

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value=dict(ast_mock_data)), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value=dict(call_graph_mock_data)), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": []}):

        res = ingester.ingest()

    graph_dir = Path(context_dir) / "graph"
    assert graph_dir.exists() and graph_dir.is_dir()

    ast_json_file = graph_dir / "ast.json"
    call_graph_json_file = graph_dir / "call_graph.json"

    assert ast_json_file.exists()
    assert call_graph_json_file.exists()

    with open(ast_json_file, "r", encoding="utf-8") as f:
        loaded_ast = json.load(f)
        assert len(loaded_ast) == 1
        assert loaded_ast[0]["file_path"] == "main.py"

    with open(call_graph_json_file, "r", encoding="utf-8") as f:
        loaded_cg = json.load(f)
        assert loaded_cg == call_graph_mock_data


def test_ingest_db_conn_failed():
    mock_db_conn = MagicMock()
    mock_db_conn.verify_connectivity.return_value = False

    ingester = CodeContextIngester(db_conn=mock_db_conn)
    res = ingester.ingest()

    assert res["status"] == "failed"
    assert "Neo4j database connection failed" in res["error"]


def test_ingest_with_db_conn_success(tmp_path):
    mock_db_conn = MagicMock()
    mock_db_conn.database_exists.return_value = True
    mock_db_conn.verify_connectivity.return_value = True

    context_dir = str(tmp_path / "test_repo_db")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "app.py", "full_path": "app.py", "content": "import sys", "hash": "h1"},
    ]
    doc_files = []

    ingester = CodeContextIngester(context_dir=context_dir, db_conn=mock_db_conn)

    mock_inserter = ingester.neo4j_inserter
    mock_inserter.insert_entities_chunk.return_value = {"files": 1, "ast_nodes": 1}
    mock_inserter.insert_call_graph.return_value = 0
    mock_inserter.insert_doc_links.return_value = 0
    mock_inserter.get_record_counts.return_value = {"nodes": 5, "relationships": 2, "total_records": 7}

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value={"file_path": "app.py"}), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value={"edges": []}), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": []}):

        res = ingester.ingest()

    assert res["status"] == "success"
    assert res["db_result"]["status"] == "success"
    assert res["db_result"]["neo4j_record_counts"] == {"nodes": 5, "relationships": 2, "total_records": 7}


def test_ingest_incremental_skips_unchanged(tmp_path):
    mock_db_conn = MagicMock()
    mock_db_conn.database_exists.return_value = True
    mock_db_conn.verify_connectivity.return_value = True

    context_dir = str(tmp_path / "inc_repo")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "a.py", "full_path": "a.py", "content": "pass", "hash": "hash_a"},
        {"path": "b.py", "full_path": "b.py", "content": "pass", "hash": "hash_b_new"},
    ]
    doc_files = [
        {"path": "doc.md", "full_path": "doc.md", "name": "doc.md", "file_type": "md", "content": "doc", "hash": "hash_doc"},
    ]

    ingester = CodeContextIngester(context_dir=context_dir, db_conn=mock_db_conn)

    mock_inserter = ingester.neo4j_inserter
    mock_inserter.get_stored_file_hashes.return_value = {
        "a.py": "hash_a",        # unchanged -> skip
        "doc.md": "hash_doc",    # unchanged -> skip
    }
    mock_inserter.insert_entities_chunk.return_value = {"files": 1}
    mock_inserter.get_record_counts.return_value = {"nodes": 2, "relationships": 0, "total_records": 2}

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value={"file_path": "b.py"}), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value={"edges": []}), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": []}):

        res = ingester.ingest(incremental=True)

    assert res["status"] == "success"
    assert res["counts"]["code_files"] == 1
    assert res["counts"]["skipped_code_files"] == 1
    assert res["counts"]["doc_files"] == 0
    assert res["counts"]["skipped_doc_files"] == 1


def test_ingest_progress_callback(tmp_path):
    context_dir = str(tmp_path / "cb_repo")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "f1.py", "full_path": "f1.py", "content": "x=1", "hash": "h1"},
        {"path": "f2.py", "full_path": "f2.py", "content": "x=2", "hash": "h2"},
    ]
    doc_files = []

    ingester = CodeContextIngester(context_dir=context_dir)
    callback_mock = MagicMock()

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value={"file_path": "f"}), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value={"edges": []}), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": []}):

        res = ingester.ingest(progress_callback=callback_mock)

    assert callback_mock.call_count == 2
    callback_mock.assert_any_call(1, 2, "f1.py")
    callback_mock.assert_any_call(2, 2, "f2.py")


def test_ingest_step_by_step_record_verification(tmp_path, capsys):
    mock_db_conn = MagicMock()
    mock_db_conn.database_exists.return_value = True
    mock_db_conn.verify_connectivity.return_value = True

    context_dir = str(tmp_path / "step_repo")
    os.makedirs(context_dir, exist_ok=True)

    code_files = [
        {"path": "src/App.java", "full_path": "src/App.java", "content": "class App {}", "hash": "h1"},
    ]
    doc_files = [
        {"path": "docs/guide.md", "full_path": "docs/guide.md", "name": "guide.md", "file_type": "md", "content": "# Guide", "hash": "h2"},
    ]

    ingester = CodeContextIngester(context_dir=context_dir, db_conn=mock_db_conn)

    mock_get_record_counts = MagicMock(side_effect=[
        {"nodes": 10, "relationships": 0, "total_records": 10, "total": 10},  # After code chunk
        {"nodes": 11, "relationships": 0, "total_records": 11, "total": 11},  # After doc chunk
        {"nodes": 11, "relationships": 5, "total_records": 16, "total": 16},  # After call graph
        {"nodes": 11, "relationships": 7, "total_records": 18, "total": 18},  # After doc links
        {"nodes": 11, "relationships": 7, "total_records": 18, "total": 18},  # Final summary
    ])

    with patch.object(ingester, "scan_directory", return_value=(code_files, doc_files)), \
         patch.object(ingester.ast_generator, "parse_file", return_value={"file_path": "src/App.java"}), \
         patch.object(ingester.endpoint_router, "extract_endpoints", return_value=[]), \
         patch.object(ingester.call_graph_generator, "generate", return_value={"edges": [{"caller": "a", "callee": "b"}]}), \
         patch.object(ingester.doc_linker, "link_documents", return_value={"links": [{"doc_path": "docs/guide.md", "target_type": "file", "target_id": "src/App.java"}]}), \
         patch.object(ingester.neo4j_inserter, "insert_entities_chunk", return_value={"files": 1}), \
         patch.object(ingester.neo4j_inserter, "insert_call_graph", return_value=1), \
         patch.object(ingester.neo4j_inserter, "insert_doc_links", return_value=1), \
         patch.object(ingester.neo4j_inserter, "get_record_counts", mock_get_record_counts):

        res = ingester.ingest()

    captured = capsys.readouterr()
    stdout = captured.out

    assert "Neo4j record verification after code chunk insert" in stdout
    assert "Neo4j record verification after document chunk insert" in stdout
    assert "Neo4j record verification after call graph edge insert" in stdout
    assert "Neo4j record verification after document links insert" in stdout
    assert "Ingestion complete. Total records verified in Neo4j DB: 18 (11 nodes, 7 relationships)." in stdout
    assert mock_get_record_counts.call_count == 5


def test_ingest_context_creates_database_if_not_exists(tmp_path):
    mock_db_conn = MagicMock()
    mock_db_conn.database_exists.return_value = False
    mock_db_conn.verify_connectivity.return_value = True

    context_dir = str(tmp_path / "my_ctx_repo")
    os.makedirs(context_dir, exist_ok=True)

    with patch("graph.ingester.CodeContextIngester") as mock_ingester_cls:
        ingest_context(context_dir=context_dir, db_conn=mock_db_conn)

    mock_db_conn.database_exists.assert_called_with("my_ctx_repo")
    mock_db_conn.create_database.assert_called_once_with("my_ctx_repo")
    assert mock_db_conn.database == "my_ctx_repo"
    mock_ingester_cls.assert_called_once_with(context_dir=context_dir, db_conn=mock_db_conn)


def test_ingest_context_uses_existing_database(tmp_path):
    mock_db_conn = MagicMock()
    mock_db_conn.database_exists.return_value = True
    mock_db_conn.verify_connectivity.return_value = True

    context_dir = str(tmp_path / "existing_ctx_repo")
    os.makedirs(context_dir, exist_ok=True)

    with patch("graph.ingester.CodeContextIngester") as mock_ingester_cls:
        ingest_context(context_dir=context_dir, db_conn=mock_db_conn)

    mock_db_conn.database_exists.assert_called_with("existing_ctx_repo")
    mock_db_conn.create_database.assert_not_called()
    assert mock_db_conn.database == "existing_ctx_repo"
    mock_ingester_cls.assert_called_once_with(context_dir=context_dir, db_conn=mock_db_conn)
