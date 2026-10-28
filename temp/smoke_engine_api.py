import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from Dependencies.main import slice_solidity
import engine_api

SRC = """contract C {
    function f(uint256 a) public pure returns (uint256) {
        uint256 b = a + 1;
        if (a > 10) {
            b = b + a;
        }
        return b;
    }
}"""

records = slice_solidity(SRC)
print("=== records ===")
for r in records:
    print("  ", r)

sess = engine_api.EngineSession()
st = sess.analyze(records)

print("\n=== Structure ===")
print("scope     :", st.scope)
print("diagnostics:", st.diagnostics)
print("slots     :", st.debug_slots)
print("entry/exit:", st.cfg.entry, st.cfg.exit, "| return:", st.cfg.return_exit, "error:", st.cfg.error_exit)
print("nodes:")
for n in st.cfg.nodes:
    print(f"   {n.id:18} line={n.src_line:>3} flags={n.flags} cond={n.cond_type} state={n.state}")
print("edges:")
for e in st.cfg.edges:
    print(f"   {e.from_:18} -> {e.to:18} [{e.kind}]")

print("\n=== evaluate(a=[0,5]) ===")
ev = sess.evaluate({"a": {"t": "interval", "lo": "0", "hi": "5"}})
print("scope:", ev.scope, "| top_vars:", ev.top_vars)
print("nodes(with states):")
for n in ev.cfg.nodes:
    if n.state:
        print(f"   {n.name:18} line={n.src_line:>3} b={n.state.get('b')}")
    else:
        print(f"   {n.id:18} line={n.src_line:>3} (no state)")

from engine_api import IntentItem, to_analysis_result, analyze_once

def verify_both(label, items):
    """standalone P2(캐시 사용) vs inline 재해석 등가 비교."""
    standalone = sess.verify(items)                 # 캐시 있음 → standalone P2
    sess._states_cache.clear()                      # 캐시 제거 → 강제 재해석
    inline = sess.verify(items)
    # 캐시 복구를 위해 evaluate 재실행
    sess.evaluate({"a": {"t": "interval", "lo": "0", "hi": "5"}})
    s = [(v.status, round(v.risk, 1)) for v in standalone]
    i = [(v.status, round(v.risk, 1)) for v in inline]
    ok = "OK " if s == i else "MISMATCH "
    print(f"{ok}{label}: standalone={s} inline={i}")

print("\n=== verify: standalone P2 vs inline 등가 ===")
verify_both("b > 0",            [IntentItem(scopeKind="During", text="b > 0", line=3)])
verify_both("b > 100",          [IntentItem(scopeKind="During", text="b > 100", line=3)])
verify_both("b >= a + 1",       [IntentItem(scopeKind="During", text="b >= a + 1", line=3)])
verify_both("b > 0 && b <= 6",  [IntentItem(scopeKind="During", text="b > 0 && b <= 6", line=3)])
verify_both("b >= a+1 && b > 0",[IntentItem(scopeKind="During", text="b >= a + 1 && b > 0", line=3)])

print("\n=== to_analysis_result / analyze_once ===")
res = analyze_once(records, {"a": {"t": "interval", "lo": "0", "hi": "5"}},
                   [IntentItem(scopeKind="During", text="b > 0", line=3)])
print("keys:", sorted(res.keys()))
print("scope:", res["scope"], "| #nodes:", len(res["cfg"]["nodes"]),
      "| #edges:", len(res["cfg"]["edges"]), "| #verdict:", len(res["verdict"]),
      "| #slots:", len(res["debugSlots"]))
print("edge[0]:", res["cfg"]["edges"][2], "(from/to 키 확인)")
print("verdict[0]:", {k: res["verdict"][0][k] for k in ("line", "type", "status", "risk")})

print("\n=== deps 로딩 (프로젝트 단위 deps: n_BoostToken 의 SafeMath library) ===")
try:
    s2 = analyze_once.__globals__["EngineSession"]("evaluation/Dataset/deps/n_BoostToken")
    print("loaded into contract_cfgs:", "SafeMath" in s2._ca.contract_cfgs,
          "| library_cfgs:", "SafeMath" in s2._ca.library_cfgs)
    s2.close()
except Exception as ex:
    print("deps load error:", ex)
