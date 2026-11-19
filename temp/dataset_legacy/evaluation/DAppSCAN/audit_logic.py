"""scan_projects.py 의 두 약점 규모 측정:
   (a) 한 파일에 pragma 가 여러 개 있고 최소 버전이 서로 다른 경우 (first-match 사용의 오류)
   (b) `import` 로 시작하는 줄이 있는데 IMPORT 정규식이 경로를 못 뽑는 경우 (여러 줄 import)
   (c) 패키지 경로 import 가 **다른 프로젝트** 파일로 해석된 경우 (cross-project 오염)
사용: python -I audit_logic.py <contracts_root>
"""
import os
import re
import sys
from collections import Counter, defaultdict

PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);", re.I)
IMPORT = re.compile(r"""^\s*import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""", re.M)
IMPORT_LINE = re.compile(r"(?m)^\s*import\b")


def _lp(p):
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


def min_minor(spec):
    best = None
    for m in re.finditer(r"(\d+)\.(\d+)", spec):
        if int(m.group(1)) != 0:
            continue
        v = int(m.group(2))
        best = v if best is None or v < best else best
    return best


def main():
    root = sys.argv[1]
    c = Counter()
    multi_examples, ml_examples = [], []
    files = {}
    by_project = defaultdict(list)
    index_by_suffix = defaultdict(list)
    raw_imports = {}

    for dp, _d, ns in os.walk(root):
        for nm in ns:
            if not nm.endswith(".sol"):
                continue
            p = os.path.join(dp, nm)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                txt = open(_lp(p), encoding="utf-8", errors="replace").read()
            except OSError:
                c["read_fail"] += 1
                continue
            specs = PRAGMA.findall(txt)
            mins = [min_minor(s) for s in specs]
            mins = [m for m in mins if m is not None]
            first = mins[0] if mins else None
            lo = min(mins) if mins else None
            if len(specs) > 1:
                c["multi_pragma_files"] += 1
                if first != lo:
                    c["multi_pragma_disagree"] += 1
                    if (first is not None and first >= 8) and (lo is not None and lo < 8):
                        c["wrongly_counted_as_ge08"] += 1
                        if len(multi_examples) < 5:
                            multi_examples.append((rel, specs[:4], first, lo))
            n_lines = len(IMPORT_LINE.findall(txt))
            n_caught = len(IMPORT.findall(txt))
            if n_lines > n_caught:
                c["files_with_missed_imports"] += 1
                c["missed_import_lines"] += n_lines - n_caught
                if len(ml_examples) < 5:
                    i = IMPORT_LINE.search(txt).start()
                    ml_examples.append((rel, txt[i:i + 120].replace("\n", "\\n")))
            files[rel] = {"minor_first": first, "minor_min": lo}
            raw_imports[rel] = IMPORT.findall(txt)
            by_project[rel.split("/")[0]].append(rel)
            parts = rel.split("/")
            for i in range(len(parts)):
                index_by_suffix["/".join(parts[i:])].append(rel)

    # (c) cross-project 해석
    for rel, specs in raw_imports.items():
        proj = rel.split("/")[0]
        for spec in specs:
            if spec.startswith("."):
                continue
            hits = index_by_suffix.get(spec.lstrip("/"), [])
            if not hits:
                continue
            same = [h for h in hits if h.startswith(proj + "/")]
            if not same:
                c["pkg_import_resolved_cross_project"] += 1
                tgt = hits[0]
                a, b = files[rel]["minor_min"], files[tgt]["minor_min"]
                if a is not None and a >= 8 and b is not None and b < 8:
                    c["cross_project_pulls_old"] += 1

    print("files:", len(files))
    for k, v in c.most_common():
        print(f"  {k:38s} {v}")
    print("\n-- multi-pragma 불일치 예시 (first>=8 인데 실제 최소<8) --")
    for e in multi_examples:
        print("  ", e)
    print("\n-- 놓친 import 줄 예시 --")
    for e in ml_examples:
        print("  ", e)


if __name__ == "__main__":
    main()
