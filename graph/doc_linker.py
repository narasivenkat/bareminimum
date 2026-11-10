"""
Documentation Linker module.
Analyzes documentation files (Markdown, Text, HTML, etc.) and links them to code files,
classes, methods, and API endpoints based on explicit references and symbol mentions.
"""

import os
import re
from typing import Any, Dict, List, Set


class DocLinker:
    """Links documentation files to corresponding code elements and endpoints."""

    DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".json", ".pdf", ".doc", ".docx"}

    def link_documents(
        self,
        doc_files: List[Dict[str, Any]],
        ast_outputs: List[Dict[str, Any]],
        endpoints: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Link doc files to AST code structures and endpoints.

        :param doc_files: List of dicts with 'path' and 'content'.
        :param ast_outputs: AST parse outputs from ASTGenerator.
        :param endpoints: Extracted endpoints from EndpointRouter.
        :return: Dict containing 'doc_files' metadata and 'links' list.
        """
        processed_docs: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        seen_links: Set[tuple] = set()

        # Build index of code symbols
        classes_index: Dict[str, Dict[str, Any]] = {}
        methods_index: Dict[str, Dict[str, Any]] = {}
        files_index: Dict[str, Dict[str, Any]] = {}

        for ast in ast_outputs:
            file_path = ast.get("file_path", "")
            file_name = os.path.basename(file_path)
            files_index[file_name] = ast
            files_index[file_path] = ast

            for cls in ast.get("classes", []):
                cls_name = cls.get("name")
                if cls_name:
                    classes_index[cls_name] = cls
                    classes_index[cls.get("full_name", cls_name)] = cls

                for method in cls.get("methods", []):
                    m_name = method.get("name")
                    m_full = method.get("full_name", m_name)
                    if m_name:
                        methods_index[m_name] = method
                    if m_full:
                        methods_index[m_full] = method

        # Process each document
        for doc in doc_files:
            doc_path = doc.get("path", "")
            content = doc.get("content", "")
            doc_name = os.path.basename(doc_path)

            doc_meta = {
                "path": doc_path,
                "name": doc_name,
                "file_type": os.path.splitext(doc_path)[1].lower(),
                "content_preview": content[:200] if content else "",
            }
            processed_docs.append(doc_meta)

            if not content:
                continue

            content_lower = content.lower()

            # 1. Match Class references
            for cls_name, cls in classes_index.items():
                if len(cls_name) < 3:
                    continue  # Skip short identifiers to avoid false positives
                # Word boundary match
                pattern = r"\b" + re.escape(cls_name) + r"\b"
                if re.search(pattern, content):
                    target_id = cls.get("full_name", cls_name)
                    link_key = (doc_path, "class", target_id)
                    if link_key not in seen_links:
                        seen_links.add(link_key)
                        links.append({
                            "doc_path": doc_path,
                            "target_type": "class",
                            "target_id": target_id,
                            "target_name": cls_name,
                            "link_type": "DOCUMENTS",
                            "match_reason": f"Document mentions class '{cls_name}'",
                        })

            # 2. Match Method references
            for m_name, m in methods_index.items():
                if len(m_name) < 4:
                    continue
                # Match method name followed by () or word boundary
                pattern = r"\b" + re.escape(m_name) + r"(?:\(\))?\b"
                if re.search(pattern, content):
                    target_id = m.get("full_name", m_name)
                    link_key = (doc_path, "method", target_id)
                    if link_key not in seen_links:
                        seen_links.add(link_key)
                        links.append({
                            "doc_path": doc_path,
                            "target_type": "method",
                            "target_id": target_id,
                            "target_name": m_name,
                            "link_type": "DOCUMENTS",
                            "match_reason": f"Document mentions method '{m_name}'",
                        })

            # 3. Match Code File references
            for f_name, ast in files_index.items():
                if f_name in content:
                    f_path = ast.get("file_path", f_name)
                    link_key = (doc_path, "file", f_path)
                    if link_key not in seen_links:
                        seen_links.add(link_key)
                        links.append({
                            "doc_path": doc_path,
                            "target_type": "file",
                            "target_id": f_path,
                            "target_name": f_name,
                            "link_type": "DOCUMENTS",
                            "match_reason": f"Document explicitly references file '{f_name}'",
                        })

            # 4. Match Endpoint routes
            for ep in endpoints:
                route_path = ep.get("path", "")
                if route_path and route_path != "/" and route_path in content:
                    ep_key = f"{ep.get('http_method')} {route_path}"
                    link_key = (doc_path, "endpoint", ep_key)
                    if link_key not in seen_links:
                        seen_links.add(link_key)
                        links.append({
                            "doc_path": doc_path,
                            "target_type": "endpoint",
                            "target_id": ep_key,
                            "target_name": route_path,
                            "link_type": "DOCUMENTS",
                            "match_reason": f"Document references API endpoint '{ep_key}'",
                        })

        return {
            "doc_files": processed_docs,
            "links": links,
        }
