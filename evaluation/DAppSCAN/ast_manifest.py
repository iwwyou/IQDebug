"""AST 기반 함수 manifest (2026-10-28).

왜: 지금까지의 함수 수(정규식 근사)는 RQ1 의 **분모**인데 가시성·종류·구현 여부·호출 구조를 알 수 없어
    타깃 계층(T1/T2)을 확정할 수 없었다. 공식 컴파일러의 AST 로 그 분모를 확정한다.
    **엔진의 ANTLR 파서를 쓰지 않는 이유**: 우리 파서가 못 읽는 파일이 "함수 없는 파일" 로 집계되면
    데이터셋 사실이 아니라 엔진 한계(B단계)가 모집단에 섞인다. 분모는 도구와 독립적이어야 한다.

방법: `solc --standard-json` + `settings.stopAfter = "parsing"`.
  · import 를 해석하지 않으므로 의존성이 없어도 파싱된다(ImportDirective 는 노드로만 남는다).
  · 그러나 **버전 pragma 는 파싱 단계에서 강제**된다("Source file requires different compiler version").
    그래서 파일의 허용 버전 집합에서 **최대 버전**을 골라 그 바이너리로 파싱한다(`evaluation/solc/`).
  · 파싱 성공 ≠ 컴파일 성공. 타입 검사·상속 해석·오버로드 해소는 하지 않는다.

5차 검토 반영
  · 타깃 계층(T1/T2)을 **엔진 실행 전에** AST 로 확정한다(실행 결과로 T2 를 고르면 selection bias).
  · T1 = **일반 계약(contract)에 선언된 구현 public/external 일반 함수** = ABI-visible contract function.
    "트랜잭션 진입점" 이라고 단정하지 않는다(library public, abstract 계약의 virtual 등은 성격이 다르다).
  · T2 = 그 외 구현된 named 함수(internal/private, library, free function).
  · constructor/receive/fallback 은 `special` 로 기록만 하고 타깃이 아니다(연구 범위를 named regular
    function 의 디버깅 의미론으로 한정). fallback 은 파라미터를 가질 수 있으므로 "입력이 없어서" 가
    제외 사유가 아니다.
  · 함수 정체성 = `definition_id`(스냅샷|파일|계약|src 바이트 범위). 실행 컨텍스트 ID 와 분리한다.
  · 호출 지점은 **확신도와 함께** 기록한다(`resolution_basis="syntax_only"`). internal 단정 금지.
  · modifier 는 `ModifierDefinition` 의 placeholder(`_`) 개수까지 기록한다(본문이 여러 번 실행될 수 있다).

6차 검토 반영 (2026-10-28)
  · modifier **사용**을 선언과 분리해 `modifier_records` 로 기록한다 — 계약별 선언 키, 해석 상태
    (declaring_contract / same_file_unique / ambiguous_in_file / unresolved), placeholder 앞뒤 문장 수.
    예전의 파일 단위 이름 조회는 동명 modifier 충돌 + 상속·import 누락으로 **조용히 빠졌다**.
  · parse-only 는 `ModifierInvocation.kind` 를 채우지 않아(실측 None) `constructor() Base(1)` 의
    base-constructor specifier 가 modifier 와 같은 모양으로 들어온다 → base 계약 이름으로 분리한다.
  · 타입 표기는 중첩을 보존한다(`uint256[][]`, `mapping(address=>uint256)`, `user:IERC20[]`).
    파라미터 **이름**과 반환 파라미터도 싣는다.
  · 타깃 계층(T1/T2)과 실질 타깃 규칙 자체는 바꾸지 않았다 — 분석은 `target_analysis.py` 가 한다.
  · `implemented` 와 `nontrivial_body` 를 구분한다(`constructor() {}` 는 구현됐지만 본문이 비었다).
  · AST 의 `src` 는 **UTF-8 바이트 오프셋**이다(파이썬 문자 인덱스와 다름).

사용:
  python -I ast_manifest.py <contracts_root> <targets_vN.json> <solc_dir> <out_json> [--limit N]
"""
import io
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_targets as st  # noqa: E402  (semver·경로 유틸 재사용)

LOOPS = {"ForStatement", "WhileStatement", "DoWhileStatement"}
BRANCHES = {"IfStatement", "Conditional"}


def walk(node, fn):
    if isinstance(node, dict):
        if node.get("nodeType"):
            fn(node)
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def solc_bin(solc_dir: str, version: str):
    p = os.path.join(solc_dir, f"solc-{version}.exe")
    return p if os.path.exists(p) else None


def parse(solc: str, unit: str, src: str):
    payload = {"language": "Solidity", "sources": {unit: {"content": src}},
               "settings": {"stopAfter": "parsing", "outputSelection": {"*": {"": ["ast"]}}}}
    try:
        p = subprocess.run([solc, "--standard-json"], input=json.dumps(payload),
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        out = json.loads(p.stdout)
    except subprocess.TimeoutExpired:
        return None, [("error", "Timeout", "solc timeout")]
    except Exception as e:                       # noqa: BLE001
        return None, [("error", type(e).__name__, str(e)[:120])]
    errs = [(e.get("severity"), e.get("type"), (e.get("message") or "").strip()[:160])
            for e in out.get("errors", [])]
    ast = (out.get("sources", {}).get(unit, {}) or {}).get("ast")
    return ast, errs


BUILTINS = {"require", "assert", "revert", "keccak256", "sha256", "ripemd160", "ecrecover",
            "selfdestruct", "addmod", "mulmod", "blockhash", "gasleft", "type", "payable",
            "sha3", "suicide"}


def _tn_repr(tn, depth=0):
    """typeName 노드 → 표기. parse-only 라 `typeDescriptions` 가 비어 있어 노드에서 재구성한다.

    ★ 6차 검토 §④: 예전엔 배열의 내부 타입을 **한 단계만** 읽어 `uint256[][]` 가 `uint256[]` 로,
      사용자정의 타입의 배열이 `array[]`(실측 59건) 로, mapping 전체가 `mapping` 으로 뭉개졌다.
      입력 생성은 엔진 `debugSlots` 를 쓰므로 생성 차단 요인은 아니었지만, **생성 실패 원인 분석과
      타입별 보고 지표**에 필요하다. 중첩을 보존한다."""
    if not isinstance(tn, dict) or depth > 8:
        return "?"
    nt = tn.get("nodeType")
    if nt == "ElementaryTypeName":
        return tn.get("name") or "?"
    if nt == "UserDefinedTypeName":
        return "user:" + ((tn.get("pathNode") or {}).get("name") or tn.get("name") or "?")
    if nt == "ArrayTypeName":
        ln = tn.get("length") or {}
        n = ln.get("value") if ln.get("nodeType") == "Literal" else None
        return f"{_tn_repr(tn.get('baseType'), depth + 1)}[{n or ''}]"
    if nt == "Mapping":
        return (f"mapping({_tn_repr(tn.get('keyType'), depth + 1)}"
                f"=>{_tn_repr(tn.get('valueType'), depth + 1)})")
    if nt == "FunctionTypeName":
        ps = [_tn_repr(q.get("typeName"), depth + 1)
              for q in (tn.get("parameterTypes") or {}).get("parameters", [])]
        return "function(" + ",".join(ps) + ")"
    return tn.get("name") or nt or "?"


def type_repr(p):
    return _tn_repr(p.get("typeName"))


def params_of(fn, which="parameters"):
    """파라미터 레코드. **이름**을 함께 싣는다(6차 검토 §④ — 예전엔 타입과 data location 만 있어
    manifest 만으로는 어느 슬롯에 대응하는지 알 수 없었다). `type_node` 는 생성 실패 분류용."""
    return [{"name": p.get("name") or None,
             "type": type_repr(p),
             "type_node": (p.get("typeName") or {}).get("nodeType"),
             "data_location": p.get("storageLocation")}
            for p in (fn.get(which) or {}).get("parameters", [])]


def mod_body_shape(mdef):
    """ModifierDefinition 본문 모양. placeholder 가 2개 이상이면 타깃 본문이 **여러 번** 실행되고,
    placeholder 앞뒤에 문장이 있으면 타깃 본문이 modifier 문장 **사이에 끼인다**
    (`nonReentrant`/`initializer`). 둘 다 탐색 의미론에 영향이 있어 따로 센다."""
    body = mdef.get("body") or {}
    stmts = body.get("statements") or []
    c = Counter()
    walk(body, lambda n: c.update(["p"] if n["nodeType"] == "PlaceholderStatement" else []))
    idx = next((i for i, s in enumerate(stmts)
                if isinstance(s, dict) and s.get("nodeType") == "PlaceholderStatement"), None)
    return {"placeholder_count": c["p"],
            "stmts_before": idx if idx is not None else None,
            "stmts_after": (len(stmts) - idx - 1) if idx is not None else None}


_MOD_UNKNOWN = {"placeholder_count": None, "stmts_before": None, "stmts_after": None}


def modifier_records(fn, owner, owner_bases, by_owner, by_name):
    """함수의 modifier **사용**을 선언과 분리해 기록한다(6차 검토 C10).

    왜: 예전엔 파일 전체에서 이름 하나로 placeholder 수를 찾아(`mods[name]`) 함수에 붙였다.
      서로 다른 계약이 같은 이름의 modifier 를 선언하면 충돌하고, 상속·import 로 들어온 modifier 는
      이 파일에 없어 **조용히 빠졌다**. 그래서 집계된 `multi_placeholder_modifier = 0` 은
      "데이터셋에 그런 패턴이 없다" 는 근거가 되지 못했다(해석된 부분집합에 대한 0 이었다).
      이제 해석 상태를 남기고, 확정은 B단계(의존성 해석 후)로 미룬다.

    ★ parse-only 는 `ModifierInvocation.kind` 를 채우지 않는다(실측: 항상 None). 그래서
      `constructor() Base(1)` 의 base-constructor specifier 가 modifier 사용과 구별되지 않는다 →
      생성자에서 이름이 base 계약과 일치하면 specifier 로 본다."""
    out = []
    is_ctor = fn.get("kind") == "constructor"
    for m in (fn.get("modifiers") or []):
        name = (m.get("modifierName") or {}).get("name")
        rec = {"modifier_name": name, "arg_count": len(m.get("arguments") or []),
               "resolution_basis": "syntax_only"}
        if is_ctor and name in owner_bases:
            out.append({**rec, **_MOD_UNKNOWN, "role": "base_constructor_specifier",
                        "resolution": "not_applicable"})
            continue
        rec["role"] = "modifier"
        if (owner, name) in by_owner:
            out.append({**rec, **by_owner[(owner, name)], "resolution": "declaring_contract"})
            continue
        cands = by_name.get(name) or []
        if len(cands) == 1:
            out.append({**rec, **cands[0][1], "resolution": "same_file_unique"})
        else:
            out.append({**rec, **_MOD_UNKNOWN,
                        "resolution": "ambiguous_in_file" if cands else "unresolved"})
    return out


def call_sites(body):
    """함수 본문의 호출 지점. 해석은 하지 않고 **구문 형태와 후보 이름**만 기록한다."""
    sites = []

    def visit(n):
        if n.get("nodeType") != "FunctionCall":
            return
        expr = n.get("expression") or {}
        et = expr.get("nodeType")
        kind, name, recv = "other", None, None
        if et == "Identifier":
            kind, name = "identifier_call", expr.get("name")
        elif et == "MemberAccess":
            kind, name = "member_call", expr.get("memberName")
            base = expr.get("expression") or {}
            recv = base.get("name") or base.get("nodeType")
            if base.get("nodeType") == "Identifier" and base.get("name") == "super":
                kind = "super_call"
        elif et == "NewExpression":
            kind = "new_contract"
        elif et == "ElementaryTypeNameExpression":
            kind = "type_cast"
        sites.append({"call_site_id": n.get("src"), "syntax_kind": kind,
                      "callee_name": name, "receiver": recv,
                      "is_builtin": bool(name and name in BUILTINS),
                      "resolution": "unresolved", "resolution_basis": "syntax_only"})
    walk(body, visit)
    return sites


def fn_features(body):
    c = Counter()

    def visit(n):
        t = n["nodeType"]
        if t in LOOPS:
            c["loops"] += 1
        if t in BRANCHES:
            c["branches"] += 1
        if t == "UncheckedBlock":
            c["unchecked"] += 1
        if t == "InlineAssembly":
            c["inline_assembly"] += 1
        if t == "TryStatement":
            c["try_catch"] += 1
        if t == "Return":
            c["returns"] += 1
        if t in ("RevertStatement",):
            c["revert"] += 1
        if t == "FunctionCall":
            c["calls"] += 1
    walk(body, visit)
    return c


def tier_of(fn, owner_kind, owner_abstract, is_free):
    """T1/T2/special/declaration_only — **엔진 실행 전에** 정적으로 결정한다."""
    kind = fn.get("kind", "function")
    body = fn.get("body")
    implemented = bool(body)
    if kind in ("constructor", "receive", "fallback"):
        return "special", kind
    if not implemented:
        return "declaration_only", kind
    # ★ 5차 검토: `implemented` 와 `nontrivial_body` 를 구분한다. 본문이 `{}` 인 함수는 디버깅할
    #   내용이 없으므로 타깃 계층에 넣지 않고 따로 센다(T1 수를 부풀리지 않기 위해).
    if not body.get("statements"):
        return "empty_body", kind
    if is_free:
        return "T2", "free_function"
    if owner_kind == "library":
        return "T2", "library_function"
    if owner_kind == "interface":
        return "declaration_only", kind
    vis = fn.get("visibility")
    if vis in ("public", "external"):
        return "T1", "abi_visible_contract_function"
    return "T2", "internal_contract_function"


def main():
    root, targets, solc_dir, out = sys.argv[1:5]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None

    tgt = json.load(io.open(targets, encoding="utf-8"))
    files = tgt["selected_option2"]              # 745 (옵션 2′ 569 는 이 부분집합)
    prime = set(tgt["selected_option2prime"])
    meta = tgt["selected_meta"]
    if limit:
        files = files[:limit]

    rows, parse_stat, per_file = [], Counter(), {}
    feat_files = Counter()
    missing_bins = Counter()
    for i, rel in enumerate(files, 1):
        if i % 100 == 0:
            print(f"  {i}/{len(files)}", flush=True)
        allowed = st.file_allowed(meta[rel]["specs"])
        version = "%d.%d.%d" % max(allowed) if allowed else None
        binp = solc_bin(solc_dir, version) if version else None
        if binp is None:
            parse_stat["solc_binary_missing"] += 1
            missing_bins[version] += 1
            per_file[rel] = {"parse_status": "solc_binary_missing", "solc_version": version}
            continue
        src = io.open(st.lp(os.path.join(root, rel)), encoding="utf-8", errors="replace").read()
        ast, errs = parse(binp, rel.replace("\\", "/"), src)
        hard = [e for e in errs if e[0] == "error"]
        if ast is None:
            kind = hard[0][1] if hard else "NoAst"
            parse_stat[f"fail:{kind}"] += 1
            per_file[rel] = {"parse_status": "fail", "solc_version": version,
                             "errors": hard[:3]}
            continue
        parse_stat["success"] += 1
        per_file[rel] = {"parse_status": "success", "solc_version": version,
                         "warnings": len(errs) - len(hard)}

        snap = meta[rel]["snap"]
        fileset = "option2prime" if rel in prime else "option2_only"
        # modifier 선언은 **계약별로** 모은다 — 이름만으로 모으면 서로 다른 계약의 동명 modifier 가
        #   서로를 덮어쓴다(6차 검토 C10).
        mods_by_owner, mods_by_name = {}, defaultdict(list)
        for cd in ast.get("nodes", []):
            if cd.get("nodeType") != "ContractDefinition":
                continue
            for n in cd.get("nodes", []):
                if n.get("nodeType") == "ModifierDefinition":
                    shape = mod_body_shape(n)
                    mods_by_owner[(cd.get("name"), n.get("name"))] = shape
                    mods_by_name[n.get("name")].append((cd.get("name"), shape))

        seen_feats = set()
        for unit_child in ast.get("nodes", []):
            ct = unit_child.get("nodeType")
            if ct == "FunctionDefinition":
                fns = [(unit_child, None, None, False, True, frozenset())]
            elif ct == "ContractDefinition":
                bases = frozenset((b.get("baseName") or {}).get("name")
                                  for b in (unit_child.get("baseContracts") or []))
                fns = [(n, unit_child.get("name"), unit_child.get("contractKind"),
                        bool(unit_child.get("abstract")), False, bases)
                       for n in unit_child.get("nodes", [])
                       if n.get("nodeType") == "FunctionDefinition"]
            else:
                continue
            for fn, owner, okind, oabs, is_free, obases in fns:
                tier, label = tier_of(fn, okind, oabs, is_free)
                body = fn.get("body")
                feats = fn_features(body) if body else Counter()
                params = params_of(fn, "parameters")
                needs_storage = any(p["data_location"] == "storage" for p in params)
                mrecs = modifier_records(fn, owner, obases, mods_by_owner, mods_by_name)
                mnames = [m["modifier_name"] for m in mrecs if m["role"] == "modifier"]
                row = {
                    "definition_id": f"{snap}|{rel}|{owner or '<file>'}|{fn.get('src')}",
                    "file": rel, "snapshot": snap, "file_set": fileset,
                    "solc_version": version,
                    "contract": owner, "contract_kind": okind,
                    "is_abstract_contract": oabs, "is_free_function": is_free,
                    "function": fn.get("name") or fn.get("kind"),
                    "function_kind": fn.get("kind", "function"),
                    "tier": tier, "tier_label": label,
                    "visibility": fn.get("visibility"),
                    "state_mutability": fn.get("stateMutability"),
                    "implemented": bool(body),
                    "nontrivial_body": bool(body and body.get("statements")),
                    "is_virtual": bool(fn.get("virtual")),
                    "has_override": bool(fn.get("overrides")),
                    "src": fn.get("src"),
                    "parameters": params,
                    "returns": params_of(fn, "returnParameters"),
                    "requires_storage_binding": needs_storage,
                    "modifiers": mnames,
                    "modifier_records": mrecs,
                    "features": {k: feats[k] for k in
                                 ("loops", "branches", "unchecked", "inline_assembly",
                                  "try_catch", "calls", "returns", "revert")},
                    "call_sites": call_sites(body) if body else [],
                }
                rows.append(row)
                for k in ("loops", "branches", "unchecked", "inline_assembly", "try_catch"):
                    if feats[k]:
                        seen_feats.add(k)
        for k in seen_feats:
            feat_files[k] += 1

    # ── 집계 ────────────────────────────────────────────────────────
    def agg(sel_rows, label):
        tiers = Counter(r["tier"] for r in sel_rows)
        return {
            "label": label,
            "functions_total": len(sel_rows),
            "by_tier": dict(tiers),
            "T1": tiers.get("T1", 0), "T2": tiers.get("T2", 0),
            "special": tiers.get("special", 0),
            "declaration_only": tiers.get("declaration_only", 0),
            "by_tier_label": dict(Counter(r["tier_label"] for r in sel_rows)),
            "by_visibility": dict(Counter(r["visibility"] for r in sel_rows
                                          if r["tier"] in ("T1", "T2"))),
            "requires_storage_binding": sum(1 for r in sel_rows
                                            if r["tier"] == "T2" and r["requires_storage_binding"]),
            "with_modifiers": sum(1 for r in sel_rows if r["modifiers"]),
            "modifier_resolution": dict(Counter(
                m["resolution"] for r in sel_rows for m in r["modifier_records"]
                if m["role"] == "modifier")),
            "functions_with_unresolved_modifier": sum(
                1 for r in sel_rows
                if any(m["role"] == "modifier"
                       and m["resolution"] in ("unresolved", "ambiguous_in_file")
                       for m in r["modifier_records"])),
            # ★ 아래 두 수는 **해석된 modifier 에 한해서만** 유효하다 — 미해결분(위 항목)은 알 수 없고,
            #   B단계에서 의존성을 해석한 뒤에야 확정된다. "0 = 그런 패턴 없음" 으로 읽으면 안 된다.
            "multi_placeholder_modifier_resolved_only": sum(
                1 for r in sel_rows
                if any((m.get("placeholder_count") or 0) > 1 for m in r["modifier_records"])),
            "split_body_modifier_resolved_only": sum(
                1 for r in sel_rows
                if any((m.get("stmts_before") or 0) > 0 or (m.get("stmts_after") or 0) > 0
                       for m in r["modifier_records"])),
            "base_constructor_specifiers": sum(
                1 for r in sel_rows for m in r["modifier_records"]
                if m["role"] == "base_constructor_specifier"),
            "targets_with_loops": sum(1 for r in sel_rows
                                      if r["tier"] in ("T1", "T2") and r["features"]["loops"]),
            "targets_with_branches": sum(1 for r in sel_rows
                                         if r["tier"] in ("T1", "T2") and r["features"]["branches"]),
            "targets_with_unchecked": sum(1 for r in sel_rows
                                          if r["tier"] in ("T1", "T2") and r["features"]["unchecked"]),
            "targets_with_assembly": sum(1 for r in sel_rows
                                         if r["tier"] in ("T1", "T2")
                                         and r["features"]["inline_assembly"]),
            "targets_with_try_catch": sum(1 for r in sel_rows
                                          if r["tier"] in ("T1", "T2") and r["features"]["try_catch"]),
            "empty_body": tiers.get("empty_body", 0),
            "call_sites_total": sum(len(r["call_sites"]) for r in sel_rows
                                    if r["tier"] in ("T1", "T2")),
            "call_sites_non_builtin": sum(
                1 for r in sel_rows if r["tier"] in ("T1", "T2")
                for cs in r["call_sites"] if not cs["is_builtin"]),
            "call_sites_by_kind": dict(Counter(
                cs["syntax_kind"] for r in sel_rows if r["tier"] in ("T1", "T2")
                for cs in r["call_sites"])),
            "snapshots": len({r["snapshot"] for r in sel_rows}),
            "files": len({r["file"] for r in sel_rows}),
        }

    summary = {
        "solc_releases_asof": st.SOLC_RELEASES_ASOF,
        "files_attempted": len(files),
        "parse_status": dict(parse_stat),
        "solc_binaries_missing_for": dict(missing_bins),
        "files_with_feature": dict(feat_files),
        "option2_all": agg(rows, "option2 (전체)"),
        "option2prime": agg([r for r in rows if r["file_set"] == "option2prime"],
                            "option2′ (프레임워크 제외)"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    json.dump({"summary": summary, "per_file": per_file, "functions": rows},
              io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
