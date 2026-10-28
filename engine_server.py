"""engine_server — engine_api 를 프론트(frontend/src/engine/analyzeClient.ts)에 노출하는 HTTP 서버.

3콜(analyze/evaluate/verify)을 세션 영속으로 묶는다(= dirty 신호 + 캐시 활용):
  * /analyze   — 코드 변경: 세션 재빌드(full source → slice → CFG 구조). 캐시 리셋.
  * /evaluate  — 값 변경  : 같은 세션 재사용 → fixpoint states (frozen 캐시).
  * /verify    — intent 변경: 같은 세션의 보존 states 에 standalone P2.
세션 키 = "contract.func". (단일 사용자 dev 도구 전제 — 동시성 잠금 없음.)

실행: .venv\Scripts\python.exe engine_server.py   (uvicorn, 포트 8000)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from dataclasses import asdict

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from Dependencies.main import slice_solidity
import engine_api
from engine_api import EngineSession, IntentItem, to_analysis_result, _cfg_to_dict

app = FastAPI(title="IQDebug engine_api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev: Vite(5173) 등 로컬 origin 허용
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션 키("contract.func") → EngineSession. analyze 가 재빌드, evaluate/verify 가 재사용.
_sessions: dict[str, EngineSession] = {}


class Req(BaseModel):
    contract: str
    func: str
    source: str = ""
    inputs: dict = {}     # slotKey → RangeValue
    intents: list = []    # [{scopeKind, text, line?}]
    # (2026-10-28, 6차 검토 C9) overload 를 시그니처로 지정한다. 생략하면 이름만으로 고르고
    #   (= 먼저 등록된 overload) 그 사실이 Structure 에 드러나지 않는다. 프론트는 아직 안 보낸다.
    param_types: list[str] | None = None


class OutlineReq(BaseModel):
    source: str = ""


# 아웃라인 캐시: 같은 소스로 반복 호출(타이핑마다)될 때 전체 재파싱을 피한다.
# 키 = 소스 해시. 단일 사용자 dev 도구 전제라 크기 상한만 둔다.
_outline_cache: dict[int, list] = {}


def _key(r: Req) -> str:
    # overload 는 세션도 분리한다 — 같은 이름의 서로 다른 시그니처가 한 세션을 공유하면
    #   CFG 와 debug 값이 섞인다.
    sig = "(" + ",".join(r.param_types) + ")" if r.param_types is not None else ""
    return f"{r.contract}.{r.func}{sig}"


def _to_intents(items: list) -> list[IntentItem]:
    return [IntentItem(scopeKind=i["scopeKind"], text=i["text"], line=i.get("line"))
            for i in items]


def _build(r: Req):
    """새 세션 생성 + full source → slice → analyze 1회. (session, Structure) 반환.
    (analyze 를 같은 세션에 두 번 호출하면 records 이중 적용으로 손상 → 항상 fresh.)"""
    sess = EngineSession()
    _sessions[_key(r)] = sess
    # (2026-10-24, M8) intent 도 함께 — 슬롯 도출이 intent 가 참조하는 진입 값(상태변수·글로벌·경로)을 포함한다.
    st = sess.analyze_source(r.source, contract=r.contract, func=r.func,
                             intents=_to_intents(r.intents), param_types=r.param_types)
    return sess, st


def _session(r: Req) -> EngineSession:
    """기존 세션 재사용. 없으면(서버 재시작 등) source 로 lazy 빌드."""
    sess = _sessions.get(_key(r))
    if sess is None:
        sess, _ = _build(r)
    else:
        # 요청마다 명시적으로 설정 — 생략하면 이전 요청의 타깃 시그니처가 남는다.
        sess.set_target_param_types(r.param_types)
    return sess


@app.get("/health")
def health() -> dict:
    return {"ok": True, "sessions": list(_sessions.keys())}


@app.post("/outline")
def outline(r: OutlineReq) -> dict:
    """소스에 있는 디버그 가능한 (contract, function) 목록.

    프론트가 함수 목록을 mock 에 하드코딩하던 문제를 없앤다 — 파일을 열거나 편집할 때
    이걸 불러 목록을 갱신하고, 그 다음에야 /analyze 의 스코프(contract/func)가 정해진다.
    (그래서 세션 키 "contract.func" 를 쓰는 _session() 경로와 분리돼 있어야 한다.)

    분석 세션은 여기서 만들고 버린다 — 스코프별 세션을 오염시키지 않기 위함.
    대신 소스 해시로 캐시해 타이핑마다 전체 재파싱하지 않는다.
    """
    h = hash(r.source)
    hit = _outline_cache.get(h)
    if hit is None:
        sess = EngineSession()
        sess.analyze(slice_solidity(r.source))
        hit = [asdict(f) for f in sess.outline()]
        if len(_outline_cache) > 32:        # dev 도구: 단순 상한
            _outline_cache.clear()
        _outline_cache[h] = hit
    return {"functions": hit,
            "contracts": sorted({f["contract"] for f in hit})}


@app.post("/analyze")
def analyze(r: Req) -> dict:
    # B1: 첫 분석은 fresh 빌드, 이후 코드 변경은 같은 세션에 증분 update(SyntaxValid 게이트+diff apply).
    key = _key(r)
    sess = _sessions.get(key)
    if sess is None:
        sess, st = _build(r)
        diags = [asdict(d) for d in st.diagnostics]
    else:
        try:
            res = sess.update(r.source)     # invalid 면 상태 유지 + diagnostics 반환
        except engine_api.IncrementalSurgeryError:
            # (2026-10-22) fail loud: 수술 실패·미지원은 오류로 올린다(전체 재빌드 폴백 폐기).
            #   변경은 후보에만 적용되므로 세션은 마지막 정상 상태 그대로다 — 캐시에 남겨 둔다.
            raise
        # ★ 스코프 명시 필수: 생략하면 structure() → _resolve_fcfg(None, None) 폴백이
        #   "컨트랙트의 마지막 함수"를 집어 다른 함수의 슬롯/CFG 를 돌려준다(_slots 도 오염).
        #   (M8) 요청의 intent 로 슬롯을 도출한다(세션 기억 대신 요청이 진실의 원천).
        st = sess.set_intents(_to_intents(r.intents), r.contract, r.func)  # 갱신(또는 거부 시 직전) 구조 재직렬화
        diags = res["diagnostics"]          # [{line, column, message, severity}]
    return _structure_dict(st, diags)


def _structure_dict(st, diags: list) -> dict:
    return {
        "scope": asdict(st.scope),
        "cfg": _cfg_to_dict(st.cfg),
        "debugSlots": [asdict(s) for s in st.debug_slots],
        "diagnostics": diags,
    }


@app.post("/set_intents")
def set_intents(r: Req) -> dict:
    """(2026-10-24, M8) intent 만 바뀌었을 때 — CFG 는 그대로, debug 슬롯만 다시 도출(intent 가 참조하는 진입 값)."""
    st = _session(r).set_intents(_to_intents(r.intents), r.contract, r.func)
    return _structure_dict(st, [asdict(d) for d in st.diagnostics])


@app.post("/evaluate")
def evaluate(r: Req) -> dict:
    ev = _session(r).evaluate(r.inputs, contract=r.contract, func=r.func)
    return {
        "scope": asdict(ev.scope),
        "cfg": _cfg_to_dict(ev.cfg),
        # (2026-10-22) Step Into 대상 callee CFG(상태 포함) — 예전엔 보내지 않아 실제 UI 에선 Step Into 가 불가능했다
        "callees": {k: _cfg_to_dict(c) for k, c in (ev.callees or {}).items()},
        "top_vars": [asdict(t) for t in ev.top_vars],
        "diagnostics": [asdict(d) for d in ev.diagnostics],
    }


@app.post("/verify")
def verify(r: Req) -> dict:
    # 스코프·intent·debug 값 모두 요청이 진실의 원천(세션 기억에서 추측 금지 — 유령 상태 차단).
    verdict = _session(r).verify(_to_intents(r.intents), contract=r.contract, func=r.func,
                                 debug_values=r.inputs)
    return {"verdict": [asdict(v) for v in verdict]}


@app.post("/analyze_once")
def analyze_once(r: Req) -> dict:
    return engine_api.analyze_once(
        slice_solidity(r.source), r.inputs, _to_intents(r.intents))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
