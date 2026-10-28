"""Offline correctness smoke tests. Not a substitute for live-model evaluation."""
from __future__ import annotations
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.agent import AgentConfig, run_agent
from src.mock_model import OfflineScriptedMock
from src.model import LiveChatModel
from src.tools import KnowledgeTools


def run(question, failure="", history=None, cfg=None):
    return run_agent(question, OfflineScriptedMock(), KnowledgeTools(fail_once=failure),
                     history=history, config=cfg, mode="offline_mock")


class AgentTests(unittest.TestCase):
    def test_normal(self):
        v = run("新员工入职之后怎么领取办公电脑？")
        self.assertEqual(v["status"], "complete")
        self.assertEqual([x["document_id"] for x in v["citations"]], ["D03"])
        self.assertEqual(v["trace"][0]["action"], "search_documents")
        self.assertIn("read_document", [r["action"] for r in v["trace"]])
        self.assertEqual(v["tool_calls_used"], 3)

    def test_conflict(self):
        v = run("差旅报销最晚多久提交？")
        self.assertEqual(v["status"], "needs_clarification")
        self.assertTrue({"D01", "D02"}.issubset({c["document_id"] for c in v["citations"]}))

    def test_unknown(self):
        v = run("食堂今日晚餐菜单是什么？")
        self.assertEqual(v["status"], "not_found")
        self.assertGreaterEqual(len([x for x in v["trace"] if x["action"] == "search_documents"]), 2)

    def test_paraphrase(self):
        v = run("刚入职的新人要领笔记本怎么办？")
        self.assertEqual(v["status"], "complete")
        self.assertEqual(v["citations"][0]["document_id"], "D03")

    def test_followup(self):
        hist = [{"role": "user", "content": "新员工怎么领取电脑？"},
                {"role": "assistant", "content": "可向IT服务台申领设备。"}]
        v = run("需要主管审批吗？", history=hist)
        self.assertEqual(v["status"], "complete")
        self.assertEqual(v["citations"][0]["document_id"], "D03")

    def test_search_failure_recovery(self):
        v = run("忘记系统密码怎么找回？", "search_documents")
        self.assertEqual(v["status"], "complete")
        self.assertTrue(any("FAIL" in t["tool_result_summary"] for t in v["trace"]))
        self.assertEqual(v["trace"][1]["action"], "search_documents")

    def test_read_failure_recovery(self):
        v = run("远程办公如何登录VPN？", "read_document")
        self.assertEqual(v["status"], "complete")
        self.assertTrue(any("FAIL" in t["tool_result_summary"] for t in v["trace"]))

    def test_step_limit(self):
        v = run("办公电脑怎么领取？", cfg=AgentConfig(max_steps=1, max_tool_calls=5))
        self.assertEqual(v["status"], "incomplete")
        self.assertIn("max_steps", v["gaps"][0])

    def test_tool_limit(self):
        v = run("办公电脑怎么领取？", cfg=AgentConfig(max_steps=9, max_tool_calls=1))
        self.assertEqual(v["status"], "incomplete")
        self.assertIn("max_tool_calls", v["gaps"][0])

    def test_submit_answer_counts_toward_tool_limit(self):
        v = run("办公电脑怎么领取？", cfg=AgentConfig(max_steps=9, max_tool_calls=2))
        self.assertEqual(v["status"], "incomplete")
        self.assertEqual(v["tool_calls_used"], 2)
        self.assertEqual(v["trace"][-1]["action"], "submit_answer")
        self.assertIn("BLOCKED", v["trace"][-1]["tool_result_summary"])

    def test_quote_guard(self):
        kb = KnowledgeTools()
        kb.execute("search_documents", {"query": "新员工设备申领"})
        kb.execute("read_document", {"document_id": "D03"})
        valid, msg = kb.validate_answer({"status": "complete", "answer": "随便回答",
            "citations": [{"document_id": "D03", "quote": "IT无需审批"}], "gaps": []})
        self.assertFalse(valid)
        self.assertIn("verbatim", msg)

    def test_conflict_guard(self):
        kb = KnowledgeTools()
        kb.execute("search_documents", {"query": "差旅报销期限"})
        kb.execute("read_document", {"document_id": "D01"})
        kb.execute("read_document", {"document_id": "D02"})
        valid, msg = kb.validate_answer({"status": "complete", "answer": "30天", "gaps": [],
            "citations": [{"document_id": "D01", "quote": "30个自然日"}]})
        self.assertFalse(valid)
        self.assertIn("Conflicting", msg)

    def test_conflict_guard_requires_all_searched_candidates_to_be_read(self):
        kb = KnowledgeTools()
        kb.execute("search_documents", {"query": "差旅报销期限"})
        kb.execute("read_document", {"document_id": "D01"})
        valid, msg = kb.validate_answer({"status": "complete", "answer": "30天", "gaps": [],
            "citations": [{"document_id": "D01", "quote": "30个自然日"}]})
        self.assertFalse(valid)
        self.assertIn("read all candidates", msg)
        self.assertIn("D02", msg)

    def test_search_changes_when_documents_change(self):
        kb = KnowledgeTools()
        before = kb.execute("search_documents", {"query": "新员工设备申领"})
        kb.docs = [d for d in kb.docs if d["id"] != "D03"]
        # Search is reading the current dataset, not a canned predetermined log.
        after = kb.execute("search_documents", {"query": "新员工设备申领"})
        self.assertNotEqual(before["results"], after["results"])

    def test_answer_changes_when_document_text_changes(self):
        original = json.loads(Path("data/documents.json").read_text(encoding="utf-8"))
        changed_text = "新员工入职后应在资产门户提交设备申请，由直属主管和IT资产管理员依次审批。"
        for item in original:
            if item["id"] == "D03":
                item["text"] = changed_text
                item["claim_value"] = "主管和IT资产管理员依次审批"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "documents.json"
            path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            value = run_agent(
                "新员工入职之后怎么领取办公电脑？",
                OfflineScriptedMock(),
                KnowledgeTools(path=path),
                mode="offline_mock",
            )
        self.assertEqual(value["status"], "complete")
        self.assertIn(changed_text, value["answer"])
        self.assertEqual(value["citations"][0]["quote"], changed_text)

    def test_different_tool_results_change_next_action_in_mock_flow(self):
        success = run("新员工入职之后怎么领取办公电脑？")
        failure = run("忘记系统密码怎么找回？", "search_documents")
        self.assertTrue(success["trace"][0]["tool_result"]["ok"])
        self.assertFalse(failure["trace"][0]["tool_result"]["ok"])
        self.assertEqual(success["trace"][0]["next_action"], "read_document")
        self.assertEqual(failure["trace"][0]["next_action"], "search_documents")

    def test_read_requires_search_result(self):
        kb = KnowledgeTools()
        result = kb.execute("read_document", {"document_id": "D03"})
        self.assertFalse(result["ok"])
        self.assertIn("successful search", result["error"])

    def test_not_found_requires_search(self):
        kb = KnowledgeTools()
        valid, message = kb.validate_answer({
            "status": "not_found", "answer": "没有资料", "citations": [],
            "gaps": ["未找到制度"],
        })
        self.assertFalse(valid)
        self.assertIn("successful document search", message)

    def test_empty_search_is_a_tool_error_not_valid_not_found_evidence(self):
        kb = KnowledgeTools()
        result = kb.execute("search_documents", {"query": ""})
        self.assertFalse(result["ok"])
        self.assertEqual(kb.successful_searches, 0)
        valid, _ = kb.validate_answer({
            "status": "not_found", "answer": "没有资料", "citations": [],
            "gaps": ["未找到制度"],
        })
        self.assertFalse(valid)

    def test_trace_contains_full_tool_observation(self):
        value = run("新员工入职之后怎么领取办公电脑？")
        search = value["trace"][0]
        self.assertTrue(search["tool_result"]["ok"])
        self.assertEqual(search["tool_result"]["results"][0]["document_id"], "D03")
        self.assertEqual(search["next_action"], "read_document")

    def test_model_error_is_traced(self):
        class BrokenModel:
            model = "broken-test-model"

            def next(self, messages, tools):
                raise TimeoutError("test timeout")

        value = run_agent("问题", BrokenModel(), KnowledgeTools(), mode="test")
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["model_steps_used"], 1)
        self.assertEqual(value["trace"][0]["action"], "model_error")
        self.assertEqual(value["trace"][0]["tool_result"]["error_type"], "TimeoutError")

    def test_parallel_calls_are_rejected_and_traced(self):
        class ParallelModel:
            model = "parallel-test-model"

            def __init__(self):
                self.turn = 0
                self.feedback_messages = []

            def next(self, messages, tools):
                self.turn += 1
                if self.turn == 2:
                    self.feedback_messages = list(messages)
                    return {"content": "", "tool_calls": [{
                        "id": "call-3", "name": "search_documents",
                        "arguments_json": json.dumps({
                            "query": "不存在的主题", "reason": "改为单步检索"
                        }, ensure_ascii=False),
                    }], "usage": {}}
                if self.turn == 3:
                    return {"content": "", "tool_calls": [{
                        "id": "call-4", "name": "submit_answer",
                        "arguments_json": json.dumps({
                            "status": "not_found", "answer": "未找到资料。",
                            "citations": [], "gaps": ["没有相关文档"],
                            "reason": "单次检索无结果"
                        }, ensure_ascii=False),
                    }], "usage": {}}
                def call(index):
                    return {"id": f"call-{index}", "name": "search_documents",
                            "arguments_json": json.dumps({"query": "设备", "reason": "test"})}
                return {"content": "", "tool_calls": [call(1), call(2)], "usage": {}}

        model = ParallelModel()
        value = run_agent("问题", model, KnowledgeTools(), mode="test")
        self.assertEqual(value["status"], "not_found")
        self.assertEqual(value["trace"][0]["action"], "model_protocol_error")
        self.assertEqual(value["trace"][0]["next_action"], "search_documents")
        self.assertEqual(value["tool_calls_used"], 2)
        feedback = [message for message in model.feedback_messages if message["role"] == "tool"]
        self.assertEqual(len(feedback), 2)
        self.assertTrue(all("Only one tool call" in message["content"] for message in feedback))

    def test_deepseek_required_tools_disable_thinking(self):
        captured = {}

        class Completions:
            def create(self, **kwargs):
                captured.update(kwargs)
                tool_calls = [
                    SimpleNamespace(id=f"deepseek-call-{index}", function=SimpleNamespace(
                        name="search_documents", arguments='{"query":"设备","reason":"测试"}'
                    ))
                    for index in (1, 2)
                ]
                message = SimpleNamespace(content="", tool_calls=tool_calls)
                usage = SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3,
                                        prompt_cache_hit_tokens=1, prompt_cache_miss_tokens=1)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

        model = LiveChatModel.__new__(LiveChatModel)
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        model.model = "deepseek-flash"
        model.is_deepseek_official = True
        result = model.next([], [])
        self.assertEqual(captured["tool_choice"], "required")
        self.assertEqual(captured["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertNotIn("parallel_tool_calls", captured)
        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["usage"]["prompt_cache_hit_tokens"], 1)

    def test_model_errors_redact_api_credentials(self):
        secret = "unit-test-secret-value-1234567890"

        class LeakyModel:
            model = "leaky-test-model"

            def next(self, messages, tools):
                raise RuntimeError(f"Authorization: Bearer {secret}; api_key={secret}")

        with patch.dict("os.environ", {"OPENAI_API_KEY": secret}):
            value = run_agent("问题", LeakyModel(), KnowledgeTools(), mode="test")
        serialized = json.dumps(value, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertIn("REDACTED", serialized)

    def test_parallel_conflict_proposal_recovers_and_requires_both_documents(self):
        class ConflictModel:
            model = "parallel-conflict-test-model"

            def __init__(self):
                self.turn = 0

            @staticmethod
            def call(call_id, name, arguments):
                return {"id": call_id, "name": name,
                        "arguments_json": json.dumps(arguments, ensure_ascii=False)}

            def next(self, messages, tools):
                self.turn += 1
                if self.turn == 1:
                    calls = [self.call("c1", "search_documents", {
                        "query": "差旅报销期限", "reason": "搜索制度"
                    })]
                elif self.turn == 2:
                    calls = [
                        self.call("c2", "read_document", {"document_id": "D01", "reason": "读取甲"}),
                        self.call("c3", "read_document", {"document_id": "D02", "reason": "读取乙"}),
                    ]
                elif self.turn == 3:
                    calls = [self.call("c4", "read_document", {"document_id": "D01", "reason": "先读甲"})]
                elif self.turn == 4:
                    calls = [self.call("c5", "submit_answer", {
                        "answer": "期限为30天。", "status": "complete", "gaps": [],
                        "citations": [{"document_id": "D01", "quote": "30个自然日"}],
                        "reason": "尝试单边提交"
                    })]
                elif self.turn == 5:
                    calls = [self.call("c6", "read_document", {"document_id": "D02", "reason": "补读乙"})]
                else:
                    calls = [self.call("c7", "submit_answer", {
                        "answer": "两份制度分别规定30天和15天，需确认适用制度。",
                        "status": "needs_clarification",
                        "citations": [
                            {"document_id": "D01", "quote": "员工应在差旅行程结束后30个自然日内提交报销申请。"},
                            {"document_id": "D02", "quote": "员工应在差旅行程结束后15个自然日内提交报销申请。"},
                        ],
                        "gaps": ["缺少适用范围和优先级"], "reason": "证据冲突"
                    })]
                return {"content": "", "tool_calls": calls, "usage": {}}

        value = run_agent("差旅报销最晚多久提交？", ConflictModel(), KnowledgeTools(), mode="test")
        self.assertEqual(value["status"], "needs_clarification")
        self.assertEqual({item["document_id"] for item in value["citations"]}, {"D01", "D02"})
        self.assertEqual(value["trace"][1]["action"], "model_protocol_error")
        rejected = [step for step in value["trace"] if step["action"] == "submit_answer"
                    and not step["tool_result"]["ok"]]
        self.assertIn("read all candidates", rejected[0]["tool_result"]["message"])

    def test_parallel_proposal_then_search_failure_still_requires_model_retry(self):
        class FailureRecoveryModel:
            model = "parallel-failure-test-model"

            def __init__(self):
                self.turn = 0

            @staticmethod
            def call(call_id, name, arguments):
                return {"id": call_id, "name": name,
                        "arguments_json": json.dumps(arguments, ensure_ascii=False)}

            def next(self, messages, tools):
                self.turn += 1
                if self.turn == 1:
                    calls = [
                        self.call("f1", "search_documents", {"query": "密码找回", "reason": "查询一"}),
                        self.call("f2", "search_documents", {"query": "密码重置", "reason": "查询二"}),
                    ]
                elif self.turn in (2, 3):
                    calls = [self.call(f"f{self.turn + 1}", "search_documents", {
                        "query": "忘记密码 重置", "reason": "观察失败后由模型决定重试"
                    })]
                elif self.turn == 4:
                    calls = [self.call("f5", "read_document", {"document_id": "D04", "reason": "读取原文"})]
                else:
                    calls = [self.call("f6", "submit_answer", {
                        "answer": "通过统一身份认证页面重置密码。", "status": "complete", "gaps": [],
                        "citations": [{"document_id": "D04", "quote":
                            "忘记办公系统密码时，在统一身份认证页面点击“忘记密码”并通过工号验证；验证失败可提交IT工单。"}],
                        "reason": "重试成功并读取原文"
                    })]
                return {"content": "", "tool_calls": calls, "usage": {}}

        value = run_agent("忘记系统密码怎么找回？", FailureRecoveryModel(),
                          KnowledgeTools(fail_once="search_documents"), mode="test")
        self.assertEqual(value["status"], "complete")
        failed_search = next(step for step in value["trace"]
                             if step["action"] == "search_documents"
                             and not step["tool_result"]["ok"])
        self.assertEqual(failed_search["next_action"], "search_documents")
        self.assertEqual(value["trace"][0]["action"], "model_protocol_error")


if __name__ == "__main__":
    unittest.main()
