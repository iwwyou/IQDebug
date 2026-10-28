#!/usr/bin/env python3
"""
Run an IntentChecker case JSON file and print results.

Usage:
    python run_case.py <case.json>
    python run_case.py evaluation/RQ1/cases/web3bugs_5_H_07/web3bugs_5_H_07.json
"""
import json, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from main import simulate_inputs, sa, contract_analyzer, batch_mgr


def run(path: str):
    records = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    case_name = pathlib.Path(path).stem
    print(f"{'='*60}")
    print(f"  Case: {case_name}")
    print(f"  Records: {len(records)}")
    print(f"{'='*60}\n")

    simulate_inputs(records, silent=True)

    # ── 결과 수집 ──
    # 1) analysis_per_line (Engine._process_node_intents 경로)
    apl = getattr(contract_analyzer, 'analysis_per_line', {})
    # 2) recorder.ledger (ContractAnalyzer.process_during 경로)
    ledger = contract_analyzer.recorder.ledger

    results = []

    for ln, entries in sorted(apl.items()):
        for entry in entries:
            if entry.get('type') in ('during_intent', 'post_intent'):
                r = entry.get('result', {})
                results.append({
                    'line': ln,
                    'type': entry['type'],
                    'status': r.get('status', 'unknown'),
                    'message': r.get('message', ''),
                    'risk': r.get('risk_score', ''),
                })

    for ln, entries in sorted(ledger.items()):
        for entry in entries:
            if entry.get('kind') == 'verification':
                results.append({
                    'line': ln,
                    'type': entry.get('verification_type', '?'),
                    'status': entry.get('status', 'unknown'),
                    'message': entry.get('message', ''),
                    'risk': '',
                })

    # ── 결과 출력 ──
    if results:
        print(f"\n{'='*60}")
        print(f"  Verification Results")
        print(f"{'='*60}")
        violated = sum(1 for r in results if r['status'] in ('violated', 'violation'))
        satisfied = sum(1 for r in results if r['status'] == 'satisfied')
        warning = sum(1 for r in results if r['status'] not in ('violated', 'violation', 'satisfied'))
        print(f"  Total: {len(results)}  |  Violated: {violated}  |  Satisfied: {satisfied}  |  Warning: {warning}\n")

        for r in results:
            status_icon = "X" if r['status'] in ('violated', 'violation') else ("O" if r['status'] == 'satisfied' else "?")
            print(f"  [{status_icon}] L{r['line']:>4} | {r['type']:>14} | {r['status']:>10} | {r['message'][:80]}")
        print(f"{'='*60}")
    else:
        print("\n  (No verification results)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_case.py <case.json>")
        sys.exit(1)
    run(sys.argv[1])
