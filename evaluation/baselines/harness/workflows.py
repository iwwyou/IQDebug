"""S0 워크플로 실행기.

  W1   forge test --debug, 상태 주입 = vm.store (하네스가 계산한 슬롯, A_slot_harness)
  W1s  W1 과 같되 getter 가 있는 상태는 stdstore (도구 기능, A_slot_tool), 없는 것만 vm.store
  W2   anvil + forge create + cast call --debug --override-state-diff (상태는 호출 시에만 덮어씀)
  W3   Hardhat 3: W3a = eth_call(staticCall) 반환값, W3b = 트랜잭션 실행 중 console.log

결과 형식: {"workflow", "setup", "termination", "ret0", "post": {path: value} | None, "points": {line: [{var: value}]},
           "steps", "sec", "artifacts", "dir"}
관찰 시점은 phase=before (그 줄이 실행되기 직전). W3b 는 console.log 를 그 줄의 문장 앞(같은 줄)에 넣어 맞춘다.
대상 호출이 revert 해도 디버깅·관찰을 계속하도록 W1 테스트는 try/catch 로 부른다 (revert 는 실패가 아니다, B.1).
"""
import json
import os
import re
import shutil

import common as C


def _calldata(case, base):
    sig = f"{base['call']}({','.join(p['type'] for p in case['params'])})"
    rc, out, _ = C.run([C.CAST, "calldata", sig, *[str(p["value"]) for p in case["params"]]])
    return out.strip().splitlines()[-1]


def _points_from_hits(case, hits):
    pts = {}
    for p in case["observe"]["points"]:
        hs = [h for h in hits if h["line"] == p["line"] and h["file"].endswith(case["file"])]
        pts[str(p["line"])] = [{v: C.to_int(h["vars"].get(v)) for v in p["vars"]} for h in hs]
    return pts


def _exit_slots(case, base):
    return [s for s in base["slots"] if s["path"] in case["observe"]["exit"]]


def _forge_trace_result(out, call_prefix):
    """-vvvv trace 에서 대상 호출 줄을 찾고, 같은 깊이의 첫 `└─ ← [Return|Revert]` 를 읽는다."""
    lines = out.splitlines()
    for i, l in enumerate(lines):
        j = l.find(call_prefix)
        if j < 0 or "] " not in l[:j]:
            continue
        mk = max(l.rfind("├─", 0, j), l.rfind("└─", 0, j))
        col = mk + 4 if mk >= 0 else 2          # 자식 줄의 └─ 는 호출 줄 표지보다 4 칸 안쪽
        for k in range(i + 1, len(lines)):
            m = re.search(r"└─ ← \[(Return|Revert)\]\s*(.*)", lines[k])
            if m and lines[k].find("└─") == col:
                term = "return" if m.group(1) == "Return" else "revert"
                val = m.group(2).strip()
                return (C.to_int(val.split()[0]) if term == "return" and val else None), term
    return None, None


# ------------------------------------------------------------------ W1 / W1s

def _stdstore_line(s):
    chain = f'stdstore.target(address(c)).sig("{s["getter_sig"]}")'
    for k in s["keys"]:
        chain += f".with_key({k})"
    return f"        {chain}.checked_write(uint256({s['value']}));  // {s['path']} (stdstore)"


def w1(case, base, use_stdstore=False):
    name = "W1s" if use_stdstore else "W1"
    d = C.fresh_dir(case["case_id"], name.lower())
    C.copy_sources(case, os.path.join(d, "src"))
    C.place_target(case, os.path.join(d, "src"))
    os.makedirs(os.path.join(d, "test"))
    C.write_foundry_toml(d, case, with_std=True)
    lines, n_tool, n_harness = [], 0, 0
    for s in base["slots"]:
        if use_stdstore and s["getter_sig"]:
            lines.append(_stdstore_line(s))
            n_tool += 1
        else:
            lines.append(f"        vm.store(address(c), bytes32(uint256({s['slot']})), bytes32(uint256({s['value']})));  // {s['path']}")
            n_harness += 1
    posts = "\n".join(f'        console.log("__POST__|{s["path"]}|%s", uint256(vm.load(address(c), bytes32(uint256({s["slot"]})))));'
                      for s in _exit_slots(case, base))
    args = ", ".join(str(p["value"]) for p in case["params"])
    imp = f'import {{{base["deploy"]}}} from "../src/{base["harness_file"] or case["file"]}";'
    std_imp = 'import {stdStorage, StdStorage} from "forge-std/StdStorage.sol";\n' if use_stdstore else ""
    using = "    using stdStorage for StdStorage;\n" if use_stdstore else ""
    test = f"""// 생성물 (harness/workflows.py {name}).
pragma solidity ^0.8.0;
import {{Test, console}} from "forge-std/Test.sol";
{std_imp}{imp}

contract S0Test is Test {{
{using}    function test_target() public {{
        {base['deploy']} c = new {base['deploy']}();
{chr(10).join(lines)}
        vm.prank({case['sender']});
        try c.{base['call']}({args}) {{}} catch {{}}
{posts}
    }}
}}
"""
    with open(os.path.join(d, "test", "S0.t.sol"), "w", encoding="utf-8") as f:
        f.write(test)
    sec = {}
    rc, out, sec["compile"] = C.run([C.FORGE, "build", "--offline"], cwd=d)
    if rc != 0:
        return {"workflow": name, "setup": "F_COMPILE", "log": out}
    rc_t, out_t, sec["execute"] = C.run([C.FORGE, "test", "--offline", "--match-test", "test_target", "-vvvv"], cwd=d)
    if "[PASS]" not in out_t:
        return {"workflow": name, "setup": "F_STATE", "log": out_t}
    ret0, term = _forge_trace_result(out_t, f"{base['deploy']}::{base['call']}(")
    post = {m.group(1): int(m.group(2)) for m in re.finditer(r"__POST__\|([^|]+)\|(\d+)", out_t)}
    rc_d, out_d, sec["debug_session"] = C.run(
        [C.FORGE, "test", "--offline", "--match-test", "test_target\\(\\)", "--debug"], cwd=d, stdin=C.soldb_commands(case))
    with open(os.path.join(d, "debug.log"), "w", encoding="utf-8") as f:
        f.write(out_d)
    p = C.parse_soldb(out_d)
    return {"workflow": name, "setup": "OK", "termination": term, "ret0": ret0, "post": post,
            "points": _points_from_hits(case, p["hits"]), "steps": p["steps"], "warn_inferred": p["warn_inferred"],
            "sec": {k: round(v, 3) for k, v in sec.items()},
            "artifacts": {"test_file": "test/S0.t.sol", "harness_file": base["harness_file"],
                          "A_slot_tool": n_tool, "A_slot_harness": n_harness}, "dir": d}


# ------------------------------------------------------------------ W2

def w2(case, base, anvil):
    d = C.fresh_dir(case["case_id"], "w2")
    C.copy_sources(case, os.path.join(d, "src"))
    C.place_target(case, os.path.join(d, "src"))
    C.write_foundry_toml(d, case)
    sec = {}
    rc, out, sec["compile"] = C.run([C.FORGE, "build", "--offline"], cwd=d)
    if rc != 0:
        return {"workflow": "W2", "setup": "F_COMPILE", "log": out}
    target = f"src/{base['harness_file'] or case['file']}:{base['deploy']}"
    rc, out, sec["deploy"] = C.run([C.FORGE, "create", target, "--rpc-url", anvil.url,
                                    "--private-key", C.ANVIL_KEY0, "--broadcast"], cwd=d)
    m = re.search(r"Deployed to: (0x[0-9a-fA-F]{40})", out)
    if not m:
        return {"workflow": "W2", "setup": "F_DEPLOY", "log": out}
    addr = m.group(1)
    ov = ",".join(f"{addr}:{s['slot']}:{s['value']}" for s in base["slots"])
    sig = f"{base['call']}({','.join(p['type'] for p in case['params'])})"
    args = [str(p["value"]) for p in case["params"]]
    common = ["--from", case["sender"], "--rpc-url", anvil.url, "--override-state-diff", ov]
    rc_r, out_r, sec["execute"] = C.run([C.CAST, "call", addr, sig + case["returns"], *args, *common], cwd=d)
    term = "return" if rc_r == 0 else "revert"
    ret0 = C.to_int(out_r.strip().split()[0]) if rc_r == 0 and out_r.strip() else None
    rc_d, out_d, sec["debug_session"] = C.run(
        [C.CAST, "call", addr, sig, *args, *common, "--trace", "--debug", "--with-local-artifacts"],
        cwd=d, stdin=C.soldb_commands(case))
    with open(os.path.join(d, "debug.log"), "w", encoding="utf-8") as f:
        f.write(out_d)
    p = C.parse_soldb(out_d)
    # post-state: cast call 은 체인 상태를 바꾸지 않으므로 W2 의 G2 는 tracer 경로(= 구체 기준값 경로)로만 얻는다
    return {"workflow": "W2", "setup": "OK", "termination": term, "ret0": ret0, "post": None,
            "points": _points_from_hits(case, p["hits"]), "steps": p["steps"], "warn_inferred": p["warn_inferred"],
            "sec": {k: round(v, 3) for k, v in sec.items()},
            "artifacts": {"address": addr, "override_state_diff": ov, "n_slots": len(base["slots"]),
                          "harness_file": base["harness_file"]}, "dir": d}


# ------------------------------------------------------------------ W3 (Hardhat 3)

HH_CONFIG = """import hardhatEthers from "@nomicfoundation/hardhat-ethers";
import {{ defineConfig }} from "hardhat/config";
export default defineConfig({{
  plugins: [hardhatEthers],
  solidity: {{ version: "{ver}", path: "{path}",
    settings: {{ optimizer: {{ enabled: false }}, metadata: {{ bytecodeHash: "none" }} }} }},
  networks: {{ sim: {{ type: "edr-simulated", chainType: "l1" }} }},
}});
"""

HH_SCRIPT = """// 생성물 (harness/workflows.py w3). W3a = eth_call(staticCall) 반환값, W3b = 트랜잭션 실행 중 console.log.
import {{ network }} from "hardhat";
const {{ ethers, provider }} = await network.create();
const [me] = await ethers.getSigners();
if (me.address.toLowerCase() !== "{sender}".toLowerCase()) throw new Error("sender mismatch " + me.address);
const c = await ethers.deployContract("{contract}");
await c.waitForDeployment();
const addr = await c.getAddress();
for (const [slot, val] of {slots}) {{
  await provider.request({{ method: "hardhat_setStorageAt", params: [addr, ethers.toBeHex(BigInt(slot), 32), ethers.toBeHex(BigInt(val), 32)] }});
}}
const args = {args};
let ret = null, term = null;
console.log("__W3A_BEGIN__");
try {{ ret = (await c.{fn}.staticCall(...args)).toString(); term = "return"; }} catch (e) {{ term = "revert"; }}
console.log("__W3A_END__");
console.log("__W3B_BEGIN__");
const view = {is_view};
if (view) {{ await c.{fn}.staticCall(...args).catch(() => {{}}); }}
else {{ try {{ await (await c.{fn}(...args, {{ gasLimit: 5000000 }})).wait(); }} catch (e) {{}} }}
console.log("__W3B_END__");
const post = {{}};
for (const [path, slot] of {exit_slots}) {{
  post[path] = BigInt(await provider.request({{ method: "eth_getStorageAt", params: [addr, ethers.toBeHex(BigInt(slot), 32), "latest"] }})).toString();
}}
console.log("__RESULT__" + JSON.stringify({{ ret, term, post }}));
"""


def _instrument(case, text):
    """관찰 줄의 문장 앞(같은 줄)에 console.log, import 는 파일 끝 → 줄 번호 보존. 추가 문장 수를 E_src 로 센다."""
    lines = text.split("\n")
    e_src = 0
    for p in case["observe"]["points"]:
        i = p["line"] - 1
        indent = re.match(r"\s*", lines[i]).group(0)
        logs = " ".join(f'console.log("__OBS__|{p["line"]}|{v}|%s", {v});' for v in p["vars"])
        lines[i] = indent + logs + " " + lines[i].lstrip()
        e_src += len(p["vars"])
    lines.append('import "hardhat/console.sol";')
    return "\n".join(lines), e_src + 1


def w3(case, base):
    d = C.fresh_dir(case["case_id"], "w3")
    csrc = os.path.join(d, "contracts")
    C.copy_sources(case, csrc)
    C.place_target(case, csrc)
    os.makedirs(os.path.join(d, "scripts"))
    with open(os.path.join(case["_src_dir"], case["file"]), encoding="utf-8") as f:
        inst, e_src = _instrument(case, f.read())
    with open(os.path.join(csrc, case["file"]), "w", encoding="utf-8") as f:
        f.write(inst)
    with open(os.path.join(d, "hardhat.config.mjs"), "w", encoding="utf-8") as f:
        f.write(HH_CONFIG.format(ver=case["solc"], path=C.solc_path(case["solc"])))
    with open(os.path.join(d, "package.json"), "w", encoding="utf-8") as f:
        f.write('{"name":"s0","private":true,"type":"module"}\n')
    script = HH_SCRIPT.format(
        sender=case["sender"], contract=base["deploy"], fn=base["call"],
        slots=json.dumps([[s["slot"], str(s["value"])] for s in base["slots"]]),
        exit_slots=json.dumps([[s["path"], s["slot"]] for s in _exit_slots(case, base)]),
        args=json.dumps([str(p["value"]) for p in case["params"]]),
        is_view="true" if case.get("mutability") in ("view", "pure") else "false")
    with open(os.path.join(d, "scripts", "s0.js"), "w", encoding="utf-8") as f:
        f.write(script)
    cli = os.path.join(C.WORK, "node_modules", "hardhat", "dist", "src", "cli.js")
    sec = {}
    rc, out, sec["compile"] = C.run(["node", cli, "build", "--config", "hardhat.config.mjs"], cwd=d)
    if rc != 0:
        return {"workflow": "W3", "setup": "F_COMPILE", "log": out}
    rc, out, sec["execute_script"] = C.run(["node", cli, "run", "scripts/s0.js", "--network", "sim",
                                            "--config", "hardhat.config.mjs"], cwd=d)
    with open(os.path.join(d, "run.log"), "w", encoding="utf-8") as f:
        f.write(out)
    m = re.search(r"__RESULT__(\{.*\})", out)
    if rc != 0 or not m:
        return {"workflow": "W3", "setup": "F_STATE", "log": out}
    res = json.loads(m.group(1))
    w3b = out.split("__W3B_BEGIN__")[1].split("__W3B_END__")[0]
    pts = {}
    for p in case["observe"]["points"]:
        rows = re.findall(rf"__OBS__\|{p['line']}\|(\w+)\|(\S+)", w3b)
        seq, cur = [], {}
        for var, val in rows:              # 같은 줄의 변수들이 한 번의 도달을 이룬다
            if var in cur:
                seq.append(cur)
                cur = {}
            cur[var] = C.to_int(val)
        if cur:
            seq.append(cur)
        pts[str(p["line"])] = seq
    return {"workflow": "W3", "setup": "OK", "termination": res["term"],
            "ret0": C.to_int(res["ret"]) if res["ret"] else None,
            "post": {k: int(v) for k, v in res["post"].items()},
            "points": pts, "steps": None, "sec": {k: round(v, 3) for k, v in sec.items()},
            "artifacts": {"E_src_stmts": e_src, "instrumented": f"contracts/{case['file']}",
                          "harness_file": base["harness_file"]}, "dir": d}


# ------------------------------------------------------------------ 구체 기준값 (부록 C: anvil eth_call + debug_traceCall)

def truth(case, base, anvil, addr):
    data = _calldata(case, base)
    so = {addr: {"stateDiff": {C.u256_hex(int(s["slot"], 16)): C.u256_hex(s["value"]) for s in base["slots"]}}}
    tx = {"from": case["sender"], "to": addr, "data": data}
    out = {}
    try:
        r = C.rpc(anvil.url, "eth_call", [tx, "latest", so])
        out["termination"], out["ret0"] = "return", (int(r, 16) if r not in ("0x", "") else None)
    except RuntimeError as e:
        out["termination"], out["ret0"], out["error"] = "revert", None, str(e)[:200]
    diff = C.rpc(anvil.url, "debug_traceCall", [tx, "latest", {"tracer": "prestateTracer",
                                                               "tracerConfig": {"diffMode": True}, "stateOverrides": so}])
    post_st = diff.get("post", {}).get(addr.lower(), {}).get("storage", {})
    pre_st = diff.get("pre", {}).get(addr.lower(), {}).get("storage", {})
    post = {}
    for s in _exit_slots(case, base):
        k = C.u256_hex(int(s["slot"], 16))
        if out["termination"] == "revert":   # revert: 상태가 되돌아가므로 주입값 그대로 (diff 는 비어야 한다)
            post[s["path"]] = s["value"]
        elif k in post_st:
            post[s["path"]] = int(post_st[k], 16)
        elif k in pre_st:                    # G2 정규화: post 에 없고 pre 에만 있으면 0 으로 지워진 것
            post[s["path"]] = 0
        else:
            post[s["path"]] = s["value"]
    out["post"] = post
    out["revert_diff_empty"] = (not post_st) if out["termination"] == "revert" else None
    return out
