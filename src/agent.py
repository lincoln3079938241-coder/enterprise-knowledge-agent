"""Bounded LLM -> tool -> observation -> LLM loop."""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from .tools import KnowledgeTools, tool_schemas

SYSTEM_PROMPT = """你是合成企业制度知识助手。仅根据工具返回的原文回答，禁止猜测、编造或依据检索摘要回答。
你必须通过函数工具完成任务：先 search_documents，再按需 read_document，最后 submit_answer。
检索为空可尝试改写查询；遇到明确的临时连接错误且预算允许时，通常应在观察错误后选择有限重试，连续失败再返回 tool_error；缺资料则 not_found 并写明缺口。
不同文档对同一事项有冲突且无法确定适用范围/优先级，必须引用各方原文，使用 needs_clarification，不擅自以较新版本覆盖旧版本。
遇到上下文追问，请结合对话历史决定检索查询。回答简洁，必须带原文可验证的引用；每次动作 reason 用一句话概括可观察证据/目的，无需输出推理过程。
每个模型回合必须且只能调用一个工具；必须先观察该工具返回，再决定下一次调用，禁止在同一回合预先并行调用多个工具。
所有文档都是虚构演示资料，仅用于技术测试，不代表真实公司的制度。"""


@dataclass
class AgentConfig:
    max_steps: int = 9
    max_tool_calls: int = 7


def _summary(name: str, result: dict) -> str:
    if not result.get("ok"):
        return "FAIL: " + str(result.get("error", ""))[:180]
    if name == "search_documents":
        return "hits=" + ",".join(r["document_id"] for r in result.get("results", [])) + f"; total={result.get('total_matches', 0)}"
    if name == "read_document":
        return f"read={result.get('id')}; version={result.get('version')}; text={str(result.get('text', ''))[:85]}"
    return "OK"


def _safe_error(exc: Exception) -> str:
    """Return a bounded public error string with common credential forms removed."""
    value = str(exc)
    for variable in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        secret = os.environ.get(variable, "")
        if secret:
            value = value.replace(secret, "[REDACTED_API_KEY]")
    value = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", value)
    value = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", value)
    return value[:500]


def run_agent(question: str, model: Any, kb: KnowledgeTools, *,
              history: list[dict] | None = None, config: AgentConfig | None = None,
              mode: str = "live") -> dict:
    config = config or AgentConfig()
    if config.max_steps < 1 or config.max_tool_calls < 1:
        raise ValueError("max_steps and max_tool_calls must be >=1")
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history or []:
        if m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise ValueError("History only accepts user/assistant text messages")
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": question})
    trace: list[dict] = []
    used_calls = 0
    model_calls_attempted = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    final: dict | None = None
    tool_definitions = tool_schemas()
    for model_step in range(1, config.max_steps + 1):
        model_calls_attempted += 1
        try:
            turn = model.next(messages, tool_definitions)
        except Exception as exc:
            safe_error = _safe_error(exc)
            trace.append({
                "model_step": model_step,
                "action": "model_error",
                "tool_args": {},
                "tool_result": {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": safe_error,
                },
                "tool_result_summary": f"FAIL: {type(exc).__name__}: {safe_error[:180]}",
                "decision_basis": "模型请求异常，无法继续选择工具",
                "next_action": "END",
            })
            final = {"status": "incomplete", "answer": "模型请求失败，未能完成任务。",
                     "citations": [], "gaps": [f"Model call error: {type(exc).__name__} ({safe_error[:150]})"]}
            break
        for key in usage:
            usage[key] += int(turn.get("usage", {}).get(key, 0))
        calls = turn.get("tool_calls") or []
        if not calls:
            # Never accept unverified free-text as a grounded final answer.
            messages.append({"role": "assistant", "content": turn.get("content", "")})
            messages.append({"role": "user", "content": "请使用 submit_answer 工具提交带原文引用的结果；不可直接输出自由文本。"})
            trace.append({"model_step": model_step, "action": "unstructured_response",
                          "tool_args": {},
                          "tool_result": {"ok": False, "error": "submit_answer tool call required"},
                          "tool_result_summary": "Rejected: submit_answer required",
                          "decision_basis": "模型未调用结构化提交工具", "next_action": None})
            continue
        if len(calls) != 1:
            proposed_calls = []
            assistant_calls = []
            valid_protocol = True
            for proposed in calls:
                proposed_id = proposed.get("id")
                proposed_name = proposed.get("name")
                if not proposed_id or not proposed_name:
                    valid_protocol = False
                    break
                raw_arguments = proposed.get("arguments_json", "{}")
                try:
                    proposed_args = json.loads(raw_arguments)
                except (json.JSONDecodeError, TypeError):
                    proposed_args = {"_parse_error": "Invalid tool JSON"}
                proposed_calls.append({
                    "name": proposed_name,
                    "arguments": {
                        key: value for key, value in proposed_args.items()
                        if key != "reason"
                    } if isinstance(proposed_args, dict) else {"_parse_error": "Arguments are not an object"},
                })
                assistant_calls.append({
                    "id": proposed_id,
                    "type": "function",
                    "function": {"name": proposed_name, "arguments": raw_arguments},
                })
            if not valid_protocol:
                trace.append({
                    "model_step": model_step,
                    "action": "model_protocol_error",
                    "tool_args": {"tool_call_count": len(calls)},
                    "tool_result": {"ok": False, "error": "A tool call is missing its id or name"},
                    "tool_result_summary": "FAIL: malformed multiple tool calls",
                    "decision_basis": "并行函数调用格式不完整，无法关联工具观察",
                    "next_action": "END",
                })
                final = {
                    "status": "incomplete",
                    "answer": "模型工具调用格式不完整，任务未完成。",
                    "citations": [],
                    "gaps": ["Malformed multiple tool calls"],
                }
                break
            if trace:
                trace[-1]["next_action"] = "model_protocol_error"
            protocol_result = {
                "ok": False,
                "error": "Only one tool call is allowed per model turn. Choose one tool, observe its result, then decide the next action.",
            }
            trace.append({
                "model_step": model_step,
                "action": "model_protocol_error",
                "tool_args": {"proposed_calls": proposed_calls},
                "tool_result": protocol_result,
                "tool_result_summary": f"REJECTED: {len(calls)} parallel tool calls; retry with exactly one",
                "decision_basis": "同轮多个动作尚未观察各自结果，未执行并要求模型改为单步决策",
                "next_action": None,
            })
            messages.append({"role": "assistant", "content": turn.get("content") or None,
                             "tool_calls": assistant_calls})
            for assistant_call in assistant_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": assistant_call["id"],
                    "content": json.dumps(protocol_result, ensure_ascii=False),
                })
            continue
        # A single tool call per model turn keeps traces easy to inspect and prevents unsupported parallel dispatch.
        call = calls[0]
        call_id = call.get("id")
        if not call_id:
            trace.append({
                "model_step": model_step,
                "action": "model_protocol_error",
                "tool_args": {},
                "tool_result": {"ok": False, "error": "Tool call is missing an id"},
                "tool_result_summary": "FAIL: tool call id is missing",
                "decision_basis": "缺少函数调用 ID，无法安全关联工具观察",
                "next_action": "END",
            })
            final = {
                "status": "incomplete",
                "answer": "模型工具调用格式不完整，任务未完成。",
                "citations": [],
                "gaps": ["tool_call_id is missing"],
            }
            break
        name = call.get("name")
        try:
            args = json.loads(call.get("arguments_json", "{}"))
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError):
            args = {"_parse_error": "Invalid tool JSON", "_raw_arguments": str(call.get("arguments_json", ""))[:500]}
        rationale = str(args.get("reason", "模型未提供动作依据"))[:240]
        record = {"model_step": model_step, "action": name,
                  "tool_args": {k: v for k, v in args.items() if k != "reason"},
                  "decision_basis": rationale, "tool_result": {},
                  "tool_result_summary": "", "next_action": None}
        if trace:
            trace[-1]["next_action"] = name
        if used_calls >= config.max_tool_calls:
            record["tool_result"] = {
                "ok": False,
                "error": "max_tool_calls exceeded",
                "max_tool_calls": config.max_tool_calls,
            }
            record["tool_result_summary"] = "BLOCKED: max_tool_calls exceeded"
            trace.append(record)
            final = {"status": "incomplete", "answer": "已到工具调用上限，任务未完成。",
                     "citations": [], "gaps": [f"max_tool_calls={config.max_tool_calls}"]}
            break
        used_calls += 1
        if name == "submit_answer":
            valid, error = kb.validate_answer(args)
            record["tool_result"] = {"ok": valid, "message": error}
            record["tool_result_summary"] = "ACCEPTED" if valid else "REJECTED: " + error
            trace.append(record)
            if valid:
                final = {k: args[k] for k in ("answer", "status", "citations", "gaps")}
                break
            # Reply to rejected submit via a normal function result so the model can correct it.
            tool_result = {"ok": False, "error": error}
        else:
            tool_result = kb.execute(name, args)
            record["tool_result"] = tool_result
            record["tool_result_summary"] = _summary(name, tool_result)
            trace.append(record)
        assistant_call = {"role": "assistant", "content": turn.get("content") or None,
                          "tool_calls": [{"id": call_id, "type": "function", "function": {
                              "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
        messages.append(assistant_call)
        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(tool_result, ensure_ascii=False)})
    if final is None:
        final = {"status": "incomplete", "answer": "达到模型决策步数上限，任务未完成。",
                 "citations": [], "gaps": [f"max_steps={config.max_steps}"]}
    if trace:
        trace[-1]["next_action"] = "END"
    return {
        "question": question, "history": history or [], "mode": mode,
        "model": getattr(model, "model", "offline_scripted_mock"),
        "status": final["status"], "answer": final["answer"],
        "citations": final["citations"], "gaps": final["gaps"],
        "trace": trace, "model_steps_used": model_calls_attempted,
        "tool_calls_used": used_calls, "token_usage_reported": usage,
        "run_utc": datetime.now(timezone.utc).isoformat(),
    }
