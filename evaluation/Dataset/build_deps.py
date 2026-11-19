# -*- coding: utf-8 -*-
"""
DAppSCAN 타깃별 의존성 프로젝트 빌드 (2026-11-06 사용자 결정, 2026-11-19 동결 데이터셋 기준으로 개정).

"프로젝트" 를 감사 보고서 폴더가 아니라 **타깃 하나의 import 폐포**로 둔다. 감사 폴더에는 다른 커밋, 독립
하위 패키지, 서로 import 하지 않는 파일이 섞여 있어 같은 이름이 여러 번 나오지만, 타깃 하나의 폐포는 solc 의
컴파일 단위와 같아서 이름이 유일하다.

  1. 타깃 = `target_functions.json` 에서 stage == "selected" 인 파일(415). 중복 제거는 `select_functions.py` F2 가 이미 했다.
  2. 폐포 = `verify_normalize.load_closure` — `select_functions.py` F1 과 **같은 해석기**다(컴파일 검사한 폐포와 deps 가 같다).
  3. 폐포 파일을 정규화해 deps/<타깃 id>/sources/ 에 쓰고 `Dependencies.registry.build_root` 로 빌드한다.
  4. deps/targets.json 에 타깃 → (deps 디렉터리, 단위 id, exclude, 미해소 import) 를 적는다.

2026-11-19 이전 판은 targets_v5 의 551 에서 자체 중복 제거로 targets_final.json(486)을 만들고 자체 import 해석기를 썼다
(그 판의 산출물은 temp/dataset_legacy/).

사용:
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

from normalize_source import normalize, read_source  # noqa: E402

SCAN = REPO / "evaluation" / "DAppSCAN"
SRC_ROOT = SCAN / "repo" / "DAppSCAN-source" / "contracts"
DEPS = DS / "deps"
TARGETS = DS / "target_functions.json"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def full(rel: str) -> str:
    return os.path.join(str(SRC_ROOT), rel.replace("/", os.sep))


def read(rel: str):
    try:
        return read_source(full(rel))
    except OSError:
        return None


def tid_of(rel: str) -> str:
    stem = _SAFE.sub("_", os.path.splitext(os.path.basename(rel))[0])[:40]
    return "%s_%s" % (stem, hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8])


def load_targets():
    """동결 데이터셋의 평가 대상 파일과 그 폐포. (생성 시각, [(타깃, 폐포 파일 목록, 미해소)])"""
    import verify_normalize as vn
    data = json.loads(TARGETS.read_text(encoding="utf-8"))
    sel = sorted(r["target"] for r in data["files"] if r.get("stage") == "selected")
    idx = vn.file_index()
    work = []
    for t in sel:
        cl = vn.load_closure(t, idx)
        if cl is None:
            work.append((t, [t], ["(폐포 해석 실패)"]))
        else:
            work.append((t, sorted(m for m in set(cl.values()) if read(m) is not None), []))
    return data["summary"].get("generated"), work


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
        # newline="\n": Windows 기본 변환(\n → \r\n)을 막아 norm_sources/ 트리와 바이트 단위로 같게 쓴다(2026-11-19)
        (root / "sources" / (uid + ".sol")).write_text(normalize(read(f)).text, encoding="utf-8", newline="\n")

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
    a = ap.parse_args()

    generated, work = load_targets()
    if a.only:
        work = [w for w in work if a.only.lower() in w[0].lower()]
    print("타깃 %d (target_functions.json %s), 폐포 크기 합 %d, 폐포 해석 실패 %d"
          % (len(work), generated, sum(len(w[1]) for w in work), sum(bool(w[2]) for w in work)), flush=True)

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
    report_path.write_text(json.dumps({"built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       "source": "target_functions.json (generated %s)" % generated,
                                       "targets": done}, ensure_ascii=False, indent=1), encoding="utf-8")

    st = collections.Counter()
    for i in done.values():
        st["빌드 예외" if "build_error" in i else "폐포 전부 Complete" if not i.get("not_complete") else "미완 단위 있음"] += 1
        st["미해소 import 있음"] += bool(i["unresolved_imports"])
    print(dict(st), "manifest:", report_path)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
