"""
File Scanner module.
Recursively walks a directory and classifies files into code files and document files,
reading their content and computing a stable content hash for incremental ingestion.
"""

import hashlib
import logging
import os
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class FileScanner:
    """Scans a directory tree and separates code files from documentation files."""

    # Source code file extensions treated as "code files".
    CODE_EXTENSIONS = {
        ".java", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".php",
        ".cs", ".c", ".h", ".cpp", ".cc", ".hpp", ".kt", ".kts", ".scala",
        ".rs", ".swift", ".m", ".mm", ".groovy", ".sql", ".sh", ".bat",
    }

    # Documentation file extensions treated as "doc files".
    DOC_EXTENSIONS = {
        ".md", ".markdown", ".txt", ".rst", ".html", ".htm",
        ".json", ".yaml", ".yml", ".xml", ".pdf", ".doc", ".docx",
    }

    # Directories skipped during traversal.
    IGNORED_DIRS = {
        ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
        "node_modules", "venv", ".venv", "env", ".env", "dist", "build",
        "target", "out", "bin", "obj", ".idea", ".vscode", ".tox", "site-packages",
    }

    def __init__(self):
        self.code_extensions = set(self.CODE_EXTENSIONS)
        self.doc_extensions = set(self.DOC_EXTENSIONS)
        self.ignored_dirs = set(self.IGNORED_DIRS)

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute a stable SHA-256 hash of the file content."""
        return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()

    def _read_file(self, full_path: str) -> str:
        """Read a file's text content, tolerating encoding issues."""
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def _make_entry(self, root: str, full_path: str, ext: str) -> Dict[str, Any]:
        """Build a file metadata dict shared by code and doc files."""
        rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
        content = self._read_file(full_path)
        return {
            "path": rel_path,
            "full_path": full_path,
            "name": os.path.basename(full_path),
            "file_type": ext.lstrip("."),
            "content": content,
            "hash": self._compute_hash(content),
        }

    def scan_directory(
        self, directory: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Recursively scan ``directory`` for code and documentation files.

        Returns:
            Tuple[code_files, doc_files] where each file is a dict with
            path, full_path, name, file_type, content, and hash.
        """
        code_files: List[Dict[str, Any]] = []
        doc_files: List[Dict[str, Any]] = []

        root = os.path.abspath(directory)
        if not os.path.isdir(root):
            logger.warning(f"Scan target '{directory}' is not a directory; nothing to scan.")
            return code_files, doc_files

        for current_dir, dirnames, filenames in os.walk(root):
            # Prune ignored directories in-place so os.walk does not descend into them.
            dirnames[:] = [
                d for d in dirnames
                if d not in self.ignored_dirs and not d.startswith(".")
            ]

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in self.code_extensions and ext not in self.doc_extensions:
                    continue

                full_path = os.path.join(current_dir, filename)
                try:
                    entry = self._make_entry(root, full_path, ext)
                except Exception as e:
                    logger.error(f"Error reading file '{full_path}': {e}")
                    continue

                if ext in self.code_extensions:
                    code_files.append(entry)
                else:
                    doc_files.append(entry)

        return code_files, doc_files
