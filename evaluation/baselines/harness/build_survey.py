"""415 타깃 전체의 forge · Hardhat 빌드 조사 (EXPERIMENT_PLAN §0.2, §0.6-5).

- 입력: evaluation/Dataset/norm_sources/<id>/ (읽기 전용). 타깃마다 harness/work/<id>/ 로 복사한 뒤 그 사본에서만 빌드한다.
- 복사 직후 manifest.json 의 sha256 과 대조해 출발점이 같음을 기록한다.
- solc 는 manifest 의 타깃별 버전, evaluation/solc/solc-X.Y.Z.exe 를 두 도구가 같은 바이너리로 쓴다.
  설정: optimizer off, bytecodeHash none.
- 출력: harness/results/build_survey.jsonl (타깃당 한 줄), 끝에 요약 출력.

사용:
  .venv/Scripts/python.exe evaluation/baselines/harness/build_survey.py [--tools forge,hardhat] [--only ID,...] [--limit N] [--jobs N]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, "..", ".."))
NORM = os.path.join(EVAL, "Dataset", "norm_sources")
SOLC = os.path.join(EVAL, "solc")
FORGE = os.path.join(EVAL, "baselines", "forge", "bin", "forge.exe")
HH_PROJECT = os.path.join(EVAL, "baselines", "playground", "hardhat")  # node_modules 출처 (hardhat 3.21.1)
WORK = os.path.join(HERE, "work")
RESULTS = os.path.join(HERE, "results")


def posix(p):
    return p.replace("\\", "/")


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def prepare(t):
    """norm_sources/<id> → work/<id>/src 복사, sha256 대조."""
    d = os.path.join(WORK, t["id"])
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)  # 다른 프로세스가 폴더를 잡고 있어도 멈추지 않는다
    shutil.rmtree(os.path.join(d, "src"), ignore_errors=True)
    shutil.copytree(os.path.join(NORM, t["id"]), os.path.join(d, "src"), dirs_exist_ok=True)
    bad = [f for f, h in t["sha256"].items() if sha256(os.path.join(d, "src", f)) != h]
    remaps = []
    rp = os.path.join(d, "src", "remappings.txt")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            remaps = [l.strip() for l in f if l.strip()]
    return d, bad, remaps


def forge_build(t, d, remaps):
    solc = posix(os.path.join(SOLC, f"solc-{t['solc']}.exe"))
    rm = ",".join(json.dumps(r.split("=", 1)[0] + "=src/" + r.split("=", 1)[1]) for r in remaps)
    toml = (
        "[profile.default]\nsrc = \"src\"\nout = \"out-forge\"\ncache_path = \"cache-forge\"\nlibs = []\n"
        f"solc = \"{solc}\"\noptimizer = false\nbytecode_hash = \"none\"\n"
        f"auto_detect_remappings = false\nremappings = [{rm}]\n"
    )
    with open(os.path.join(d, "foundry.toml"), "w", encoding="utf-8") as f:
        f.write(toml)
    t0 = time.perf_counter()
    r = subprocess.run([FORGE, "build", "--offline"], cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    sec = time.perf_counter() - t0
    out = (r.stdout or "") + (r.stderr or "")
    with open(os.path.join(d, "forge.log"), "w", encoding="utf-8") as f:
        f.write(out)
    err = next((l.strip() for l in out.splitlines() if "Error" in l or "error" in l), None)
    # 사용된 solc 버전 확인 (artifact metadata)
    ver = None
    outdir = os.path.join(d, "out-forge")
    if r.returncode == 0 and os.path.isdir(outdir):
        for dp, _, fs in os.walk(outdir):
            for fn in fs:
                if fn.endswith(".json") and dp != os.path.join(outdir, "build-info"):
                    try:
                        with open(os.path.join(dp, fn), encoding="utf-8") as f:
                            m = json.load(f).get("metadata", {})
                        ver = m.get("compiler", {}).get("version")
                    except Exception:
                        pass
                    if ver:
                        break
            if ver:
                break
    return {"ok": r.returncode == 0, "sec": round(sec, 2), "error": None if r.returncode == 0 else err,
            "solc_used": ver}


def hardhat_build(t, d, remaps):
    solc = posix(os.path.join(SOLC, f"solc-{t['solc']}.exe"))
    cfg = (
        'import { defineConfig } from "hardhat/config";\n'
        "export default defineConfig({\n"
        f'  paths: {{ sources: "src", artifacts: "out-hardhat", cache: "cache-hardhat" }},\n'
        f'  solidity: {{ version: "{t["solc"]}", path: "{solc}",\n'
        "    settings: { optimizer: { enabled: false }, metadata: { bytecodeHash: \"none\" } } },\n"
        "});\n"
    )
    with open(os.path.join(d, "hardhat.config.mjs"), "w", encoding="utf-8") as f:
        f.write(cfg)
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
        f.write('{"name":"t","private":true,"type":"module"}\n')
    if remaps:  # Hardhat 3 는 프로젝트 루트의 remappings.txt 를 읽는다
        with open(os.path.join(d, "remappings.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(r.split("=", 1)[0] + "=src/" + r.split("=", 1)[1] for r in remaps) + "\n")
    cli = os.path.join(WORK, "node_modules", "hardhat", "dist", "src", "cli.js")
    t0 = time.perf_counter()
    r = subprocess.run(["node", cli, "build", "--config", "hardhat.config.mjs"], cwd=d, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    sec = time.perf_counter() - t0
    out = (r.stdout or "") + (r.stderr or "")
    with open(os.path.join(d, "hardhat.log"), "w", encoding="utf-8") as f:
        f.write(out)
    err = next((l.strip() for l in out.splitlines() if "Error" in l or "error" in l), None)
    wasm = "wasm" in out.lower()
    return {"ok": r.returncode == 0, "sec": round(sec, 2), "error": None if r.returncode == 0 else err,
            "wasm_mentioned": wasm}


def ensure_node_modules():
    """work/node_modules → playground/hardhat/node_modules 디렉터리 정션 (관리자 권한 불필요)."""
    link = os.path.join(WORK, "node_modules")
    if not os.path.exists(link):
        subprocess.run(["cmd", "/c", "mklink", "/J", link, os.path.join(HH_PROJECT, "node_modules")],
                       check=True, capture_output=True)


def run_one(t, tools):
    rec = {"id": t["id"], "solc": t["solc"], "entry": t["entry"], "n_files": len(t["sha256"])}
    d, bad, remaps = prepare(t)
    rec["sha_mismatch"] = bad
    rec["remappings"] = bool(remaps)
    if "forge" in tools:
        rec["forge"] = forge_build(t, d, remaps)
    if "hardhat" in tools:
        rec["hardhat"] = hardhat_build(t, d, remaps)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default="forge,hardhat")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(RESULTS, "build_survey.jsonl"))
    a = ap.parse_args()
    tools = set(a.tools.split(","))

    with open(os.path.join(EVAL, "Dataset", "target_functions.json"), encoding="utf-8") as f:
        sel = {x["target"] for x in json.load(f)["files"] if x["stage"] == "selected"}
    with open(os.path.join(NORM, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)["targets"]
    targets = [v for k, v in sorted(man.items()) if k in sel]
    assert len(targets) == len(sel), (len(targets), len(sel))
    if a.only:
        ids = set(a.only.split(","))
        targets = [t for t in targets if t["id"] in ids]
    if a.limit:
        targets = targets[: a.limit]

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)
    if "hardhat" in tools:
        ensure_node_modules()

    t0 = time.perf_counter()
    recs = []
    with open(a.out, "w", encoding="utf-8") as fo, ThreadPoolExecutor(a.jobs) as ex:
        for rec in ex.map(lambda t: run_one(t, tools), targets):
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fo.flush()
            recs.append(rec)
            print(rec["id"], {k: rec[k]["ok"] for k in ("forge", "hardhat") if k in rec}, flush=True)

    print(f"\n== {len(recs)} targets, {time.perf_counter() - t0:.0f}s ==")
    print("sha256 mismatch:", sum(bool(r["sha_mismatch"]) for r in recs))
    for k in ("forge", "hardhat"):
        rs = [r[k] for r in recs if k in r]
        if rs:
            print(f"{k}: ok {sum(x['ok'] for x in rs)}/{len(rs)}")
            for r in recs:
                if k in r and not r[k]["ok"]:
                    print(f"  FAIL {r['id']}: {r[k]['error']}")
    fv = [r for r in recs if "forge" in r and r["forge"]["ok"] and r["forge"]["solc_used"]
          and not r["forge"]["solc_used"].startswith(r["solc"] + "+")]
    print("forge solc version mismatch:", len(fv), [(r["id"], r["forge"]["solc_used"]) for r in fv[:5]])


if __name__ == "__main__":
    main()
