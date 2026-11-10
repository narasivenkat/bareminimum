"""
AST Generator module using tree-sitter (and tree-sitter-java) to parse source code files
into structured semantic models (packages, imports, classes, methods, annotations, and method calls)
without producing bloated fine-grained AST tokens.
"""

import os
import re
from typing import Any, Dict, List, Optional

# Attempt to load tree-sitter and tree-sitter-java
HAS_TREE_SITTER = False
_JAVA_PARSER = None

try:
    import tree_sitter
    import tree_sitter_java as tsjava

    try:
        if hasattr(tsjava, "language"):
            _JAVA_LANG = tree_sitter.Language(tsjava.language())
        elif hasattr(tsjava, "language_path"):
            _JAVA_LANG = tree_sitter.Language(tsjava.language_path(), "java")
        else:
            _JAVA_LANG = None

        if _JAVA_LANG is not None:
            if hasattr(tree_sitter, "Parser"):
                _JAVA_PARSER = tree_sitter.Parser(_JAVA_LANG)
            HAS_TREE_SITTER = True
    except Exception:
        # Fallback if tree_sitter Language creation signature differs
        pass
except ImportError:
    HAS_TREE_SITTER = False


class ASTGenerator:
    """Generates clean code structures using tree-sitter with regex fallback."""

    def __init__(self):
        self.parser = _JAVA_PARSER

    def parse_file(self, file_path: str, content: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse a source file and return its semantic model (classes, methods, calls).
        """
        if content is None:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

        ext = os.path.splitext(file_path)[1].lower()

        if self.parser and ext == ".java":
            return self._parse_java_tree_sitter(file_path, content)
        else:
            return self._parse_fallback(file_path, content, ext)

    def _parse_java_tree_sitter(self, file_path: str, content: str) -> Dict[str, Any]:
        """Parse Java source code into high-level entities (Classes, Methods, Calls)."""
        content_bytes = content.encode("utf-8")
        tree = self.parser.parse(content_bytes)
        root_node = tree.root_node

        classes: List[Dict[str, Any]] = []
        imports: List[str] = []
        package_name = ""

        # Extract package, imports, and top-level class declarations directly from root
        for child in root_node.children:
            if child.type == "package_declaration":
                # Extract clean package name
                for sub in child.children:
                    if sub.type == "scoped_identifier" or sub.type == "identifier":
                        package_name = content_bytes[sub.start_byte:sub.end_byte].decode("utf-8",
                                                                                         errors="ignore").strip()
            elif child.type == "import_declaration":
                imp = content_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore").replace("import",
                                                                                                              "").replace(
                    ";", "").strip()
                imports.append(imp)
            elif child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                cls_info = self._extract_class_tree_sitter(child, content_bytes, package_name, file_path)
                classes.append(cls_info)

        # NOTE: 'ast_nodes' is omitted to prevent fine-grained ASTNode explosion in Neo4j.
        return {
            "file_path": file_path,
            "language": "java",
            "package": package_name,
            "imports": imports,
            "classes": classes,
        }

    def _extract_class_tree_sitter(self, class_node, content_bytes: bytes, package_name: str, file_path: str) -> Dict[
        str, Any]:
        """Extract class, annotations, methods, and fields without sub-tree noise."""
        class_name = ""
        annotations = []
        methods = []
        fields = []

        for child in class_node.children:
            if child.type in ("identifier", "type_identifier"):
                class_name = content_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            elif child.type == "modifiers":
                for mod_child in child.children:
                    if "annotation" in mod_child.type:
                        ann_text = content_bytes[mod_child.start_byte:mod_child.end_byte].decode("utf-8",
                                                                                                 errors="ignore").strip()
                        annotations.append(ann_text)
            elif child.type in ("class_body", "interface_body", "enum_body"):
                for body_child in child.children:
                    if body_child.type in ("method_declaration", "constructor_declaration"):
                        m_info = self._extract_method_tree_sitter(body_child, content_bytes, class_name, package_name)
                        methods.append(m_info)
                    elif body_child.type == "field_declaration":
                        f_text = content_bytes[body_child.start_byte:body_child.end_byte].decode("utf-8",
                                                                                                 errors="ignore").strip()
                        fields.append(f_text)

        full_class_name = f"{package_name}.{class_name}" if package_name else class_name
        start_line = class_node.start_point[0] + 1
        end_line = class_node.end_point[0] + 1

        
        return {
            "id": f"{file_path}::{full_class_name}",
            "name": class_name,
            "full_name": full_class_name,
            "type": class_node.type,
            "annotations": annotations,
            "start_line": start_line,
            "end_line": end_line,
            "methods": methods,
            "fields": fields,
        }

    def _extract_method_tree_sitter(self, method_node, content_bytes: bytes, class_name: str, package_name: str) -> \
    Dict[str, Any]:
        """Extract method details including full source code, parameters, and invocation calls."""
        method_name = ""
        return_type = "void"
        annotations = []
        parameters = []
        calls = []

        # 1. Capture the ENTIRE method source code directly from byte bounds
        method_code = content_bytes[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")

        for child in method_node.children:
            if child.type == "modifiers":
                for mod_child in child.children:
                    if "annotation" in mod_child.type:
                        ann_text = content_bytes[mod_child.start_byte:mod_child.end_byte].decode("utf-8",
                                                                                                 errors="ignore").strip()
                        annotations.append(ann_text)
            elif child.type == "identifier":
                method_name = content_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            elif child.type in ("type_identifier", "integral_type", "floating_point_type", "boolean_type", "void_type",
                                "generic_type"):
                return_type = content_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="ignore")
            elif child.type == "formal_parameters":
                parameters = [
                    content_bytes[p.start_byte:p.end_byte].decode("utf-8", errors="ignore").strip()
                    for p in child.children if p.type == "formal_parameter"
                ]

        # 2. Extract method invocations ONLY (Call Graph edges)
        def collect_calls(node):
            if node.type == "method_invocation":
                call_text = content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                name_child = node.child_by_field_name("name")
                callee_name = content_bytes[name_child.start_byte:name_child.end_byte].decode("utf-8",
                                                                                              errors="ignore") if name_child else call_text
                calls.append({"raw": call_text, "callee": callee_name, "line": node.start_point[0] + 1})

            for c in node.children:
                collect_calls(c)

        collect_calls(method_node)

        full_class = f"{package_name}.{class_name}" if package_name else class_name
        full_method_name = f"{full_class}.{method_name}" if full_class else method_name
        node = {
            "id": f"{full_method_name}",
            "name": method_name,
            "full_name": full_method_name,
            "class_name": class_name,
            "return_type": return_type,
            "parameters": parameters,
            "annotations": annotations,
            "code": method_code,  # <--- Source code attached here!
            "calls": calls,
            "start_line": method_node.start_point[0] + 1,
            "end_line": method_node.end_point[0] + 1,
        }
        return node

    def _parse_fallback(self, file_path: str, content: str, ext: str) -> Dict[str, Any]:
        """Fallback regex parser when tree-sitter is unavailable."""
        package_match = re.search(r"^\s*package\s+([\w\.]+);", content, re.MULTILINE)
        package_name = package_match.group(1) if package_match else ""

        imports = re.findall(r"^\s*import\s+([\w\.\*]+);", content, re.MULTILINE)
        annotations = re.findall(r"(@\w+(?:\([^\)]*\))?)", content)

        class_match = re.search(r"(?:public|protected|private)?\s*(?:class|interface|enum)\s+(\w+)", content)
        class_name = class_match.group(1) if class_match else os.path.basename(file_path).split(".")[0]
        full_class_name = f"{package_name}.{class_name}" if package_name else class_name

        method_pattern = re.compile(
            r"((?:@\w+(?:\([^\)]*\))?\s*)*)(?:public|protected|private|static|\s)+([\w\<\>\[\]]+)\s+(\w+)\s*\(([^\)]*)\)\s*\{",
            re.MULTILINE
        )

        methods = []
        for match in method_pattern.finditer(content):
            annos_str, ret_type, m_name, params_str = match.groups()
            m_annos = re.findall(r"@\w+(?:\([^\)]*\))?", annos_str)
            params = [p.strip() for p in params_str.split(",") if p.strip()]

            body_start = match.end()
            body_sample = content[body_start: body_start + 1000]
            m_calls = []
            for call_m in re.finditer(r"(\w+)\s*\(", body_sample):
                c_name = call_m.group(1)
                if c_name not in ("if", "for", "while", "switch", "catch", m_name):
                    m_calls.append(
                        {"raw": call_m.group(0), "callee": c_name, "line": content[:match.start()].count("\n") + 1})

            # Capture approx code snippet in regex fallback
            method_code = content[match.start(): min(match.end() + 500, len(content))]

            methods.append({
                "id": f"{full_class_name}.{m_name}",
                "name": m_name,
                "full_name": f"{full_class_name}.{m_name}",
                "class_name": class_name,
                "return_type": ret_type,
                "parameters": params,
                "annotations": m_annos,
                "code": method_code,
                "calls": m_calls,
                "start_line": content[:match.start()].count("\n") + 1,
                "end_line": content[:match.end()].count("\n") + 10,
            })

        return {
            "file_path": file_path,
            "language": ext.lstrip("."),
            "package": package_name,
            "imports": imports,
            "classes": [{
                "id": f"{file_path}::{full_class_name}",
                "name": class_name,
                "full_name": full_class_name,
                "type": "class",
                "annotations": annotations,
                "start_line": 1,
                "end_line": content.count("\n") + 1,
                "methods": methods,
                "fields": [],
            }],
        }
