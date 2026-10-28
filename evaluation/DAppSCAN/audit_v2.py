"""2차 외부 검토가 지적한 v2 결함의 **실제 규모**를 측정한다 (2026-10-28).

`select_targets.py`(v2)를 수정하지 않고, 그 로직을 충실히 복제한 뒤 지적 사항을 하나씩
덧붙여 후보 집합이 어떻게 변하는지 단계별로 보고한다. V0 가 682 를 재현하는지로 복제의
충실성을 self-check 한다.

  V0 = v2 그대로                         (기대: 682)
  V1 = V0 + import 정규식의 `^` 앵커 제거(한 줄에 여러 문장) + **문자열 밖에서만** 추출
  V2 = V1 + 폐포 전체의 **공통 컴파일러 버전** 요구
  V3 = V2 + pragma·함수를 **문자열까지 마스킹한 코드**에서 추출
  V4 = V3 + flattened `// File:` 마커를 **원문**에서 판정, 약한 신호(import 0 + 최상위 선언 3개↑)는
            제외 사유에서 빼고 `suspected_flattened` 태그로만 기록

추가 집계: 라이브러리 저장소 구성, lib/libs 단독 사유 제외, semver 구문 오류(fail-open),
0.8.30 초과 버전 참조, 폐포 크기 분포, v1↔v2 집합 교체 규모.

사용: python -I audit_v2.py <contracts_root> <v1_json> <v2_json> <out_json>
"""
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

# ─────────────────────────────────────────────────────────────────────────────
# v2 로직 **내장 사본** (2026-10-28, 3차 검토 지적 ②-2 반영).
#   원래는 `import select_targets as st` 로 당시의 v2 모듈을 썼는데, 그 뒤 모듈이 v3/v4 로
#   교체되면서 `IMPORT`·`snapshot_root` 가 사라져 **이 감사 스크립트가 재실행되지 않았다**.
#   재현성을 위해 v2 가 쓰던 규칙만 여기에 그대로 복제한다. 이 파일은 외부 모듈에 의존하지 않는다.
#   (v2 전체 소스는 당시 버전 관리 밖에서 덮어써져 남아 있지 않다. 이후 판은
#    `historical/select_targets_v*.py` 로 보존한다.)
# ─────────────────────────────────────────────────────────────────────────────
PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);", re.I)
IMPORT = re.compile(r"""^\s*import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""", re.M)
FUNC = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(")
TOPDECL = re.compile(r"(?m)^\s*(?:abstract\s+)?(contract|library|interface)\s+\w+")
FILE_MARK = re.compile(r"(?m)^\s*//\s*File:")
_CMP = re.compile(r"(\^|~|>=|<=|>|<|=)?\s*(\d+)\.(\d+)(?:\.(\d+))?")
SOLC_VERSIONS = ([(0, 4, i) for i in range(0, 27)] + [(0, 5, i) for i in range(0, 18)]
                 + [(0, 6, i) for i in range(0, 13)] + [(0, 7, i) for i in range(0, 7)]
                 + [(0, 8, i) for i in range(0, 31)])          # v2 의 상한(0.8.30)
EXT_DIRS = {"node_modules", "lib", "libs", "vendor", "external", "third_party",
            "third-party", "deps", "dependencies", "submodules"}
EXT_PKGS = {"openzeppelin", "openzeppelin-contracts", "openzeppelin-contracts-upgradeable",
            "uniswap", "uniswap-v2-core", "uniswap-v3-core", "chainlink", "aave",
            "solmate", "forge-std", "ds-test", "hardhat", "truffle"}
TEST_DIRS = {"test", "tests", "testing", "mock", "mocks", "test_helpers", "test-utilities",
             "testutils", "test-utils"}
VERIF_DIRS = {"certora", "spec", "specs", "echidna", "scribble", "halmos"}
SNAPSHOT_MARKERS = {"package.json", "foundry.toml", "hardhat.config.js", "hardhat.config.ts",
                    "truffle-config.js", "truffle.js", "remappings.txt", "brownie-config.yaml",
                    ".git", "yarn.lock", "package-lock.json"}


def lp(p):
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


def _walk_error(exc):
    raise RuntimeError(f"디렉터리 탐색 실패: {getattr(exc, 'filename', exc)}") from exc


def collect(root):
    sols, marker_dirs = [], set()
    for dirpath, dirs, names in os.walk(lp(root), onerror=_walk_error, followlinks=False):
        dirs.sort()
        names.sort()
        rel_dir = os.path.relpath(dirpath, lp(root)).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for nm in names:
            if nm.endswith(".sol"):
                sols.append(f"{rel_dir}/{nm}" if rel_dir else nm)
        if (SNAPSHOT_MARKERS & set(names)) or ".git" in dirs:
            marker_dirs.add(rel_dir)
    return sols, marker_dirs, {}


def snapshot_root(rel, marker_dirs):
    parts = rel.split("/")[:-1]
    for i in range(2, len(parts) + 1):
        cand = "/".join(parts[:i])
        if cand in marker_dirs:
            return cand, "marker"
    if len(parts) >= 2:
        return "/".join(parts[:2]), "depth2_fallback"
    return (parts[0] if parts else ""), "depth1_fallback"


def path_tags(rel):
    parts = rel.lower().split("/")
    dirs, name = parts[:-1], parts[-1]
    tags = set()
    if any(d in EXT_DIRS for d in dirs) or any(d.startswith("@") for d in dirs) \
            or any(d in EXT_PKGS for d in dirs):
        tags.add("external_dependency")
    if any(d in TEST_DIRS for d in dirs) or name.endswith(".t.sol") \
            or name.startswith(("mock", "test")):
        tags.add("test_or_mock")
    if any(d in VERIF_DIRS for d in dirs):
        tags.add("verification")
    if "migration" in name:
        tags.add("migration")
    return tags


def func_count(txt):
    n = 0
    for m in FUNC.finditer(txt):
        tail = txt[m.end():m.end() + 400]
        b, s = tail.find("{"), tail.find(";")
        if b != -1 and (s == -1 or b < s):
            n += 1
    return n


def _sat(v, op, major, minor, patch):
    target = (major, minor, patch if patch is not None else 0)
    if op in ("^", "~"):
        return v >= target and (v[0], v[1]) == (major, minor)
    if op == ">=":
        return v >= target
    if op == ">":
        return v > target
    if op == "<=":
        return v <= (major, minor, patch) if patch is not None else v[:2] <= (major, minor)
    if op == "<":
        return v < target if patch is not None else v[:2] < (major, minor)
    return v == target if patch is not None else v[:2] == (major, minor)


def allowed_versions(spec):
    out = set()
    for part in spec.split("||"):
        cmps = [(m.group(1), int(m.group(2)), int(m.group(3)),
                 None if m.group(4) is None else int(m.group(4)))
                for m in _CMP.finditer(part)]
        if cmps:
            out |= {v for v in SOLC_VERSIONS if all(_sat(v, *c) for c in cmps)}
    return out


def file_allowed(specs):
    if not specs:
        return None
    acc = None
    for s in specs:
        a = allowed_versions(s)
        acc = a if acc is None else (acc & a)
    return acc

# ── 어휘 처리 ────────────────────────────────────────────────────────
# bare : 주석만 공백화 (길이 보존, 문자열 유지)  ← v2 가 쓰는 것
# mask : 주석 + **문자열 내용**까지 공백화 (길이 보존)
_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def lex(raw: str):
    def blank(t):
        return re.sub(r"[^\n]", " ", t)

    bare_parts, mask_parts, pos = [], [], 0
    for m in _TOKEN.finditer(raw):
        bare_parts.append(raw[pos:m.start()])
        mask_parts.append(raw[pos:m.start()])
        t = m.group()
        if t.startswith(("//", "/*")):
            bare_parts.append(blank(t))
            mask_parts.append(blank(t))
        else:                      # 문자열 리터럴
            bare_parts.append(t)
            mask_parts.append(blank(t))
        pos = m.end()
    bare_parts.append(raw[pos:])
    mask_parts.append(raw[pos:])
    return "".join(bare_parts), "".join(mask_parts)


IMPORT_PATH = re.compile(r"""import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""")
IMPORT_KW = re.compile(r"\bimport\b")


def imports_unanchored(bare: str, mask: str) -> list:
    """`import` 키워드를 **문자열 밖**(mask)에서 찾고 경로는 원문(bare)에서 읽는다."""
    out = []
    for m in IMPORT_KW.finditer(mask):
        mm = IMPORT_PATH.match(bare, m.start())
        if mm:
            out.append(mm.group(1))
    return out


_SPEC_GARBAGE = re.compile(r"[^\s]")


def spec_syntax_ok(spec: str) -> bool:
    for part in spec.split("||"):
        if _SPEC_GARBAGE.search(_CMP.sub("", part)):
            return False
    return True


def main():
    root, v1json, v2json, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    sols, marker_dirs, _ = collect(root)
    print("수집:", len(sols))

    files = {}
    idx = defaultdict(list)
    by_snap = defaultdict(list)
    stat = Counter()
    flat_rule = Counter()
    for rel in sols:
        raw = io.open(lp(os.path.join(root, rel)), encoding="utf-8", errors="replace").read()
        bare, mask = lex(raw)
        imp_anchored = IMPORT.findall(bare)
        imp_fixed = imports_unanchored(bare, mask)
        specs_bare = PRAGMA.findall(bare)
        specs_mask = PRAGMA.findall(mask)
        f_bare, f_mask = func_count(bare), func_count(mask)
        if len(imp_fixed) > len(imp_anchored):
            stat["files_with_missed_imports"] += 1
            stat["missed_imports"] += len(imp_fixed) - len(imp_anchored)
        if len(specs_bare) != len(specs_mask):
            stat["pragma_in_string"] += 1
        if not specs_mask and specs_bare:
            stat["pragma_only_in_string"] += 1
        if f_bare != f_mask:
            stat["func_count_inflated_by_strings"] += 1
            stat["func_overcount_strings"] += f_bare - f_mask
        for s in specs_mask:
            if not spec_syntax_ok(s):
                stat["spec_syntax_garbage"] += 1
                break
        if re.search(r"0\.8\.(3[1-9]|[4-9]\d)", " ".join(specs_mask)):
            stat["refs_above_0_8_30"] += 1
        # flattened 판정 근거 분리
        marker_raw = len(FILE_MARK.findall(raw)) >= 2
        marker_bare = len(FILE_MARK.findall(bare)) >= 2
        weak_decl = len(imp_fixed) == 0 and len(TOPDECL.findall(mask)) >= 3
        name_flat = "flat" in rel.lower().split("/")[-1] or any(
            "flat" in p for p in rel.lower().split("/")[:-1])
        if marker_raw:
            flat_rule["marker_raw"] += 1
        if marker_bare:
            flat_rule["marker_bare(v2가 본 것)"] += 1
        if weak_decl:
            flat_rule["weak_decl_only"] += 1
        if name_flat:
            flat_rule["path_name_flat"] += 1
        snap, basis = snapshot_root(rel, marker_dirs)
        files[rel] = {
            "snap": snap, "basis": basis,
            "imp_anchored": imp_anchored, "imp_fixed": imp_fixed,
            "allowed_bare": file_allowed(specs_bare),
            "allowed_mask": file_allowed(specs_mask),
            "funcs_bare": f_bare, "funcs_mask": f_mask,
            "tags": path_tags(rel),
            "marker_raw": marker_raw, "marker_bare": marker_bare,
            "weak_decl": weak_decl, "name_flat": name_flat,
        }
        by_snap[snap].append(rel)
        parts = rel.split("/")
        for i in range(len(parts)):
            idx["/".join(parts[i:])].append(rel)
    print("지적별 발생 규모:", dict(stat))
    print("flattened 판정 근거별:", dict(flat_rule))
    print("스냅샷 판정 근거:", dict(Counter(r["basis"] for r in files.values())))

    # ── 해석기 (v2 복제; import 목록과 pragma 출처만 변형으로 갈아끼운다) ──
    def build(imp_key):
        how = Counter()
        edges, miss = {}, Counter()

        def resolve(src, spec):
            snap = files[src]["snap"]
            base = os.path.dirname(src)
            if spec.startswith("."):
                t = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
                if t in files:
                    if files[t]["snap"] == snap:
                        how["relative"] += 1
                        return t
                    how["relative_escaped_boundary"] += 1
                else:
                    how["relative_missing"] += 1
                return None
            t = os.path.normpath(os.path.join(snap, spec.lstrip("/"))).replace("\\", "/")
            if t in files and files[t]["snap"] == snap:
                how["snapshot_relative"] += 1
                return t
            t = os.path.normpath(os.path.join(base, spec.lstrip("/"))).replace("\\", "/")
            if t in files and files[t]["snap"] == snap:
                how["same_dir"] += 1
                return t
            hits = [h for h in idx.get(spec.lstrip("/"), ()) if files[h]["snap"] == snap]
            if len(hits) == 1:
                how["unique_suffix"] += 1
                return hits[0]
            if len(hits) > 1:
                how["ambiguous_rejected"] += 1
                return None
            how["unresolved_nonrelative"] += 1
            return None

        for rel, rec in files.items():
            o = []
            for spec in rec[imp_key]:
                r = resolve(rel, spec)
                if r:
                    o.append(r)
                else:
                    miss[rel] += 1
            edges[rel] = o
        return edges, miss, how

    def closures(edges, miss):
        cache = {}

        def cl(rel):
            if rel in cache:
                return cache[rel]
            seen, m = {rel}, miss[rel]
            work = list(edges.get(rel, ()))
            while work:
                cur = work.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                m += miss.get(cur, 0)
                work.extend(edges.get(cur, ()))
            cache[rel] = (seen, m)
            return cache[rel]
        return cl

    EXCLUDE = {"external_dependency", "test_or_mock", "verification", "migration"}

    def run(imp_key, allow_key, common_compiler, flat_mode):
        """flat_mode: 'v2'(bare 마커+약신호) | 'fixed'(raw 마커+경로명만 제외)"""
        edges, miss, how = build(imp_key)
        cl = closures(edges, miss)
        rej = Counter()
        cand = []
        for rel, rec in files.items():
            a = rec[allow_key]
            if a is None:
                rej["no_real_pragma"] += 1
                continue
            if not a:
                rej["contradictory_pragma"] += 1
                continue
            if min(a) < (0, 8, 0):
                rej["below_0.8"] += 1
                continue
            tags = set(rec["tags"])
            if flat_mode == "v2":
                if rec["marker_bare"] or rec["weak_decl"] or rec["name_flat"]:
                    tags.add("flattened")
            else:
                if rec["marker_raw"] or rec["name_flat"]:
                    tags.add("flattened")
            if tags & (EXCLUDE | {"flattened"}):
                rej["tag_excluded"] += 1
                continue
            if rec["funcs_mask" if allow_key == "allowed_mask" else "funcs_bare"] == 0:
                rej["no_body_function"] += 1
                continue
            cand.append(rel)
        clean = []
        for rel in cand:
            seen, m = cl(rel)
            if m > 0:
                rej["closure_unresolved"] += 1
                continue
            sets = [files[x][allow_key] for x in seen]
            if any(s is None for s in sets):
                rej["closure_no_pragma"] += 1
                continue
            if any((not s) or min(s) < (0, 8, 0) for s in sets):
                rej["closure_below_0.8"] += 1
                continue
            if common_compiler:
                common = set.intersection(*(set(s) for s in sets))
                if not common:
                    rej["closure_no_common_compiler"] += 1
                    continue
                if min(common) < (0, 8, 0):
                    rej["closure_common_below_0.8"] += 1
                    continue
            clean.append(rel)
        return clean, rej, how, cl

    variants = {}
    v0, rej0, how0, _ = run("imp_anchored", "allowed_bare", False, "v2")
    variants["V0_v2재현"] = v0
    v1, rej1, how1, _ = run("imp_fixed", "allowed_bare", False, "v2")
    variants["V1_import수정"] = v1
    v2_, rej2, _, _ = run("imp_fixed", "allowed_bare", True, "v2")
    variants["V2_공통컴파일러"] = v2_
    v3, rej3, _, _ = run("imp_fixed", "allowed_mask", True, "v2")
    variants["V3_문자열마스킹"] = v3
    v4, rej4, how4, cl4 = run("imp_fixed", "allowed_mask", True, "fixed")
    variants["V4_flatten수정"] = v4

    print("\n── 단계별 후보 수 ──")
    for k, v in variants.items():
        print(f"  {k:22s} {len(v):5d}")
    print("\nV0 탈락 사유:", dict(rej0))
    print("V4 탈락 사유:", dict(rej4))
    print("\nV0 import 해석:", dict(how0))
    print("V4 import 해석:", dict(how4))

    # ── 라이브러리 저장소 구성 (최종 V4 기준) ───────────────────────
    per_snap = Counter(files[r]["snap"] for r in v4)
    print("\n── V4 상위 스냅샷 ──")
    for s, n in per_snap.most_common(12):
        print(f"  {n:4d}  {s}")
    OZ = re.compile(r"openzeppelin", re.I)
    oz_snaps = {s: n for s, n in per_snap.items() if OZ.search(s.split("/")[-1])}
    print("스냅샷 이름이 openzeppelin 인 것:", oz_snaps)

    # ── lib/libs 단독 사유 제외 ─────────────────────────────────────
    libonly = [r for r, rec in files.items()
               if "external_dependency" in rec["tags"]
               and rec["allowed_mask"] and min(rec["allowed_mask"]) >= (0, 8, 0)
               and not ({"test_or_mock", "verification", "migration"} & rec["tags"])
               and any(p in ("lib", "libs") for p in r.lower().split("/")[:-1])
               and not any(p == "node_modules" for p in r.lower().split("/")[:-1])]
    nested = [r for r in libonly
              if any(p in ("contracts", "src") for p in r.lower().split("/")[:-1])]
    print("\nlib/libs 때문에 external 로 빠진 ≥0.8 파일:", len(libonly),
          "| 그중 contracts//src/ 하위(프로젝트 자작 가능성):", len(nested))

    # ── 폐포 크기 분포 (V4) ────────────────────────────────────────
    sizes = Counter(len(cl4(r)[0]) for r in v4)
    print("폐포 크기 1:", sizes.get(1, 0), "| 2~4:", sum(v for k, v in sizes.items() if 2 <= k <= 4),
          "| 5+:", sum(v for k, v in sizes.items() if k >= 5))

    # ── v1 ↔ v2 교체 규모 ──────────────────────────────────────────
    s1 = set(json.load(io.open(v1json, encoding="utf-8"))["clean_closure_files"])
    s2 = set(json.load(io.open(v2json, encoding="utf-8"))["clean_files"])
    print("\nv1:", len(s1), "v2:", len(s2), "공통:", len(s1 & s2),
          "v1만:", len(s1 - s2), "v2만:", len(s2 - s1))

    json.dump({
        "scale_of_defects": dict(stat),
        "flatten_rule_counts": dict(flat_rule),
        "variant_sizes": {k: len(v) for k, v in variants.items()},
        "V0_rejected": dict(rej0), "V4_rejected": dict(rej4),
        "V0_import_resolution": dict(how0), "V4_import_resolution": dict(how4),
        "V4_per_snapshot": per_snap.most_common(),
        "oz_named_snapshots": oz_snaps,
        "lib_libs_only_excluded": len(libonly),
        "lib_libs_only_nested_in_contracts_or_src": len(nested),
        "V4_closure_size": {"1": sizes.get(1, 0),
                            "2-4": sum(v for k, v in sizes.items() if 2 <= k <= 4),
                            "5+": sum(v for k, v in sizes.items() if k >= 5)},
        "v1_v2_overlap": {"v1": len(s1), "v2": len(s2), "both": len(s1 & s2),
                          "v1_only": len(s1 - s2), "v2_only": len(s2 - s1)},
        "V4_files": sorted(v4),
    }, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
