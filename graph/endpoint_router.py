"""
Endpoint Router module.
Extracts API routing endpoints (HTTP method, path, handler class/method) from source ASTs and annotations.
Supports Spring Boot, JAX-RS, and generic endpoint annotations.
"""

import re
from typing import Any, Dict, List, Optional


class EndpointRouter:
    """Extracts endpoint routing definitions from parsed code structures."""

    ANNOTATION_MAP = {
        "@getmapping": "GET",
        "@postmapping": "POST",
        "@putmapping": "PUT",
        "@deletemapping": "DELETE",
        "@patchmapping": "PATCH",
        "@requestmapping": "ALL",
        "@get": "GET",
        "@post": "POST",
        "@put": "PUT",
        "@delete": "DELETE",
        "@patch": "PATCH",
        "@head": "HEAD",
        "@options": "OPTIONS",
    }

    def extract_endpoints(self, ast_outputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract all endpoint routes from AST parse results.

        :param ast_outputs: List of parsed file structures from ASTGenerator.
        :return: List of endpoint dicts.
        """
        endpoints: List[Dict[str, Any]] = []

        for parsed_file in ast_outputs:
            file_path = parsed_file.get("file_path", "")

            for cls in parsed_file.get("classes", []):
                class_name = cls.get("name", "")
                full_class_name = cls.get("full_name", class_name)
                class_annotations = cls.get("annotations", [])

                # Determine base path from class annotations
                base_path = self._extract_path_from_annotations(class_annotations)

                for method in cls.get("methods", []):
                    m_name = method.get("name", "")
                    m_full = method.get("full_name", f"{full_class_name}.{m_name}")
                    m_annos = method.get("annotations", [])

                    route_info = self._extract_method_route(m_annos)
                    if route_info:
                        http_method, sub_path = route_info
                        
                        # Combine class base path and method path
                        full_path = self._combine_paths(base_path, sub_path)

                        endpoints.append({
                            "http_method": http_method,
                            "path": full_path,
                            "handler_class": class_name,
                            "full_handler_class": full_class_name,
                            "handler_method": m_name,
                            "full_handler_method": m_full,
                            "file_path": file_path,
                            "line": method.get("start_line", 1),
                            "annotations": m_annos,
                        })

        return endpoints

    def _extract_path_from_annotations(self, annotations: List[str]) -> str:
        """Extract path value from class-level annotations like @RequestMapping("/api") or @Path("/api")."""
        for anno in annotations:
            anno_lower = anno.lower().strip()
            if "@requestmapping" in anno_lower or "@path" in anno_lower:
                path = self._parse_anno_path_arg(anno)
                if path:
                    return path
        return ""

    def _extract_method_route(self, annotations: List[str]) -> Optional[tuple[str, str]]:
        """Identify HTTP method and path for a method's annotations."""
        for anno in annotations:
            anno_strip = anno.strip()
            anno_lower = anno_strip.lower()

            # Check mapping annotation prefix
            for prefix, default_http in self.ANNOTATION_MAP.items():
                if anno_lower.startswith(prefix):
                    http_verb = default_http

                    # Check for explicit method parameter in @RequestMapping(method = RequestMethod.POST)
                    if "method" in anno_lower:
                        if "get" in anno_lower:
                            http_verb = "GET"
                        elif "post" in anno_lower:
                            http_verb = "POST"
                        elif "put" in anno_lower:
                            http_verb = "PUT"
                        elif "delete" in anno_lower:
                            http_verb = "DELETE"
                        elif "patch" in anno_lower:
                            http_verb = "PATCH"

                    path = self._parse_anno_path_arg(anno_strip)
                    return (http_verb, path)

        return None

    def _parse_anno_path_arg(self, anno_text: str) -> str:
        """Extract string argument inside annotation parens e.g. @GetMapping("/users") or path="/users"."""
        match = re.search(r'\((?:value\s*=\s*|path\s*=\s*)?["\']([^"\']+)["\']', anno_text)
        if match:
            return match.group(1)

        # Fallback single string quote inside parens
        match_simple = re.search(r'["\']([^"\']+)["\']', anno_text)
        if match_simple:
            return match_simple.group(1)

        return ""

    def _combine_paths(self, base_path: str, sub_path: str) -> str:
        """Sanitize and combine base path and sub path into a normalized endpoint route URL."""
        base = (base_path or "").strip()
        sub = (sub_path or "").strip()

        if base and not base.startswith("/"):
            base = "/" + base
        if base.endswith("/"):
            base = base[:-1]

        if sub and not sub.startswith("/"):
            sub = "/" + sub

        full = f"{base}{sub}"
        if not full:
            return "/"
        return full
