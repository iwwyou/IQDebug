"""M8: analyze/set_intents 가 intent 가 참조하는 진입 값까지 debug 슬롯으로 내는지 (engine_server 경유)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine_server

SRC = """pragma solidity ^0.8.0;
contract C {
    uint256 a;
    uint256 cap;
    function f(uint256 x) public {
        a = x;
    }
}
"""
ok = fail = 0
def check(name, cond):
    global ok, fail
    ok += bool(cond); fail += (not cond)
    print(("PASS " if cond else "FAIL ") + name)

class _R:  # httpx 미설치 → 엔드포인트 함수를 직접 호출(직렬화 경로 동일)
    def __init__(self, d): self.status_code, self._d = 200, d
    def json(self): return self._d
class _C:
    def post(self, path, json):
        fn = {"/analyze": engine_server.analyze, "/set_intents": engine_server.set_intents}[path]
        return _R(fn(engine_server.Req(**json)))
c = _C()
base = {"contract": "C", "func": "f", "source": SRC, "inputs": {}}
keys = lambda r: {s["key"] for s in r.json()["debugSlots"]}
engine_server._sessions.clear()
r = c.post("/analyze", json={**base, "intents": []})
check("analyze ok", r.status_code == 200)
check("cap not slot without intent", not any("cap" in k for k in keys(r)))
it = [{"scopeKind": "post", "text": "a <= cap", "line": None}]
r = c.post("/set_intents", json={**base, "intents": it})
check("set_intents ok", r.status_code == 200)
check("cap slot after set_intents", any("cap" in k for k in keys(r)))
r = c.post("/set_intents", json={**base, "intents": []})
check("cap removed", not any("cap" in k for k in keys(r)))
engine_server._sessions.clear()
r = c.post("/analyze", json={**base, "intents": it})
check("fresh analyze with intents has cap", any("cap" in k for k in keys(r)))
r = c.post("/analyze", json={**base, "source": SRC.replace("a = x;", "a = x + 1;"), "intents": it})
check("update path keeps cap", r.status_code == 200 and any("cap" in k for k in keys(r)))
print(f"{ok} passed, {fail} failed")
