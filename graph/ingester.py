import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from graph.ast_generator import ASTGenerator
from graph.call_graph_generator import CallGraphGenerator
from graph.dbconn import Neo4jConnection
from graph.doc_linker import DocLinker
from graph.endpoint_router import EndpointRouter
from graph.file_scanner import FileScanner
from graph.neo4j_inserter import Neo4jInserter

logger = logging.getLogger(__name__)


def ingest_context(
    context_dir: str = ".",
    db_conn: Optional[Neo4jConnection] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    incremental: bool = False,
    batch_size: int = 50,
) -> Dict[str, Any]:
    """Convenience helper function for running context ingestion."""
    context_path = Path(context_dir).resolve()
    context_dir_name = context_path.name

    if db_conn:
        try:
            if hasattr(db_conn, "database_exists") and not db_conn.database_exists(context_dir_name):
                if hasattr(db_conn, "create_database"):
                    db_conn.create_database(context_dir_name)
            if hasattr(db_conn, "database_exists") and db_conn.database_exists(context_dir_name):
                db_conn.database = context_dir_name
        except Exception as e:
            logger.warning(f"Error checking or creating database '{context_dir_name}': {e}")

    ingester = CodeContextIngester(context_dir=context_dir, db_conn=db_conn)
    return ingester.ingest(
        context_dir=context_dir,
        progress_callback=progress_callback,
        incremental=incremental,
        batch_size=batch_size,
    )


class CodeContextIngester:
    """Orchestrates hybrid ingestion pipeline for codebases and documentation."""

    def __init__(
        self,
        context_dir: str = ".",
        db_conn: Optional[Neo4jConnection] = None,
    ):
        self.context_dir = context_dir
        self.db_conn = db_conn
        self.file_scanner = FileScanner()
        self.ast_generator = ASTGenerator()
        self.endpoint_router = EndpointRouter()
        self.call_graph_generator = CallGraphGenerator()
        self.doc_linker = DocLinker()
        self.neo4j_inserter = Neo4jInserter(connection=self.db_conn)

    def scan_directory(
        self, target_dir: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Scan directory for code files and document files.

        Returns:
            Tuple[code_files, doc_files] where each file is a dict with path, content, file_type, hash.
        """
        directory = target_dir or self.context_dir
        return self.file_scanner.scan_directory(directory)

    def _report_progress(
        self,
        current: int,
        total: int,
        file_path: str,
        callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        """Helper to invoke progress callback and print progress safely."""
        pct = (current / total * 100) if total > 0 else 100.0
        msg = f"Ingestion progress: {current}/{total} files ({pct:.1f}%) ingested - {file_path}"
        print(msg)
        logger.info(msg)
        if callback:
            try:
                callback(current, total, file_path)
            except Exception as e:
                logger.warning(f"Error in progress callback: {e}")

    def _get_and_report_db_counts(self, stage_description: str) -> Dict[str, int]:
        """
        Helper method to query Neo4j for current record counts and report to logs/console.
        """
        db_counts = self.neo4j_inserter.get_record_counts()
        if isinstance(db_counts, dict):
            nodes = db_counts.get("nodes", 0)
            rels = db_counts.get("relationships", 0)
            total_recs = db_counts.get("total_records", db_counts.get("total", 0))
        else:
            nodes = rels = total_recs = 0

        msg = f"Neo4j record verification after {stage_description}: {total_recs} total records in DB ({nodes} nodes, {rels} relationships)"
        print(msg)
        logger.info(msg)
        return db_counts if isinstance(db_counts, dict) else {}

    def ingest(
        self,
        context_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        incremental: bool = False,
        batch_size: int = 50,
    ) -> Dict[str, Any]:
        """
        Run recommended hybrid multi-pass ingestion pipeline.

        Phases:
        - Phase 1: Chunked Entity Ingestion (parse ASTs, extract endpoints, batch upsert entities)
        - Phase 2: Global Edge Resolution (Call graph and Doc links resolution)
        - Phase 3: Incremental State Tracking (Skip unchanged files if incremental=True)

        Args:
            context_dir: Target directory path to ingest (defaults to self.context_dir)
            progress_callback: Optional callback(current, total, file_path)
            incremental: If True, skip files whose content hash matches stored DB hash
            batch_size: Chunk size for Phase 1 entity ingestion (default: 50)

        Returns:
            Dict containing ingestion counts, ASTs, call graph, endpoints, doc links, and DB status.
        """
        target_dir = context_dir or self.context_dir
        target_path = Path(target_dir).resolve()
        root_folder_name = target_path.name

        db_connected = False
        if self.db_conn:
            try:
                target_db = root_folder_name
                if hasattr(self.db_conn, "database_exists") and not self.db_conn.database_exists(target_db):
                    if hasattr(self.db_conn, "create_database"):
                        self.db_conn.create_database(target_db)
                if hasattr(self.db_conn, "database_exists") and self.db_conn.database_exists(target_db):
                    self.db_conn.database = target_db
                db_connected = self.db_conn.verify_connectivity()
            except Exception as e:
                logger.warning(f"Neo4j database connection check failed: {e}")
                db_connected = False

        if self.db_conn and not db_connected:
            logger.warning("Neo4j database connection failed. Cannot proceed with ingestion.")
            return {
                "context_dir": target_dir,
                "status": "failed",
                "error": "Neo4j database connection failed",
                "counts": {
                    "code_files": 0,
                    "doc_files": 0,
                    "ast_files": 0,
                    "call_graph_edges": 0,
                    "endpoints": 0,
                    "doc_links": 0,
                    "skipped_code_files": 0,
                    "skipped_doc_files": 0,
                },
                "ast_outputs": [],
                "call_graph": {},
                "endpoints": [],
                "doc_links": {},
                "db_result": None,
            }

        code_files, doc_files = self.scan_directory(target_dir)
        total_files = len(code_files) + len(doc_files)

        print(f"Found {len(code_files)} code file(s) and {len(doc_files)} document file(s) to ingest.")
        logger.info(
            f"Starting hybrid ingestion for directory '{target_dir}': "
            f"{len(code_files)} code, {len(doc_files)} docs (batch_size={batch_size}, incremental={incremental})."
        )

        # Phase 3: Differential / Delta check against Neo4j stored hashes
        stored_hashes = {}
        if incremental and db_connected:
            stored_hashes = self.neo4j_inserter.get_stored_file_hashes()

        code_files_to_process = []
        skipped_code_files = []
        for cf in code_files:
            if incremental and cf["path"] in stored_hashes and stored_hashes[cf["path"]] == cf["hash"]:
                skipped_code_files.append(cf)
            else:
                code_files_to_process.append(cf)

        doc_files_to_process = []
        skipped_doc_files = []
        for df in doc_files:
            if incremental and df["path"] in stored_hashes and stored_hashes[df["path"]] == df["hash"]:
                skipped_doc_files.append(df)
            else:
                doc_files_to_process.append(df)

        if incremental and (skipped_code_files or skipped_doc_files):
            logger.info(
                f"Incremental check: Skipping {len(skipped_code_files)} code files "
                f"and {len(skipped_doc_files)} doc files as unchanged."
            )

        ingested_count = 0

        # Account for skipped code files in progress reporting
        for skipped_f in skipped_code_files:
            ingested_count += 1
            self._report_progress(ingested_count, total_files, skipped_f["path"], progress_callback)

        all_ast_outputs: List[Dict[str, Any]] = []
        all_endpoints: List[Dict[str, Any]] = []
        all_doc_files: List[Dict[str, Any]] = list(doc_files)

        cumulative_inserted = {
            "files": 0,
            "ast_nodes": 0,
            "classes": 0,
            "methods": 0,
            "endpoints": 0,
            "documents": 0,
            "call_edges": 0,
            "doc_links": 0,
        }

        # ------------------------------------------------------------------
        # Phase 1: Chunked Entity Ingestion
        # ------------------------------------------------------------------

        # 1A. Process Code Files in Chunks
        for i in range(0, len(code_files_to_process), batch_size):
            chunk_files = code_files_to_process[i : i + batch_size]
            chunk_ast_outputs = []
            chunk_endpoints = []

            for code_file in chunk_files:
                ingested_count += 1
                self._report_progress(ingested_count, total_files, code_file["path"], progress_callback)

                # Fault Isolation: Wrap file parsing in try-except
                try:
                    ast_data = self.ast_generator.parse_file(
                        file_path=code_file["path"],
                        content=code_file["content"],
                    )
                    ast_data["hash"] = code_file["hash"]
                    chunk_ast_outputs.append(ast_data)
                    all_ast_outputs.append(ast_data)
                except Exception as e:
                    logger.error(f"Error parsing file '{code_file['path']}': {e}")
                    continue

            # Extract endpoints for this chunk
            if chunk_ast_outputs:
                try:
                    eps = self.endpoint_router.extract_endpoints(chunk_ast_outputs)
                    chunk_endpoints.extend(eps)
                    all_endpoints.extend(eps)
                except Exception as e:
                    logger.error(f"Error extracting endpoints for code chunk: {e}")

            # Upsert entity chunk into Neo4j
            if db_connected and (chunk_ast_outputs or chunk_endpoints):
                try:
                    chunk_counts = self.neo4j_inserter.insert_entities_chunk(
                        ast_outputs=chunk_ast_outputs,
                        doc_files=[],
                        endpoints=chunk_endpoints,
                    )
                    for k, v in chunk_counts.items():
                        cumulative_inserted[k] += v

                    # Query Neo4j at each step after insert to check inserted record count
                    self._get_and_report_db_counts(f"code chunk insert ({i + len(chunk_files)}/{len(code_files_to_process)} code files)")
                except Exception as e:
                    logger.error(f"Error inserting entity chunk into Neo4j: {e}")

        # Account for skipped doc files in progress reporting
        for skipped_d in skipped_doc_files:
            ingested_count += 1
            self._report_progress(ingested_count, total_files, skipped_d["path"], progress_callback)

        # 1B. Process Document Files in Chunks
        for i in range(0, len(doc_files_to_process), batch_size):
            chunk_docs = doc_files_to_process[i : i + batch_size]

            for doc_file in chunk_docs:
                ingested_count += 1
                self._report_progress(ingested_count, total_files, doc_file["path"], progress_callback)

            if db_connected and chunk_docs:
                try:
                    doc_chunk_counts = self.neo4j_inserter.insert_entities_chunk(
                        ast_outputs=[],
                        doc_files=chunk_docs,
                        endpoints=[],
                    )
                    for k, v in doc_chunk_counts.items():
                        cumulative_inserted[k] += v

                    # Query Neo4j at each step after insert to check inserted record count
                    self._get_and_report_db_counts(f"document chunk insert ({i + len(chunk_docs)}/{len(doc_files_to_process)} doc files)")
                except Exception as e:
                    logger.error(f"Error inserting doc chunk into Neo4j: {e}")

        # ------------------------------------------------------------------
        # Phase 2: Global Edge Resolution
        # ------------------------------------------------------------------

        # 2A. Global Call Graph Resolution
        call_graph: Dict[str, Any] = {"nodes": [], "edges": []}
        if all_ast_outputs:
            try:
                if hasattr(self.call_graph_generator, "generate"):
                    call_graph = self.call_graph_generator.generate(all_ast_outputs)
                elif hasattr(self.call_graph_generator, "build_call_graph"):
                    call_graph = self.call_graph_generator.build_call_graph(all_ast_outputs)
            except Exception as e:
                logger.error(f"Error building call graph: {e}")

        if db_connected and call_graph.get("edges"):
            try:
                edges_inserted = self.neo4j_inserter.insert_call_graph(call_graph)
                cumulative_inserted["call_edges"] = edges_inserted
                self._get_and_report_db_counts("call graph edge insert")
            except Exception as e:
                logger.error(f"Error inserting call graph edges into Neo4j: {e}")

        # 2B. Global Document Links Resolution
        doc_links: Dict[str, Any] = {"links": []}
        if all_doc_files and (all_ast_outputs or all_endpoints):
            try:
                if hasattr(self.doc_linker, "link_documents"):
                    doc_links = self.doc_linker.link_documents(
                        doc_files=all_doc_files,
                        ast_outputs=all_ast_outputs,
                        endpoints=all_endpoints,
                    )
                elif hasattr(self.doc_linker, "resolve_links"):
                    doc_links = self.doc_linker.resolve_links(
                        doc_files=all_doc_files,
                        ast_outputs=all_ast_outputs,
                        endpoints=all_endpoints,
                    )
            except Exception as e:
                logger.error(f"Error resolving doc links: {e}")

        if db_connected and doc_links.get("links"):
            try:
                links_inserted = self.neo4j_inserter.insert_doc_links(doc_links)
                cumulative_inserted["doc_links"] = links_inserted
                self._get_and_report_db_counts("document links insert")
            except Exception as e:
                logger.error(f"Error inserting document links into Neo4j: {e}")

        # Save AST and Call Graph JSON files in 'graph' directory under context directory
        try:
            graph_dir = Path(target_dir) / "graph"
            graph_dir.mkdir(parents=True, exist_ok=True)

            with open(graph_dir / "ast.json", "w", encoding="utf-8") as f:
                json.dump(all_ast_outputs, f, indent=2)

            with open(graph_dir / "call_graph.json", "w", encoding="utf-8") as f:
                json.dump(call_graph, f, indent=2)

            with open(graph_dir / "ast_outputs.json", "w", encoding="utf-8") as f:
                json.dump(all_ast_outputs, f, indent=2)

            with open(graph_dir / "endpoints.json", "w", encoding="utf-8") as f:
                json.dump(all_endpoints, f, indent=2)

            with open(graph_dir / "doc_links.json", "w", encoding="utf-8") as f:
                json.dump(doc_links, f, indent=2)
        except Exception as e:
            logger.error(f"Error writing graph JSON files under context directory: {e}")

        # Final record count check
        db_result = None
        if db_connected:
            final_db_counts = self._get_and_report_db_counts("full ingestion completion")
            nodes = final_db_counts.get("nodes", 0)
            rels = final_db_counts.get("relationships", 0)
            total_recs = final_db_counts.get("total_records", final_db_counts.get("total", 0))
            completion_msg = f"Ingestion complete. Total records verified in Neo4j DB: {total_recs} ({nodes} nodes, {rels} relationships)."
            print(completion_msg)
            logger.info(completion_msg)
            db_result = {
                "status": "success",
                "neo4j_record_counts": final_db_counts,
            }

        result = {
            "context_dir": target_dir,
            "status": "success",
            "counts": {
                "code_files": len(code_files_to_process),
                "doc_files": len(doc_files_to_process),
                "ast_files": len(all_ast_outputs),
                "call_graph_edges": len(call_graph.get("edges", [])),
                "endpoints": len(all_endpoints),
                "doc_links": len(doc_links.get("links", [])),
                "skipped_code_files": len(skipped_code_files),
                "skipped_doc_files": len(skipped_doc_files),
            },
            "inserted": cumulative_inserted,
            "ast_outputs": all_ast_outputs,
            "call_graph": call_graph,
            "endpoints": all_endpoints,
            "doc_links": doc_links,
            "db_result": db_result,
        }

        logger.info(f"Ingestion complete for '{target_dir}': {result['counts']}")
        return result
