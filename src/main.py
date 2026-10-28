"""CLI: python -m src.main --help"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .agent import run_agent, AgentConfig
from .model import LiveChatModel
from .mock_model import OfflineScriptedMock
from .tools import KnowledgeTools


def main() -> None:
    p = argparse.ArgumentParser(description="Evidence-grounded enterprise FAQ agent")
    p.add_argument("--mode", choices=["live", "offline"], default="offline",
                   help="offline is a scripted mock, NOT evidence of LLM decisions")
    p.add_argument("--question", required=True)
    p.add_argument("--history", default="", help="Path to a JSON array of user/assistant messages")
    p.add_argument("--inject-failure", choices=["search_documents", "read_document"], default="")
    p.add_argument("--max-steps", type=int, default=9)
    p.add_argument("--max-tool-calls", type=int, default=7)
    p.add_argument("--save", default="", help="Write full output and trace JSON here")
    args = p.parse_args()
    history = json.loads(Path(args.history).read_text(encoding="utf-8")) if args.history else []
    model = LiveChatModel() if args.mode == "live" else OfflineScriptedMock()
    result = run_agent(args.question, model, KnowledgeTools(fail_once=args.inject_failure),
                       history=history, mode=("live_llm" if args.mode == "live" else "offline_mock"),
                       config=AgentConfig(args.max_steps, args.max_tool_calls))
    if args.save:
        target = Path(args.save)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
