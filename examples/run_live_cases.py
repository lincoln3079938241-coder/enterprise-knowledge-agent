"""Real-model evaluation runner. Requires OPENAI_* environment variables."""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from src.agent import run_agent, AgentConfig
from src.model import LiveChatModel
from src.tools import KnowledgeTools
from examples.generate_examples import CASES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Run all 7 acceptance scenarios; costs API tokens")
    ap.add_argument("--case", action="append", choices=[case[0] for case in CASES],
                    help="Run only one named case; repeat this option for multiple failed cases")
    ap.add_argument("--out", default="examples/live")
    args = ap.parse_args()
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        model = LiveChatModel()
    except Exception as exc:
        evidence = {
            "mode": "live_preflight_error",
            "status": "blocked",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "environment_present": {
                name: bool(os.environ.get(name, "").strip())
                for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
            },
            "run_utc": datetime.now(timezone.utc).isoformat(),
            "note": "No model output or live trace was produced.",
        }
        path = dest / "preflight-error.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"Live-model preflight failed; evidence saved to {path}: {exc}") from exc
    if args.case:
        selected_names = set(args.case)
        selection = [case for case in CASES if case[0] in selected_names]
    else:
        selection = CASES if args.all else [CASES[0], CASES[1], CASES[5]]
    for name, question, failure, history in selection:
        result = run_agent(question, model, KnowledgeTools(fail_once=failure),
                           history=history, mode="live_llm", config=AgentConfig())
        filename = f"{name}.json"
        (dest / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(name, result["status"], result["token_usage_reported"])
    manifest = []
    for name, *_ in CASES:
        path = dest / f"{name}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        manifest.append({"file": path.name, "status": result["status"],
                         "token_usage_reported": result["token_usage_reported"],
                         "trace_actions": [x["action"] for x in result["trace"]]})
    (dest / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
