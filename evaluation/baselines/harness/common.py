"""S0 하네스 공용 모듈: 경로, 케이스 로드, storage 경로 → 슬롯, SolDB 구동·파서, anvil, JSON-RPC.

원칙 (EXPERIMENT_PLAN §0.2): 입력 소스 디렉터리는 읽기만 하고, 모든 생성물은 harness/work/<case>/<workflow>/ 에 만든다.
타이머는 이 프로세스 안의 perf_counter 다 (부록 E).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(HERE, ".."))           # evaluation/baselines
EVAL = os.path.normpath(os.path.join(BASE, ".."))           # evaluation
BIN = os.path.join(BASE, "forge", "bin")
FORGE, CAST, ANVIL = (os.path.join(BIN, f"{n}.exe") for n in ("forge", "cast", "anvil"))
SOLC_DIR = os.path.join(EVAL, "solc")
FORGE_STD = os.path.join(BASE, "playground", "forge", "lib", "forge-std", "src")
WORK = os.path.join(HERE, "work")
RESULTS = os.path.join(HERE, "results")


def posix(p):
    return p.replace("\\", "/")


def solc_path(ver):
    return posix(os.path.join(SOLC_DIR, f"solc-{ver}.exe"))


def load_case(path):
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    c["_src_dir"] = os.path.join(BASE, c["source_dir"])
    return c


def fresh_dir(*parts):
    d = os.path.join(WORK, *parts)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def copy_sources(case, dst_src):
    """입력 소스 디렉터리를 작업 사본으로 복사 (원본은 건드리지 않는다)."""
    shutil.copytree(case["_src_dir"], dst_src, dirs_exist_ok=True)


def run(cmd, cwd=None, stdin=None, timeout=300, env=None):
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, timeout=timeout, env=env)
    sec = time.perf_counter() - t0
    out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    return r.returncode, out, sec


def write_foundry_toml(d, case, with_std=False):
    """solc 고정(타깃 버전 바이너리), optimizer off, bytecodeHash none (§0.2-3)."""
    rm = [f'"forge-std/={posix(FORGE_STD)}/"'] if with_std else []
    allow = [f'"{posix(os.path.dirname(FORGE_STD))}"'] if with_std else []
    toml = (
        "[profile.default]\nsrc = \"src\"\ntest = \"test\"\nout = \"out\"\nlibs = []\n"
        f"solc = \"{solc_path(case['solc'])}\"\noptimizer = false\nbytecode_hash = \"none\"\n"
        f"auto_detect_remappings = false\nremappings = [{', '.join(rm)}]\nallow_paths = [{', '.join(allow)}]\n"
    )
    with open(os.path.join(d, "foundry.toml"), "w", encoding="utf-8") as f:
        f.write(toml)


HARNESS_DIR = "__harness__"


def harness_source(case):
    """접근 규칙 §0.4-2: internal 타깃 → 상속 하네스의 external 노출 함수. 원본 파일은 바꾸지 않고 별도 파일로 둔다."""
    fn = case["function"].split("(")[0]
    ptypes = [p["type"] for p in case["params"]]
    pdecl = ", ".join(f"{t} p{i}" for i, t in enumerate(ptypes))
    pargs = ", ".join(f"p{i}" for i in range(len(ptypes)))
    rets = [t for t in case["returns"].strip("()").split(",") if t.strip()]
    rdecl = f" returns ({', '.join(rets)})" if rets else ""
    mut = {"view": " view", "pure": " pure", "payable": " payable"}.get(case.get("mutability"), "")
    body = f"return {fn}({pargs});" if rets else f"{fn}({pargs});"
    name = f"{case['contract']}Harness"
    src = (f"// 생성물 (harness/common.py). 타깃 {case['contract']}.{case['function']} 노출용 상속 하네스.\n"
           f"pragma solidity ^0.8.0;\nimport {{{case['contract']}}} from \"../{case['file']}\";\n\n"
           f"contract {name} is {case['contract']} {{\n"
           f"    function exposed_{fn}({pdecl}) external{mut}{rdecl} {{ {body} }}\n}}\n")
    return name, f"exposed_{fn}", src


def place_target(case, src_root):
    """작업 사본에 하네스를 더하고, (배포할 계약 이름, 부를 함수 이름, 하네스 파일 상대경로) 를 돌려준다."""
    fn = case["function"].split("(")[0]
    if case.get("access", "direct") == "direct":
        return case["contract"], fn, None
    if case["access"] != "internal":
        raise NotImplementedError(f"access {case['access']} not in S0")
    name, call, src = harness_source(case)
    rel = f"{HARNESS_DIR}/{name}.sol"
    os.makedirs(os.path.join(src_root, HARNESS_DIR), exist_ok=True)
    with open(os.path.join(src_root, rel), "w", encoding="utf-8") as f:
        f.write(src)
    return name, call, rel


def prepare_base(case):
    """작업 사본 + 빌드 + storage layout + 상태 경로별 슬롯 + getter 유무. 세 워크플로가 같은 슬롯 값을 쓴다."""
    d = fresh_dir(case["case_id"], "base")
    copy_sources(case, os.path.join(d, "src"))
    deploy, call, hfile = place_target(case, os.path.join(d, "src"))
    write_foundry_toml(d, case)
    rc, out, sec = run([FORGE, "build", "--offline"], cwd=d)
    if rc != 0:
        raise RuntimeError("base build failed:\n" + out)
    layout = storage_layout(d, case["contract"])
    rc, abi_out, _ = run([FORGE, "inspect", case["contract"], "abi", "--json"], cwd=d)
    abi = json.loads(abi_out[abi_out.index("["):])
    slots = []
    for s in case["state"]:
        slot, off, typ = resolve_slot(layout, s["path"], case["sender"])
        if off != 0:
            raise NotImplementedError("packed slot not supported in S0")
        name = re.match(r"\w+", s["path"]).group(0)
        depth = s["path"].count("[")
        getter = next((f for f in abi if f.get("type") == "function" and f["name"] == name
                       and len(f["inputs"]) == depth), None)
        sig = f"{name}({','.join(i['type'] for i in getter['inputs'])})" if getter else None
        keys = re.findall(r"\[(\w+)\]", s["path"])
        keys = [case["sender"] if k == "sender" else k for k in keys]
        slots.append({"path": s["path"], "slot": slot, "value": s["value"], "getter_sig": sig, "keys": keys,
                      "key_types": [i["type"] for i in getter["inputs"]] if getter else []})
    return {"dir": d, "layout": layout, "slots": slots, "build_sec": sec,
            "deploy": deploy, "call": call, "harness_file": hfile}


# ---------------------------------------------------------------- storage layout → slot

def storage_layout(project_dir, contract):
    rc, out, _ = run([FORGE, "inspect", contract, "storageLayout", "--json"], cwd=project_dir)
    if rc != 0:
        raise RuntimeError("forge inspect failed:\n" + out)
    return json.loads(out[out.index("{"):])


def resolve_slot(layout, path, sender):
    """'name' 또는 'name[key]'(1단 mapping) → (slot hex, offset, type label). S0 범위만 지원."""
    m = re.fullmatch(r"(\w+)(?:\[(\w+)\])?", path)
    if not m:
        raise NotImplementedError(f"path not supported in S0: {path}")
    name, key = m.groups()
    ent = next((e for e in layout["storage"] if e["label"] == name), None)
    if ent is None:
        raise KeyError(f"state variable not found: {name}")
    base = int(ent["slot"])
    if key is None:
        return hex(base), ent["offset"], ent["type"]
    key = sender if key == "sender" else key
    ktype = layout["types"][ent["type"]]["key"]
    kind = "address" if "address" in ktype else "uint256"
    rc, out, _ = run([CAST, "index", kind, key, str(base)])
    return out.strip().splitlines()[-1], 0, layout["types"][ent["type"]]["value"]


def u256_hex(v):
    return "0x" + format(int(v), "064x")


# ---------------------------------------------------------------- SolDB

def soldb_commands(case):
    """관찰 지점마다 break, 반복 수만큼 continue+vars. 마지막에 bt, quit."""
    cmds = []
    src = case["file"]
    for p in case["observe"]["points"]:
        cmds.append(f"break src/{src}:{p['line']}")
    n_hits = sum(len(p["iterations"]) for p in case["observe"]["points"])
    for _ in range(n_hits):
        cmds += ["continue", "vars"]
    cmds += ["bt", "quit"]
    return ("\n".join(cmds) + "\n").encode("utf-8")   # bytes: PowerShell BOM 문제 회피 (부록 C)


HIT = re.compile(r"Breakpoint #\d+ hit at step (\d+), ([^\s:]+):(\d+)")
VAR = re.compile(r"^\s*(?:\(soldb\)\s*)?([\w\[\]() =>.,]+?)\s+(\w+) = (.*?) \[(stack[^\]]*|slot[^\]]*|memory[^\]]*|calldata[^\]]*)\]\s*$")
STEPS = re.compile(r"Loaded trace with (\d+) steps")


def parse_soldb(out):
    """디버거 출력 → {'steps': N, 'hits': [{'step', 'file', 'line', 'vars': {name: value}}], 'warn_inferred': bool}."""
    res = {"steps": None, "hits": [], "warn_inferred": "inferred" in out}
    m = STEPS.search(out)
    if m:
        res["steps"] = int(m.group(1))
    cur = None
    for line in out.splitlines():
        h = HIT.search(line)
        if h:
            cur = {"step": int(h.group(1)), "file": h.group(2), "line": int(h.group(3)), "vars": {}}
            res["hits"].append(cur)
            continue
        v = VAR.match(line)
        if v and cur is not None:
            name, val = v.group(2), v.group(3)
            if name not in cur["vars"]:        # 같은 hit 안에서 첫 표시만
                cur["vars"][name] = val
    return res


def to_int(s):
    try:
        return int(s, 0)
    except (TypeError, ValueError):
        return s


# ---------------------------------------------------------------- anvil / RPC

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Anvil:
    def __init__(self, port=None):
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.p = subprocess.Popen([ANVIL, "--port", str(self.port), "--silent"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                rpc(self.url, "eth_chainId", [])
                return self
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("anvil did not start")

    def __exit__(self, *a):
        self.p.kill()
        self.p.wait()


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    if "error" in d:
        raise RuntimeError(f"{method}: {d['error']}")
    return d["result"]


ANVIL_KEY0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # anvil·Hardhat 공통 0번 테스트 계정
