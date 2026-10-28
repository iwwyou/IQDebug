"""DAppSCAN-source/contracts 프로젝트별 pragma 구성 + import 폐포 분석.

산출:
  1) 프로젝트 분류: 전부 >=0.8 / 벤더만 구버전 / 혼재 / >=0.8 없음
  2) 후보 파일(>=0.8, 벤더 경로 아님)의 **import 폐포**가 전부 >=0.8 인지, 미해결 import 가 있는지
  3) 폐포가 깨끗한 후보 파일에서 함수 선언 수(대략치)

사용: python -I scan_projects.py <contracts_root> <out_json>
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);", re.I)
IMPORT = re.compile(r"""^\s*import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""", re.M)
# 본문 있는 함수 선언 대략치: 선언 뒤 '{' 가 세미콜론보다 먼저 오는 것
FUNC = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(")
VENDOR_PARTS = ("node_modules", "@openzeppelin", "openzeppelin", "uniswap", "chainlink",
                "aave", "@chainlink", "forge-std", "ds-test", "hardhat")


def min_minor(spec):
    best = None
    for m in re.finditer(r"(\d+)\.(\d+)", spec):
        major, minor = int(m.group(1)), int(m.group(2))
        if major != 0:
            continue
        if best is None or minor < best:
            best = minor
    return best


def min_minor_all(txt):
    """파일 안 **모든** pragma 의 최소 허용 minor.

    ★ 2026-10-27 수정: 예전에는 `PRAGMA.search` 로 **첫 pragma 하나만** 읽었다. Etherscan
      flatten 파일은 pragma 가 여러 번(서로 다른 버전으로) 나오므로 첫 줄이 `^0.8.0` 이고
      뒤쪽 섹션이 `^0.5.16` 인 파일이 0.8 로 잘못 분류됐다(측정: 그런 파일 7개).
    """
    best = None
    for spec in PRAGMA.findall(txt):
        v = min_minor(spec)
        if v is not None and (best is None or v < best):
            best = v
    return best


def is_vendor(rel):
    low = rel.lower()
    parts = low.split("/")
    if any(v in parts for v in ("node_modules", "lib", "vendor", "mocks", "test", "tests")):
        return True
    return any(v in low for v in VENDOR_PARTS)


def func_count(txt):
    n = 0
    for m in FUNC.finditer(txt):
        tail = txt[m.end():m.end() + 400]
        brace = tail.find("{")
        semi = tail.find(";")
        if brace != -1 and (semi == -1 or brace < semi):
            n += 1
    return n



def _lp(p):
    r"""Windows MAX_PATH 회피: 260자 넘는 경로는 \\?\ 확장 경로로 연다."""
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


def main():
    root, out = sys.argv[1], sys.argv[2]
    files = {}            # rel -> dict(minor, vendor, imports[list of raw], funcs, project)
    by_project = defaultdict(list)
    index_by_suffix = defaultdict(list)   # 경로 접미사 매칭용

    for dirpath, _dirs, names in os.walk(root):
        for nm in names:
            if not nm.endswith(".sol"):
                continue
            p = os.path.join(dirpath, nm)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                txt = open(_lp(p), "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            minor = min_minor_all(txt)
            proj = rel.split("/")[0]
            rec = {
                "minor": minor,
                "vendor": is_vendor(rel),
                "imports": IMPORT.findall(txt),
                "funcs": func_count(txt),
                "project": proj,
            }
            files[rel] = rec
            by_project[proj].append(rel)
            parts = rel.split("/")
            for i in range(len(parts)):
                index_by_suffix["/".join(parts[i:])].append(rel)

    # ── import 해석 ───────────────────────────────────────────────
    # ★ 2026-10-27 수정: **같은 프로젝트 안에서만** 해석한다(엄격 모드).
    #   예전에는 패키지 경로(`@openzeppelin/...`)를 자기 저장소에서 못 찾으면 **다른 프로젝트의
    #   사본**을 채택했다. 측정 결과 4,195건이 그렇게 해석됐고(그중 끌어온 사본이 0.8 미만 913건),
    #   폐포 버전 판정이 무관한 프로젝트의 파일에 좌우됐다. 지금은 미해결로 세고, 그런 타깃은
    #   후보에서 떨어진다(= 외부 의존성을 npm/forge 로 복원해야 하는 "옵션 3" 대상).
    cross_blocked = Counter()

    def resolve(src_rel, spec):
        base = os.path.dirname(src_rel)
        if spec.startswith("."):
            cand = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
            if cand in files:
                return cand
            return None
        # 절대/패키지 경로: 같은 프로젝트 안에서 접미사 매칭
        proj = src_rel.split("/")[0]
        spec_n = spec.lstrip("/")
        hits = index_by_suffix.get(spec_n, [])
        same = [h for h in hits if h.startswith(proj + "/")]
        if same:
            return same[0]
        if hits:
            cross_blocked[src_rel] += 1   # 다른 프로젝트에만 있음 → 미해결로 둔다
            return None
        # basename 매칭 (같은 프로젝트 안에서 유일할 때만)
        bn = spec_n.split("/")[-1]
        cands = [h for h in by_project.get(proj, []) if h.endswith("/" + bn)]
        if len(cands) == 1:
            return cands[0]
        return None

    edges = {}
    unresolved = Counter()
    for rel, rec in files.items():
        outs, miss = [], 0
        for spec in rec["imports"]:
            r = resolve(rel, spec)
            if r:
                outs.append(r)
            else:
                miss += 1
        edges[rel] = outs
        unresolved[rel] = miss

    # ── 폐포 ──────────────────────────────────────────────────────
    closure_cache = {}

    def closure(rel, stack=()):
        if rel in closure_cache:
            return closure_cache[rel]
        seen = {rel}
        miss = unresolved[rel]
        work = list(edges.get(rel, ()))
        while work:
            cur = work.pop()
            if cur in seen:
                continue
            seen.add(cur)
            miss += unresolved.get(cur, 0)
            work.extend(edges.get(cur, ()))
        closure_cache[rel] = (seen, miss)
        return closure_cache[rel]

    # ── 프로젝트 분류 ─────────────────────────────────────────────
    proj_stats = {}
    for proj, rels in by_project.items():
        minors = [files[r]["minor"] for r in rels]
        n = len(rels)
        n_none = sum(1 for m in minors if m is None)
        n_ge = sum(1 for m in minors if m is not None and m >= 8)
        n_lt = sum(1 for m in minors if m is not None and m < 8)
        n_lt_nonvendor = sum(1 for r in rels
                             if files[r]["minor"] is not None and files[r]["minor"] < 8
                             and not files[r]["vendor"])
        if n_ge == 0:
            cls = "D_no_ge08"
        elif n_lt == 0 and n_none == 0:
            cls = "A_all_ge08"
        elif n_lt == 0:
            cls = "A2_ge08_plus_nopragma"
        elif n_lt_nonvendor == 0:
            cls = "B_old_only_vendor"
        else:
            cls = "C_mixed"
        proj_stats[proj] = {"files": n, "ge08": n_ge, "lt08": n_lt, "nopragma": n_none,
                            "lt08_nonvendor": n_lt_nonvendor, "class": cls}

    # ── 후보 파일(>=0.8, 비벤더)의 폐포 품질 ───────────────────────
    cand = [r for r, rec in files.items()
            if rec["minor"] is not None and rec["minor"] >= 8 and not rec["vendor"] and rec["funcs"] > 0]
    cstat = Counter()
    clean_files, clean_funcs = [], 0
    clean_by_proj = Counter()
    for r in cand:
        seen, miss = closure(r)
        worst = min((files[x]["minor"] for x in seen if files[x]["minor"] is not None), default=None)
        has_none = any(files[x]["minor"] is None for x in seen)
        if miss > 0:
            cstat["closure_unresolved_import"] += 1
        elif worst is not None and worst < 8:
            cstat["closure_has_old"] += 1
        elif has_none:
            cstat["closure_has_nopragma"] += 1
        else:
            cstat["closure_clean"] += 1
            clean_files.append(r)
            clean_funcs += files[r]["funcs"]
            clean_by_proj[files[r]["project"]] += 1

    cls_count = Counter(v["class"] for v in proj_stats.values())
    summary = {
        "projects": len(by_project),
        "files": len(files),
        "project_classes": dict(cls_count),
        "candidate_files_ge08_nonvendor_withfuncs": len(cand),
        "candidate_closure": dict(cstat),
        "clean_closure_files": len(clean_files),
        "clean_closure_functions_approx": clean_funcs,
        "clean_closure_projects": len(clean_by_proj),
        "cross_project_imports_blocked": sum(cross_blocked.values()),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n-- class 별 프로젝트 예시 --")
    for c in ("A_all_ge08", "A2_ge08_plus_nopragma", "B_old_only_vendor", "C_mixed", "D_no_ge08"):
        ex = [p for p, v in proj_stats.items() if v["class"] == c][:3]
        print(f"  {c:24s} {cls_count.get(c,0):4d}  예: {ex}")
    print("\n-- clean closure 후보가 많은 프로젝트 top 15 --")
    for p, n in clean_by_proj.most_common(15):
        print(f"  {n:4d} files  {p}  (class={proj_stats[p]['class']})")
    json.dump({"summary": summary, "projects": proj_stats,
               "clean_closure_files": sorted(clean_files),
               "clean_by_project": clean_by_proj.most_common()},
              open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
