"""Quick consistency checks for REAL LLM traces. Not a semantic ground-truth grader."""
from __future__ import annotations
import json
from pathlib import Path

from src.tools import DATA_FILE

EXPECTED = {
    '01-normal': 'complete',
    '02-conflict': 'needs_clarification',
    '03-out-of-scope': 'not_found',
    '04-paraphrase': 'complete',
    '05-follow-up': 'complete',
    '06-search-failure': 'complete',
    '07-read-failure': 'complete',
}


def main():
    home = Path('examples/live')
    problems = []
    docs = {
        item['id']: item
        for item in json.loads(DATA_FILE.read_text(encoding='utf-8'))
    }
    case_results = {}
    for case, expected in EXPECTED.items():
        path = home / f'{case}.json'
        if not path.exists():
            problems.append(f'{case}: no live trace recorded')
            continue
        result = json.loads(path.read_text(encoding='utf-8'))
        case_results[case] = result
        if result.get('mode') != 'live_llm':
            problems.append(f'{case}: this is not a live LLM trace')
        if result['status'] != expected:
            problems.append(f'{case}: expected {expected}, received {result["status"]}')
        acts = [t['action'] for t in result.get('trace', [])]
        if 'search_documents' not in acts or 'submit_answer' not in acts:
            problems.append(f'{case}: search and structured finish not both present')
        if case == '02-conflict' and not {'D01', 'D02'}.issubset(
            {x['document_id'] for x in result.get('citations', [])}
        ):
            problems.append(f'{case}: D01 / D02 were not both cited')
        if case.endswith('failure') and not any('FAIL:' in x['tool_result_summary'] for x in result.get('trace', [])):
            problems.append(f'{case}: expected injected failure not observed')
        for index, step in enumerate(result.get('trace', []), start=1):
            for field in ('tool_args', 'tool_result', 'decision_basis', 'next_action'):
                if field not in step:
                    problems.append(f'{case}: trace step {index} misses {field}')
        for citation in result.get('citations', []):
            document_id = citation.get('document_id')
            quote = citation.get('quote')
            if document_id not in docs or not isinstance(quote, str) or quote not in docs[document_id]['text']:
                problems.append(f'{case}: citation is not a verbatim quote from {document_id}')

    # A successful result and a failed result must lead the LLM to different next actions.
    normal = case_results.get('01-normal', {}).get('trace', [])
    failed = case_results.get('06-search-failure', {}).get('trace', [])
    normal_search = next((x for x in normal if x.get('action') == 'search_documents'
                          and x.get('tool_result', {}).get('ok')), None)
    failed_search = next((x for x in failed if x.get('action') == 'search_documents'
                          and not x.get('tool_result', {}).get('ok')), None)
    if normal_search and failed_search:
        if failed_search.get('next_action') in (None, 'END'):
            problems.append('dynamic-path check: failed search was not followed by an LLM-selected action')
        if normal_search.get('next_action') == failed_search.get('next_action'):
            problems.append('dynamic-path check: successful and failed search led to the same next action')
    elif len(case_results) == len(EXPECTED):
        problems.append('dynamic-path check: could not find both successful and failed search observations')
    if problems:
        print('NOT READY - Review live runs:')
        for message in problems:
            print(' -', message)
        raise SystemExit(1)
    print('PASS: basic status/coverage checks on seven real-model records.')
    print('Dynamic path:',
          f"successful search -> {normal_search.get('next_action')};",
          f"failed search -> {failed_search.get('next_action')}.")
    print('Manual inspection of grounding and follow-up decision quality is still required.')


if __name__ == '__main__':
    main()
