# -*- coding: utf-8 -*-
"""
normalize_source 가 의미를 바꾸지 않는지 검증한다 (2026-11-06).

  --tokens  ≥0.8 전체(3,787): 토큰 열이 삽입한 괄호를 빼면 같은가, 멱등인가, 무중괄호가 남지 않았는가
  --solc    선정 타깃 중 폐포에 삽입이 있는 것: 원본과 정규화본을 같은 solc 로 컴파일해 바이트코드 비교

사용: .venv/Scripts/python.exe evaluation/Dataset/verify_normalize.py --tokens --solc -o report.json
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from normalize_source import _TOK, blank_comments, find_braceless, normalize, read_source  # noqa: E402

EVAL = os.path.dirname(HERE)
SCAN = os.path.join(EVAL, "DAppSCAN")
ROOT = os.path.join(SCAN, "repo", "DAppSCAN-source", "contracts")
IMPORT = re.compile(r'\bimport\b[^;"\']*["\']([^"\']+)["\'][^;]*;', re.S)


def tokens(src):
    return _TOK.findall(blank_comments(src.replace("\r\n", "\n").replace("\r", "\n"))[1])


def tokens_equal(a, b):
    """b 는 a 에 `{` `}` 만 끼워 넣은 것이어야 한다."""
    i = j = 0
    while j < len(b):
        if i < len(a) and a[i] == b[j]:
            i += 1
        elif b[j] not in "{}":
            return False
        j += 1
    return i == len(a)


def check_tokens(paths):
    r = collections.Counter()
    bad = []
    for rel in paths:
        s = read_source(os.path.join(ROOT, rel.replace("/", os.sep)))
        r["파일"] += 1
        try:
            n = normalize(s)
        except (ValueError, IndexError) as e:
            r["정규화 실패"] += 1
            bad.append((rel, "fail: " + str(e)))
            continue
        r["삽입"] += n.braces_inserted
        r["체인 결합"] += n.chains_joined
        r["삽입 파일"] += bool(n.braces_inserted)
        if not tokens_equal(tokens(s), tokens(n.text)):
            r["토큰 불일치"] += 1
            bad.append((rel, "tokens"))
        if normalize(n.text).text != n.text:
            r["멱등 실패"] += 1
            bad.append((rel, "idempotent"))
        if find_braceless(blank_comments(n.text)[1])[0]:
            r["무중괄호 잔존"] += 1
            bad.append((rel, "left"))
        lines = n.text.count("\n") + 1
        if sorted(n.line_map) != list(range(1, lines + 1)) or \
                any(a > b for a, b in zip(list(n.line_map.values()), list(n.line_map.values())[1:])
                    if False):
            r["줄 대응 불완전"] += 1
    return dict(r), bad


def file_index():
    idx = collections.defaultdict(list)
    for d, _, fs in os.walk(ROOT):
        for f in fs:
            if f.endswith(".sol"):
                idx[f.lower()].append(os.path.relpath(os.path.join(d, f), ROOT).replace("\\", "/"))
    return idx


def exists(rel):
    try:
        read_source(os.path.join(ROOT, rel.replace("/", os.sep)))
        return True
    except OSError:
        return False


def load_closure(target, idx):
    """{solc 소스 이름: 실제 상대 경로}. 해석 못 한 import 가 있으면 None."""
    proj = target.split("/")[0]
    out, todo = {}, [(target, target)]
    while todo:
        vname, real = todo.pop()
        if vname in out:
            continue
        out[vname] = real
        masked = blank_comments(read_source(os.path.join(ROOT, real.replace("/", os.sep))))[0]
        for im in IMPORT.findall(masked):
            if im.startswith("."):
                v2 = os.path.normpath(os.path.join(os.path.dirname(vname), im)).replace("\\", "/")
                r2 = os.path.normpath(os.path.join(os.path.dirname(real), im)).replace("\\", "/")
                if not exists(r2):
                    return None
            else:
                key = im.lstrip("/").lower()
                hits = [h for h in idx.get(key.rsplit("/", 1)[-1], []) if h.split("/")[0] == proj]
                exact = [h for h in hits if h.lower().endswith("/" + key)]
                pool = exact or hits
                if len(set(pool)) != 1 and not exact:
                    return None
                v2, r2 = im, sorted(pool, key=len)[0]
            todo.append((v2, r2))
    return out


def solc_versions():
    found = []
    for p in glob.glob(os.path.join(EVAL, "solc", "solc-*.exe")):
        m = re.search(r"solc-(\d+)\.(\d+)\.(\d+)", p)
        if m:
            found.append((tuple(int(x) for x in m.groups()), p))
    return [p for _, p in sorted(found)]


def compile_all(sources, solc):
    req = {"language": "Solidity", "sources": {k: {"content": v} for k, v in sources.items()},
           "settings": {"optimizer": {"enabled": False}, "metadata": {"bytecodeHash": "none"},
                        "outputSelection": {"*": {"*": ["evm.bytecode.object", "evm.deployedBytecode.object"]}}}}
    p = subprocess.run([solc, "--standard-json"], input=json.dumps(req).encode("utf-8"),
                       capture_output=True, timeout=180)
    out = json.loads(p.stdout.decode("utf-8", "replace"))
    errs = [e for e in out.get("errors", []) if e.get("severity") == "error"]
    if errs:
        return None, errs[0].get("formattedMessage", "")[:200]
    code = {}
    for f, cs in out.get("contracts", {}).items():
        for c, v in cs.items():
            code[f + ":" + c] = (v["evm"]["bytecode"]["object"], v["evm"]["deployedBytecode"]["object"])
    return code, None


def check_solc(targets):
    idx = file_index()
    versions = solc_versions()
    r = collections.Counter()
    detail = []
    for t in targets:
        cl = load_closure(t, idx)
        if cl is None:
            r["import 해석 실패(제외)"] += 1
            continue
        raw = {k: read_source(os.path.join(ROOT, v.replace("/", os.sep))) for k, v in cl.items()}
        norm = {k: normalize(v) for k, v in raw.items()}
        ins = sum(n.braces_inserted for n in norm.values())
        if not ins:
            continue
        r["대상"] += 1
        for solc in versions:
            a, err = compile_all(raw, solc)
            if a is not None:
                break
        if a is None:
            r["원본 컴파일 실패(제외)"] += 1
            detail.append({"target": t, "status": "orig-fail", "err": err})
            continue
        b, err = compile_all({k: n.text for k, n in norm.items()}, solc)
        if b is None:
            r["정규화본 컴파일 실패"] += 1
            detail.append({"target": t, "status": "norm-fail", "err": err})
            continue
        same = a == b
        r["바이트코드 동일" if same else "바이트코드 다름"] += 1
        r["비교 컨트랙트"] += len(a)
        detail.append({"target": t, "status": "same" if same else "diff", "inserted": ins,
                       "contracts": len(a), "solc": os.path.basename(solc),
                       "diff": [k for k in a if a[k] != b.get(k)][:5]})
    return dict(r), detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", action="store_true")
    ap.add_argument("--solc", action="store_true")
    ap.add_argument("-o", "--output")
    a = ap.parse_args()
    rep = {}
    if a.tokens:
        with open(os.path.join(SCAN, "dappscan_ge08.json"), encoding="utf-8") as f:
            rep["tokens"], rep["token_problems"] = check_tokens(json.load(f)["ge08_unique_paths"])
        print(rep["tokens"], rep["token_problems"][:8])
    if a.solc:
        with open(os.path.join(SCAN, "targets_v5.json"), encoding="utf-8") as f:
            t = json.load(f)["selected_option2prime_minus_suspected"]
        rep["solc"], rep["solc_detail"] = check_solc(t)
        print(rep["solc"])
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
