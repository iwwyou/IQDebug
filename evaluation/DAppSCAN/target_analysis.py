"""타깃 함수 집계 (2026-10-28, 6차 검토 F3′).

`ast_manifest.json`(사실) → **타깃 정책**(판단)을 분리한다. manifest 는 가시성·구현 여부·호출 구조만
기록하고, "무엇을 디버깅 타깃으로 삼는가" 는 전부 여기서 결정·집계한다. 정책이 바뀌어도 manifest 를
다시 만들 필요가 없고, 논문에 쓸 숫자의 출처가 한 파일에 모인다.

핵심 정의
  · 실질 타깃 = T1 중 **본문에 탐색할 구조가 있는** 함수
    (비-builtin 호출 ∨ 분기 ∨ 루프). 호출은 Step Into 의 대상, 분기·루프는 커서가 갈라지는 지점이다.
  · 제외 사유를 3분류로 남긴다 — 예전엔 "view/pure 462 + 기타 199" 라 기타의 정체가 설명되지 않았다.

6차 검토 C11 에 대한 판단 (채택하지 않음)
  외부 검토는 `or bool(modifiers)` 를 넣어 166개를 타깃에 넣으라고 권고했다. 실측 결과 그 166개는
  modifier 가 전부 접근 통제이고(onlyOwner 62 · onlyByOwnerOrGovernance 61 · onlyAdmin 9 …),
  해석된 placeholder 가 전부 1개(본문 분할 없음), 166개 중 145개는 본문에 호출·분기·루프·return 이
  아예 없는 단일 대입 setter(`setTimelock`·`setOwner`·`setAdmin` …)다. `require(msg.sender == owner)`
  의 결과는 사용자가 넣은 `@GlobalVar msg.sender`/`@StateVar owner` 로 **이미 결정**되므로 추상 상태를
  탐색해 알아낼 것이 없다. 타깃에 넣으면 커버리지 분모만 희석된다 → **제외 사유로 명시**한다.

사용: python -I target_analysis.py <ast_manifest.json> <targets_vN.json> <out.json> [--fileset option2prime]
"""
import io
import json
import re
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INT_SCALAR = re.compile(r"^u?int(\d+)?$")
INT_ARRAY = re.compile(r"^u?int(\d+)?\[\d*\]$")


def has_debug_structure(r):
    """실질 타깃 규칙 — 본문에 탐색할 구조가 있는가."""
    return (any(not c["is_builtin"] for c in r["call_sites"])
            or r["features"]["branches"] > 0
            or r["features"]["loops"] > 0)


def exclusion_reason(r):
    """실질 타깃에서 빠진 T1 의 사유. 순서가 의미를 가진다(view/pure 를 먼저 본다)."""
    if r["state_mutability"] in ("view", "pure"):
        return "trivial_view_pure_getter"
    if r["modifiers"]:
        return "access_control_guarded_setter"
    return "other"


def type_bucket(t):
    """입력 생성 규칙이 갈리는 단위로 파라미터 타입을 묶는다.
    숫자 스칼라만 `[lo,hi]` 폭 실험의 대상이고, 정수 배열은 `array[...]` 로 **다른 문법**이다
    (6차 검토에서 외부 검토가 둘을 한 분모로 묶어 1,087 을 보고했다 — 분리해야 한다)."""
    t = t or "?"
    if INT_SCALAR.match(t):
        return "int_scalar"
    if INT_ARRAY.match(t):
        return "int_array"
    if t == "address":
        return "address"
    if t.startswith("address["):
        return "address_array"
    if t == "bool":
        return "bool"
    if t.startswith("bytes") or t == "string":
        return "bytes_string"
    if t.startswith("user:"):
        return "user_defined"
    if t.startswith("mapping"):
        return "mapping"
    return "other"


def modifier_uses(r):
    """base-constructor specifier 를 뺀 실제 modifier 사용만."""
    return [m for m in r["modifier_records"] if m["role"] == "modifier"]


def main():
    man_path, tgt_path, out_path = sys.argv[1:4]
    fileset = sys.argv[sys.argv.index("--fileset") + 1] if "--fileset" in sys.argv else "option2prime"

    man = json.load(io.open(man_path, encoding="utf-8"))
    tgt = json.load(io.open(tgt_path, encoding="utf-8"))
    meta = tgt["selected_meta"]
    rows = [r for r in man["functions"]
            if fileset == "option2" or r["file_set"] == "option2prime"]
    sel_files = set(tgt["selected_option2prime"] if fileset == "option2prime"
                    else tgt["selected_option2"])
    parsed = {f for f, v in man["per_file"].items()
              if v.get("parse_status") == "success" and f in sel_files}

    out = {"fileset": fileset,
           "files_selected": len(sel_files),
           "files_parsed": len(parsed),
           "files_unparsed": sorted(sel_files - parsed),
           "snapshots": len({r["snapshot"] for r in rows})}

    # ── 계층 ────────────────────────────────────────────────────────
    tier = Counter(r["tier"] for r in rows)
    out["by_tier"] = dict(tier)
    T1 = [r for r in rows if r["tier"] == "T1"]
    T2 = [r for r in rows if r["tier"] == "T2"]
    out["T1_by_label"] = dict(Counter(r["tier_label"] for r in T1))
    out["T2_by_label"] = dict(Counter(r["tier_label"] for r in T2))
    out["T1_in_abstract_contract"] = sum(1 for r in T1 if r["is_abstract_contract"])
    out["library_by_visibility"] = dict(Counter(
        r["visibility"] for r in T2 if r["contract_kind"] == "library"))

    # ── 실질 타깃과 제외 사유 ──────────────────────────────────────
    real = [r for r in T1 if has_debug_structure(r)]
    excl = [r for r in T1 if not has_debug_structure(r)]
    reasons = Counter(exclusion_reason(r) for r in excl)
    out["real_targets"] = len(real)
    out["excluded_from_T1"] = {"total": len(excl), "by_reason": dict(reasons)}

    guarded = [r for r in excl if exclusion_reason(r) == "access_control_guarded_setter"]
    out["access_control_setters"] = {
        "count": len(guarded),
        "modifier_names": dict(Counter(m["modifier_name"] for r in guarded
                                       for m in modifier_uses(r)).most_common(15)),
        "placeholder_counts": dict(Counter(
            m.get("placeholder_count") for r in guarded for m in modifier_uses(r))),
        "bodies_with_no_feature": sum(1 for r in guarded
                                      if not any(r["features"][k] for k in r["features"])),
        "function_names": dict(Counter(r["function"] for r in guarded).most_common(12)),
    }

    # ── overload (F4 로 지정 가능해졌다) ───────────────────────────
    groups = defaultdict(list)
    for r in T1:
        groups[(r["file"], r["contract"], r["function"])].append(r)
    ov = {k: v for k, v in groups.items() if len(v) > 1}
    real_ids = {r["definition_id"] for r in real}
    out["overloads"] = {
        "groups": len(ov), "definitions": sum(len(v) for v in ov.values()),
        "unreachable_by_name_only": sum(len(v) - 1 for v in ov.values()),
        "real_targets_in_overload_groups": sum(
            1 for v in ov.values() for r in v if r["definition_id"] in real_ids),
        "names": dict(Counter(k[2] for k in ov).most_common(10)),
    }

    # ── 중복 파일 제거 후 ──────────────────────────────────────────
    #   같은 내용의 파일이 여러 스냅샷에 들어 있다(normalized_hash 기준). 실행 집합은 하나만 쓴다.
    by_hash = defaultdict(list)
    for f in sorted(sel_files):
        h = (meta.get(f) or {}).get("normalized_hash")
        by_hash[h or f].append(f)
    keep = {v[0] for v in by_hash.values()}
    out["duplicate_files"] = {"groups": sum(1 for v in by_hash.values() if len(v) > 1),
                              "files_dropped": len(sel_files) - len(keep)}
    out["real_targets_deduped"] = sum(1 for r in real if r["file"] in keep)

    # ── 입력 생성 관련 형태 ────────────────────────────────────────
    out["real_target_shape"] = {
        "param_count": dict(sorted(Counter(min(len(r["parameters"]), 4) for r in real).items())),
        "no_params": sum(1 for r in real if not r["parameters"]),
        "param_type_buckets": dict(Counter(type_bucket(p["type"])
                                           for r in real for p in r["parameters"]).most_common()),
        "with_int_scalar_param": sum(
            1 for r in real if any(INT_SCALAR.match(p["type"] or "") for p in r["parameters"])),
        "with_int_array_but_no_scalar": sum(
            1 for r in real
            if not any(INT_SCALAR.match(p["type"] or "") for p in r["parameters"])
            and any(INT_ARRAY.match(p["type"] or "") for p in r["parameters"])),
        "unnamed_params": sum(1 for r in real for p in r["parameters"] if not p["name"]),
        "state_mutability": dict(Counter(r["state_mutability"] for r in real)),
    }

    # ── modifier 해석 상태 (C10) ───────────────────────────────────
    uses = [m for r in rows for m in modifier_uses(r)]
    unresolved = {"unresolved", "ambiguous_in_file"}
    out["modifiers"] = {
        "uses_total": len(uses),
        "by_resolution": dict(Counter(m["resolution"] for m in uses)),
        "functions_with_use": sum(1 for r in rows if modifier_uses(r)),
        "functions_with_unresolved": sum(
            1 for r in rows if any(m["resolution"] in unresolved for m in modifier_uses(r))),
        "T1_with_unresolved": sum(
            1 for r in T1 if any(m["resolution"] in unresolved for m in modifier_uses(r))),
        "base_constructor_specifiers": sum(
            1 for r in rows for m in r["modifier_records"]
            if m["role"] == "base_constructor_specifier"),
        # ★ 아래 둘은 **해석된 사용분에 한해서만** 유효하다.
        "multi_placeholder_resolved_only": sum(
            1 for m in uses if (m.get("placeholder_count") or 0) > 1),
        "split_body_resolved_only": sum(
            1 for m in uses if (m.get("stmts_before") or 0) > 0 or (m.get("stmts_after") or 0) > 0),
    }

    print(json.dumps(out, indent=2, ensure_ascii=False))
    json.dump(out, io.open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out_path)


if __name__ == "__main__":
    main()
