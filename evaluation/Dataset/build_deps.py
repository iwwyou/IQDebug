# -*- coding: utf-8 -*-
"""
DAppSCAN 타깃별 의존성 프로젝트 빌드 (2026-11-06, 사용자 결정).

"프로젝트" 를 감사 보고서 폴더가 아니라 **타깃 하나의 import 폐포**로 둔다. 감사 폴더에는 다른 커밋, 독립
하위 패키지, 서로 import 하지 않는 파일이 섞여 있어 같은 이름이 여러 번 나오지만, 타깃 하나의 폐포는 solc 의
컴파일 단위와 같아서 이름이 유일하다(선정 타깃 551 전수 확인).

  1. 선정 타깃에서 **같은 파일**(주석·공백을 뺀 내용이 같은 것)을 하나로 줄인다 → targets_final.json
  2. 타깃마다 import 를 따라 폐포를 구한다. 상대 경로는 그대로, 비상대 경로는 **같은 감사 폴더 안**에서 찾는다.
  3. 폐포 파일을 정규화해 deps/<타깃 id>/sources/ 에 쓰고 `Dependencies.registry.build_root` 로 빌드한다.
  4. deps/targets.json 에 타깃 → (deps 디렉터리, 단위 id, exclude, 미해소 import) 를 적는다.

사용:
  .venv/Scripts/python.exe evaluation/Dataset/build_deps.py --dedup-only
  .venv/Scripts/python.exe evaluation/Dataset/build_deps.py --jobs 6
  .venv/Scripts/python.exe evaluation/Dataset/build_deps.py --only QuickSelect
"""
import argparse
import collections
import concurrent.futures as cf
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import time
import traceback

DS = pathlib.Path(__file__).resolve().parent
REPO = DS.parents[1]
for p in (str(REPO), str(DS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from normalize_source import blank_comments, normalize, read_source  # noqa: E402

SCAN = REPO / "evaluation" / "DAppSCAN"
SRC_ROOT = SCAN / "repo" / "DAppSCAN-source" / "contracts"
DEPS = DS / "deps"
FINAL = DS / "targets_final.json"
IMPORT = re.compile(r'\bimport\b[^;"\']*["\']([^"\']+)["\'][^;]*;', re.S)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def full(rel: str) -> str:
    return os.path.join(str(SRC_ROOT), rel.replace("/", os.sep))


def read(rel: str):
    try:
        return read_source(full(rel))
    except OSError:
        return None


def content_key(src: str) -> str:
    code = blank_comments(src.replace("\r\n", "\n").replace("\r", "\n"))[0]
    return hashlib.sha256(re.sub(r"\s+", "", code).encode("utf-8", "replace")).hexdigest()


def tid_of(rel: str) -> str:
    stem = _SAFE.sub("_", os.path.splitext(os.path.basename(rel))[0])[:40]
    return "%s_%s" % (stem, hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8])


def build_index() -> dict:
    """{소문자 basename: [상대 경로]}"""
    idx = collections.defaultdict(list)
    for d, _, fs in os.walk(str(SRC_ROOT)):
        for f in fs:
            if f.endswith(".sol"):
                idx[f.lower()].append(os.path.relpath(os.path.join(d, f), str(SRC_ROOT)).replace("\\", "/"))
    return idx


def resolve(imp: str, frm: str, idx: dict):
    if imp.startswith("."):
        c = os.path.normpath(os.path.join(os.path.dirname(frm), imp)).replace("\\", "/")
        return c if read(c) is not None else None
    proj = frm.split("/")[0]
    key = imp.lstrip("/").lower()
    hits = [h for h in idx.get(key.rsplit("/", 1)[-1], []) if h.split("/")[0] == proj]
    exact = sorted((h for h in hits if h.lower().endswith("/" + key)), key=len)
    if exact:
        return exact[0]
    return hits[0] if len(hits) == 1 else None


def closure(target: str, idx: dict):
    """(폐포 상대 경로 집합, 미해소 import 목록)"""
    seen, todo, missing = {target}, [target], []
    while todo:
        cur = todo.pop()
        src = read(cur)
        if src is None:
            missing.append(cur + " (읽기 실패)")
            continue
        for imp in IMPORT.findall(blank_comments(src)[0]):
            hit = resolve(imp, cur, idx)
            if hit is None:
                missing.append(imp)
            elif hit not in seen:
                seen.add(hit)
                todo.append(hit)
    return seen, sorted(set(missing))


def select_final(idx: dict) -> dict:
    """선정 타깃에서 같은 파일을 하나로 줄인다. 경로 정렬 순서로 첫 파일을 남긴다."""
    with open(SCAN / "targets_v5.json", encoding="utf-8") as f:
        sel = sorted(json.load(f)["selected_option2prime_minus_suspected"])
    groups = collections.defaultdict(list)
    for rel in sel:
        groups[content_key(read(rel))].append(rel)
    kept, dups, closure_differs = [], {}, 0
    for paths in groups.values():
        kept.append(paths[0])
        if len(paths) > 1:
            dups[paths[0]] = paths[1:]
            prints = set()
            for p in paths:
                cl, _ = closure(p, idx)
                prints.add(tuple(sorted(content_key(read(c)) for c in cl if read(c) is not None)))
            closure_differs += len(prints) > 1
    return {"source": "targets_v5.json:selected_option2prime_minus_suspected",
            "rule": "주석·공백을 뺀 내용이 같으면 같은 파일. 경로 정렬 순 첫 파일을 남긴다.",
            "selected": len(sel), "kept": len(kept), "dropped": len(sel) - len(kept),
            "duplicate_groups": len(dups), "groups_with_different_closure": closure_differs,
            "targets": sorted(kept), "duplicates": dups}


def build_one(args):
    rel, members, missing = args
    from Dependencies import registry as reg

    tid = tid_of(rel)
    root = DEPS / tid
    if root.exists():
        shutil.rmtree(root)
    (root / "sources").mkdir(parents=True)

    uid_of, origins = {}, {}
    for f in sorted(members):
        stem = _SAFE.sub("_", os.path.splitext(os.path.basename(f))[0])[:60]
        uid = stem if stem not in origins else "%s_%s" % (stem, hashlib.sha1(f.encode("utf-8")).hexdigest()[:6])
        uid_of[f], origins[uid] = uid, f
        (root / "sources" / (uid + ".sol")).write_text(normalize(read(f)).text, encoding="utf-8")

    text = normalize(read(rel)).text
    info = {"deps": tid, "unit": uid_of[rel], "units": len(members), "unresolved_imports": missing,
            "exclude": sorted({n for _, n in reg.scan_unit(text)["decls"]})}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            reg.build_root(root, origins=origins)
        _, man = reg.current_build(root)
        info["status"] = dict(collections.Counter(m.get("status", "?") for m in man["units"].values()))
        info["not_complete"] = {u: (m.get("errors") or list(m.get("unresolved") or []) or m.get("collision") or [""])[0]
                                for u, m in man["units"].items() if m.get("status") != "Complete"}
    except Exception as e:                                                       # noqa: BLE001
        info["build_error"] = "%s: %s" % (type(e).__name__, str(e)[:240])
        info["traceback"] = traceback.format_exc(limit=3)
    return rel, info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="타깃 경로에 이 문자열이 들어간 것만")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--dedup-only", action="store_true")
    a = ap.parse_args()

    idx = build_index()
    final = select_final(idx)
    FINAL.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    print("타깃 %d → %d (같은 파일 %d 제거, %d 그룹, 폐포가 다른 그룹 %d)"
          % (final["selected"], final["kept"], final["dropped"], final["duplicate_groups"],
             final["groups_with_different_closure"]), flush=True)
    if a.dedup_only:
        return 0

    targets = [t for t in final["targets"] if not a.only or a.only.lower() in t.lower()]
    work = []
    for t in targets:
        members, missing = closure(t, idx)
        work.append((t, sorted(m for m in members if read(m) is not None), missing))
    print("폐포 크기 합 %d, 미해소 import 가 있는 타깃 %d" % (sum(len(w[1]) for w in work),
                                                 sum(bool(w[2]) for w in work)), flush=True)

    DEPS.mkdir(exist_ok=True)
    report_path = DEPS / "targets.json"
    done = {}
    if a.only and report_path.exists():
        done = json.loads(report_path.read_text(encoding="utf-8")).get("targets", {})
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for n, (rel, info) in enumerate(ex.map(build_one, work), 1):
            done[rel] = info
            if n % 50 == 0 or n == len(work):
                print("  %d/%d (%.0fs)" % (n, len(work), time.time() - t0), flush=True)
    report_path.write_text(json.dumps({"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "targets": done},
                                      ensure_ascii=False, indent=1), encoding="utf-8")

    st = collections.Counter()
    for i in done.values():
        st["빌드 예외" if "build_error" in i else "폐포 전부 Complete" if not i.get("not_complete") else "미완 단위 있음"] += 1
        st["미해소 import 있음"] += bool(i["unresolved_imports"])
    print(dict(st), "manifest:", report_path)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
