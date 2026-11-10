"""Async tools for Orchestrator and Sub-Agents using AutoGen.

Read operations are state-agnostic and read-only, allowing concurrent execution
throttled by an asyncio.Semaphore. Write/Delete operations mutate file state and are
strictly serialized using an asyncio.Lock to prevent race conditions and lost updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any, Dict, Optional

from tools.delete_file import delete_file
from tools.file_search import file_search
from tools.graph_retrieve import graph_retrieve
from tools.list_dir import list_dir
from tools.read_file import read_file
from tools.tool_output_sanitizer import sanitize_tool_output
from tools.write_file import write_file

logger = logging.getLogger(__name__)

# Mutex lock for serializing write tasks across sub-agents / orchestrator
_write_lock = asyncio.Lock()

# Semaphore to cap max concurrent read tasks (prevent rate limits & descriptor exhaustion)
_read_semaphore = asyncio.Semaphore(10)


def get_write_lock() -> asyncio.Lock:
    """Return the global write mutex lock."""
    return _write_lock


def get_read_semaphore() -> asyncio.Semaphore:
    """Return the global read semaphore."""
    return _read_semaphore


async def async_read_file(
    filepath: Annotated[str, "Path of file to read relative to workspace root"],
    start_line: Annotated[Optional[int], "1-based first line to read (optional)"] = None,
    end_line: Annotated[Optional[int], "1-based last line to read inclusive (optional)"] = None,
) -> str:
    """Read file content asynchronously and safely in parallel."""
    async with _read_semaphore:
        logger.info("[Sub-Agent Read] Reading file: %s", filepath)
        try:
            kwargs: Dict[str, Any] = {"path": filepath}
            if start_line is not None:
                kwargs["start_line"] = start_line
            if end_line is not None:
                kwargs["end_line"] = end_line

            result = await asyncio.to_thread(read_file, **kwargs)
            return json.dumps(result, indent=2)
        except Exception as exc:
            logger.warning("[Sub-Agent Read] Failed reading '%s': %s", filepath, exc)
            return json.dumps({"error": str(exc), "filepath": filepath})


async def async_list_dir(
    path: Annotated[str, "Directory path relative to workspace root"] = ".",
) -> str:
    """List directory contents asynchronously and safely in parallel."""
    async with _read_semaphore:
        logger.info("[Sub-Agent Read] Listing directory: %s", path)
        try:
            result = await asyncio.to_thread(list_dir, path=path)
            return json.dumps(result, indent=2)
        except Exception as exc:
            logger.warning("[Sub-Agent Read] Failed listing dir '%s': %s", path, exc)
            return json.dumps({"error": str(exc), "path": path})


async def async_file_search(
    pattern: Annotated[str, "Glob or wildcard search pattern (e.g. '*.py')"],
    path: Annotated[str, "Directory path relative to workspace root"] = ".",
) -> str:
    """Search workspace files asynchronously and safely in parallel."""
    async with _read_semaphore:
        logger.info("[Sub-Agent Read] Searching pattern '%s' in '%s'", pattern, path)
        try:
            result = await asyncio.to_thread(file_search, pattern=pattern, path=path)
            return json.dumps(result, indent=2)
        except Exception as exc:
            logger.warning("[Sub-Agent Read] Failed searching pattern '%s': %s", pattern, exc)
            return json.dumps({"error": str(exc), "pattern": pattern})


async def async_graph_retrieve(
    request: Annotated[
        Optional[str],
        "Retrieval query or search term (entity name, Cypher query, node label, etc.)",
    ] = None,
    node_label: Annotated[Optional[str], "Optional node label filter"] = None,
    cypher_query: Annotated[Optional[str], "Optional Cypher query to run directly"] = None,
    max_depth: Annotated[Optional[int], "Optional depth for neighborhood traversal"] = None,
) -> str:
    """Retrieve knowledge graph data, node details, or execute Cypher queries asynchronously."""
    async with _read_semaphore:
        logger.info("[Sub-Agent Read] Retrieving from graph: request='%s', cypher='%s'", request, cypher_query)
        try:
            result = await asyncio.to_thread(
                graph_retrieve,
                request=request,
                node_label=node_label,
                cypher_query=cypher_query,
                max_depth=max_depth,
            )
            return json.dumps(result, indent=2)
        except Exception as exc:
            logger.warning("[Sub-Agent Read] Failed graph retrieve: %s", exc)
            return json.dumps({"error": str(exc), "request": request})


async def async_write_file(
    filepath: Annotated[str, "Target file path relative to workspace root"],
    content: Annotated[str, "Full file content to write"],
    overwrite: Annotated[bool, "Overwrite the file if it already exists"] = True,
    interactive: Annotated[bool, "Require human approval before writing if True"] = False,
) -> str:
    """Write content to file strictly sequentially using asyncio.Lock."""
    async with _write_lock:
        logger.info("[Orchestrator Write Lock Acquired] Writing file: %s", filepath)
        try:
            result = await asyncio.to_thread(
                write_file,
                path=filepath,
                content=content,
                overwrite=overwrite,
                interactive=interactive,
            )
            logger.info("[Orchestrator Write Success] Finished writing: %s", filepath)
            return json.dumps({"status": "success", "result": result}, indent=2)
        except Exception as exc:
            logger.warning("[Orchestrator Write Failed] File '%s': %s", filepath, exc)
            return json.dumps({"status": "error", "error": str(exc), "filepath": filepath})


async def async_delete_file(
    filepath: Annotated[str, "Target file or directory path relative to workspace root"],
    recursive: Annotated[bool, "Recursively delete directories if True"] = False,
    interactive: Annotated[bool, "Require human approval before deleting if True"] = False,
) -> str:
    """Delete file or directory strictly sequentially using asyncio.Lock."""
    async with _write_lock:
        logger.info("[Orchestrator Write Lock Acquired] Deleting path: %s", filepath)
        try:
            result = await asyncio.to_thread(
                delete_file,
                path=filepath,
                recursive=recursive,
                interactive=interactive,
            )
            logger.info("[Orchestrator Delete Success] Finished deleting: %s", filepath)
            return json.dumps({"status": "success", "result": result}, indent=2)
        except Exception as exc:
            logger.warning("[Orchestrator Delete Failed] Path '%s': %s", filepath, exc)
            return json.dumps({"status": "error", "error": str(exc), "filepath": filepath})


__all__ = [
    "async_read_file",
    "async_list_dir",
    "async_file_search",
    "async_graph_retrieve",
    "async_write_file",
    "async_delete_file",
    "get_write_lock",
    "get_read_semaphore",
]
