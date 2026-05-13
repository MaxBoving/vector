import sys
import io
import math
import contextlib
import re

class MathSandbox:
    """
    STORY-030: Python Tool-Grounding for Mathematical Foundation.
    Safely executes agent-generated math scripts.
    """
    @staticmethod
    def execute_math(code: str) -> dict:
        stdout = io.StringIO()
        locals_dict = {}
        try:
            _allowed_imports = {"math"}
            def _safe_import(name, *args, **kwargs):
                if name not in _allowed_imports:
                    raise ImportError(f"import '{name}' not allowed in math sandbox")
                return __import__(name, *args, **kwargs)
            _safe_globals = {
                "__builtins__": {
                    "print": print, "round": round, "abs": abs,
                    "min": min, "max": max, "sum": sum, "len": len,
                    "range": range, "int": int, "float": float, "str": str,
                    "list": list, "dict": dict, "tuple": tuple, "zip": zip,
                    "enumerate": enumerate, "sorted": sorted, "bool": bool,
                    "__import__": _safe_import,
                },
                "math": math,
            }
            with contextlib.redirect_stdout(stdout):
                exec(code, _safe_globals, locals_dict)
            import json as _json
            serializable_vars = {}
            for k, v in locals_dict.items():
                if not k.startswith("__"):
                    try:
                        _json.dumps(v)
                        serializable_vars[k] = v
                    except (TypeError, ValueError):
                        pass
            return {
                "success": True,
                "output": stdout.getvalue(),
                "variables": serializable_vars,
                "error": None
            }
        except Exception as e:
            return {"success": False, "output": stdout.getvalue(), "variables": {}, "error": str(e)}

class SecurityScan:
    """
    STORY-036: Multi-Stage Security & Integrity Scan.
    """
    @staticmethod
    def scan_file(content: str, filename: str) -> dict:
        malware_signatures = ["VIRUS", "MALWARE", "DROP_TABLE", "rm -rf /"]
        for sig in malware_signatures:
            if sig in content.upper():
                return {"safe": False, "reason": f"Malicious signature detected: {sig}"}
        ssn_pattern = r'\d{3}-\d{2}-\d{4}'
        if re.search(ssn_pattern, content):
            return {"safe": False, "reason": "Sensitive PII (SSN) detected."}
        return {"safe": True, "reason": "Security Pass: Clean."}

class StrategicTagger:
    """
    STORY-037: Strategic Domain Mapping.
    Categorizes documents into domains based on linguistic density.
    """
    DOMAINS = {
        "Financials": ["revenue", "burn", "cash", "runway", "capital", "series", "budget", "ebitda"],
        "Governance": ["policy", "covenant", "board", "approval", "compliance", "mandate", "legal"],
        "Operations": ["hiring", "headcount", "team", "org", "structure", "sales", "ops"],
        "Competition": ["competitor", "market", "threat", "cloudscale", "pricing", "rival"],
        "Strategy": ["initiative", "project", "kepler", "horizon", "pivot", "roadmap", "vision"]
    }

    @staticmethod
    def tag_document(content: str, title: str) -> list:
        found_domains = []
        text = (content + " " + title).lower()
        for domain, keywords in StrategicTagger.DOMAINS.items():
            if any(k in text for k in keywords):
                found_domains.append(domain)
        return found_domains if found_domains else ["General"]
