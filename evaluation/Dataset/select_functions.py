# -*- coding: utf-8 -*-
"""타깃 파일 집합(select_targets.py 출력) → 평가 대상 파일·함수 (2026-11-19, 외부 검토용 초안).

데이터셋 숫자를 **이 스크립트 하나**에서 낸다. 단계와 규칙은 `PIPELINE_RECORD.md` §3 에 적었다.

  입력  evaluation/DAppSCAN/targets_v5.json 의 파일 목록(기본 `selected_option2prime` = 569)
        evaluation/DAppSCAN/repo/DAppSCAN-source/contracts (원천 소스), evaluation/solc/solc-0.8.*.exe
  파일 단계
    F1 폐포 해석     verify_normalize.load_closure (스냅샷이 아니라 감사 폴더 안 해석. 11-17 측정: 569 전부 선정 해석기와 같은 폐포)
    F2 중복 제거     키 = (타깃 내용, 폐포 다른 파일들의 (타깃 기준 상대경로, 내용) 집합).
                     내용 = 주석 제거 후 토큰열(문자열 리터럴 보존) sha256. (11-19 이전: 공백 전부 제거)
                     같은 키면 경로 정렬 순 첫 파일을 남긴다.
    F3 컴파일        폐포 pragma 교집합 중 설치된 최대 solc, optimizer off, bytecodeHash none, **정규화 소스**.
                     실패하면 제외(보완 규칙 없음, ROADMAP §0.A-3). 원본도 컴파일해 바이트코드 일치를 기록.
    F4 후보 함수     타깃 파일 AST 의 FunctionDefinition 중 kind == function ∧ 본문 있음. 없으면 파일 제외(stub).
  함수 단계 (타입 있는 AST, 엔진 실행 전 고정)
    계층 T1 = contractKind == contract ∧ visibility ∈ {public, external}. 나머지 = T2.
    구조 계층  본문(modifier 제외)에 비-builtin 호출 ∨ 분기 ∨ 루프.
    연산 계층  구조가 없는 함수 중 (A) ∨ (B)  (ROADMAP §0.A-5)
               (A) 문장 수 ≥ 3 (emit·placeholder 제외) ∧ 입력 의존 산술 연산 ≥ 1
               (B) 한 문장 안 산술 연산자 ≥ 2  ∨  두 피연산자가 모두 입력 의존인 비선형 연산(* / % ** << >>)
               컴파일 시점 상수 부분식(`-2**127`, `10**18`)은 산술로 세지 않는다(11-19).
    T2 래퍼    T2 중 분기·루프·어셈블리·산술이 없고 문장 ≤ 2 이며 호출이 전부 본문 있는 CG1 인 함수는 단독 타깃에서 뺀다(11-19).
    선정 = 구조 ∨ 연산 (래퍼 제외 후). 나머지는 제외 사유(view/pure → 접근 통제 modifier → 기타)와 함께 남긴다.
  산출  out.json — funnel, 파일별 단계·사유, 함수별 원시 특징·판정·태그.
        원시 특징을 모두 남기므로 규칙을 바꿔도 다시 컴파일하지 않고 재집계할 수 있다.

사용: .venv/Scripts/python.exe evaluation/Dataset/select_functions.py out.json [--jobs 6] [--limit N]
          [--fileset selected_option2prime|selected_option2prime_minus_suspected|selected_option2]
"""
import collections
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DS = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(DS))
sys.path.insert(0, DS)
import compile_survey as cs                                   # noqa: E402  pick_solc·compile_·Index·classify_call·survey_function
from compile_survey import vn, walk, tstr, sig                 # noqa: E402
from normalize_source import normalize, read_source, blank_comments  # noqa: E402

ARITH_BIN = {"+", "-", "*", "/", "%", "**", "<<", ">>"}
NONLINEAR_BIN = {"*", "/", "%", "**", "<<", ">>"}
ARITH_ASSIGN = {"+=", "-=", "*=", "/=", "%=", "<<=", ">>="}
NONLINEAR_ASSIGN = {"*=", "/=", "%=", "<<=", ">>="}
ARITH_UNARY = {"++", "--", "-"}
BRANCH_NODES = {"IfStatement", "Conditional", "TryStatement", "YulIf", "YulSwitch"}
LOOP_NODES = {"ForStatement", "WhileStatement", "DoWhileStatement", "YulForLoop"}
GLOBAL_BASES = {"msg", "block", "tx"}
ACCESS_HINT = re.compile(r"only|auth|admin|owner|governance|role|whitelist|guardian|operator", re.I)

# (A) 의 문장 수 기준. 외부 검토에서 바꿀 수 있게 상수로 둔다.
A_MIN_STMTS = 3


# 문자열 리터럴을 한 토큰으로 보존한다(안의 공백 포함). 토큰 사이 공백·줄바꿈·주석만 무시한다.
_KEY_TOK = re.compile(r'"(?:\\.|[^"\\\n])*"' + r"|'(?:\\.|[^'\\\n])*'" + r"|[A-Za-z_$][A-Za-z0-9_$]*|\d[\w.]*|\S")


def content_key(src):
    """F2 내용 키 (2026-11-19 외부 검토 반영). 이전 판은 공백을 전부 지워 `"a b"`≡`"ab"`, `uint a`≡`uinta` 였다.
    이제 주석 제거 후 토큰열(문자열 리터럴 보존)을 NUL 로 이어 해시한다."""
    code = blank_comments(src.replace("\r\n", "\n").replace("\r", "\n"))[0]
    return hashlib.sha256("\0".join(_KEY_TOK.findall(code)).encode("utf-8", "replace")).hexdigest()


# ─────────────────────────── 함수 특징 ───────────────────────────
def statements(body):
    """본문 문장을 평탄하게 나열(Block·UncheckedBlock 은 펼친다). 분기·루프 본문 안은 내려가지 않는다."""
    out = []
    for s in (body or {}).get("statements", []) or []:
        if s.get("nodeType") in ("Block", "UncheckedBlock"):
            out.extend(statements(s))
        else:
            out.append(s)
    return out


def _is_this(e):
    """`this` 또는 `address(this)`."""
    e = e or {}
    if e.get("nodeType") == "Identifier" and e.get("name") == "this":
        return True
    if e.get("nodeType") == "FunctionCall" and e.get("kind") == "typeConversion":
        args = e.get("arguments") or []
        return len(args) == 1 and _is_this(args[0])
    return False


def _is_const_expr(e, ix):
    """리터럴 · constant 상태변수 · `type(T).min/max` 만으로 된 식이면 True (컴파일 시점 상수)."""
    ok = [True]

    def v(n, _p):
        if not ok[0]:
            return
        t = n.get("nodeType")
        if t == "Identifier":
            ref = n.get("referencedDeclaration")
            if n.get("name") == "type" and (ref is None or ref < 0):
                return
            d = ix.by_id.get(ref) if ref is not None else None
            if not (d and d.get("nodeType") == "VariableDeclaration" and d.get("constant")):
                ok[0] = False
        elif t == "FunctionCall" and n.get("kind") != "typeConversion" and \
                (n.get("expression") or {}).get("name") != "type":
            ok[0] = False
        elif t in ("IndexAccess", "Assignment"):
            ok[0] = False
    walk(e, v)
    return ok[0]


class Taint:
    """입력 의존 판정. 입력 = 파라미터 · 비상수 상태변수(immutable 포함) · msg/block/tx 멤버 · address(this).balance.
    구조가 없는(직선형) 함수에서만 쓰므로 문장 순서대로 한 번 지나가며 지역변수에 전파하면 된다."""

    def __init__(self, fd, ix):
        self.ix = ix
        self.ids = {p["id"] for p in (fd.get("parameters") or {}).get("parameters", [])}
        self.killed = set()          # 입력 무관 값으로 덮어쓴 선언(파라미터·상태변수 포함)
        self.unhandled = 0           # 추적하지 않는 대입 형태(튜플 등) 수 — 특징으로 남긴다

    def _set(self, ref, dep):
        if dep:
            self.ids.add(ref)
            self.killed.discard(ref)
        else:
            self.ids.discard(ref)
            self.killed.add(ref)

    def is_input_decl(self, ref):
        if ref in self.killed:
            return False
        if ref in self.ids:
            return True
        d = self.ix.by_id.get(ref)
        return bool(d and d.get("nodeType") == "VariableDeclaration" and d.get("stateVariable")
                    and not d.get("constant"))

    def dep(self, e):
        hit = [False]

        def v(n, _p):
            if hit[0]:
                return
            t = n.get("nodeType")
            if t == "MemberAccess" and n.get("memberName") == "balance" and _is_this(n.get("expression")):
                hit[0] = True                     # address(this).balance — 엔진의 @GlobalVar 슬롯(engine_api 1244·1411)
            elif t == "Identifier":
                ref = n.get("referencedDeclaration")
                if (ref is not None and ref >= 0 and self.is_input_decl(ref)) or \
                        (n.get("name") in GLOBAL_BASES and (ref is None or ref < 0)):
                    hit[0] = True
        walk(e, v)
        return hit[0]

    def feed(self, stmt):
        """선언·대입으로 의존을 갱신한다 (2026-11-19 외부 검토 반영: `x = a; x = 0;` 뒤에도 x 를 입력 의존으로
        보던 결함 수정). 단순 대입 `=` 은 rhs 에 맞춰 덮어쓰고, 복합 대입은 기존 의존을 유지하며 rhs 의존을 더한다.
        튜플 대입은 rhs 가 의존이면 lhs 의 Identifier 를 모두 의존으로 두고(보수적), 그 수를 unhandled 로 센다."""
        t = stmt.get("nodeType")
        if t == "VariableDeclarationStatement":
            dep = stmt.get("initialValue") is not None and self.dep(stmt["initialValue"])
            for d in stmt.get("declarations") or []:
                if d:
                    self._set(d["id"], dep)
        elif t == "ExpressionStatement":
            e = stmt.get("expression") or {}
            if e.get("nodeType") != "Assignment":
                return
            lhs, rhs, op = e.get("leftHandSide") or {}, e.get("rightHandSide"), e.get("operator")
            if lhs.get("nodeType") == "Identifier" and lhs.get("referencedDeclaration") is not None:
                ref = lhs["referencedDeclaration"]
                if op == "=":
                    self._set(ref, self.dep(rhs))
                elif self.dep(rhs):
                    self._set(ref, True)
            elif lhs.get("nodeType") == "TupleExpression":
                self.unhandled += 1
                if self.dep(rhs):
                    for c in lhs.get("components") or []:
                        if c and c.get("nodeType") == "Identifier" and c.get("referencedDeclaration") is not None:
                            self._set(c["referencedDeclaration"], True)


def features(fd, ix):
    body = fd.get("body") or {}
    f = collections.Counter()
    # 구조: 본문 전체(중첩 포함)
    def v(n, _p):
        t = n.get("nodeType")
        if t in BRANCH_NODES:
            f["branches"] += 1
        elif t in LOOP_NODES:
            f["loops"] += 1
        elif t == "FunctionCall":
            c = cs.classify_call(n, ix)
            if c:
                f["nonbuiltin_calls"] += 1
                if c[0] == "CG1" and c[2] is not None:
                    f["calls_cg1_with_body"] += 1      # 본문이 있는 정의로 하강하는 호출(래퍼 판정용)
        elif t == "YulFunctionCall" and (n.get("functionName") or {}).get("name") in cs.YUL_CALLS:
            f["nonbuiltin_calls"] += 1
        elif t == "InlineAssembly":
            f["assembly"] += 1
    walk(body, v)
    # 문장·산술: 최상위 문장 단위
    tnt = Taint(fd, ix)
    max_per_stmt = 0
    for s in statements(body):
        t = s.get("nodeType")
        f["stmts_all"] += 1
        if t == "EmitStatement":
            f["stmts_emit"] += 1
        elif t == "PlaceholderStatement":
            f["stmts_placeholder"] += 1
        elif t == "VariableDeclarationStatement":
            f["stmts_decl"] += 1
        elif t == "ExpressionStatement" and ((s.get("expression") or {}).get("expression") or {}).get("name") in ("require", "assert"):
            f["stmts_require"] += 1
        elif t in ("RevertStatement",) or (t == "ExpressionStatement" and ((s.get("expression") or {}).get("expression") or {}).get("name") == "revert"):
            f["stmts_revert"] += 1
        elif t == "Return":
            f["stmts_return"] += 1
        n_ops = [0]

        def a(n, _p):
            nt = n.get("nodeType")
            # (2026-11-19 사용자 결정) 컴파일 시점 상수 부분식(`-2**127`, `10**18`)은 산술로 세지 않는다 — 엔진에서도 단일 값이라
            # 구간 연산을 거치지 않는다. 수는 `arith_const_ops` 로 따로 남긴다.
            if nt in ("BinaryOperation", "UnaryOperation") and _is_const_expr(n, ix):
                if n.get("operator") in ARITH_BIN or n.get("operator") in ARITH_UNARY:
                    f["arith_const_ops"] += 1
                return
            if nt == "BinaryOperation" and n.get("operator") in ARITH_BIN:
                n_ops[0] += 1
                l_dep, r_dep = tnt.dep(n.get("leftExpression")), tnt.dep(n.get("rightExpression"))
                if l_dep or r_dep:
                    f["arith_input_dep"] += 1
                if n.get("operator") in NONLINEAR_BIN and l_dep and r_dep:
                    f["nonlinear_two_input"] += 1
            elif nt == "Assignment" and n.get("operator") in ARITH_ASSIGN:
                n_ops[0] += 1
                l_dep, r_dep = tnt.dep(n.get("leftHandSide")), tnt.dep(n.get("rightHandSide"))
                if l_dep or r_dep:
                    f["arith_input_dep"] += 1
                if n.get("operator") in NONLINEAR_ASSIGN and l_dep and r_dep:
                    f["nonlinear_two_input"] += 1
            elif nt == "UnaryOperation" and n.get("operator") in ARITH_UNARY:
                n_ops[0] += 1
                if tnt.dep(n.get("subExpression")):
                    f["arith_input_dep"] += 1
        if t != "EmitStatement":
            walk(s, a)
        f["arith_ops"] += n_ops[0]
        max_per_stmt = max(max_per_stmt, n_ops[0])
        tnt.feed(s)
    f["max_arith_per_stmt"] = max_per_stmt
    if tnt.unhandled:
        f["taint_unhandled_assign"] = tnt.unhandled
    f["stmts_counted"] = f["stmts_all"] - f["stmts_emit"] - f["stmts_placeholder"]
    return dict(f)


def is_thin_wrapper(f):
    """분기·루프·산술이 없고 셀 문장이 2 개 이하이며, 호출이 **전부 본문 있는 CG1 정의로의 하강**인 함수
    (`mulWadDown` → `mulDivDown`). 계산이 callee 에 있다는 근거가 서는 경우만이다. 외부·저수준 호출(`token.call(...)`)이나
    어셈블리 호출이 있으면 그 호출 자체가 내용이므로 래퍼가 아니다."""
    n = f.get("nonbuiltin_calls", 0)
    return (n > 0 and f.get("calls_cg1_with_body", 0) == n and not f.get("assembly", 0)
            and not f.get("branches", 0) and not f.get("loops", 0)
            and f.get("arith_ops", 0) == 0 and f.get("stmts_counted", 0) <= 2)


def judge(fd, f, tier):
    structural = f.get("nonbuiltin_calls", 0) > 0 or f.get("branches", 0) > 0 or f.get("loops", 0) > 0
    op_a = f.get("stmts_counted", 0) >= A_MIN_STMTS and f.get("arith_input_dep", 0) >= 1
    op_b = f.get("max_arith_per_stmt", 0) >= 2 or f.get("nonlinear_two_input", 0) >= 1
    layer = "structural" if structural else ("operation" if (op_a or op_b) else None)
    reason = None
    # (2026-11-19 사용자 결정) T2 의 얇은 래퍼는 단독 타깃에서 뺀다. 계산은 callee 에 있고 callee 가 따로 선정된다.
    # 호출자의 실행 안에서는 그대로 하강·관찰된다(callee 관찰만).
    if tier == "T2" and layer == "structural" and is_thin_wrapper(f):
        layer, reason = None, "below_criterion:t2_thin_wrapper"
    elif layer is None:
        mods = [m for m in fd.get("modifiers") or [] if m.get("kind", "modifierInvocation") == "modifierInvocation"]
        # 사유 이름은 중립적으로 둔다(2026-11-19): "디버깅 가치 없음"이 아니라 "사전 기준 미달"이다.
        if fd.get("stateMutability") in ("view", "pure"):
            reason = "below_criterion:view_pure"
        elif mods:
            reason = "below_criterion:modifier_guarded"
        else:
            reason = "below_criterion:other"
    return {"structural": structural, "op_A": op_a, "op_B": op_b, "layer": layer,
            "selected": layer is not None, "exclusion_reason": reason}


# ─────────────────────────── 호출 그래프(is_root) ───────────────────────────
def callers_map(asts, ix):
    """폐포 안 모든 함수·modifier 본문에서 CG1 호출이 가리키는 정의 id → 호출하는 정의 수."""
    callers = collections.defaultdict(set)
    for ast in asts.values():
        def reg(n, _p):
            if n.get("nodeType") in ("FunctionDefinition", "ModifierDefinition") and n.get("body"):
                owner = n["id"]

                def v(m, _q):
                    if m.get("nodeType") == "FunctionCall":
                        c = cs.classify_call(m, ix)
                        if c and c[0] == "CG1" and c[2] is not None:
                            callers[c[2]].add(owner)
                walk(n["body"], v)
        walk(ast, reg)
    return callers


# ─────────────────────────── 타깃 하나 ───────────────────────────
def run_target(t, idx, snap_of):
    rec = {"target": t, "snapshot": snap_of.get(t)}
    t0 = time.time()
    cl = vn.load_closure(t, idx)
    if cl is None:
        rec["stage"] = "F1_closure_unresolved"
        return rec
    raw = {k: read_source(os.path.join(vn.ROOT, v.replace("/", os.sep))) for k, v in cl.items()}
    rec["closure_files"] = len(cl)
    keys = {v: content_key(raw[k]) for k, v in cl.items()}
    # 폐포 파일은 (타깃 기준 상대경로, 내용) 쌍으로 묶는다 — 어느 파일에 어느 내용이 있는지까지 같아야 중복이다.
    tdir = os.path.dirname(t)
    rel = lambda p: os.path.relpath(p, tdir).replace(os.sep, "/")
    rec["dedup_key"] = hashlib.sha256((keys[t] + "|" + ",".join(sorted(
        "%s=%s" % (rel(p), x) for p, x in keys.items() if p != t))).encode()).hexdigest()
    rec["_raw"], rec["_cl"] = raw, cl
    rec["sec_prep"] = round(time.time() - t0, 2)
    return rec


def compile_and_judge(rec):
    t, raw, cl = rec["target"], rec.pop("_raw"), rec.pop("_cl")
    t0 = time.time()
    norm = {k: normalize(v).text for k, v in raw.items()}
    ver, solc = cs.pick_solc(raw)
    rec["solc"] = "%d.%d.%d" % ver if ver else None
    if solc is None:
        rec["stage"] = "F3_no_solc"
        return rec
    a, asts, err = cs.compile_(norm, solc, True)
    if a is None:
        rec["stage"] = "F3_compile_fail"
        rec["err"] = err
        b, _, err2 = cs.compile_(raw, solc, False)
        rec["orig_compiles"] = b is not None
        return rec
    b, _, _ = cs.compile_(raw, solc, False)
    rec["bytecode_same"] = None if b is None else (set(a) == set(b) and all(a[k] == b[k] for k in a))
    ix = cs.Index(asts)
    tgt_ast = asts.get(t) or next((asts[k] for k in asts if cl.get(k) == t), None) or {}
    callers = callers_map(asts, ix)
    funcs = []
    for top in tgt_ast.get("nodes", []):
        if top.get("nodeType") != "ContractDefinition" and top.get("nodeType") != "FunctionDefinition":
            continue
        members = [top] if top.get("nodeType") == "FunctionDefinition" else top.get("nodes", [])
        ckind = None if top.get("nodeType") == "FunctionDefinition" else top.get("contractKind")
        cname = None if ckind is None else top.get("name")
        for fd in members:
            if fd.get("nodeType") != "FunctionDefinition" or not fd.get("body"):
                continue
            if fd.get("kind") not in ("function", "freeFunction"):
                continue
            f = features(fd, ix)
            tier = "T1" if ckind == "contract" and fd.get("visibility") in ("public", "external") else "T2"
            j = judge(fd, f, tier)
            cnt, reach = cs.survey_function(fd, ix)
            cg3 = cnt.get("CG3", 0)
            cg3_effect = cg3 - cnt.get("CG3:contract_typed_view", 0)       # ROADMAP §0.A-6: view 외부 호출은 효과 안전
            params = (fd.get("parameters") or {}).get("parameters", [])
            mods = [(m.get("modifierName") or {}).get("name") for m in fd.get("modifiers") or []
                    if m.get("kind", "modifierInvocation") == "modifierInvocation"]
            funcs.append({
                "definition_id": "%s|%s|%s|%s" % (rec["snapshot"], t, cname, fd.get("src")),
                "contract": cname, "contract_kind": ckind, "abstract": bool(top.get("abstract")),
                "name": fd.get("name"), "sig": sig(fd), "visibility": fd.get("visibility"),
                "mutability": fd.get("stateMutability"), "virtual": bool(fd.get("virtual")),
                "tier": tier,
                "modifiers": mods, "modifiers_access_like": sum(1 for m in mods if m and ACCESS_HINT.search(m)),
                "features": f, **j,
                "calls": dict(cnt), "reached_defs": reach,
                "cg3_effect": cg3_effect, "has_delegatecall": bool(cnt.get("CG3:lowlevel_delegatecall") or cnt.get("CG3:assembly_delegatecall")),
                "callers_in_closure": len(callers.get(fd["id"], ())), "is_root": not callers.get(fd["id"]),
                "params": [{"name": p.get("name"), "type": tstr(p), "location": p.get("storageLocation")} for p in params],
                "requires_storage_binding": any(p.get("storageLocation") == "storage" for p in params),
            })
    rec["functions"] = funcs
    rec["stage"] = ("F4_no_body_function" if not funcs else
                    "F5_no_selected_function" if not any(x["selected"] for x in funcs) else "selected")
    rec["sec_compile"] = round(time.time() - t0, 2)
    return rec


def main():
    out = sys.argv[1]
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 6
    fileset = sys.argv[sys.argv.index("--fileset") + 1] if "--fileset" in sys.argv else "selected_option2prime"
    v5 = json.load(open(os.path.join(R, "evaluation", "DAppSCAN", "targets_v5.json"), encoding="utf-8"))
    targets = sorted(v5[fileset])
    if "--limit" in sys.argv:
        targets = targets[:int(sys.argv[sys.argv.index("--limit") + 1])]
    snap_of = {k: m.get("snap") for k, m in v5["selected_meta"].items()}
    idx = vn.file_index()

    # F1·F2 (순차: 결정적)
    recs = [run_target(t, idx, snap_of) for t in targets]
    groups = collections.defaultdict(list)
    for r in recs:
        if "dedup_key" in r:
            groups[r["dedup_key"]].append(r["target"])
    keep = {sorted(v)[0] for v in groups.values()}
    dups = {sorted(v)[0]: sorted(v)[1:] for v in groups.values() if len(v) > 1}
    todo = []
    for r in recs:
        if r.get("stage"):
            continue
        if r["target"] not in keep:
            r["stage"] = "F2_duplicate"
            r["duplicate_of"] = next(k for k, v in dups.items() if r["target"] in v)
            r.pop("_raw", None)
            r.pop("_cl", None)
        else:
            todo.append(r)
    # F3–F5 (병렬, 타깃마다 독립)
    with cf.ThreadPoolExecutor(jobs) as ex:
        futs = {ex.submit(compile_and_judge, r): r for r in todo}
        for i, fu in enumerate(cf.as_completed(futs), 1):
            r = futs[fu]
            try:
                fu.result()
            except Exception as e:                                  # noqa: BLE001
                r["stage"] = "script_error"
                r["err"] = "%s: %s" % (type(e).__name__, e)
                r.pop("_raw", None)
                r.pop("_cl", None)
            if i % 50 == 0:
                print("  %d/%d" % (i, len(todo)), flush=True)
    recs.sort(key=lambda r: r["target"])

    # 집계
    stage = collections.Counter(r["stage"] for r in recs)
    compiled = [r for r in recs if r["stage"] in ("selected", "F4_no_body_function", "F5_no_selected_function")]
    F = [f for r in compiled for f in r["functions"]]                 # 컴파일된 파일의 후보 함수 전부
    sel = [f for f in F if f["selected"]]
    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M"), "fileset": fileset, "input_files": len(targets),
        "rule": {"A_min_stmts": A_MIN_STMTS, "structural": "nonbuiltin_call or branch or loop",
                 "op_A": "stmts_counted >= A_min_stmts and arith_input_dep >= 1",
                 "op_B": "max_arith_per_stmt >= 2 or nonlinear_two_input >= 1"},
        "file_stage": dict(stage),
        "duplicate_groups": len(dups),
        "bytecode_same": dict(collections.Counter(str(r.get("bytecode_same")) for r in compiled)),
        "candidate_functions": len(F),
        "candidate_by_tier": dict(collections.Counter(f["tier"] for f in F)),
        "functions_selected": len(sel),
        "by_tier_layer": {"%s/%s" % k: v for k, v in sorted(collections.Counter((f["tier"], f["layer"]) for f in sel).items())},
        "operation_by_rule": {"A_only": sum(1 for f in sel if f["op_A"] and not f["op_B"] and not f["structural"]),
                              "B_only": sum(1 for f in sel if f["op_B"] and not f["op_A"] and not f["structural"]),
                              "A_and_B": sum(1 for f in sel if f["op_A"] and f["op_B"] and not f["structural"])},
        "excluded_reason": {"%s/%s" % k: v for k, v in sorted(collections.Counter(
            (f["tier"], f["exclusion_reason"]) for f in F if not f["selected"]).items())},
        "selected_with_cg3_effect": sum(1 for f in sel if f["cg3_effect"]),
        "selected_with_delegatecall": sum(1 for f in sel if f["has_delegatecall"]),
        "selected_is_root": sum(1 for f in sel if f["is_root"]),
        "selected_requires_storage_binding": sum(1 for f in sel if f["requires_storage_binding"]),
        "solc": dict(collections.Counter(r.get("solc") for r in recs if r.get("solc"))),
    }
    json.dump({"summary": summary, "duplicates": dups, "files": recs}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
