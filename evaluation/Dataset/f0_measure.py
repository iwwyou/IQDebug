# -*- coding: utf-8 -*-
"""F0 파일럿 — 동결 데이터셋(415 파일 · 2,653 함수) 위의 적용 범위 측정 (2026-11-20, ROADMAP §F0).

선정 함수마다 analyze → 슬롯 → 기본 입력으로 evaluate → 탐색 성질 검사를 하고, 어디서 왜 멈췄는지를 함수 단위로 남긴다.

  기본 입력(D1 입력 생성기 이전의 가장 단순한 판):
    interval 슬롯 = 타입 전 범위, bool = any, address = 슬롯마다 서로 다른 symbolicAddress 1개.
    `requiresIndex` 슬롯(인덱스가 지역변수)은 넣을 수 없으므로 건너뛰고 수만 센다.

  함수마다 기록:
    stage      어디서 멈췄나 — deps_load / analyze / structure / resolve / evaluate / nav / ok
    reason     멈춘 이유(예외 종류 + 메시지 앞부분)
    slots      종류별 수(Param/StateVar/GlobalVar/IReturn), opaque, requiresIndex, 입력 오류
    nav        S1 대칭 · S2 줄 단사 · S3 frontier · S4 탈출 요약 위반 (`temp/test_nav_symmetry.check` 와 같은 규칙)
    states     노드 수, 부분 ⊥ 노드(F0-3), 빈 in_state 노드(F0-4), EXIT 의 ⊤·⊥ 변수 수, 엔진 top_vars
    calls      enterable callee 수, opaque 호출 지점 수
    ast        (solc AST) 미구현 함수 도달 여부·이름, immutable 참조, 소속 계약 abstract·fullyImplemented
    dataset    tier · layer · CG 분류 · 저수준 call 여부 · deps 상태(타깃 단위 미완 / 의존 단위만 미완 / 완전)
    time       analyze(파일 단위) · structure · evaluate 초

  격리: 타깃 파일 하나 = 하위 프로세스 하나(--worker). 시간 초과·메모리 문제가 다른 타깃을 오염시키지 않고,
  함수 결과는 한 줄씩 jsonl 로 바로 쓰므로 시간 초과 전까지의 결과는 남는다. 한 함수에서 예외가 나면 세션을
  새로 만들어 다음 함수로 간다(batch restore 가 안 된 세션을 재사용하지 않는다).

사용:
  .venv/Scripts/python.exe evaluation/Dataset/f0_measure.py --jobs 6
  .venv/Scripts/python.exe evaluation/Dataset/f0_measure.py --only WrappedPosition --jobs 1
  .venv/Scripts/python.exe evaluation/Dataset/f0_measure.py --report-only        (기존 결과로 집계만)
출력: evaluation/Dataset/f0_results/<타깃 id>.jsonl, summary.json
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback

DS = pathlib.Path(__file__).resolve().parent
REPO = DS.parents[1]
for p in (str(REPO), str(DS), str(REPO / "temp")):
    if p not in sys.path:
        sys.path.insert(0, p)

TARGETS = DS / "target_functions.json"
DEPS = DS / "deps"
NORM = DS / "norm_sources"
OUT = DS / "f0_results"
PY = sys.executable


def lp(p) -> str:
    return "\\\\?\\" + os.path.abspath(str(p)) if os.name == "nt" else str(p)


# ── 데이터셋 ─────────────────────────────────────────────────────────────────
def selected_files():
    data = json.loads(TARGETS.read_text(encoding="utf-8"))
    return [r for r in data["files"] if r.get("stage") == "selected"]


def deps_status(info, fn_contract):
    """타깃 단위가 미완인가, 의존 단위만 미완인가."""
    nc = info.get("not_complete") or {}
    if "build_error" in info:
        return "build_error"
    if not nc:
        return "complete"
    return "target_unit_incomplete" if info["unit"] in nc else "deps_only_incomplete"


# ── solc AST 쪽 사실 (엔진과 독립) ───────────────────────────────────────────
def ast_facts(target, man_entry, sel_src):
    """{src: {...}} — 미구현 함수 도달(내부 호출 전이 폐포, modifier 포함), immutable 참조, 계약 abstract 여부."""
    import compile_survey as cs
    from compile_survey import walk
    root = NORM / man_entry["id"]
    src = {n: open(lp(root / n.replace("/", os.sep)), encoding="utf-8").read() for n in man_entry["files"]}
    _, solc = cs.pick_solc(src)
    _, asts, err = cs.compile_(src, solc, True)
    if asts is None:
        return None, "compile: %s" % str(err)[:120]
    by_id = {}
    for a in asts.values():
        walk(a, lambda n, _p: by_id.__setitem__(n.get("id"), n) if n.get("id") is not None else None)

    def closure_facts(fd):
        seen, stack, unimpl, imm = set(), [fd], [], set()
        while stack:
            f = stack.pop()
            if f.get("id") in seen:
                continue
            seen.add(f.get("id"))

            def v(n, _p):
                if n.get("nodeType") == "Identifier":
                    d = by_id.get(n.get("referencedDeclaration"))
                    if d and d.get("nodeType") == "VariableDeclaration" and d.get("mutability") == "immutable":
                        imm.add(d.get("name"))
                if n.get("nodeType") != "FunctionCall":
                    return
                e = n.get("expression") or {}
                if e.get("nodeType") != "Identifier":          # 주소·인터페이스 경유 = CG3, 여기서는 내부 이름 호출만
                    return
                d = by_id.get(e.get("referencedDeclaration"))
                if not d or d.get("nodeType") != "FunctionDefinition":
                    return
                (stack.append(d) if d.get("implemented") else unimpl.append(d.get("name")))
            walk(f.get("body") or {}, v)
            for m in f.get("modifiers") or []:
                d = by_id.get((m.get("modifierName") or {}).get("referencedDeclaration"))
                if d and d.get("nodeType") == "ModifierDefinition":
                    stack.append(d)
        return sorted(set(unimpl)), sorted(imm)

    out = {}
    for c in asts[man_entry["entry"]].get("nodes", []):
        if c.get("nodeType") != "ContractDefinition":
            continue
        for fd in c.get("nodes", []):
            if fd.get("nodeType") == "FunctionDefinition" and fd.get("src") in sel_src:
                un, im = closure_facts(fd)
                out[fd["src"]] = {"contract_abstract": bool(c.get("abstract")),
                                  "contract_fully_implemented": bool(c.get("fullyImplemented")),
                                  "reaches_unimplemented": un, "immutables_read": im}
    return out, None


# ── 엔진 쪽 ─────────────────────────────────────────────────────────────────
_SOLC_PREFIX = re.compile(r"\b(contract|struct|enum)\s+")


def engine_param_type(t: str) -> str:
    """solc typeString → 엔진 시그니처 표기 근사 (`contract IERC20` → `IERC20`). 나머지는 engine_api._norm_type 이 맞춘다."""
    return _SOLC_PREFIX.sub("", t).replace(" ", "")


def default_values(slots):
    vals, skipped, aid = {}, 0, 1
    for s in slots:
        if s.requiresIndex:
            skipped += 1
            continue
        # 동적 배열 길이: 비워 두면 엔진이 미지 길이(⊤)로 둔다. 전 범위 구간을 명시하면 `DebugInitializer` 가
        # 구체 길이만 받으므로 오류가 난다(재측정 1: 176건) — 기본 입력 = 미지정.
        if s.key.endswith(".length"):
            continue
        if s.valueKind == "interval":
            b = s.bits or 256
            lo, hi = ((-(1 << (b - 1)), (1 << (b - 1)) - 1) if s.signed else (0, (1 << b) - 1))
            vals[s.key] = {"t": "interval", "lo": str(lo), "hi": str(hi)}
        elif s.valueKind == "bool":
            vals[s.key] = {"t": "bool", "v": "any"}
        elif s.valueKind == "address":
            vals[s.key] = {"t": "address", "id": aid}
            aid += 1
    return vals, skipped


def short_exc(e) -> str:
    return "%s: %s" % (type(e).__name__, str(e).replace("\n", " ")[:200])


def state_facts(cfg):
    ex = next((n for n in cfg.nodes if n.id == cfg.exit), None)
    exit_state = ex.state if ex else {}
    partial_bot = sum(1 for n in cfg.nodes
                      if n.state and any(v == "⊥" for v in n.state.values())
                      and not all(v == "⊥" for v in n.state.values()))
    empty_in = sum(1 for n in cfg.nodes if n.state and not n.in_state)
    callees = [r for n in cfg.nodes for r in (n.callees or [])]
    return {"nodes": len(cfg.nodes),
            "partial_bottom_nodes": partial_bot,
            "empty_in_state_nodes": empty_in,
            "exit_vars": len(exit_state),
            "exit_top_vars": sum(1 for v in exit_state.values() if "⊤" in v or v == "top"),
            "exit_bottom_vars": sum(1 for v in exit_state.values() if v == "⊥"),
            "enterable_calls": sum(1 for r in callees if r.enterable),
            "opaque_calls": sum(1 for r in callees if not r.enterable)}


def nav_facts(cfg):
    from test_nav_symmetry import check
    bad = check("f0", cfg)
    kinds = collections.Counter(b.split(":", 1)[0] if b.startswith(("S1", "S2", "S4")) else "S3" for b in bad)
    return {k: kinds.get(k, 0) for k in ("S1", "S2", "S3", "S4")}, bad[:3]


def worker(target: str) -> int:
    """타깃 파일 하나의 선정 함수 전부 — 한 줄에 함수 하나씩 stdout 으로 JSON."""
    import engine_api
    rec = next(r for r in selected_files() if r["target"] == target)
    info = json.loads((DEPS / "targets.json").read_text(encoding="utf-8"))["targets"][target]
    man = json.loads((NORM / "manifest.json").read_text(encoding="utf-8"))["targets"][target]
    fns = [f for f in rec["functions"] if f["selected"]]
    sel_src = {f["definition_id"].split("|")[-1]: f for f in fns}

    try:
        facts, ast_err = ast_facts(target, man, sel_src)
    except Exception as e:                                                       # noqa: BLE001
        facts, ast_err = None, short_exc(e)
    root = DEPS / info["deps"]
    source = (root / "sources" / (info["unit"] + ".sol")).read_text(encoding="utf-8")

    def emit(d):
        sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    sess, t_an = None, None

    def new_session(r):
        nonlocal t_an
        t0 = time.time()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            r["stage"] = "deps_load"
            s = engine_api.EngineSession(str(root), tuple(info["exclude"]))
            r["stage"] = "analyze"
            st = s.analyze_source(source)
        t_an = time.time() - t0
        return s, st

    for f in fns:
        src_id = f["definition_id"].split("|")[-1]
        calls = f.get("calls") or {}
        r = {"target": target, "id": info["deps"], "definition_id": f["definition_id"],
             "contract": f["contract"], "name": f["name"], "sig": f["sig"], "tier": f["tier"],
             "layer": f["layer"], "visibility": f["visibility"], "mutability": f["mutability"],
             "deps": deps_status(info, f["contract"]),
             "cg": {k: v for k, v in calls.items() if ":" not in k},
             "lowlevel_call": any(k.startswith("CG3:lowlevel_") or k.startswith("CG3:assembly_") for k in calls),
             "ast": (facts or {}).get(src_id), "ast_error": ast_err,
             "stage": None, "reason": None, "time": {}}
        # (2026-11-20) 함수마다 새 세션 — 한 세션에서 evaluate 를 두 번 하면 두 번째의 callee 캡처가 깨지는
        #   엔진 버그(`callee capture: call expression not found`)가 있어, 세션을 이어 쓰면 측정이 실행 순서에 의존한다.
        #   함수별 독립 실행이 실험 조건으로도 맞다.
        sess = None
        try:
            if sess is None:
                sess, st0 = new_session(r)
                if st0.diagnostics:
                    r["analyze_diagnostic"] = str(st0.diagnostics[0].message)[:200]
            r["time"]["analyze_file"] = round(t_an, 3)
            r["stage"] = "structure"
            t0 = time.time()
            with contextlib.redirect_stdout(io.StringIO()):
                sess.set_target_param_types([engine_param_type(p["type"]) for p in f["params"]])
                st = sess.structure(contract=f["contract"], func=f["name"])
            r["time"]["structure"] = round(time.time() - t0, 3)
            r["resolution"] = sess.target_resolution()
            if not st.cfg.nodes:
                r["stage"], r["reason"] = "resolve", ("함수 CFG 없음 — " + (str(st.diagnostics[0].message)[:160]
                                                                         if st.diagnostics else "진단 없음"))
                emit(r)
                continue
            kinds = collections.Counter(s.kind for s in st.debug_slots)
            vals, skipped = default_values(st.debug_slots)
            r["slots"] = {"total": len(st.debug_slots), **dict(kinds),
                          "opaque": sum(1 for s in st.debug_slots if s.opaque),
                          "requires_index_skipped": skipped,
                          "immutable_keys": sorted(s.key for s in st.debug_slots
                                                   if s.kind == "StateVar" and r["ast"]
                                                   and s.key.split("[")[0].split(".")[0] in r["ast"]["immutables_read"])}
            r["stage"] = "evaluate"
            t0 = time.time()
            with contextlib.redirect_stdout(io.StringIO()):
                ev = sess.evaluate(vals, contract=f["contract"], func=f["name"])
            r["time"]["evaluate"] = round(time.time() - t0, 3)
            r["slots"]["value_errors"] = len(ev.diagnostics)
            if ev.diagnostics:
                r["value_error_first"] = str(ev.diagnostics[0].message)[:160]
            if not any(n.state for n in ev.cfg.nodes):
                r["stage"], r["reason"] = "evaluate", "states 없음 (입력 0개 또는 전부 입력 오류)"
                emit(r)
                continue
            r["states"] = state_facts(ev.cfg)
            r["top_vars_engine"] = len(ev.top_vars)
            r["stage"] = "nav"
            r["nav"], r["nav_examples"] = nav_facts(ev.cfg)
            r["stage"] = "ok"
        except Exception as e:                                                   # noqa: BLE001
            r["reason"] = short_exc(e)
            tb = traceback.extract_tb(e.__traceback__)
            r["where"] = ["%s:%d %s" % (os.path.relpath(x.filename, REPO) if x.filename.startswith(str(REPO)) else
                                        os.path.basename(x.filename), x.lineno, x.name) for x in tb[-3:]]
            sess = None                                                          # 손상됐을 수 있는 세션은 버린다
        emit(r)
    return 0


# ── 지휘 ─────────────────────────────────────────────────────────────────────
def run_one(target, tid, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([PY, __file__, "--worker", target], capture_output=True, timeout=timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(REPO))
        out, err, status = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        err, status = "", "timeout"
    rows = []
    for line in out.splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    (OUT / (tid + ".jsonl")).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    meta = {"target": target, "id": tid, "status": status, "rows": len(rows), "sec": round(time.time() - t0, 1)}
    if status not in (0, "timeout"):
        meta["stderr_tail"] = err[-600:]
    return meta


def report(files, metas):
    by_target = {r["target"]: r for r in files}
    rows, missing = [], collections.Counter()
    for r in files:
        tid = None
        m = metas.get(r["target"])
        if m:
            tid = m["id"]
        path = OUT / ((tid or "") + ".jsonl")
        got = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if tid and path.exists() else []
        done = {x["definition_id"] for x in got}
        rows += got
        for f in r["functions"]:
            if f["selected"] and f["definition_id"] not in done:
                missing["timeout" if m and m["status"] == "timeout" else "worker_crash"] += 1
                rows.append({"target": r["target"], "definition_id": f["definition_id"], "tier": f["tier"],
                             "stage": "timeout" if m and m["status"] == "timeout" else "worker_crash",
                             "deps": None, "ast": None})
    C = collections.Counter
    reason_key = lambda x: (x.get("reason") or "").split(" — ")[0][:110]
    s = {"functions": len(rows),
         "stage": dict(C(x["stage"] for x in rows)),
         "stage_by_tier": {t: dict(C(x["stage"] for x in rows if x["tier"] == t)) for t in ("T1", "T2")},
         "stage_by_deps": {d: dict(C(x["stage"] for x in rows if x.get("deps") == d))
                           for d in ("complete", "deps_only_incomplete", "target_unit_incomplete", "build_error")},
         "fail_reasons_top": C("%s | %s" % (x["stage"], reason_key(x)) for x in rows
                               if x["stage"] != "ok").most_common(40),
         "abstract": {}, "nav_violations": {}, "slots": {}, "states": {}, "time": {}}
    ab = [x for x in rows if x.get("ast") and x["ast"]["contract_abstract"]]
    s["abstract"] = {"functions": len(ab),
                     "reaches_unimplemented": sum(1 for x in ab if x["ast"]["reaches_unimplemented"]),
                     "stage_reaching_unimpl": dict(C(x["stage"] for x in ab if x["ast"]["reaches_unimplemented"])),
                     "stage_not_reaching": dict(C(x["stage"] for x in ab if not x["ast"]["reaches_unimplemented"]))}
    ok = [x for x in rows if x["stage"] == "ok"]
    for k in ("S1", "S2", "S3", "S4"):
        s["nav_violations"][k] = sum(1 for x in ok if x["nav"][k])
    ev = [x for x in rows if x.get("slots")]
    s["slots"] = {"functions_with_slots": len(ev),
                  "total": sum(x["slots"]["total"] for x in ev),
                  "by_kind": dict(sum((C({k: x["slots"].get(k, 0) for k in ("Param", "StateVar", "GlobalVar", "IReturn")})
                                       for x in ev), C())),
                  "functions_with_IReturn": sum(1 for x in ev if x["slots"].get("IReturn")),
                  "functions_with_requires_index": sum(1 for x in ev if x["slots"]["requires_index_skipped"]),
                  "functions_with_value_errors": sum(1 for x in ev if x["slots"].get("value_errors")),
                  "functions_with_immutable_slot": sum(1 for x in ev if x["slots"]["immutable_keys"]),
                  "functions_reading_immutable_ast": sum(1 for x in rows if x.get("ast") and x["ast"]["immutables_read"])}
    s["states"] = {"ok_functions": len(ok),
                   "with_partial_bottom": sum(1 for x in ok if x["states"]["partial_bottom_nodes"]),
                   "with_empty_in_state": sum(1 for x in ok if x["states"]["empty_in_state_nodes"]),
                   "with_exit_top": sum(1 for x in ok if x["states"]["exit_top_vars"]),
                   "with_opaque_call": sum(1 for x in ok if x["states"]["opaque_calls"]),
                   "with_lowlevel_call": sum(1 for x in ok if x.get("lowlevel_call"))}
    ts = sorted(x["time"]["evaluate"] for x in ok if "evaluate" in x["time"])
    if ts:
        s["time"] = {"evaluate_median": ts[len(ts) // 2], "evaluate_p95": ts[int(len(ts) * .95)], "evaluate_max": ts[-1]}
    s["workers"] = {"timeout": sum(1 for m in metas.values() if m["status"] == "timeout"),
                    "crash": sum(1 for m in metas.values() if m["status"] not in (0, "timeout"))}
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker")
    ap.add_argument("--only")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900, help="타깃 파일 하나의 제한 시간(초)")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()
    if a.worker:
        return worker(a.worker)

    files = selected_files()
    if a.only:
        files = [r for r in files if a.only.lower() in r["target"].lower()]
    OUT.mkdir(exist_ok=True)
    meta_path = OUT / "workers.json"
    metas = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if not a.report_only:
        import concurrent.futures as cf
        dep = json.loads((DEPS / "targets.json").read_text(encoding="utf-8"))["targets"]
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = [ex.submit(run_one, r["target"], dep[r["target"]]["deps"], a.timeout) for r in files]
            for n, fu in enumerate(cf.as_completed(futs), 1):
                m = fu.result()
                metas[m["target"]] = m
                if n % 25 == 0 or n == len(files):
                    print("  %d/%d (%.0fs)" % (n, len(files), time.time() - t0), flush=True)
        meta_path.write_text(json.dumps(metas, ensure_ascii=False, indent=1), encoding="utf-8")
    s = report(files, metas)
    s["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    s["scope"] = a.only or "all"
    (OUT / ("summary.json" if not a.only else "summary_only.json")).write_text(
        json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: s[k] for k in ("functions", "stage", "stage_by_tier", "abstract", "nav_violations", "workers")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
