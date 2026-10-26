"""DAppSCAN-source/contracts 를 훑어 pragma 버전·중복·벤더 비율을 집계한다.

사용: python -I scan_pragma.py <contracts_root> [out_json]
"""
import hashlib
import json
import os
import re
import sys
from collections import Counter

PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]+);", re.I)
VENDOR = ("node_modules", "@openzeppelin", "openzeppelin", "/lib/", "\\lib\\",
          "uniswap", "chainlink", "safemath")


def min_minor(spec: str):
    """pragma 표현식에서 '허용되는 최소 0.x' 의 x 를 뽑는다. 못 뽑으면 None."""
    best = None
    for m in re.finditer(r"(\d+)\.(\d+)(?:\.(\d+))?", spec):
        major, minor = int(m.group(1)), int(m.group(2))
        if major != 0:
            continue
        if best is None or minor < best:
            best = minor
    return best


def main():
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    files = 0
    projects = set()
    proj_ge08 = set()
    by_minor = Counter()
    nopragma = 0
    hashes = {}
    dup = 0
    vendor_ge08 = 0
    ge08_files = []
    for dirpath, _dirs, names in os.walk(root):
        for nm in names:
            if not nm.endswith(".sol"):
                continue
            p = os.path.join(dirpath, nm)
            rel = os.path.relpath(p, root).replace("\\", "/")
            files += 1
            projects.add(rel.split("/")[0])
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    txt = fh.read()
            except OSError:
                continue
            m = PRAGMA.search(txt)
            if not m:
                nopragma += 1
                continue
            mi = min_minor(m.group(1))
            by_minor[mi if mi is not None else "?"] += 1
            if mi is not None and mi >= 8:
                h = hashlib.sha256(txt.encode("utf-8", "replace")).hexdigest()
                if h in hashes:
                    dup += 1
                else:
                    hashes[h] = rel
                low = rel.lower()
                if any(v in low for v in VENDOR):
                    vendor_ge08 += 1
                proj_ge08.add(rel.split("/")[0])
                ge08_files.append(rel)
    res = {
        "sol_files": files,
        "projects": len(projects),
        "no_pragma": nopragma,
        "by_min_minor": {str(k): v for k, v in sorted(by_minor.items(), key=lambda kv: str(kv[0]))},
        "ge08_files": sum(v for k, v in by_minor.items() if isinstance(k, int) and k >= 8),
        "ge08_unique_by_content": len(hashes),
        "ge08_duplicates": dup,
        "ge08_vendor_path": vendor_ge08,
        "ge08_projects": len(proj_ge08),
    }
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"summary": res, "ge08_unique_paths": sorted(hashes.values())},
                      fh, indent=1, ensure_ascii=False)
        print("wrote", out)


if __name__ == "__main__":
    main()
