"""Small, deterministic local tools; NOT model decision logic."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "documents.json"

SYNONYMS = {
    "电脑": "设备", "笔记本": "设备", "怎么领": "申领", "领取": "申领",
    "新同事": "新员工", "新人": "新员工", "刚入职": "新员工",
    "最迟": "最晚", "多长时间": "多久", "几天": "多久", "报帐": "报销",
    "出差": "差旅", "居家": "远程", "找回": "重置", "忘了": "忘记",
}


def normalize(text: str) -> str:
    text = str(text).lower().replace(" ", "")
    for a, b in SYNONYMS.items():
        text = text.replace(a.lower(), b.lower())
    return re.sub(r"[，。？！、：；（）\s,.!?;:()]", "", text)


class KnowledgeTools:
    def __init__(self, path: Path = DATA_FILE, fail_once: str = "") -> None:
        self.docs = json.loads(Path(path).read_text(encoding="utf-8"))
        ids = [d["id"] for d in self.docs]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate document IDs")
        self.by_id = {d["id"]: d for d in self.docs}
        self.fail_once = fail_once
        self.failed = False
        self.read_docs: dict[str, dict[str, Any]] = {}
        self.searched_document_ids: set[str] = set()
        self.successful_searches = 0
        self.error_count = 0

    def _maybe_fail(self, tool_name: str) -> None:
        if self.fail_once == tool_name and not self.failed:
            self.failed = True
            raise ConnectionError(f"SIMULATED_TEMPORARY_FAILURE: {tool_name}")

    def search_documents(self, query: str, top_k: int = 4) -> dict:
        self._maybe_fail("search_documents")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Search query must be a non-empty string")
        q = normalize(query)
        ranked: list[tuple[int, str, dict]] = []
        for d in self.docs:
            matches = [normalize(t) for t in d["keywords"] if normalize(t) in q]
            # Avoid unrelated results on generic words alone ("审批", "办公").
            specific = [x for x in matches if x not in {"审批", "办公", "申请", "提交", "登录", "费用", "多久"}]
            if not specific:
                continue
            score = len(matches) + sum(len(x) > 2 for x in matches)
            ranked.append((score, d["id"], d))
        ranked.sort(key=lambda r: (-r[0], r[1]))
        results = [
            {"document_id": d["id"], "title": d["title"],
             "version": d["version"], "version_date": d["version_date"],
             "preview": d["preview"], "match_score": score}
            for score, _, d in ranked[:max(1, min(7, int(top_k)))]]
        self.successful_searches += 1
        self.searched_document_ids.update(item["document_id"] for item in results)
        return {"results": results, "total_matches": len(ranked), "query": query}

    def read_document(self, document_id: str) -> dict:
        self._maybe_fail("read_document")
        document_id = str(document_id)
        if document_id not in self.searched_document_ids:
            raise ValueError(
                f"Document ID was not returned by a successful search: {document_id}"
            )
        doc = self.by_id.get(str(document_id))
        if doc is None:
            raise KeyError(f"Document ID not found: {document_id}")
        self.read_docs[document_id] = doc
        return {k: doc[k] for k in ("id", "title", "version", "version_date", "scope", "text")}

    def execute(self, name: str, arguments: dict) -> dict:
        try:
            if name == "search_documents":
                return {"ok": True, **self.search_documents(arguments.get("query", ""), arguments.get("top_k", 4))}
            if name == "read_document":
                return {"ok": True, **self.read_document(arguments.get("document_id", ""))}
            self.error_count += 1
            return {"ok": False, "error": f"Unknown tool: {name}"}
        except (ConnectionError, KeyError, ValueError, TypeError) as exc:
            self.error_count += 1
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    def validate_answer(self, args: dict) -> tuple[bool, str]:
        status = args.get("status")
        if status not in {"complete", "needs_clarification", "not_found", "tool_error"}:
            return False, "Invalid status"
        answer = args.get("answer", "")
        citations = args.get("citations", [])
        gaps = args.get("gaps", [])
        if not isinstance(answer, str) or not answer.strip() or not isinstance(citations, list) or not isinstance(gaps, list):
            return False, "answer, citations or gaps malformed"
        if status == "complete" and not citations:
            return False, "A completed answer requires at least one document quote"
        if status == "not_found" and self.successful_searches < 1:
            return False, "not_found requires at least one successful document search"
        if status == "not_found" and citations:
            return False, "not_found must not pretend to cite evidence"
        if status == "tool_error" and self.error_count < 1:
            return False, "tool_error is only valid after an actual tool failure"
        for c in citations:
            if not isinstance(c, dict):
                return False, "Each citation must be an object"
            did, quote = c.get("document_id"), c.get("quote")
            if did not in self.read_docs or not isinstance(quote, str) or not quote.strip():
                return False, f"Citation {did} is not a successfully read document / quote is empty"
            if quote not in self.read_docs[did]["text"]:
                return False, f"Quote of {did} is not a verbatim substring"
        topics: dict[str, dict[str, str]] = {}
        searched_docs = [self.by_id[did] for did in self.searched_document_ids if did in self.by_id]
        for d in searched_docs:
            topics.setdefault(d["topic"], {})[d["id"]] = d["claim_value"]
        for topic, claims in topics.items():
            read_conflict_ids = set(claims).intersection(self.read_docs)
            if len(set(claims.values())) > 1 and read_conflict_ids:
                unread_conflict_ids = set(claims).difference(self.read_docs)
                if unread_conflict_ids:
                    return False, (
                        f"Conflicting searched evidence in {topic}; read all candidates before answering: "
                        + ", ".join(sorted(unread_conflict_ids))
                    )
                if status != "needs_clarification":
                    return False, f"Conflicting evidence in {topic}; do not choose one silently"
                if not set(claims).issubset({c["document_id"] for c in citations}):
                    return False, "Conflicting documents must all be cited"
        if status == "needs_clarification" and not gaps:
            return False, "Clarification status requires describing unresolved issue"
        if status == "not_found" and not gaps:
            return False, "Absent data requires a gap description"
        if status == "tool_error" and not gaps:
            return False, "Tool failure status requires a gap description"
        return True, "OK"


def tool_schemas() -> list[dict]:
    """Low-token function schemas compatible with Chat Completions."""
    return [
        {"type": "function", "function": {
            "name": "search_documents", "description": "Search synthetic document metadata; use this before reading. Never assume snippet proves claims.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Short retrieval query"},
                "reason": {"type": "string", "description": "Brief observable action rationale, not private reasoning"}
            }, "required": ["query", "reason"]}}},
        {"type": "function", "function": {
            "name": "read_document", "description": "Read the exact original text, version and scope of one searched document.",
            "parameters": {"type": "object", "properties": {
                "document_id": {"type": "string"},
                "reason": {"type": "string", "description": "Why this document is relevant"}
            }, "required": ["document_id", "reason"]}}},
        {"type": "function", "function": {
            "name": "submit_answer", "description": "Return the answer. Completed answers MUST cite verbatim substrings from read_document. Explain conflicts and gaps.",
            "parameters": {"type": "object", "properties": {
                "answer": {"type": "string"},
                "status": {"type": "string", "enum": ["complete", "needs_clarification", "not_found", "tool_error"]},
                "citations": {"type": "array", "items": {"type": "object", "properties": {
                    "document_id": {"type": "string"}, "quote": {"type": "string"}}, "required": ["document_id", "quote"]}},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string", "description": "Short evidence-based decision summary"}
            }, "required": ["answer", "status", "citations", "gaps", "reason"]}}}
    ]
