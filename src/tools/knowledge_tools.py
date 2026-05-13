import re
from typing import Any

from src.core.database import get_company_state
from src.core.knowledge import KnowledgeManager

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


_QUERY_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("decision", [r"\bdecid", r"\bapprove", r"\bagreed?\b", r"\bresolved?\b", r"\bvoted?\b", r"\bboard\b", r"\bsigned off\b"]),
    ("status",   [r"\bstatus\b", r"\bupdate\b", r"\bprogress\b", r"\bcurrent(ly)?\b", r"\blatest\b", r"\bright now\b", r"\bwhere (are|is) we\b"]),
    ("document", [r"\bdocument\b", r"\breport\b", r"\bmemo\b", r"\bdeck\b", r"\bfile\b", r"\battachment\b", r"\bslide\b", r"\bspreadsheet\b"]),
    ("temporal", [r"\blast (week|month|quarter|year)\b", r"\bthis (week|month|quarter|year)\b", r"\byesterday\b", r"\bQ[1-4]\b", r"\bfiscal\b", r"\bwhen\b"]),
    ("factual",  [r"\bwhat is\b", r"\bhow much\b", r"\bhow many\b", r"\bdefine\b", r"\bwhat are\b", r"\bwho is\b"]),
]

_QUERY_TYPE_RANK_WEIGHTS: dict[str, dict[str, float]] = {
    "decision":   {"authority": 0.35, "freshness": 0.25, "relevance": 0.40},
    "status":     {"authority": 0.10, "freshness": 0.50, "relevance": 0.40},
    "document":   {"authority": 0.30, "freshness": 0.10, "relevance": 0.60},
    "temporal":   {"authority": 0.15, "freshness": 0.45, "relevance": 0.40},
    "factual":    {"authority": 0.40, "freshness": 0.10, "relevance": 0.50},
    "exploratory":{"authority": 0.20, "freshness": 0.20, "relevance": 0.60},
}

_PURPOSE_AUTHORITY: dict[str, float] = {
    "identity":               0.90,
    "audited_finance_doc":    0.85,
    "weekly_finance_checkin": 0.75,
    "example_material":       0.70,
    "reference":              0.60,
}


def _classify_query_type(query: str) -> str:
    lower = query.lower()
    for query_type, patterns in _QUERY_TYPE_PATTERNS:
        if any(re.search(p, lower) for p in patterns):
            return query_type
    return "exploratory"


def _authority_score(doc: dict[str, Any]) -> float:
    purpose = doc.get("purpose", "reference")
    identity_role = doc.get("identity_role")
    if identity_role:
        return 0.90
    return _PURPOSE_AUTHORITY.get(purpose, 0.50)


class SemanticSearchTool(BaseTool):
    metadata = ToolMetadata(
        name="semantic_search",
        description="Run semantic retrieval against the company knowledge base.",
        read_only=True,
        side_effects=False,
        tags=["knowledge", "retrieval"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        company_name = kwargs.get("company_name") or context.company_name
        query = kwargs.get("query")
        limit = int(kwargs.get("limit", 5))
        preferred_document_ids = kwargs.get("preferred_document_ids") or context.metadata.get("preferred_document_ids") or []
        if not company_name or not query:
            return ToolResult(tool_name=self.metadata.name, success=False, error="company_name and query are required")

        query_type = _classify_query_type(query)

        state = get_company_state(company_name)
        indexed_documents = state.knowledge_base if state else []
        if not indexed_documents:
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"results": [], "count": 0, "mode": "empty", "query_type": query_type},
            )
        if preferred_document_ids:
            project_documents = [
                document
                for document in indexed_documents
                if document.get("document_id") in preferred_document_ids
            ]
            if project_documents:
                project_results = self._lexical_search(project_documents, query, limit, query_type)
                if project_results:
                    return ToolResult(
                        tool_name=self.metadata.name,
                        success=True,
                        data={
                            "results": project_results,
                            "count": len(project_results),
                            "mode": "project_lexical",
                            "query_type": query_type,
                            "project_biased": True,
                        },
                    )
        lexical_results = self._lexical_search(indexed_documents, query, limit, query_type)
        if lexical_results:
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"results": lexical_results, "count": len(lexical_results), "mode": "lexical", "query_type": query_type},
            )

        # Broaden: try without preferred_document_ids filter already done above.
        # Fall back to semantic search.
        try:
            manager = KnowledgeManager(company_name)
            results = manager.semantic_search(query, limit=limit)
        except Exception as exc:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                data={"results": [], "count": 0, "query_type": query_type},
                error=str(exc),
            )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"results": results, "count": len(results), "mode": "semantic", "query_type": query_type},
        )

    def _lexical_search(
        self,
        documents: list[dict[str, Any]],
        query: str,
        limit: int,
        query_type: str = "exploratory",
    ) -> list[dict[str, Any]]:
        if not documents:
            return []

        weights = _QUERY_TYPE_RANK_WEIGHTS.get(query_type, _QUERY_TYPE_RANK_WEIGHTS["exploratory"])
        query_terms = {term for term in query.lower().split() if len(term) > 2}
        scored: list[tuple[float, dict[str, Any]]] = []

        for document in documents:
            title = document.get("title", "")
            content = document.get("content", "")
            haystack = f"{title} {content}".lower()
            term_hits = sum(1 for term in query_terms if term in haystack)
            if term_hits == 0:
                continue
            relevance = term_hits / max(len(query_terms), 1)
            authority = _authority_score(document)
            combined = (
                weights["relevance"] * relevance
                + weights["authority"] * authority
            )
            scored.append(
                (
                    combined,
                    {
                        "title": title or "Untitled",
                        "content": content[:1200],
                        "purpose": document.get("purpose", "reference"),
                        "identity_role": document.get("identity_role"),
                        "domains": document.get("domains", []),
                        "document_id": document.get("document_id"),
                        "source_authority": authority,
                    },
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        return [document for _, document in scored[:limit]]
