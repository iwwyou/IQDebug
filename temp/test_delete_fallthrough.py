# -*- coding: utf-8 -*-
"""삭제 수술의 fall-through 복원 회귀 (2026-11-13, KNOWN_ISSUES I1).

`delete_cfg_range` 가 지운 구간의 뒤 노드 중 공유 sink 를 모두 버려, 함수 끝 `}` 바로 앞 문장·블록을 지우면
앞 노드가 EXIT 에 다시 이어지지 않아 EXIT 가 ⊥ 였다. 흘러 지나가는 간선(→ 함수 EXIT)은 잇고, 지운 문장의
제어 효과(require/assert → ERROR, return → RETURN, break → loop exit)는 잇지 않아야 한다.

같은 최종 소스를 증분(update)과 새 분석(analyze)으로 각각 만들어 비교한다.
  - EXIT 의 모든 변수 값
  - 줄별 상태(문장이 있는 줄의 노드 OUT 상태)
  - 조건 노드마다 (줄, 종류, 간선 라벨) → 그 분기가 ERROR / RETURN / EXIT 에 닿는지
  - ENTRY 에서 EXIT 도달
사용: PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe temp/test_delete_fallthrough.py
"""
import contextlib, io, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import engine_api
from Dependencies.main import slice_solidity

fails = 0
def check(name, ok, extra=""):
    global fails
    print(("  ok  " if ok else "  FAIL ") + name + ("" if ok else "\n        " + str(extra)))
    fails += 0 if ok else 1


def W(body):
    return ("contract C {\n    uint256 public total;\n    function f(uint256 n) public {\n"
            + "\n".join("        " + x for x in body.split("\n")) + "\n    }\n}")

INP = {"n": {"t": "interval", "lo": "0", "hi": "10"}, "total": {"t": "interval", "lo": "0", "hi": "0"}}


def summarize(ev):
    cfg = ev.cfg
    by = {n.id: n for n in cfg.nodes}
    succ = {}
    for e in cfg.edges:
        succ.setdefault(e.from_, []).append((e.to, e.kind))

    def reach(src):
        seen, st = set(), [src]
        while st:
            x = st.pop()
            if x in seen:
                continue
            seen.add(x)
            st.extend(t for t, _ in succ.get(x, ()))
        return seen

    sinks = {"ERROR": cfg.error_exit, "RETURN": cfg.return_exit, "EXIT": cfg.exit}
    branches = set()
    for n in cfg.nodes:
        if not n.cond_type:
            continue
        for t, k in succ.get(n.id, ()):
            if k not in ("true", "false"):
                continue
            r = reach(t)
            branches.add((n.src_line, n.cond_type, k,
                          tuple(name for name, s in sinks.items() if s is not None and s in r)))
    exit_state = {k: v for k, v in by[cfg.exit].state.items() if k in ("n", "total")}
    return {"exit": exit_state, "exit_reachable": cfg.exit in reach(cfg.entry), "branches": branches}


def run(before, after):
    with contextlib.redirect_stdout(io.StringIO()):
        s = engine_api.EngineSession()
        s.analyze(slice_solidity(W(before)), contract="C", func="f")
        r = s.update(W(after), contract="C", func="f")
        inc = summarize(s.evaluate(INP, contract="C", func="f"))
        f = engine_api.EngineSession()
        f.analyze(slice_solidity(W(after)), contract="C", func="f")
        fresh = summarize(f.evaluate(INP, contract="C", func="f"))
    return r, inc, fresh


CASES = [
    # (이름, 이전 본문, 이후 본문)
    ("마지막 문장 삭제", "total = 1;\ntotal = total + 2;", "total = 1;"),
    ("가운데 문장 삭제", "total = 1;\ntotal = total + 2;\ntotal = total + 3;", "total = 1;\ntotal = total + 3;"),
    ("if 뒤 마지막 문장 삭제", "if (n > 5) {\n    total = 1;\n}\ntotal = total + 2;", "if (n > 5) {\n    total = 1;\n}"),
    ("I1 원래 사례(앞 추가 + 뒤 삭제)", "if (n > 5) {\n    total = 1;\n}\ntotal = total + 2;",
     "total = 9;\nif (n > 5) {\n    total = 1;\n}"),
    ("마지막 if 블록 통째 삭제", "total = 1;\nif (n > 5) {\n    total = 2;\n}", "total = 1;"),
    ("마지막 if-else 블록 통째 삭제", "total = 1;\nif (n > 5) {\n    total = 2;\n} else {\n    total = 3;\n}", "total = 1;"),
    ("마지막 while 통째 삭제", "total = 1;\nwhile (total < n) {\n    total = total + 1;\n}", "total = 1;"),
    ("마지막 for 통째 삭제", "total = 1;\nfor (uint256 i = 0; i < n; i++) {\n    total = total + 1;\n}", "total = 1;"),
    ("if 본문의 마지막 문장 삭제", "if (n > 5) {\n    total = 1;\n    total = 2;\n}", "if (n > 5) {\n    total = 1;\n}"),
    ("while 본문의 마지막 문장 삭제", "while (total < n) {\n    total = total + 1;\n    total = total + 2;\n}",
     "while (total < n) {\n    total = total + 1;\n}"),
    ("while 뒤 마지막 문장 삭제", "while (total < n) {\n    total = total + 1;\n}\ntotal = 7;",
     "while (total < n) {\n    total = total + 1;\n}"),
    ("안쪽 while 이 바깥 본문의 마지막 — 삭제",
     "while (total < n) {\n    total = total + 1;\n    while (total < 3) {\n        total = total + 1;\n    }\n}",
     "while (total < n) {\n    total = total + 1;\n}"),
    ("while 본문 안 if 블록 삭제",
     "while (total < n) {\n    total = total + 1;\n    if (total > 5) {\n        total = total + 1;\n    }\n}",
     "while (total < n) {\n    total = total + 1;\n}"),
    ("while 본문 안 가운데 if 블록 삭제",
     "while (total < n) {\n    if (total > 5) {\n        total = total + 1;\n    }\n    total = total + 1;\n}",
     "while (total < n) {\n    total = total + 1;\n}"),
    ("마지막 require 삭제(ERROR 간선 제거)", "total = 1;\nrequire(n > 3);", "total = 1;"),
    ("첫 require 삭제(ERROR 간선 제거)", "require(n > 3);\ntotal = 1;", "total = 1;"),
    ("마지막 assert 삭제", "total = 1;\nassert(n > 3);", "total = 1;"),
    ("require 뒤 마지막 문장 삭제(ERROR 간선 보존)", "require(n > 3);\ntotal = 1;\ntotal = total + 2;",
     "require(n > 3);\ntotal = 1;"),
    ("루프 본문 마지막 require 삭제", "while (total < n) {\n    total = total + 1;\n    require(total < 8);\n}",
     "while (total < n) {\n    total = total + 1;\n}"),
]

print("[1] 증분 update ≡ 새 analyze (EXIT 값·분기 라벨·sink 도달)")
for name, b, a in CASES:
    try:
        r, inc, fresh = run(b, a)
    except Exception as e:
        check(name, False, "예외 %s: %s" % (type(e).__name__, e))
        continue
    if isinstance(r, dict) and r.get("ok") is False:
        check(name, False, "update 거부 %s" % r.get("diagnostics"))
        continue
    problems = []
    if not inc["exit_reachable"]:
        problems.append("EXIT 미도달")
    if inc["exit"] != fresh["exit"]:
        problems.append("EXIT 증분=%s 새분석=%s" % (inc["exit"], fresh["exit"]))
    if inc["branches"] != fresh["branches"]:
        problems.append("분기 증분-새분석=%s 새분석-증분=%s"
                        % (sorted(inc["branches"] - fresh["branches"]), sorted(fresh["branches"] - inc["branches"])))
    check(name, not problems, "; ".join(problems))

print("실패 %d" % fails)
sys.exit(1 if fails else 0)
