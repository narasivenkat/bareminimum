"""
Call Graph Generator module.
Analyzes method declarations and calls across AST parse outputs to build a project-wide call graph.
"""

from typing import Any, Dict, List, Set


class CallGraphGenerator:
    """Generates call graphs from parsed AST structures."""

    def generate(self, ast_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate call graph nodes and edges from parsed AST results.

        :param ast_outputs: List of parsed file structures from ASTGenerator.
        :return: Dict containing 'nodes' (methods) and 'edges' (caller -> callee links).
        """
        methods_by_name: Dict[str, List[Dict[str, Any]]] = {}
        all_methods: Dict[str, Dict[str, Any]] = {}
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        visited_nodes: Set[str] = set()

        # Phase 1: Index all declared methods across all files
        for parsed_file in ast_outputs:
            file_path = parsed_file.get("file_path", "")
            for cls in parsed_file.get("classes", []):
                class_name = cls.get("name", "")
                full_class_name = cls.get("full_name", class_name)

                for method in cls.get("methods", []):
                    m_name = method.get("name", "")
                    m_full = method.get("full_name", f"{full_class_name}.{m_name}")
                    
                    method_meta = {
                        "id": m_full,
                        "name": m_name,
                        "full_name": m_full,
                        "class_name": class_name,
                        "full_class_name": full_class_name,
                        "file_path": file_path,
                        "start_line": method.get("start_line", 1),
                        "end_line": method.get("end_line", 1),
                        "return_type": method.get("return_type", "void"),
                        "parameters": method.get("parameters", []),
                    }

                    all_methods[m_full] = method_meta
                    methods_by_name.setdefault(m_name, []).append(method_meta)

                    if m_full not in visited_nodes:
                        nodes.append(method_meta)
                        visited_nodes.add(m_full)

        # Phase 2: Create edges for body method calls
        for parsed_file in ast_outputs:
            file_path = parsed_file.get("file_path", "")
            for cls in parsed_file.get("classes", []):
                class_name = cls.get("name", "")
                full_class_name = cls.get("full_name", class_name)

                for method in cls.get("methods", []):
                    caller_full = method.get("full_name", f"{full_class_name}.{method.get('name')}")

                    for call in method.get("calls", []):
                        callee_name = call.get("callee", "")
                        line_no = call.get("line", 1)

                        # Resolve target callee method
                        target_full_name = None
                        
                        # 1. Check if same class has this method
                        same_cls_full = f"{full_class_name}.{callee_name}"
                        if same_cls_full in all_methods:
                            target_full_name = same_cls_full
                        elif callee_name in methods_by_name:
                            # Match first known method with this name
                            target_full_name = methods_by_name[callee_name][0]["full_name"]
                        else:
                            target_full_name = callee_name

                        edges.append({
                            "caller": caller_full,
                            "callee": callee_name,
                            "callee_full_name": target_full_name,
                            "file_path": file_path,
                            "line": line_no,
                            "raw_call": call.get("raw", callee_name),
                        })

        return {
            "nodes": nodes,
            "edges": edges,
        }
