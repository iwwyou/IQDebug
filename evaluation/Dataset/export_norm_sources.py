# -*- coding: utf-8 -*-
"""동결 데이터셋(415 타깃)의 정규화 소스를 **세 도구(IQDebug · forge · Hardhat)가 같이 쓰는 트리**로 내보낸다 (2026-11-19).

  입력  evaluation/Dataset/target_functions.json — stage == "selected" 인 파일
  폐포  verify_normalize.load_closure — select_functions F1 · build_deps 와 같은 해석기
  출력  evaluation/Dataset/norm_sources/
          manifest.json          타깃 → 프로젝트 디렉터리 · solc 버전 · 진입 파일 · 소스 이름 → 원천 경로 · 검증 결과
          <타깃 id>/             타깃 하나 = 독립 프로젝트(id 는 deps/ 와 같다)
            <소스 이름>          정규화 소스. 경로 = solc 에 넘긴 소스 이름에서 폐포 공통 디렉터리를 뗀 것
            line_maps.json       {소스 이름: {정규화 줄: 원본 줄}}
            remappings.txt       비상대 import 가 있을 때만(3 타깃: `@openzeppelin/` 이 스냅샷 안 사본을 가리킴)

  배치 규칙
    - 상대 import 만 있는 파일은 원천 상대경로에서 **폐포 공통 디렉터리**를 뗀 경로에 둔다. 상대 위치가 그대로라 import 가 풀린다.
    - 비상대 import(`@openzeppelin/...`)는 solc 소스 이름 그대로 프로젝트 루트 아래에 둔다(base path = 프로젝트 루트).
  검증(타깃마다)
    - 트리에서 다시 읽은 파일로 컴파일 → 원본(원천 경로 이름)으로 컴파일한 것과 creation · deployed 바이트코드가 같은지.
      소스 이름이 바뀌면 외부 library 링크 자리표시자(`__$…$__`)가 달라지므로 그것만 같은 표시로 바꿔 비교한다.
    - solc 버전이 target_functions.json 의 기록과 같은지.

사용: .venv/Scripts/python.exe evaluation/Dataset/export_norm_sources.py [--jobs 6]
"""
import concurrent.futures as cf
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DS)
import compile_survey as cs                      # noqa: E402  pick_solc · compile_
from compile_survey import vn                    # noqa: E402
from normalize_source import normalize, read_source  # noqa: E402
from build_deps import tid_of                    # noqa: E402  deps/ 와 같은 타깃 id

OUT = os.path.join(DS, "norm_sources")
LINK = re.compile(r"__\$[0-9a-f]{34}\$__")


def lp(p):
    p = os.path.abspath(p)
    return p if os.name != "nt" or p.startswith("\\\\?\\") else "\\\\?\\" + p


def write(path, text):
    os.makedirs(lp(os.path.dirname(path)), exist_ok=True)
    with open(lp(path), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def layout(cl):
    """{solc 소스 이름: 원천 경로} → {트리 안 경로: 원천 경로}. 상대 이름들의 공통 디렉터리를 뗀다."""
    rel = [k for k, v in cl.items() if k == v]
    common = posixpath.commonpath([posixpath.dirname(k) for k in rel]) if rel else ""
    out = {}
    for k, v in cl.items():
        out[posixpath.relpath(k, common) if k == v and common else k] = v
    return out


def norm_code(code):
    return {k: tuple(LINK.sub("__$LINK$__", x) for x in v) for k, v in code.items()}


def export_one(args):
    t, rec_solc = args
    idx = export_one.idx
    cl = vn.load_closure(t, idx)
    tid = tid_of(t)
    root = os.path.join(OUT, tid)
    if os.path.exists(lp(root)):
        shutil.rmtree(lp(root))
    tree = layout(cl)
    back = {v: k for k, v in tree.items()}
    raw = {k: read_source(os.path.join(vn.ROOT, v.replace("/", os.sep))) for k, v in cl.items()}
    line_maps, files = {}, {}
    for name, src in tree.items():
        n = normalize(read_source(os.path.join(vn.ROOT, src.replace("/", os.sep))))
        write(os.path.join(root, name.replace("/", os.sep)), n.text)
        line_maps[name] = n.line_map
        files[name] = src
    write(os.path.join(root, "line_maps.json"), json.dumps(line_maps, ensure_ascii=False))
    nonrel = sorted({k.split("/")[0] + "/" for k, v in cl.items() if k != v})
    if nonrel:
        write(os.path.join(root, "remappings.txt"), "".join("%s=%s\n" % (p, p) for p in nonrel))
    # 검증: 트리에서 다시 읽어 컴파일 ↔ 원본 컴파일
    ver, solc = cs.pick_solc(raw)
    from_tree = {name: read_source(os.path.join(root, name.replace("/", os.sep))) for name in tree}
    a, _, ea = cs.compile_(from_tree, solc, False)
    b, _, eb = cs.compile_(raw, solc, False)
    same = None
    if a is not None and b is not None:
        bt = {back[cl[f.rsplit(":", 1)[0]]] + ":" + f.rsplit(":", 1)[1]: v for f, v in b.items()}
        same = norm_code(a) == norm_code(bt)
    sv = "%d.%d.%d" % ver if ver else None
    return t, {"id": tid, "solc": sv, "solc_matches_record": sv == rec_solc,
               "entry": back[t], "files": files, "nonrelative_import_roots": nonrel,
               "tree_compiles": a is not None, "tree_error": ea, "bytecode_same_as_original": same,
               "sha256": {k: hashlib.sha256(from_tree[k].encode("utf-8")).hexdigest() for k in sorted(from_tree)}}


def init(idx):
    export_one.idx = idx


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 6
    tf = json.load(open(os.path.join(DS, "target_functions.json"), encoding="utf-8"))
    sel = sorted((r["target"], r.get("solc")) for r in tf["files"] if r.get("stage") == "selected")
    if os.path.exists(lp(OUT)):
        shutil.rmtree(lp(OUT))
    os.makedirs(OUT)
    idx = vn.file_index()
    init(idx)
    t0, res = time.time(), {}
    with cf.ThreadPoolExecutor(jobs) as ex:
        for i, (t, info) in enumerate(ex.map(export_one, sel), 1):
            res[t] = info
            if i % 50 == 0 or i == len(sel):
                print("  %d/%d (%.0fs)" % (i, len(sel), time.time() - t0), flush=True)
    norm_sha = hashlib.sha256(open(os.path.join(DS, "normalize_source.py"), "rb").read()).hexdigest()
    summary = {"targets": len(res),
               "tree_compiles": sum(v["tree_compiles"] for v in res.values()),
               "bytecode_same_as_original": sum(bool(v["bytecode_same_as_original"]) for v in res.values()),
               "solc_matches_record": sum(v["solc_matches_record"] for v in res.values()),
               "with_remappings": sum(bool(v["nonrelative_import_roots"]) for v in res.values()),
               "files_written": sum(len(v["files"]) for v in res.values())}
    man = {"generated": time.strftime("%Y-%m-%d %H:%M"), "source": "target_functions.json (generated %s)" % tf["summary"].get("generated"),
           "normalize_source_sha256": norm_sha,
           "compile_settings": {"optimizer": {"enabled": False}, "metadata": {"bytecodeHash": "none"}},
           "summary": summary, "targets": res}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    bad = [t for t, v in res.items() if not (v["tree_compiles"] and v["bytecode_same_as_original"] and v["solc_matches_record"])]
    for t in bad:
        print("  BAD", t, res[t]["tree_error"], res[t]["bytecode_same_as_original"], res[t]["solc_matches_record"])


if __name__ == "__main__":
    main()
