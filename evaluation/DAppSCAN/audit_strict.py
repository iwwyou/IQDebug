"""옵션 2 를 두 해석으로 비교한다.

  loose  = 현재 scan_projects.py (패키지 import 를 **다른 프로젝트** 사본으로도 해석)
  strict = 같은 프로젝트 안에서 해석되지 않는 패키지 import 는 **미해결**로 취급

출력: 각 모드의 clean 집합 크기, 교집합, loose 에서만 clean 인 파일(= cross-project 해석에
기댄 판정)과 그 이유.

사용: python -I audit_strict.py <contracts_root> <out_json>
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);", re.I)
IMPORT = re.compile(r"""^\s*import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""", re.M)
FUNC = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(")
VENDOR_PARTS = ("node_modules", "@openzeppelin", "openzeppelin", "uniswap", "chainlink",
                "aave", "@chainlink", "forge-std", "ds-test", "hardhat")


def _lp(p):
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


def min_minor_all(txt):
    """파일 안 **모든** pragma 의 최소 허용 minor (first-match 가 아니라 전체 최소)."""
    best = None
    for spec in PRAGMA.findall(txt):
        for m in re.finditer(r"(\d+)\.(\d+)", spec):
            if int(m.group(1)) != 0:
                continue
            v = int(m.group(2))
            best = v if best is None or v < best else best
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
        b, s = tail.find("{"), tail.find(";")
        if b != -1 and (s == -1 or b < s):
            n += 1
    return n


def main():
    root, out = sys.argv[1], sys.argv[2]
    files, by_project, idx = {}, defaultdict(list), defaultdict(list)
    for dp, _d, ns in os.walk(root):
        for nm in ns:
            if not nm.endswith(".sol"):
                continue
            p = os.path.join(dp, nm)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                txt = open(_lp(p), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            files[rel] = {"minor": min_minor_all(txt), "vendor": is_vendor(rel),
                          "imports": IMPORT.findall(txt), "funcs": func_count(txt),
                          "project": rel.split("/")[0]}
            by_project[rel.split("/")[0]].append(rel)
            parts = rel.split("/")
            for i in range(len(parts)):
                idx["/".join(parts[i:])].append(rel)

    def resolve(src, spec, strict):
        proj = src.split("/")[0]
        if spec.startswith("."):
            cand = os.path.normpath(os.path.join(os.path.dirname(src), spec)).replace("\\", "/")
            return (cand, False) if cand in files else (None, False)
        hits = idx.get(spec.lstrip("/"), [])
        same = [h for h in hits if h.startswith(proj + "/")]
        if same:
            return same[0], False
        if hits and not strict:
            return hits[0], True
        if hits and strict:
            return None, True
        bn = spec.lstrip("/").split("/")[-1]
        cands = [h for h in by_project.get(proj, []) if h.endswith("/" + bn)]
        if len(cands) == 1:
            return cands[0], False
        return None, False

    def build(strict):
        edges, miss, xproj = {}, Counter(), Counter()
        for rel, rec in files.items():
            o = []
            for spec in rec["imports"]:
                r, cross = resolve(rel, spec, strict)
                if cross:
                    xproj[rel] += 1
                if r:
                    o.append(r)
                else:
                    miss[rel] += 1
            edges[rel] = o
        return edges, miss, xproj

    def clean_set(edges, miss, xproj):
        cache = {}

        def closure(rel):
            if rel in cache:
                return cache[rel]
            seen, m, x = {rel}, miss[rel], xproj[rel]
            work = list(edges.get(rel, ()))
            while work:
                cur = work.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                m += miss.get(cur, 0)
                x += xproj.get(cur, 0)
                work.extend(edges.get(cur, ()))
            cache[rel] = (seen, m, x)
            return cache[rel]

        clean, info = set(), {}
        for rel, rec in files.items():
            if rec["minor"] is None or rec["minor"] < 8 or rec["vendor"] or rec["funcs"] == 0:
                continue
            seen, m, x = closure(rel)
            worst = min((files[y]["minor"] for y in seen if files[y]["minor"] is not None),
                        default=None)
            has_none = any(files[y]["minor"] is None for y in seen)
            if m == 0 and worst is not None and worst >= 8 and not has_none:
                clean.add(rel)
                info[rel] = {"closure": len(seen), "cross_edges": x,
                             "funcs": rec["funcs"], "project": rec["project"]}
        return clean, info

    le, lm, lx = build(strict=False)
    se, sm, sx = build(strict=True)
    loose, loose_info = clean_set(le, lm, lx)
    strict, _ = clean_set(se, sm, sx)

    only_loose = sorted(loose - strict)
    both = loose & strict
    dep_on_cross = [r for r in both if loose_info[r]["cross_edges"] > 0]
    res = {
        "loose_clean": len(loose),
        "strict_clean": len(strict),
        "strict_only": len(strict - loose),
        "only_loose (cross-project 해석에 기대어 통과)": len(only_loose),
        "both_but_closure_used_cross_edges": len(dep_on_cross),
        "loose_clean_functions": sum(loose_info[r]["funcs"] for r in loose),
        "strict_clean_functions": sum(loose_info[r]["funcs"] for r in both),
        "loose_clean_projects": len({loose_info[r]["project"] for r in loose}),
        "strict_clean_projects": len({loose_info[r]["project"] for r in both}),
    }
    print(json.dumps(res, indent=2, ensure_ascii=False))
    print("\n-- only_loose 예시 5 --")
    for r in only_loose[:5]:
        print("  ", r, loose_info[r])
    json.dump({"summary": res, "only_loose": only_loose,
               "strict_clean": sorted(strict)}, open(out, "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
