"""S0: Vault 케이스들로 W1·W1s·W2·W3 자동 실행 → 손 실행 결과 · 구체 기준값과 대조 (EXPERIMENT_PLAN §0.6a-1).

사용: .venv/Scripts/python.exe evaluation/baselines/harness/run_s0.py [case.json ...]   (인자 없으면 cases/s0_*.json 전부)
출력: harness/results/s0.json
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C          # noqa: E402
import workflows as W       # noqa: E402


def check(rows, case_id, wf, label, got, want):
    ok = got == want
    rows.append({"case": case_id, "workflow": wf, "check": label, "ok": ok, "got": got, "want": want})
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: got={got} want={want}")
    return ok


def run_case(path, rows):
    case = C.load_case(path)
    cid = case["case_id"]
    exp = case["expected_manual"]
    print(f"\n######## {cid}  ({case['function']}, access={case.get('access')}, expect {case['expected_termination']})")
    base = C.prepare_base(case)
    print("slots:", [(s["path"], s["slot"], s["getter_sig"]) for s in base["slots"]], "harness:", base["harness_file"])

    res = {}
    with C.Anvil() as anvil:
        res["W2"] = W.w2(case, base, anvil)
        addr = res["W2"].get("artifacts", {}).get("address")
        res["truth"] = W.truth(case, base, anvil, addr) if addr else None
    res["W1"] = W.w1(case, base)
    res["W1s"] = W.w1(case, base, use_stdstore=True)
    res["W3"] = W.w3(case, base)

    ok = True
    t = res["truth"]
    print("== 구체 기준값 (anvil) vs 손 실행 ==")
    if not t:
        print("  [FAIL] truth unavailable")
        return False, res
    ok &= check(rows, cid, "truth", "termination", t["termination"], case["expected_termination"])
    ok &= check(rows, cid, "truth", "ret0", t["ret0"], exp["ret0"])
    ok &= check(rows, cid, "truth", "post", t["post"], exp["post"])
    if t["revert_diff_empty"] is not None:
        ok &= check(rows, cid, "truth", "revert → diff post empty", t["revert_diff_empty"], True)
    for w in ("W1", "W1s", "W2", "W3"):
        r = res[w]
        print(f"== {w} (setup={r.get('setup')}, steps={r.get('steps')}, sec={r.get('sec')}, "
              f"artifacts={ {k: v for k, v in r.get('artifacts', {}).items() if k.startswith(('A_', 'E_', 'n_'))} }) ==")
        if r.get("setup") != "OK":
            ok = False
            rows.append({"case": cid, "workflow": w, "check": "setup", "ok": False, "got": r.get("setup")})
            print(r.get("log", "")[-2500:])
            continue
        ok &= check(rows, cid, w, "termination", r["termination"], case["expected_termination"])
        ok &= check(rows, cid, w, "ret0", r["ret0"], exp["ret0"])
        if r["post"] is None:
            print("  [n/a] post: cast call 은 상태를 바꾸지 않음 — W2 의 G2 는 tracer 경로(위 기준값)")
        else:
            ok &= check(rows, cid, w, "post", r["post"], exp["post"])
        for line, want in exp["points"].items():
            ok &= check(rows, cid, w, f"line {line} before-values", r["points"].get(line), want)
    return ok, res


def main():
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "cases", "s0_*.json")))
    rows, summary, allres = [], {}, {}
    for p in paths:
        ok, res = run_case(p, rows)
        cid = os.path.splitext(os.path.basename(p))[0]
        summary[cid] = ok
        allres[cid] = {k: {kk: vv for kk, vv in v.items() if kk != "log"} if isinstance(v, dict) else v
                       for k, v in res.items()}
    os.makedirs(C.RESULTS, exist_ok=True)
    out = os.path.join(C.RESULTS, "s0.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"pass": all(summary.values()), "cases": summary, "checks": rows, "results": allres},
                  f, ensure_ascii=False, indent=1)
    print("\n======== S0 summary ========")
    for k, v in summary.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    print(f"checks: {sum(r['ok'] for r in rows)}/{len(rows)} → {out}")
    sys.exit(0 if all(summary.values()) else 1)


if __name__ == "__main__":
    main()
