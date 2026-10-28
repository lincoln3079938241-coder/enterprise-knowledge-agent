"""Explicitly scripted OFFLINE flow smoke test. Not a real decision-making LLM."""
from __future__ import annotations
import json


class OfflineScriptedMock:
    model = "offline_scripted_mock__not_llm_evidence"
    def __init__(self):
        self.count = 0

    def _call(self, name: str, args: dict) -> dict:
        self.count += 1
        return {"content": "", "tool_calls": [{
            "id": f"offline_call_{self.count}", "name": name,
            "arguments_json": json.dumps(args, ensure_ascii=False)
        }], "usage": {}}

    def next(self, messages: list[dict], tools: list[dict]) -> dict:
        user_lines = [m["content"] for m in messages if m["role"] == "user"]
        question = user_lines[-1]
        history = user_lines[:-1]
        # Build the offline scenario search query. A live model handles this semantically.
        query = ((history[-1] + " ") if history else "") + question
        observations: list[tuple[str, dict]] = []
        pending_action = None
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                pending_action = m["tool_calls"][0]["function"]["name"]
            elif m["role"] == "tool" and pending_action:
                observations.append((pending_action, json.loads(m["content"])))
                pending_action = None
        searches = [value for name, value in observations if name == "search_documents"]
        reads = [value for name, value in observations if name == "read_document"]
        if not searches:
            return self._call("search_documents", {"query": query, "reason": "先检索相关制度"})
        last_search = searches[-1]
        if not last_search.get("ok"):
            if len([s for s in searches if not s.get("ok")]) < 2:
                return self._call("search_documents", {"query": query,
                                  "reason": "检索工具临时失败，有限重试一次"})
            return self._finish("tool_error", "检索工具连续失败，不能确认答案。", [],
                                ["文档检索未恢复"], "检索故障无法获得证据")
        found_ids = [d["document_id"] for d in last_search.get("results", [])]
        if not found_ids:
            if len(searches) < 2:
                return self._call("search_documents", {"query": query.replace("怎么", "如何"),
                                  "reason": "首次未命中，改写问题重新检索"})
            return self._finish("not_found", "现有合成制度库没有足够资料回答。", [],
                                ["未检索到与问题相关的制度文档"], "两次检索未找到文档")
        successes = {r["id"]: r for r in reads if r.get("ok")}
        for did in found_ids:
            if did not in successes:
                failures = [r for r in reads if not r.get("ok")]
                if len(failures) >= 2:
                    return self._finish("tool_error", "文档读取持续失败，无法形成可靠答案。", [],
                                        ["文档读取工具未恢复"], "读取错误超过重试限度")
                return self._call("read_document", {"document_id": did,
                                  "reason": "读取完整原文以核实引用；失败则仅重试一次"})
        cited = [{"document_id": did, "quote": successes[did]["text"]} for did in found_ids]
        # Illustrative mock conflict check only. Real enforcement is in KnowledgeTools.validate_answer.
        deadline = [r for r in successes.values() if "差旅行程结束后" in r.get("text", "") and "内提交报销" in r.get("text", "")]
        if len({r["text"].split("后")[-1].split("内")[0] for r in deadline}) > 1:
            return self._finish("needs_clarification", "不同制度对差旅报销期限的规定不一致，无法确定适用条款。", cited,
                                ["请确认适用制度、所属范围及生效优先级"], "已读证据出现期限冲突，停止选择单一答案")
        quote = cited[0]["quote"]
        return self._finish("complete", f"依据{cited[0]['document_id']}：{quote}", [cited[0]],
                            [], "已读取原文并核对所引语句")

    def _finish(self, status: str, answer: str, citations: list, gaps: list,
                reason: str) -> dict:
        return self._call("submit_answer", {"status": status, "answer": answer,
                            "citations": citations, "gaps": gaps, "reason": reason})
