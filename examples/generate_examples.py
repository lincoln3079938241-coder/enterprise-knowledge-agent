"""Generate clearly labeled offline MOCK traces. Live traces must be produced separately."""
from __future__ import annotations
import json
from pathlib import Path
from src.agent import AgentConfig, run_agent
from src.tools import KnowledgeTools
from src.mock_model import OfflineScriptedMock

CASES = [
    ("01-normal", "新员工入职之后怎么领取办公电脑？", "", []),
    ("02-conflict", "差旅报销最晚多久提交？", "", []),
    ("03-out-of-scope", "食堂今日晚餐菜单是什么？", "", []),
    ("04-paraphrase", "刚入职的新人要领笔记本怎么办？", "", []),
    ("05-follow-up", "需要主管审批吗？", "", [
        {"role": "user", "content": "新员工怎么领取电脑？"},
        {"role": "assistant", "content": "可向IT服务台申领设备。"}]),
    ("06-search-failure", "忘记系统密码怎么找回？", "search_documents", []),
    ("07-read-failure", "远程办公如何登录VPN？", "read_document", []),
]


def generate(output_dir: Path = Path("examples/offline_mock")) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for name, q, failure, history in CASES:
        result = run_agent(q, OfflineScriptedMock(), KnowledgeTools(fail_once=failure),
                           history=history, config=AgentConfig(), mode="offline_mock")
        (output_dir / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary.append({"file": f"{name}.json", "status": result["status"],
                        "actions": [r["action"] for r in result["trace"]],
                        "model_mode": "OFFLINE SCRIPTED MOCK - NOT real model evidence"})
    (output_dir / "index.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
