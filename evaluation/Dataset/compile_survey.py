# -*- coding: utf-8 -*-
"""타깃 486 전수: (1) 도구 중립 컴파일 검사, (2) 정규화 전후 바이트코드 비교, (3) 호출 지점 CG1–CG4 분류.

  - solc = 폐포 모든 파일의 pragma 교집합 중 설치된 최대 버전 (ROADMAP §0.1-7)
  - 컴파일 설정 = optimizer off, metadata.bytecodeHash = none (verify_normalize.py 와 같음)
  - 정규화본을 컴파일한 **타입 있는 AST** 로 타깃 파일의 함수(본문 있는 named 함수)마다 호출 지점을 분류한다.
    내부 callee·modifier 본문까지 따라간다(정적 referencedDeclaration 기준, virtual 은 선언된 쪽만).
    CG1 완전 실행 · CG2 인터페이스 view/pure · CG3 효과 미모델링 · CG4 free function · 기타(new, 함수 포인터)
사용: .venv/Scripts/python.exe evaluation/Dataset/compile_survey.py evaluation/Dataset/compile_survey_486.json [--jobs 6] [--limit N]
"""
import collections, concurrent.futures as cf, json, os, re, subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 저장소 루트
sys.path.insert(0, os.path.join(R, "evaluation", "Dataset"))
sys.path.insert(0, os.path.join(R, "evaluation", "DAppSCAN"))
import verify_normalize as vn          # noqa: E402  load_closure, file_index, ROOT
from normalize_source import normalize, read_source, blank_comments  # noqa: E402
import select_targets as st            # noqa: E402  allowed_versions

SOLC_DIR = os.path.join(R, "evaluation", "solc")
INSTALLED = {}
for fn in os.listdir(SOLC_DIR):
    m = re.match(r"solc-(\d+)\.(\d+)\.(\d+)\.exe$", fn)
    if m:
        INSTALLED[tuple(int(x) for x in m.groups())] = os.path.join(SOLC_DIR, fn)
PRAGMA = re.compile(r"\bpragma\s+solidity\s+([^;]+);")
BUILTIN_BASES = {"abi", "msg", "block", "tx", "bytes", "string", "type"}
LOWLEVEL = {"call", "delegatecall", "staticcall", "callcode", "transfer", "send"}
YUL_CALLS = {"call", "delegatecall", "staticcall", "callcode"}


def pick_solc(sources):
    acc = None
    for s in sources.values():
        for spec in PRAGMA.findall(blank_comments(s)[0]):
            a = st.allowed_versions(spec)
            acc = a if acc is None else acc & a
    cand = sorted(v for v in INSTALLED if acc is None or v in acc)
    return (cand[-1], INSTALLED[cand[-1]]) if cand else (None, None)


def compile_(sources, solc, want_ast):
    sel = {"*": {"*": ["evm.bytecode.object", "evm.deployedBytecode.object"]}}
    if want_ast:
        sel["*"][""] = ["ast"]
    req = {"language": "Solidity", "sources": {k: {"content": v} for k, v in sources.items()},
           "settings": {"optimizer": {"enabled": False}, "metadata": {"bytecodeHash": "none"}, "outputSelection": sel}}
    try:
        p = subprocess.run([solc, "--standard-json"], input=json.dumps(req).encode("utf-8"),
                           capture_output=True, timeout=600)
        out = json.loads(p.stdout.decode("utf-8", "replace"))
    except Exception as e:                                   # noqa: BLE001
        return None, None, "%s: %s" % (type(e).__name__, str(e)[:150])
    errs = [e for e in out.get("errors", []) if e.get("severity") == "error"]
    if errs:
        return None, None, (errs[0].get("type", "") + ": " + errs[0].get("message", ""))[:300]
    code = {f + ":" + c: (v["evm"]["bytecode"]["object"], v["evm"]["deployedBytecode"]["object"])
            for f, cs in out.get("contracts", {}).items() for c, v in cs.items()}
    asts = {f: v.get("ast") for f, v in out.get("sources", {}).items()} if want_ast else None
    return code, asts, None


# ─────────────────────────── 호출 지점 분류 ───────────────────────────
def walk(n, fn, parent=None):
    if isinstance(n, dict):
        if n.get("nodeType"):
            fn(n, parent)
            parent = n
        for v in n.values():
            walk(v, fn, parent)
    elif isinstance(n, list):
        for v in n:
            walk(v, fn, parent)


class Index:
    def __init__(self, asts):
        self.by_id, self.free, self.contract_of = {}, set(), {}
        for ast in asts.values():
            for top in ast.get("nodes", []):
                if top.get("nodeType") == "FunctionDefinition":
                    self.free.add(top["id"])
                if top.get("nodeType") == "ContractDefinition":
                    for m in top.get("nodes", []):
                        self.contract_of[m.get("id")] = top
            def reg(n, _p):
                if "id" in n:
                    self.by_id[n["id"]] = n
            walk(ast, reg)


def tstr(n):
    return ((n or {}).get("typeDescriptions") or {}).get("typeString") or ""


def classify_call(call, ix):
    """FunctionCall 노드 → (그룹, 세부, callee 정의 id 또는 None). 분류 대상이 아니면 None."""
    if call.get("kind") in ("typeConversion", "structConstructorCall"):
        return None
    e = call.get("expression") or {}
    if e.get("nodeType") == "FunctionCallOptions":
        e = e.get("expression") or {}
    nt = e.get("nodeType")
    if nt == "NewExpression":
        tn = (e.get("typeName") or {}).get("nodeType")
        return ("OTHER", "new_contract", None) if tn == "UserDefinedTypeName" else None   # new bytes(n) 등은 메모리 할당
    ref = e.get("referencedDeclaration")
    d = ix.by_id.get(ref) if ref is not None else None
    if nt == "Identifier":
        if ref is None or ref < 0:
            return None                                   # require, keccak256 …
        if d is None:
            return ("OTHER", "unresolved", None)
        k = d.get("nodeType")
        if k in ("EventDefinition", "ErrorDefinition"):
            return None
        if k == "FunctionDefinition":
            return ("CG4", "free_function", ref) if ref in ix.free else ("CG1", "internal", ref)
        if k == "VariableDeclaration":
            return ("OTHER", "function_pointer", None)
        return ("OTHER", "identifier:" + str(k), None)
    if nt == "MemberAccess":
        base = e.get("expression") or {}
        bt = tstr(base)
        member = e.get("memberName")
        if base.get("nodeType") == "Identifier" and base.get("name") in BUILTIN_BASES:
            return None
        if base.get("nodeType") == "Identifier" and base.get("name") == "this":
            return ("CG1", "this", ref)
        if base.get("nodeType") == "Identifier" and base.get("name") == "super":
            return ("CG1", "super", ref)
        if d is not None and d.get("nodeType") in ("EventDefinition", "ErrorDefinition"):
            return None
        if bt.startswith("type(library ") or (d is not None and ix.contract_of.get(ref, {}).get("contractKind") == "library"
                                               and not bt.startswith("contract ")):
            return ("CG1", "library", ref)
        if bt.startswith("type(contract "):
            return ("CG1", "base_name", ref)
        if bt.startswith("contract ") or bt.startswith("type(interface"):
            owner = ix.contract_of.get(ref, {}) if d is not None else {}
            kind = owner.get("contractKind")
            mut = (d or {}).get("stateMutability") if d is not None and d.get("nodeType") == "FunctionDefinition" else None
            if d is not None and d.get("nodeType") == "VariableDeclaration":
                mut = "view"                              # public getter
            if kind == "interface" or (d is not None and not (d.get("body") or {}).get("statements")
                                       and d.get("nodeType") == "FunctionDefinition" and kind != "library"):
                return ("CG2", "interface_view", None) if mut in ("view", "pure") else ("CG3", "interface_mutating", None)
            return ("CG3", "contract_typed" + ("_view" if mut in ("view", "pure") else ""), None)
        if bt.startswith("address") and member in LOWLEVEL:
            return ("CG3", "lowlevel_" + member, None)
        if ref is None:
            return None                                   # push/pop/concat 등 내장 멤버
        if d is not None and d.get("nodeType") == "FunctionDefinition":
            return ("CG1", "library", ref) if ix.contract_of.get(ref, {}).get("contractKind") == "library" \
                else ("CG1", "member_internal", ref)
        return ("OTHER", "member:" + bt[:30], None)
    if tstr(e).startswith("function "):
        return ("OTHER", "function_pointer", None)
    return ("OTHER", "expr:" + str(nt), None)


def survey_function(fd, ix):
    """함수 하나 + 그 내부 callee·modifier 본문을 따라가며 호출 지점을 분류."""
    cnt = collections.Counter()
    seen, todo = set(), [fd["id"]]
    while todo:
        fid = todo.pop()
        if fid in seen:
            continue
        seen.add(fid)
        node = ix.by_id.get(fid)
        if node is None:
            continue
        bodies = [node.get("body")]
        for mi in node.get("modifiers", []) or []:
            mref = (mi.get("modifierName") or {}).get("referencedDeclaration")
            md = ix.by_id.get(mref)
            if md is not None and md.get("nodeType") == "ModifierDefinition":
                if mref not in seen:
                    todo.append(mref)
        for b in bodies:
            if not b:
                continue
            def visit(n, _p):
                t = n.get("nodeType")
                if t == "FunctionCall":
                    c = classify_call(n, ix)
                    if c:
                        cnt[c[0]] += 1
                        cnt[c[0] + ":" + c[1]] += 1
                        if c[2] is not None and c[0] == "CG1" and c[1] != "this_external":
                            if (ix.by_id.get(c[2]) or {}).get("body"):
                                todo.append(c[2])
                elif t == "YulFunctionCall":
                    nm = (n.get("functionName") or {}).get("name")
                    if nm in YUL_CALLS:
                        cnt["CG3"] += 1
                        cnt["CG3:assembly_" + nm] += 1
            walk(b, visit)
    return cnt, len(seen)


def target_functions(ast):
    out = []
    for top in ast.get("nodes", []):
        if top.get("nodeType") == "FunctionDefinition" and top.get("body"):
            out.append((None, None, top))
        if top.get("nodeType") == "ContractDefinition":
            for m in top.get("nodes", []):
                if m.get("nodeType") == "FunctionDefinition" and m.get("body") and m.get("kind") == "function":
                    out.append((top.get("name"), top.get("contractKind"), m))
    return out


def sig(fd):
    return "%s(%s)" % (fd.get("name"), ",".join(tstr(p) for p in (fd.get("parameters") or {}).get("parameters", [])))


def run_target(t, idx):
    rec = {"target": t}
    t0 = time.time()
    cl = vn.load_closure(t, idx)
    if cl is None:
        rec["status"] = "import-unresolved"
        return rec
    raw = {k: read_source(os.path.join(vn.ROOT, v.replace("/", os.sep))) for k, v in cl.items()}
    norm = {k: normalize(v).text for k, v in raw.items()}
    ver, solc = pick_solc(raw)
    rec["solc"] = "%d.%d.%d" % ver if ver else None
    rec["closure_files"] = len(cl)
    if solc is None:
        rec["status"] = "no-solc"
        return rec
    a, asts, err = compile_(norm, solc, True)
    if a is None:
        rec["status"] = "norm-compile-fail"
        rec["err"] = err
        b, _, err2 = compile_(raw, solc, False)
        rec["orig_compiles"] = b is not None
        if b is None:
            rec["orig_err"] = err2
        return rec
    rec["status"] = "ok"
    b, _, err2 = compile_(raw, solc, False)
    if b is None:
        rec["bytecode"] = "orig-compile-fail"
        rec["orig_err"] = err2
    else:
        diff = [k for k in a if a[k] != b.get(k)]
        rec["bytecode"] = "same" if not diff and set(a) == set(b) else "diff"
        rec["bytecode_diff"] = diff[:5]
        rec["contracts"] = len(a)
    ix = Index(asts)
    tgt_ast = asts.get(t)
    if tgt_ast is None:                                   # 폐포의 타깃 소스 이름 = 타깃 경로
        tgt_ast = next((asts[k] for k in asts if cl.get(k) == t), None)
    funcs = []
    for cname, ckind, fd in target_functions(tgt_ast or {}):
        cnt, reach = survey_function(fd, ix)
        funcs.append({"contract": cname, "contract_kind": ckind, "sig": sig(fd), "visibility": fd.get("visibility"),
                      "mutability": fd.get("stateMutability"), "free": cname is None,
                      "reached_defs": reach, "calls": dict(cnt)})
    rec["functions"] = funcs
    rec["free_functions_in_closure"] = len(ix.free)
    rec["sec"] = round(time.time() - t0, 1)
    return rec


def main():
    out = sys.argv[1]
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 6
    targets = json.load(open(os.path.join(R, "evaluation", "Dataset", "targets_final.json"), encoding="utf-8"))["targets"]
    if "--limit" in sys.argv:
        targets = targets[:int(sys.argv[sys.argv.index("--limit") + 1])]
    idx = vn.file_index()
    res = []
    with cf.ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(run_target, t, idx): t for t in targets}
        for i, f in enumerate(cf.as_completed(futs), 1):
            try:
                res.append(f.result())
            except Exception as e:                        # noqa: BLE001
                res.append({"target": futs[f], "status": "script-error", "err": "%s: %s" % (type(e).__name__, e)})
            if i % 25 == 0:
                print("  %d/%d" % (i, len(targets)), flush=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(collections.Counter(r["status"] for r in res))
    print(collections.Counter(r.get("bytecode") for r in res if r["status"] == "ok"))


if __name__ == "__main__":
    main()
