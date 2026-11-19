# -*- coding: utf-8 -*-
"""compile_survey 결과에서 컴파일 실패 타깃만 다른 설정으로 재시도한다(원인 확인용).
설정: optimizer(runs 200) → viaIR+optimizer, 설치된 solc 를 낮은 버전부터 시도해 처음 성공한 버전을 출력.
원본(정규화 전) 소스로 시도한다. 2026-11-17 결과: Compound cToken = optimizer 0.8.6 성공,
eden governance = optimizer 0.8.0 / viaIR 0.8.12 성공, token_audit = 모든 버전 실패(ERC20 누락).
사용: .venv/Scripts/python.exe evaluation/Dataset/compile_retry.py evaluation/Dataset/compile_survey_486.json
"""
import json, os, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compile_survey as cs  # noqa: E402
from normalize_source import read_source  # noqa: E402

d = json.load(open(sys.argv[1], encoding="utf-8"))
idx = cs.vn.file_index()
for r in d:
    if r["status"] == "ok":
        continue
    cl = cs.vn.load_closure(r["target"], idx)
    raw = {k: read_source(os.path.join(cs.vn.ROOT, v.replace("/", os.sep))) for k, v in cl.items()}
    print(r["target"][:80], "solc", r["solc"])
    for label, settings in [("optimizer", {"optimizer": {"enabled": True, "runs": 200}}),
                            ("viaIR+opt", {"optimizer": {"enabled": True, "runs": 200}, "viaIR": True})]:
        errs = []
        for ver in sorted(cs.INSTALLED):
            req = {"language": "Solidity", "sources": {k: {"content": v} for k, v in raw.items()},
                   "settings": dict(settings, outputSelection={"*": {"*": ["evm.bytecode.object"]}})}
            p = subprocess.run([cs.INSTALLED[ver], "--standard-json"], input=json.dumps(req).encode(),
                               capture_output=True, timeout=600)
            out = json.loads(p.stdout.decode("utf-8", "replace"))
            errs = [e for e in out.get("errors", []) if e.get("severity") == "error"]
            if not errs:
                print("   ok with", label, ver)
                break
        else:
            print("   ", label, "fails all versions:", errs[0]["message"][:100] if errs else "?")
