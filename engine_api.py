"""engine_api — IQDebug 프론트(↔ frontend/src/engine/analyzeClient.ts)용 분석 API.

설계 원칙(PROGRESS.md / 설계 논의 확정):
  * 코어(Interpreter/Engine.py fixpoint, Domain/, Utils/CFG.py, GuardianVerificationEngine,
    파서·visitor)는 **불변**. 이 모듈은 입력(annotation 적용)·출력(Result 직렬화) 레이어만 신설.
  * 기존 출력층 ContractAnalyzer.send_report_to_front(print만)은 이 모듈의 export가 대체.

두 레이어로 가른다:

  [결정론 코어]  abstract interpreter, edit-time, 빠름.  EngineSession 안.
    analyze(records)        → Structure        (CFG 구조 + 빈 debug 슬롯)   ── 코드 편집 트리거
    evaluate(debug_values)  → CfgWithStates     (fixpoint → states)          ── 값 변경 트리거
    verify(intents)         → list[VerdictItem] (states 읽어 판정)           ── intent 변경 트리거
        체인 종속: analyze → evaluate → verify.  verify는 states 없으면 lazy evaluate.

  [생성 보조]  solver/LLM, async, 코어 출력 + 추가입력(주석/패턴) 소비.  L2 originality.
    suggest_value(session, slot)              → Suggestion   (opaque 슬롯 채우기)
    reach(session, target)                    → dict         (특정 path 도달 입력 = target 있는 생성)
    cover(session, scope)                     → list[dict]   (전 분기 커버; reach 반복, 후순위)
    suggest_intent(source, comments, patterns)→ list[IntentItem] (주석·취약점패턴 기반 intent 제안)
        → suggest_value/reach/cover 는 표면만 분리, 내부 generator(solver/LLM seam)는 공유.

snapshot/restore 제약(중요):
  현재 debug 분석은 DebugBatchManager.flush 가 restore 로 값을 초기화 → 매 디버깅이 독립적.
  따라서 evaluate 는 **restore 가 값을 지우기 전에** CFG-with-states 를 Result(불변 스냅샷 사본)로
  직렬화해 반환한다. 초기화 로직은 그대로 두고, 엔진 객체는 다음 run 을 위해 리셋된 채 남는다.

이 파일은 계약(타입·시그니처) 우선 스켈레톤이다. 본문은 NotImplementedError.
실배선 시 참고 위치는 각 스텁 docstring에 file:line 으로 명시.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional, TypedDict, Union

from Utils.Errors import SourceError

# ─────────────────────────────────────────────────────────────────────────────
# 입력 record (slice_solidity 산출 형태와 동일: Dependencies/main.py:53)
# ─────────────────────────────────────────────────────────────────────────────


class _RecordBase(TypedDict):
    code: str
    startLine: int
    endLine: int
    event: str  # "add" | "update" | "delete"


class Record(_RecordBase, total=False):
    """순수 코드 한 조각. 어노테이션 라인은 포함하지 않는다.

    closeBefore: slice_solidity 가 `} else {`/`} while`/`} catch` 에 붙이는 플래그. update_code 의
      close_before 인자로 전달돼야 else/do-while/catch 가 직전 블록의 join 을 찾는다(누락 시 빌드 실패)."""
    closeBefore: bool


# ─────────────────────────────────────────────────────────────────────────────
# 값 모델 (frontend/src/types/result.ts 와 1:1 계약)
# ─────────────────────────────────────────────────────────────────────────────

NodeFlag = Literal[
    "condition", "branch_then", "branch_else", "merge",
    "fixpoint_evaluation", "loop_exit", "loop_body", "exit",
    # N4: 이 노드에 함수 호출이 있다 / 그 호출로는 들어갈 수 없다
    "call", "opaque",
]
EdgeKind = Literal["normal", "true", "false", "back_edge"]
VerdictStatus = Literal["Satisfied", "Warning", "Violated", "Unreachable"]
DebugKind = Literal["StateVar", "Param", "GlobalVar", "IReturn"]
ValueKind = Literal["interval", "bool", "address", "array", "bytes", "enum"]
Backend = Literal["llm", "solver", "hybrid"]


# RangeValue — 사용자가 채우는 debug 값. debugValue 캐논(Solidity.g4 329-338)으로 직렬화됨.
class IntervalValue(TypedDict):
    t: Literal["interval"]
    lo: str
    hi: str


class BoolValue(TypedDict):
    t: Literal["bool"]
    v: Literal["true", "false", "any"]


class AddressValue(TypedDict):
    t: Literal["address"]
    id: int  # symbolicAddress N


class NoneValue(TypedDict):
    t: Literal["none"]


RangeValue = Union[IntervalValue, BoolValue, AddressValue, NoneValue]
SlotKey = str  # "balance" | "a" | "msg.sender" | "token.balanceOf()"


@dataclass
class CalleeRef:
    """이 노드에서 일어나는 함수 호출 하나 (N4 Step Into 대상 또는 opaque 사유)."""
    key: str                           # callees 맵 키 = "<호출노드>#<컨트랙트>.<함수>"
    label: str                         # "Math.mulDiv" — UI 표시용
    enterable: bool                    # False = opaque
    contract: Optional[str] = None
    name: Optional[str] = None
    reason: Optional[str] = None        # enterable=False 인 이유 (화면에 띄운다)
    # callee 소스 범위. 같은 파일 callee 는 프론트가 store.functions 로도 찾을 수 있고,
    # 의존성 pkl callee 는 **그 dep 파일 기준** 라인이라 업로드한 소스(depContents)에 맞다.
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass
class CFGNode:
    id: str            # 노드 uid (2026-10-22) — 식별자. 줄 이동에도 불변
    src_line: int
    # (2026-10-22, U3) 화면 표시·breakpoint 줄. if/else-if join 은 체인을 닫는 `}` 줄, 그 외는 src_line.
    display_line: int = -1
    flags: list[NodeFlag] = field(default_factory=list)
    cond_type: Optional[str] = None  # if | else_if | require | while | for | do_while
    # 변수명 → 직렬화된 추상값 ("[lo,hi]" | "address(⊤)" | "top" | ...). 구조 단계엔 빈 dict.
    #   state = 이 노드 실행 **후**(OUT). intent 검증·변화량 표시용.
    state: dict[str, str] = field(default_factory=dict)
    # (2026-10-22) 이 노드 실행 **전**(IN) — 프론트 커서 기본 표시("이 줄 실행 직전").
    #   문장 없는 join/분기 노드는 IN == OUT 이라 state 와 같다. 루프 헤더는 루프 밖에서 들어오는 상태.
    #   구조 단계엔 빈 dict.
    in_state: dict[str, str] = field(default_factory=dict)
    # merge 노드면 join 기여 predecessor id들 (backward 양쪽 하이라이트용)
    join_from: Optional[list[str]] = None
    # 루프 헤더 노드면 "루프 진입 전 ↔ 고정점 수렴 후 값이 바뀐 변수" (N3).
    #   decision #4 가 iteration 별 stepping 을 금지하므로, 루프는 통째로 접고
    #   그 효과를 이 delta 하나로 보여준다. 루프 내부에서 새로 선언된 변수는 제외.
    loop_delta: Optional[dict[str, str]] = None
    # N4: 이 노드에서 일어나는 호출들. enterable 한 것은 `CfgWithStates.callees[key]` 에
    #     callee CFG(상태 포함)가 들어 있다.
    callees: Optional[list[CalleeRef]] = None
    # 엔진 노드 이름(`Assign_4`, `while_join_5` …) — 사람이 읽는 라벨일 뿐 식별자가 아니다(줄 이동 시 중복 가능).
    name: str = ""


@dataclass
class CFGEdge:
    from_: str
    to: str
    kind: EdgeKind = "normal"


@dataclass
class CFG:
    entry: str
    exit: str
    nodes: list[CFGNode] = field(default_factory=list)
    edges: list[CFGEdge] = field(default_factory=list)
    return_exit: Optional[str] = None
    error_exit: Optional[str] = None


@dataclass
class VerdictItem:
    line: int
    node: str
    type: str
    status: VerdictStatus
    risk: float  # 0..10
    region: tuple[int, int]
    message: str
    # 위반 영역(엔진 `false_regions`). **출력공간**(비교되는 값의 구간)이지 입력 파라미터 구간이
    # 아니다 — 입력공간 역전파/분할은 decision #40 (B). warning 이고 단일 절(during)일 때만 채워진다
    # (복합 절은 _combine_logic_results 가 드롭). 경계는 2^256 까지 가능 → JS number 정밀도 회피 위해
    # 문자열(IntervalValue 와 동일 규약, 72행). 없으면 None.
    falseRegions: Optional[dict] = None


@dataclass
class TopVar:
    var: str
    kind: str
    node: str
    src_line: int
    scope: str
    provenance: Optional[str] = None  # ⊤ 출처 태깅 (현재 엔진엔 없음 → None)


@dataclass
class Suggestion:
    value: RangeValue
    backend: Backend
    confidence: float


@dataclass
class DebugSlot:
    """분석이 노출하는 디버깅 대상 변수. 사용자는 값만 채운다."""
    kind: DebugKind
    key: SlotKey
    label: str
    valueKind: ValueKind
    opaque: bool = False           # @IReturn/외부 등 도출 불가
    line: Optional[int] = None     # debug는 대체로 함수-스코프 → 보통 None
    suggestion: Optional[Suggestion] = None
    # interval 슬롯의 타입 정보 → 빈 경계(unbounded) 를 타입 min/max 로 치환할 때 사용(Fix B).
    signed: Optional[bool] = None  # int* → True, uint* → False, 비수치 → None
    bits: Optional[int] = None     # 정수 비트폭(uint256 → 256)
    # (2026-10-23, 사용자 결정) 구체 인덱스 지정. 기본은 소스에 쓴 접근식 그대로(`poolInfo[_pid]`)이고, 원하면 인덱스 자리를
    #   구체값으로 바꾼 키(`poolInfo[1]`)로 입력한다(EngineSession.match_slot 이 템플릿과 대조).
    specializable: bool = False    # 인덱스 자리가 있는 경로 슬롯
    requiresIndex: bool = False    # 인덱스가 타깃 **지역변수**(`pegObservations[i]` 의 루프 i) — 진입 시점에 값이 없으므로
                                   #   이 키 그대로는 입력할 수 없고 구체 인덱스로만 입력한다


@dataclass
class IntentItem:
    scopeKind: Literal["During", "Post"]
    text: str
    line: Optional[int] = None  # @During = 라인-스코프, @Post = 함수-스코프(None)


@dataclass
class FunctionOutline:
    """소스에 실제로 존재하는 디버그 가능한 함수 하나.

    프론트 함수 목록이 `mock/workspace.json` 에 고정돼 있어 사용자가 자기 .sol 을 열어도
    mock 의 함수가 뜨던 문제(§A ①)를 없애기 위한 계약. `frontend/src/state/store.ts`
    의 `FunctionMeta` 와 1:1 (name / contract / startLine / source) + endLine·signature.
    """
    contract: str
    name: str
    signature: str          # 예: "(uint256)" — 오버로딩 구분용(프론트는 아직 미사용)
    startLine: int          # `function f(...) ... {` 선언 라인 (컨트랙트-절대)
    endLine: int            # 닫는 `}` 라인
    source: str             # 선언~닫는 괄호까지 원문


@dataclass
class Scope:
    contract: str
    function: str


@dataclass
class Diagnostic:
    """누적 코드 syntax gate 결과. analyze 가 delta 적용 후 *합쳐진 전체 코드*를
    ANTLR(Parser/Solidity.g4) 파스해 실패하면 채워진다. (solc 컴파일 게이트는 보류:
    런타임 무겁고 의미검사는 분석 선결조건 아님 — 필요 시 별도 deep-validate 액션으로 분리.)"""
    line: int
    message: str
    severity: Literal["error", "warning"] = "error"
    # (2026-10-22) syntax = 문법(SyntaxValid 게이트), semantic = 사용자 소스 의미 오류(SourceError)
    category: Literal["syntax", "semantic"] = "syntax"


# ─────────────────────────────────────────────────────────────────────────────
# 단계별 산출물
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Structure:
    """analyze() 산출 — CFG 구조 + 빈 debug 슬롯. 값/상태 없음.
    diagnostics 가 비어있지 않으면 누적 코드가 syntax-invalid → 이전 valid 구조 유지하고 표시만."""
    scope: Scope
    cfg: CFG                         # nodes.state == {} (구조만)
    debug_slots: list[DebugSlot] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class CfgWithStates:
    """evaluate() 산출 — fixpoint 후 states 채워진 CFG + ⊤ 변수.
    restore 전에 직렬화된 불변 스냅샷 사본."""
    scope: Scope
    cfg: CFG                         # nodes.state 채워짐 = navigation substrate
    # N4: Step Into 대상 callee 들의 CFG(상태 포함). 키 = `CalleeRef.key`.
    #   호출 지점별로 분리된다 — 같은 callee 를 두 곳에서 부르면 **별개 엔트리**다.
    callees: dict[str, CFG] = field(default_factory=dict)
    top_vars: list[TopVar] = field(default_factory=list)
    # debug 값 입력 오류(잘못된 interval 등). 비어있지 않으면 프론트가 슬롯 옆에 표시(Fix A).
    diagnostics: list[Diagnostic] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 의존성 명세 (Root B 입력)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DepSpec:
    name: str
    kind: Literal["interface", "library", "contract"]
    source: Optional[str] = None  # 최초 빌드용 원문 (.sol). 이미 빌드됐으면 None.


@dataclass
class DepInfo:
    name: str
    kind: Literal["interface", "library", "contract"]
    status: Literal["ready", "building", "error"]


# ─────────────────────────────────────────────────────────────────────────────
# 뷰 직렬화기 (엔진 FunctionCFG → 프론트 CFG). 코어 밖, 순수 함수.
#   상태 직렬화는 RecordManager._flatten_var/_serialize_val(이미 검증된 interval→str) 재사용.
# ─────────────────────────────────────────────────────────────────────────────


def _node_flags(n) -> list[NodeFlag]:
    """엔진 CFGNode 의 role 플래그 → 프론트 NodeFlag 목록."""
    flags: list[NodeFlag] = []
    if getattr(n, "condition_node", False):
        flags.append("condition")
    if getattr(n, "branch_node", False):
        flags.append("branch_then" if getattr(n, "is_true_branch", False) else "branch_else")
    if getattr(n, "join_point_node", False):
        flags.append("merge")
    if getattr(n, "fixpoint_evaluation_node", False):
        flags.append("fixpoint_evaluation")
    if getattr(n, "loop_exit_node", False):
        flags.append("loop_exit")
    if getattr(n, "is_loop_body", False):
        flags.append("loop_body")
    if (getattr(n, "function_exit_node", False)
            or getattr(n, "return_exit_node", False)
            or getattr(n, "error_exit_node", False)):
        flags.append("exit")
    return flags


def _serialize_node_state(n, recorder, env=None) -> dict[str, str]:
    """CFGNode.variables(name→Variables) → {name: "[lo,hi]"/"address(⊤)"/...}. env 를 주면 그 환경(IN 상태 등).
    RecordManager._flatten_var 는 인스턴스 상태에 의존하지 않는 순수 평탄화라 재사용 가능."""
    flat: dict[str, str] = {}
    for name, var in (getattr(n, "variables", {}) if env is None else env).items():
        if "" in name:      # (Phase 3b) shadowing 으로 보관된 바깥 binding — 내부용 키
            continue
        recorder._flatten_var(var, name, flat)  # fail-loud: 직렬화 구멍은 그대로 터뜨림
    # ⊥(bottom = 도달 불가 분기) 가독화: "[None,None]" → "⊥"
    return {k: ("⊥" if v == "[None,None]" else v) for k, v in flat.items()}


_LOOP_COND_TYPES = ("while", "for", "do_while")


def _is_loop_head(n) -> bool:
    return (getattr(n, "condition_node", False)
            and getattr(n, "condition_node_type", None) in _LOOP_COND_TYPES)


def _loop_region(fcfg, head) -> set:
    """head 에서 전진하며 모은 루프 본문. 절단점은 **자기 false 후속**뿐.

    `Engine.traverse_loop_nodes`(⑮)와 **같은 규칙**이다. 엔진 인스턴스 없이 그래프만으로
    계산해야 하므로 여기 한 번 더 둔다 — 규칙이 갈라지면 "중첩 판정" 이 엔진과 어긋난다.
    """
    G = fcfg.graph
    own_exit = {s for s in G.successors(head)
                if G[head][s].get("condition") is False}
    seen: set = set()
    stack = [head]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for s in G.successors(cur):
            if s in own_exit:
                continue
            stack.append(s)
    return seen


def _nested_loop_heads(fcfg) -> set:
    """다른 루프 본문 안에 있는 루프 헤더들 = **최외곽이 아닌** 루프.

    ★ `loop_delta` 는 **최외곽 루프 기준**으로만 내보낸다(사용자 결정). 이유:
      N2 의 step forward 는 최외곽 루프를 통째로 접고 그 **뒤**로 착지하므로, 안쪽 루프는
      애초에 navigation 착지점이 되지 않는다. 안쪽 delta 는 보여줄 자리가 없다.

    ⚠ 그냥 "안 내보낸다" 가 아니라 **걸러야** 한다. 안쪽 루프의 `loopDelta` 는 증분 빌드
      도중(아직 바깥 루프 본문에 편입되기 전) `reinterpret_from` 이 안쪽 헤더를 loop head
      로 만나 `fixpoint(안쪽)` 을 직접 불렀을 때 ledger 에 기록되고, 그 뒤로 갱신되지 않아
      **낡은 스냅샷**으로 남는다. 없는 것보다 나쁘다.
      판정을 직렬화 시점에 매번 다시 하므로 ledger 가 낡아도 영향받지 않는다.
    """
    heads = [n for n in fcfg.graph.nodes() if _is_loop_head(n)]
    if len(heads) < 2:
        return set()
    nested: set = set()
    for h in heads:
        for other in heads:
            if other is h:
                continue
            if h in _loop_region(fcfg, other):
                nested.add(h)
                break
    return nested


def _external_call_roots(ccf, interface_names, contract_cfgs) -> dict:
    """**외부 호출의 루트 식별자** → 그 호출 대상의 선언 타입명.

    두 형태를 모두 덮는다:
      · 캐스트 `IFoo(a).f()` / `Vault(a).f()` → 루트가 **타입 이름** 자체
      · 변수  `v.f()`                        → 루트가 변수명, 타입은 `typeInfo.interfaceName`

    ★ (2026-10-23 사용자 결정, F49 폐기) **인터페이스만**. IReturn 은 본문이 없는 인터페이스 호출의 반환값을 입력으로 주는
      장치다 — 본문이 있는 컨트랙트 타입(`ERC20(x).f()`, `Vault v; v.f()`)은 그 본문을 실행해야 하는 대상이지 입력이 아니다.
      다른 컨트랙트 인스턴스의 storage 모델링은 future work(그때까지 컨트랙트 타입 외부 호출 반환은 ⊤).

    ⚠ 이 판정은 **@IReturn 슬롯 도출(`_derive_debug_slots`)과 opaque 호출 표시(N4)** 양쪽이
      쓴다. 두 곳에 각각 적으면 조용히 어긋나므로 여기 한 곳에만 둔다.
    """
    from Utils.CFG import LibraryCFG
    interface_names = interface_names or set()
    contract_cfgs = contract_cfgs or {}
    roots: dict = {}
    # 타입 이름 (캐스트 형태)
    for nm in interface_names:
        roots[nm] = nm
    # 그 타입의 상태변수 (변수 형태) — visitor 는 컨트랙트 타입도 typeCategory "interface" 로 적으므로 이름으로 가른다
    svn = getattr(ccf, "state_variable_node", None) if ccf is not None else None
    for nm, var in ((getattr(svn, "variables", {}) if svn is not None else {}) or {}).items():
        ifc = getattr(getattr(var, "typeInfo", None), "interfaceName", None)
        if ifc and ifc in interface_names:
            roots[nm] = ifc
    return roots


def _loop_delta(n, recorder, nested_heads: set) -> Optional[dict[str, str]]:
    """루프 헤더 노드면 ledger 의 `loopDelta`(진입 전 ↔ 수렴 후 바뀐 변수)를 꺼낸다.

    `Engine.fixpoint` 가 고정점 후 `head.src_line` 에
    `{"kind": "loopDelta", "vars": {경로: 직렬화값}}` 로 적어둔다. 엔진에는 이미 있었지만
    `engine_api` 가 `RecordManager.ledger` 를 전혀 보지 않아 웹으로 오지 않았다(N3).

    ⚠ 한 소스 라인에 노드가 여러 개 있을 수 있다(`for (i=0; i<n; i++)` → init/cond/update).
      그래서 **루프 조건 노드에만** 붙인다. 라인만으로 매칭하면 같은 delta 가 중복된다.
    ⚠ 중첩 내부 루프는 제외한다 — `_nested_loop_heads` 참조.
    """
    if not _is_loop_head(n):
        return None
    if n in nested_heads:
        return None
    ln = getattr(n, "src_line", None)
    if ln is None:
        return None
    # ledger 는 defaultdict(list) — `.get` 으로 읽어 빈 키 생성을 막는다.
    for rec in recorder.ledger.get(ln, ()):
        if rec.get("kind") == "loopDelta":
            return dict(rec.get("vars") or {})
    return None


def _back_edges(fcfg) -> set:
    """dominator 기반 back edge 집합 {(u, v)}.

    (u, v) 가 back edge  ⟺  v 가 u 를 지배(dominate)한다.
    = 자연 루프(natural loop)의 retreating edge 정의 그대로. 엔진 flag
    (`fixpoint_evaluation_node` 등)에 기대지 않으므로 while/for/do-while/중첩을
    구분 없이 잡고, 플래그가 빠진 구조가 생겨도 깨지지 않는다.

    왜 필요한가: 프론트 `useNavigation` 은 이미 `kind !== 'back_edge'` 로 거르는데
    (`pred()` = backward, applyTick 의 `succOf()` = run-to-breakpoint) 라벨이 한 번도
    붙은 적이 없어 **필터가 무효**였다. decision #4 의 "back-edge 는 fixpoint 내부로
    흡수되어 navigation 에 노출 안 됨" 이 실제로는 안 지켜지고 있었다.
    """
    import networkx as nx
    G = fcfg.graph
    entry = fcfg.entry_node
    # entry 에서 도달 못 하는 노드는 idom 에 없다 → 아래 walk 가 즉시 False 로 빠짐.
    idom = nx.immediate_dominators(G, entry)
    back = set()
    for u, v in G.edges():
        cur = u
        while True:
            if cur is v:               # v 가 u 의 dominator 체인에 있음 = v dom u
                back.add((u, v))
                break
            nxt = idom.get(cur)
            if nxt is None or nxt is cur:   # 루트 도달 or 미지배 노드
                break
            cur = nxt
    return back


def _node_line_span(fcfg):
    """노드 `src_line` 의 min/max. `EngineSession._function_span` 은 세션-로컬
    `line_info` 를 쓰므로 **pkl 로 로드된 함수에는 None** 을 준다 — 그 폴백."""
    lns = [n.src_line for n in fcfg.graph.nodes()
           if getattr(n, "src_line", None) is not None and n.src_line > 0]
    return (min(lns), max(lns)) if lns else (None, None)


def _external_call_nodes(fcfg, ext_roots) -> dict:
    """노드명 → 그 노드의 **외부 호출** 라벨들.

    판정 규칙은 `_external_call_roots` 와 공유한다(= @IReturn 슬롯과 같은 기준).
    루트가 외부 호출 대상 타입/핸들이고 **accessor 에 call 이 있을 때만** 호출로 센다
    (`v` 를 그냥 주소로 읽는 것은 호출이 아니다).
    """
    from Domain.IR import Expression
    out: dict = {}

    def scan(e, acc):
        if not isinstance(e, Expression):
            return
        if _ap_is_access(e):
            root, accs = _ap_spine(e)
            nm = (root.identifier if _ap_is_ident(root)
                  else getattr(root, "typeName", None))
            if nm in ext_roots and any(k == "call" for k, _ in accs):
                acc.append(_ap_label(e))
                for side in _ap_side_exprs(e):
                    scan(side, acc)
                return
        for c in _ap_children(e):
            scan(c, acc)

    for n in fcfg.graph.nodes():
        acc: list = []
        for e in _ap_node_exprs(n):
            scan(e, acc)
        if acc:
            out[n.uid] = sorted(set(acc))
    return out


_OPAQUE_REASON = ("외부 호출 — callee 코드가 타입으로 정해지지 않습니다 "
                  "(컨트랙트 타입 캐스트는 런타임 검사가 없어 주소에 배포된 코드가 실행됩니다). "
                  "@IReturn 으로 반환값을 지정하세요.")


def _build_callees(fcfg, ca, ext_roots):
    """S1 이 떠낸 callee 상태 → (callees 맵, 노드명 → CalleeRef 목록).

    ★ **enterable 목록은 실제로 하강한 것**(`Engine._capture_callee_states` 적재분)에서만
      만든다. 정적 스캔으로 추정하면 "호출처럼 보이지만 하강하지 않은 것" 을 잘못
      들어갈 수 있다고 표시한다.
    ★ **opaque 목록은 외부 호출 스캔**에서 만든다 — 외부 호출은 하강 자체가 없어 포착되지 않는다.
    """
    cfgs: dict = {}
    per_node: dict = {}
    pending = []
    captured = getattr(ca, "_frozen_callee_states", None) or {}
    # ── 1차: CalleeRef 를 전부 만든다 (직렬화는 2차에서) ──────────────
    for (site, ordinal, cname, cfunc), states in captured.items():
        ccf = ((getattr(ca, "contract_cfgs", None) or {}).get(cname)
               or (getattr(ca, "library_cfgs", None) or {}).get(cname))     # (2026-10-23) library callee 도 소유자가 잡힌다
        callee_fcfg = ccf.get_function_cfg(cfunc) if ccf is not None else None
        if callee_fcfg is None or isinstance(callee_fcfg, dict):
            continue        # 못 찾음 / 오버로드 모호 → 들어갈 대상에서 제외
        key = "%s#%d#%s.%s" % (site, ordinal, cname or "?", cfunc)
        lo, hi = _node_line_span(callee_fcfg)
        # 의존성 callee 는 소스가 세션에 없다 → 빌드 시점 display_line 만 쓴다(세션 줄로 계산하면 다른 파일 줄이 섞인다)
        pending.append((key, callee_fcfg, states, None if getattr(ccf, "from_dependency", False)
                        else ca.sa.full_code_lines))
        per_node.setdefault(site, []).append(CalleeRef(
            key=key, label="%s.%s" % (cname or "?", cfunc), enterable=True,
            contract=cname, name=cfunc, start_line=lo, end_line=hi))
    for site, labels in _external_call_nodes(fcfg, ext_roots).items():
        for lb in labels:
            per_node.setdefault(site, []).append(CalleeRef(
                key="", label=lb, enterable=False, reason=_OPAQUE_REASON))
    # ── 2차: 직렬화. callee CFG **에도** `per_node` 를 넘겨 중첩 Step Into 를 가능하게 한다
    #   (`_pct` 안의 `Math.mulDiv` 호출 지점이 `_pct` 의 CFG 에서도 보여야 한다).
    #   `per_node` 는 노드 **uid** 키다(2026-10-22 — 예전 노드명 키는 줄 이동·다른 파일 같은 줄에서 겹쳤다).
    # ⚠ callee 에는 loop_delta 를 붙이지 않는다 — ledger 는 **라인 번호** 키라서
    #   pkl callee 의 라인이 사용자 파일의 라인과 겹치면 엉뚱한 delta 가 붙는다.
    for key, callee_fcfg, states, code_lines in pending:
        cfgs[key] = _serialize_cfg(callee_fcfg, ca.recorder, with_states=True,
                                   states_override=states["state"],
                                   in_states_override=states["in_state"], node_callees=per_node,
                                   with_loop_delta=False, code_lines=code_lines)
    return cfgs, per_node


def _serialize_cfg(fcfg, recorder, with_states: bool, states_override=None,
                   node_callees=None, with_loop_delta: bool = True, in_states_override=None,
                   code_lines=None) -> CFG:
    """엔진 FunctionCFG(graph=networkx DiGraph, 노드=CFGNode) → 프론트 CFG.
    with_states=False 면 state={} (analyze 구조 단계). True 면 추상상태 채움(evaluate)."""
    nodes: list[CFGNode] = []
    # 루프당 한 번만 계산 (노드 루프 안에서 부르면 O(루프²×노드)가 된다)
    nested_heads = _nested_loop_heads(fcfg) if (with_states and with_loop_delta) else set()
    node_callees = node_callees or {}
    from Utils.DisplayLines import join_display_lines
    disp = join_display_lines(fcfg, code_lines)

    def _in_of(n, st, ist_given):
        # IN 이 없는 노드: 문장이 없으면 IN == OUT(join·분기), 문장이 있으면 worklist 를 거치지 않은
        # 루프 본문(fixpoint 가 처리) — 실행 전 상태를 따로 갖지 않으므로 비워 둔다(프론트는 루프에 들어가지 않는다).
        if ist_given is not None:
            return ist_given
        return st if not getattr(n, "statements", None) else {}

    for n in fcfg.graph.nodes():
        join_from = None
        if getattr(n, "join_point_node", False):
            join_from = [p.uid for p in fcfg.graph.predecessors(n)]
        refs = node_callees.get(n.uid) or None
        flags = _node_flags(n)
        if refs:                                    # N4
            flags.append("call")
            if not any(r.enterable for r in refs):
                flags.append("opaque")
        # states_override = callee 직렬화용. callee 의 live `.variables` 는 restore 로
        #   baseline 이 복원돼 있으므로, S1 이 복귀 직전에 떠낸 dict 를 써야 한다.
        if with_states:
            if states_override is not None:
                st = states_override.get(n.uid, {})
                ist = _in_of(n, st, (in_states_override or {}).get(n.uid))
            else:
                st = _serialize_node_state(n, recorder)
                inn = getattr(n, "in_variables", None)
                ist = _in_of(n, st, None if inn is None else _serialize_node_state(n, recorder, inn))
        else:
            st = ist = {}
        src = n.src_line if getattr(n, "src_line", None) is not None else -1
        nodes.append(CFGNode(
            id=n.uid,
            name=n.name,
            src_line=src,
            display_line=disp.get(n.uid, src),
            flags=flags,
            cond_type=getattr(n, "condition_node_type", None),
            state=st,
            in_state=ist,
            join_from=join_from,
            callees=refs,
            # 구조 단계(analyze)엔 값이 없다 → delta 도 내보내지 않는다. ledger 에 이전
            # evaluate 의 loopDelta 가 남아 있어도 "값 없는 결과" 계약을 지킨다.
            loop_delta=(_loop_delta(n, recorder, nested_heads)
                        if (with_states and with_loop_delta) else None),
        ))
    back = _back_edges(fcfg)
    edges: list[CFGEdge] = []
    for u, v, data in fcfg.graph.edges(data=True):
        cond = data.get("condition", None)
        # back_edge 가 true/false 보다 우선한다 — 프론트가 이 라벨로 navigation 에서
        # 엣지를 제외하기 때문(분기 표시보다 "여긴 되돌아가는 길" 이 더 중요).
        if (u, v) in back:
            kind: EdgeKind = "back_edge"
        else:
            kind = "true" if cond is True else "false" if cond is False else "normal"
        edges.append(CFGEdge(from_=u.uid, to=v.uid, kind=kind))
    ret = getattr(fcfg, "return_exit", None)
    err = getattr(fcfg, "error_exit", None)
    return CFG(
        entry=fcfg.entry_node.uid,
        exit=fcfg.exit_node.uid,
        nodes=nodes,
        edges=edges,
        return_exit=ret.uid if ret is not None else None,
        error_exit=err.uid if err is not None else None,
    )


_KIND_KW = {"StateVar": "@StateVar", "Param": "@Param",
            "GlobalVar": "@GlobalVar", "IReturn": "@IReturn"}


def _value_text(v: RangeValue) -> Optional[str]:
    """RangeValue → debugValue 캐논 텍스트(Solidity.g4 329-338). none → None(스킵)."""
    t = v.get("t")
    if t == "interval":
        return f'[{v["lo"]},{v["hi"]}]'
    if t == "bool":
        return v["v"]  # true | false | any
    if t == "address":
        return f'symbolicAddress {v["id"]}'
    return None


def _debug_value_text(v: RangeValue, slot: Optional["DebugSlot"]):
    """RangeValue → (캐논 텍스트|None, 에러|None). interval 빈 경계는 슬롯 타입 min/max 로 치환(Fix B).
      * 양쪽 빈칸 = 미입력 → (None, None) 스킵(무제약=⊤).
      * 한쪽만 빈칸 = 그 방향 무제약 → 타입 경계(uint256 [0,2²⁵⁶-1] / int256 [-2²⁵⁵,2²⁵⁵-1]).
      * 숫자 파싱 실패·lo>hi = (None, 에러메시지) → 상위에서 Diagnostic 로 표면화(Fix A)."""
    t = v.get("t")
    if t == "interval":
        lo, hi = str(v.get("lo", "")).strip(), str(v.get("hi", "")).strip()
        if lo == "" and hi == "":
            return None, None  # 미입력 슬롯 → seed 안 함
        signed = bool(getattr(slot, "signed", False)) if slot else False
        bits = int(getattr(slot, "bits", 256) or 256) if slot else 256
        if lo == "":
            lo = str(-(1 << (bits - 1)) if signed else 0)
        if hi == "":
            hi = str((1 << (bits - 1)) - 1 if signed else (1 << bits) - 1)
        try:
            loi, hii = int(lo, 0), int(hi, 0)
        except (ValueError, TypeError):
            return None, f"잘못된 interval 값 (lo='{v.get('lo')}', hi='{v.get('hi')}')"
        if loi > hii:
            return None, f"하한이 상한보다 큼 (lo={loi} > hi={hii})"
        return f"[{loi},{hii}]", None
    if t == "bool":
        return v.get("v", "any"), None
    if t == "address":
        return f'symbolicAddress {v.get("id", 0)}', None
    return None, None


_SIG_ALIAS = {"uint": "uint256", "int": "int256", "byte": "bytes1"}


def _norm_type(t: str) -> str:
    """overload 시그니처 대조용 타입 정규화.

    엔진의 `FunctionCFG.parameter_types`(ANTLR visitor 산출)와 외부 하네스가 넘기는 표기
    (solc AST 기반 manifest 등)는 어휘가 달라 문자열 동등 비교가 깨진다 —
    `uint`/`uint256`, `IERC20`/`user:IERC20`, `uint[] memory`/`uint256[]`."""
    t = (t or "").strip()
    for q in (" memory", " storage", " calldata", " payable"):
        t = t.replace(q, "")
    t = t.replace(" ", "")
    if t.startswith("user:"):
        t = t[5:]
    base, suffix = t, ""
    while base.endswith("]"):
        i = base.rfind("[")
        if i < 0:
            break
        suffix = base[i:] + suffix
        base = base[:i]
    return _SIG_ALIAS.get(base, base) + suffix


def _norm_sig(types) -> str:
    return "(" + ",".join(_norm_type(t) for t in (types or [])) + ")"


def _collect_top_vars(fcfg, scope: str) -> list[TopVar]:
    """⊤(top) 으로 평가된 변수 수집(provenance 는 현재 엔진에 없어 None).
    1차: AddressSet/BytesSet.is_top. (interval-top 판별은 후속.)"""
    out: list[TopVar] = []
    seen: set[str] = set()
    for n in fcfg.graph.nodes():
        for name, var in getattr(n, "variables", {}).items():
            if "\x1f" in name:      # (Phase 3b) shadowing 보관 키 — 내부용
                continue
            val = getattr(var, "value", None)
            # AddressSet/BytesSet.is_top 는 bool 속성. interval 의 is_top 은 메서드라
            #   strict True 비교로 오탐 방지(interval-top 판별은 후속).
            if getattr(val, "is_top", None) is True and name not in seen:
                seen.add(name)
                out.append(TopVar(var=name, kind=type(val).__name__, node=n.uid,
                                  src_line=n.src_line if getattr(n, "src_line", None) is not None else -1,
                                  scope=scope, provenance=None))
    return out


# 엔진이 두 어휘를 혼용: 단일 절(_verify_during_clause_dynamic)=success/violation,
#   복합 절(_combine_logic_results)=satisfied/violated. 둘 다 정규화.
_VSTATUS: dict[str, VerdictStatus] = {
    "satisfied": "Satisfied", "success": "Satisfied",
    "violated": "Violated", "violation": "Violated",
    "warning": "Warning",
    "unreachable": "Unreachable",  # A2: 도달불가 노드(BOTTOM env)에 걸린 intent
}


def _extract_verdict(ca) -> list[VerdictItem]:
    """analysis_per_line(intent 검증 기록)을 프론트 VerdictItem 으로 변환.
    엔트리 type ∈ {during_intent, post_intent, during, post}, result=combined_result."""
    out: list[VerdictItem] = []
    apl = getattr(ca, "analysis_per_line", {}) or {}
    for line_no, entries in apl.items():
        for e in entries:
            etype = e.get("type", "")
            if etype not in ("during_intent", "post_intent", "during", "post"):
                continue
            r = e.get("result", {}) or {}
            status = _VSTATUS.get(r.get("status"), "Warning")  # error/pending/unknown → Warning
            det = r.get("details") if isinstance(r.get("details"), dict) else None
            risk = r.get("risk_score")
            if risk is None and det is not None:
                risk = det.get("risk_score")
            kind = "post" if "post" in etype else "during"
            out.append(VerdictItem(
                line=line_no, node="", type=kind, status=status,
                risk=float(risk or 0.0), region=(line_no, line_no),
                message=r.get("message", ""),
                falseRegions=_false_regions_out(det),
            ))
    return out


def _false_regions_out(details: Optional[dict]) -> Optional[dict]:
    """엔진 details.false_regions(내부 snake, [lo,hi] 정수) → 프론트 규약(camel, 경계 문자열).
    셋 다 None 이면 통째로 None(=위반 영역 정보 없음). 경계 문자열화 이유는 VerdictItem 참조."""
    raw = details.get("false_regions") if details else None
    if not isinstance(raw, dict):
        return None

    def _pair(p):
        if not p:
            return None
        return [str(p[0]), str(p[1])]

    lower = _pair(raw.get("lower_violation"))
    upper = _pair(raw.get("upper_violation"))
    overlap = _pair(raw.get("overlap_zone"))
    if lower is None and upper is None and overlap is None:
        return None
    return {"lowerViolation": lower, "upperViolation": upper, "overlapZone": overlap}


def _cfg_to_dict(cfg: CFG) -> dict:
    """CFG dataclass → 프론트 JSON dict. CFGEdge.from_ → "from" 으로 키 교정."""
    d = asdict(cfg)
    d["edges"] = [{"from": e["from_"], "to": e["to"], "kind": e["kind"]} for e in d["edges"]]
    return d


import re as _re

_GLOBAL_ADDR = {"msg.sender", "tx.origin", "block.coinbase"}


def _bits_from(type_name: str) -> Optional[int]:
    """'uint128' → 128, 'int' → None(호출부 기본 256)."""
    digits = "".join(c for c in type_name if c.isdigit())
    return int(digits) if digits else None


def _valuekind_of(typeInfo, class_name: str = "", name: str = ""):
    """변수 타입 → (valueKind, opaque, signed, bits). msg.sender=address, bool=bool,
    (u)int=interval(+signed/bits), 매핑·구조체·배열·enum·bytes=opaque(advanced)."""
    if class_name in ("MappingVariable", "StructVariable", "ArrayVariable"):
        return "array", True, None, None
    if class_name == "EnumVariable":
        return "enum", True, None, None
    cat = getattr(typeInfo, "typeCategory", None)
    et = getattr(typeInfo, "elementaryTypeName", None)
    tbits = getattr(typeInfo, "intTypeLength", None)
    if cat in ("interface", "contract"):          # 인터페이스/컨트랙트 핸들 = 주소 값
        return "address", False, None, None
    if cat in ("mapping", "array", "struct"):
        return "array", True, None, None
    if cat == "enum":
        return "enum", True, None, None
    if et:
        e = et.lower()
        if e.startswith("address"):
            return "address", False, None, None
        if e == "bool":
            return "bool", False, None, None
        if e.startswith("uint"):
            return "interval", False, False, (tbits or _bits_from(e) or 256)
        if e.startswith("int"):
            return "interval", False, True, (tbits or _bits_from(e) or 256)
        if e.startswith("bytes") or e == "string":
            return "bytes", True, None, None  # advanced (값 입력 후순위)
    # typeInfo 없는 글로벌 등 → 이름 기반 폴백
    if name in _GLOBAL_ADDR:
        return "address", False, None, None
    return "interval", False, False, 256  # 기본 수치(uint256)


def _referenced(name: str, text: str) -> bool:
    """변수명이 함수 소스에 단어 경계로 등장하는지(부분문자열 오탐 방지)."""
    if not name or not text:
        return False
    return _re.search(r"\b" + _re.escape(name) + r"\b", text) is not None


# ── 통합 접근-경로 스캐너 (decision #34): compound(mapping/struct/array) + @IReturn 슬롯 도출 ──
#   슬롯 = ROOT + accessor* , key = _ap_label(maximal_expr). node.statements AST 를 정적 순회(재평가 X).
#   evaluator 가 아닌 이유: 재귀 bottom-up 이라 maximal 경로 경계를 모름 + fixpoint 로 여러 번·값 있을 때만 실행.
def _ap_member_base(e):
    b = getattr(e, "base", None)
    return b if b is not None else getattr(e, "expression", None)

def _ap_is_index(e):  return getattr(e, "base", None) is not None and getattr(e, "index", None) is not None
def _ap_is_member(e): return getattr(e, "member", None) is not None and _ap_member_base(e) is not None
def _ap_is_call(e):   return getattr(e, "function", None) is not None
def _ap_is_ident(e):  return getattr(e, "identifier", None) is not None
def _ap_is_access(e): return _ap_is_index(e) or _ap_is_member(e) or _ap_is_call(e)

def _ap_label(e) -> str:
    """접근-경로 → 어노테이션 키 문자열. 메서드 호출 인자는 드롭(`f()`), 캐스트 인자는 유지(`I(a)`)."""
    from Domain.IR import Expression
    if not isinstance(e, Expression):
        return str(e)
    if _ap_is_index(e):
        return f"{_ap_label(e.base)}[{_ap_label(e.index)}]"
    if _ap_is_member(e):
        return f"{_ap_label(_ap_member_base(e))}.{e.member}"
    if _ap_is_call(e):
        fn = e.function
        if _ap_is_member(fn):                       # 메서드 호출 → 인자 드롭 (token.balanceOf())
            return f"{_ap_label(fn)}()"
        args = ",".join(_ap_label(a) for a in (getattr(e, "arguments", None) or []))
        return f"{_ap_label(fn)}({args})"           # 캐스트 등 → 인자 유지 (IERC20(baseToken))
    if _ap_is_ident(e):
        return str(e.identifier)
    if getattr(e, "literal", None) is not None:
        return str(e.literal)
    if getattr(e, "typeName", None) is not None and getattr(e, "expression", None) is not None:
        return f"{e.typeName}({_ap_label(e.expression)})"
    return str(e)

def _ap_spine(e):
    """maximal 접근식 → (root_expr, accessors root→leaf). accessor=('index',None)/('member',name)/('call',None)."""
    accs = []
    cur = e
    while True:
        if _ap_is_index(cur):    accs.append(("index", None)); cur = cur.base
        elif _ap_is_member(cur): accs.append(("member", cur.member)); cur = _ap_member_base(cur)
        elif _ap_is_call(cur):   accs.append(("call", None)); cur = cur.function
        else: break
    accs.reverse()
    return cur, accs

def _ap_side_exprs(e):
    """spine 상의 index 키·call 인자(별도 하위식) 수집 — 중첩 경로 스캔용."""
    out, cur = [], e
    while True:
        if _ap_is_index(cur):
            if cur.index is not None: out.append(cur.index)
            cur = cur.base
        elif _ap_is_member(cur):
            cur = _ap_member_base(cur)
        elif _ap_is_call(cur):
            for a in (getattr(cur, "arguments", None) or []):
                if a is not None: out.append(a)
            cur = cur.function
        else: break
    return out

def _ap_children(e):
    from Domain.IR import Expression
    for a in ("left", "right", "base", "index", "function", "expression",
              "condition", "true_expr", "false_expr", "start_index", "end_index"):
        v = getattr(e, a, None)
        if isinstance(v, Expression): yield v
    for a in ("arguments", "elements"):
        for x in (getattr(e, a, None) or []):
            if isinstance(x, Expression): yield x

def _ap_node_exprs(n):
    for stm in getattr(n, "statements", []) or []:
        for a in ("init_expr", "left", "right", "operand", "function_expr", "return_expr"):
            v = getattr(stm, a, None)
            if v is not None: yield v
        for a in (getattr(stm, "arguments", None) or []):
            if a is not None: yield a
    ce = getattr(n, "condition_expr", None)
    if ce is not None: yield ce


class _Untranslatable(Exception):
    """callee 지역변수처럼 타깃 진입 시점 이름으로 옮길 수 없는 접근식."""


def _parse_label(label: str):
    """슬롯 라벨 → (root, [("member", 이름) | ("index", 텍스트) | ("call", None)]). 괄호 균형으로 자른다."""
    m = _re.match(r"[A-Za-z_$][\w$]*", label)
    if not m:
        return None, []
    root, i, accs = m.group(0), m.end(), []
    while i < len(label):
        c = label[i]
        if c == ".":
            m2 = _re.match(r"[A-Za-z_$][\w$]*", label[i + 1:])
            if not m2:
                return root, accs + [("call", None)]
            accs.append(("member", m2.group(0)))
            i += 1 + m2.end()
        elif c in "[(":
            close = "]" if c == "[" else ")"
            depth, j = 0, i
            while j < len(label):
                if label[j] in "[(":
                    depth += 1
                elif label[j] in "])":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            accs.append(("index", label[i + 1:j]) if close == "]" else ("call", None))
            i = j + 1
        else:
            break
    return root, accs


def _navigate(obj, accs):
    """라벨 접근 경로를 따라 값 모양(타입)을 얻는다 — 키 값과 무관한 원소 하나(가상)로 내려간다."""
    from Domain.Variable import MappingVariable, StructVariable, ArrayVariable
    cur = obj
    for kind, key in accs:
        if cur is None:
            return None
        if kind == "member":
            cur = cur.members.get(key) if isinstance(cur, StructVariable) else None
        elif kind == "index":
            if isinstance(cur, MappingVariable):
                cur = cur._create_value_virtual("0")
            elif isinstance(cur, ArrayVariable):
                try:
                    cur = cur._create_new_array_element(0)
                except Exception:
                    cur = None
            else:
                cur = None
        else:
            return None
    return cur


def _interface_handles(f, interface_names) -> dict:
    """(2026-10-23, M6) 함수 f 의 파라미터·지역변수 중 **인터페이스 타입**(본문 없는 선언)인 것 → 인터페이스 이름.
    `ILpIssuerGovernance vg = ILpIssuerGovernance(addr); vg.delayedStrategyParams(n)` 처럼 지역 핸들로 하는 외부 view
    호출도 @IReturn 입력 대상이다(58_H_02 — SolidityGuardian 케이스가 이렇게 줬다). 컨트랙트 타입은 여기 넣지 않는다
    (IReturn 은 인터페이스 전용 — 2026-10-23 사용자 결정)."""
    out = {}
    def _take(name, ti):
        nm = getattr(ti, "interfaceName", None)
        if name and nm and nm in interface_names:
            out[name] = nm
    for n in f.graph.nodes():
        for st in getattr(n, "statements", []) or []:
            if getattr(st, "statement_type", None) == "variableDeclaration":
                _take(st.var_name, getattr(st, "type_obj", None))
    rv = getattr(f, "related_variables", {}) or {}
    for pn in getattr(f, "parameters", []) or []:
        _take(pn, getattr(rv.get(pn), "typeInfo", None))
    return out


def _declared_locals(f) -> set:
    """함수 안에서 선언된 지역변수 이름 (파라미터 제외)."""
    names = set()
    for n in f.graph.nodes():
        for st in getattr(n, "statements", []) or []:
            if getattr(st, "statement_type", None) == "variableDeclaration":
                names.add(st.var_name)
    return names


def _index_texts(label: str) -> list:
    return [x for k, x in _parse_label(label)[1] if k == "index"]


def _idents_in(text: str) -> set:
    return set(_re.findall(r"(?<![\w$])[A-Za-z_$][\w$]*", text or ""))   # 0x1a 의 x1a 는 식별자 아님


_CAST_CALL = _re.compile(r"(address|payable|bool|string|bytes\d*|u?int\d*|[A-Z]\w*)$")


def _entry_index_ok(text: str, locs: set) -> bool:
    """바꾼 인덱스가 진입 시점에 부작용 없이 평가될 수 있는 식 모양인가 (지역변수·비-형변환 호출·++/대입 금지).
    형 변환은 기본 타입과 대문자로 시작하는 타입 이름(인터페이스·컨트랙트 캐스트) — 최종 순수성 검사는 엔진 Storage.resolve."""
    if _re.search(r"\+\+|--|(?<![=!<>])=(?!=)", text or ""):
        return False
    if _idents_in(text) & locs:
        return False
    for m in _re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", text or ""):
        if not _CAST_CALL.match(m.group(1)):
            return False
    return True


def _all_state_vars(ccf, contract_cfgs) -> dict:
    """타깃 + 상속 조상의 상태변수 (가까운 쪽 우선). 부모(의존성) 상태변수도 타깃 실행 중 읽힌다."""
    out, seen, queue = {}, set(), [ccf] if ccf is not None else []
    while queue:
        c = queue.pop(0)
        if c is None or id(c) in seen:
            continue
        seen.add(id(c))
        svn = getattr(c, "state_variable_node", None)
        for k, v in ((getattr(svn, "variables", {}) if svn is not None else {}) or {}).items():
            out.setdefault(k, v)
        pcs = getattr(c, "parent_cfgs", {}) or {}
        for pn in getattr(c, "parent_contracts", []) or []:
            queue.append(pcs.get(pn) or (contract_cfgs or {}).get(pn))
    return out


def _is_address_this(b) -> bool:
    """`address(this)` — 형 변환 식이든 호출 모양이든."""
    if getattr(b, "typeName", None) == "address":
        return getattr(getattr(b, "expression", None), "identifier", None) == "this"
    fn = getattr(b, "function", None)
    args = getattr(b, "arguments", None) or []
    return getattr(fn, "identifier", None) == "address" and len(args) == 1 and \
        getattr(args[0], "identifier", None) == "this"


def _derive_debug_slots(fcfg, ccf=None, func_src: str = "", func_name: str = "",
                        interface_names=None, contract_cfgs=None, an=None, intent_texts=()) -> list[DebugSlot]:
    """구조적 debug 슬롯 도출(값 이전). 디버그 입력 = **타깃 함수 진입 시점**의 값이므로, 이 함수가 실행 중 읽을 수 있는
    모든 것 — 타깃 본문 + (2026-10-23) 내부 호출·modifier·라이브러리·super/this 로 이어지는 함수들(footprint) — 을 본다.
      Param     = 파라미터(스칼라) · 복합 파라미터의 접근 경로(`accepted.valuation`, `self.balances[account]`)
      StateVar  = 참조되는 상태변수(스칼라·인터페이스 핸들 주소) · 접근 경로(`m[k]`, `s[k].f`) — constant 는 값이 정해져 제외
      GlobalVar = 쓰이는 msg/block/tx · address(this).balance
      IReturn   = view/pure 외부 호출 반환(튜플이면 원소별)
    callee 안의 접근식은 호출 지점 인자로 **타깃 진입 이름으로 번역**한다(`_mint(foreignAsset)` 안의 `pairInfo[asset]` →
    `pairInfo[foreignAsset]`). 엔진도 실행 중 같은 대응을 쓴다(Storage.callee_aliases) — 그래서 이 슬롯 값이 callee 접근에
    쓰인다. 번역할 수 없는 경로(callee 지역변수 기반)는 슬롯이 아니다. 이름이 가리킬 수 있는 callee 는 오버로드까지 전부
    따라간다(과대 근사 — 남는 슬롯은 비워 두면 되지만 빠진 슬롯은 입력할 방법이 없다).
    구조체 값 경로는 멤버 leaf 까지 펼친다."""
    from Domain.IR import Expression
    from Domain.Variable import MappingVariable, StructVariable, ArrayVariable
    interface_names = interface_names or set()
    contract_cfgs = contract_cfgs or {}
    slots: list[DebugSlot] = []

    statevars = {k: v for k, v in _all_state_vars(ccf, contract_cfgs).items() if not getattr(v, "isConstant", False)}
    ext_roots = _external_call_roots(ccf, interface_names, contract_cfgs)
    params = list(getattr(fcfg, "parameters", []) or [])
    param_objs = getattr(fcfg, "related_variables", {}) or {}
    evaluator = getattr(an, "evaluator", None) if an is not None else None
    libs = dict(getattr(an, "library_cfgs", {}) or {}) if an is not None else {}

    compound: dict[str, str] = {}          # label -> "StateVar" | "Param"
    scalars: set[str] = set()              # 참조된 상태변수(스칼라 후보)
    globals_used: set[str] = set()
    ireturn_labels: set[str] = set()
    ireturn_meta: dict[str, tuple[str, str]] = {}

    # ── 프레임: 이 함수의 이름 → (타깃 진입 라벨, 분류). 타깃 = 파라미터 그대로.
    declared_locals = _declared_locals
    target_locals = _declared_locals(fcfg) - set(params)

    def root_class(name, frame):
        sub, locs = frame["sub"], frame["locs"]
        if name in sub:
            return sub[name][1]
        if name in frame.get("ifc", {}):
            return "IReturn"
        if name in locs:
            return None
        if name in ext_roots:
            return "IReturn"
        if name in statevars:
            return "StateVar"
        return None

    def lab(e, frame):
        """접근식 → 타깃 진입 이름의 라벨 (_ap_label 과 같은 모양). 번역 불가면 _Untranslatable."""
        if not isinstance(e, Expression):
            return str(e)
        if _ap_is_index(e):
            return f"{lab(e.base, frame)}[{lab(e.index, frame)}]"
        if _ap_is_member(e):
            return f"{lab(_ap_member_base(e), frame)}.{e.member}"
        if _ap_is_call(e):
            fn = e.function
            if _ap_is_member(fn):
                return f"{lab(fn, frame)}()"
            args = ",".join(lab(a, frame) for a in (getattr(e, "arguments", None) or []))
            return f"{lab(fn, frame)}({args})"
        if _ap_is_ident(e):
            nm = str(e.identifier)
            if nm in frame["sub"]:
                if frame["sub"][nm][0] is None:
                    raise _Untranslatable(nm)
                return frame["sub"][nm][0]
            if frame["strict"] and nm in frame["locs"]:
                raise _Untranslatable(nm)
            return nm
        if getattr(e, "literal", None) is not None:
            return str(e.literal)
        if getattr(e, "typeName", None) is not None and getattr(e, "expression", None) is not None:
            return f"{e.typeName}({lab(e.expression, frame)})"
        if getattr(e, "left", None) is not None and getattr(e, "right", None) is not None:
            return f"{lab(e.left, frame)} {e.operator} {lab(e.right, frame)}"
        return str(e)

    def classify(root, frame):
        if _ap_is_ident(root):
            return root_class(root.identifier, frame)
        tn = getattr(root, "typeName", None)
        if tn and tn in ext_roots:
            return "IReturn"
        return None

    def label_root_name(label):
        return _parse_label(label)[0]

    def scan(e, frame):
        if not isinstance(e, Expression):
            return
        # 전역(msg.sender 등)·address(this).balance
        if _ap_is_member(e):
            b = _ap_member_base(e)
            if getattr(b, "identifier", None) in ("msg", "block", "tx"):
                globals_used.add(f"{b.identifier}.{e.member}")
            elif e.member == "balance" and _is_address_this(b):
                globals_used.add("address(this).balance")
        if _ap_is_ident(e) and e.identifier in statevars and root_class(e.identifier, frame) in ("StateVar", "IReturn"):
            scalars.add(e.identifier)
        if _ap_is_access(e):
            root, accs = _ap_spine(e)
            cls = classify(root, frame)
            has_call = any(k == "call" for k, _ in accs)
            if cls is not None and not (cls != "IReturn" and has_call):
                if cls == "IReturn" and not has_call:
                    # (2026-10-25) 호출 없이 함수 멤버만 참조(`I.f.selector`)는 외부 호출이 아니다 — 컴파일 시점 상수, 슬롯 없음(29_H_08).
                    pass
                elif cls == "IReturn":
                    # IReturn 값은 **호출 지점 원문**(callee 안이면 callee 의 이름)으로 조회된다(ireturn_registry 키) —
                    #   슬롯 키는 원문이라 타깃 이름으로 번역할 필요가 없다(지역 핸들 `vg.f()` 도). 타입은 루트에서 찾는다.
                    raw = _ap_label(e)
                    rn = getattr(root, "identifier", None) if _ap_is_ident(root) else None
                    _tn = (frame.get("ifc", {}).get(rn) if rn else None) or ext_roots.get(rn) \
                        or ext_roots.get(getattr(root, "typeName", None))
                    _fn = next((k for kind, k in accs if kind == "member"), None)
                    ireturn_labels.add(raw)
                    if _tn and _fn:
                        ireturn_meta[raw] = (_tn, _fn)
                else:
                    try:
                        label = lab(e, frame)
                    except _Untranslatable:
                        label = None
                    if label is not None and accs:
                        compound.setdefault(label, cls)
                for side in _ap_side_exprs(e):
                    scan(side, frame)
                if _ap_is_ident(root) and root.identifier in statevars and cls in ("StateVar", "IReturn"):
                    scalars.add(root.identifier)
                if not has_call:
                    return
        if _ap_is_call(e):
            # 호출: 수신자·인자만 따로 본다 — 함수 부분(`x.add`)은 값 접근이 아니다
            follow_call(e, frame)
            fn = e.function
            if _ap_is_member(fn):
                scan(_ap_member_base(fn), frame)
            for a in (getattr(e, "arguments", None) or []):
                scan(a, frame)
            for a in (getattr(e, "named_arguments", None) or {}).values():
                scan(a, frame)
            return
        for c in _ap_children(e):
            scan(c, frame)

    # ── 호출 따라가기 ────────────────────────────────────────────
    visited: set = set()
    queue: list = []

    def lib_of(f):
        for lib in libs.values():
            if any(g is f for _, g in lib.iter_all_functions()):
                return lib
        return None

    def candidates(e, frame):
        """(callee FunctionCFG, 수신자 식 | None) 후보 — 엔진(evaluate_function_call_context)이 고를 수 있는 것 전부."""
        fn = e.function
        out = []
        if _ap_is_ident(fn):
            name = fn.identifier
            owner = frame["lib"]
            if owner is not None and name in owner.functions:
                out += [(g, None) for g in owner.functions[name].values()]
            elif evaluator is not None and ccf is not None:
                ov = evaluator.find_function_overloads_in_hierarchy(ccf, name) or {}
                out += [(g, None) for g in ov.values()]
        elif _ap_is_member(fn):
            base, member = _ap_member_base(fn), fn.member
            bname = getattr(base, "identifier", None)
            if bname == "super" and ccf is not None:
                for pn in getattr(ccf, "parent_contracts", []) or []:
                    pc = (getattr(ccf, "parent_cfgs", {}) or {}).get(pn) or contract_cfgs.get(pn)
                    if pc is not None and evaluator is not None:
                        out += [(g, None) for g in (evaluator.find_function_overloads_in_hierarchy(pc, member) or {}).values()]
            elif bname == "this" and evaluator is not None and ccf is not None:
                out += [(g, None) for g in (evaluator.find_function_overloads_in_hierarchy(ccf, member) or {}).values()]
            elif bname in libs:
                out += [(g, None) for g in (libs[bname].functions.get(member) or {}).values()]
            elif ccf is not None:
                using = [l for ls in (getattr(ccf, "using_libraries", {}) or {}).values() for l in ls]
                using += list(getattr(ccf, "using_all_libraries", []) or [])
                if frame["lib"] is not None:
                    using.append(frame["lib"])
                seen = set()
                for l in using:
                    if id(l) in seen:
                        continue
                    seen.add(id(l))
                    out += [(g, base) for g in (l.functions.get(member) or {}).values()]
        return out

    def bind(callee, recv, args, named, frame):
        ps = list(getattr(callee, "parameters", []) or [])
        exprs = [None] * len(ps)
        off = 0
        if recv is not None and len(ps) == len(args) + len(named) + 1:
            exprs[0], off = recv, 1
        for i, a in enumerate(args):
            if off + i < len(exprs):
                exprs[off + i] = a
        for k, a in named.items():
            if k in ps:
                exprs[ps.index(k)] = a
        sub = {}
        for p, a in zip(ps, exprs):
            if a is None:
                sub[p] = (None, None)
                continue
            try:
                lb = lab(a, frame)
            except _Untranslatable:
                sub[p] = (None, None)
                continue
            r = _ap_spine(a)[0]
            sub[p] = (lb, classify(r, frame) if (_ap_is_ident(r) or getattr(r, "typeName", None)) else None)
        return sub

    def enqueue(f, sub, lib, depth):
        key = (id(f), tuple(sorted((k, v) for k, v in sub.items())))
        if key in visited or depth > 6:
            return
        visited.add(key)
        locs = declared_locals(f) | {p for p, (lb, _c) in sub.items() if lb is None}
        ifc = {k: v for k, v in _interface_handles(f, interface_names).items() if k not in sub}
        queue.append((f, {"sub": sub, "locs": locs, "lib": lib, "strict": depth > 0, "ifc": ifc}, depth))

    def follow_call(e, frame):
        args = list(getattr(e, "arguments", None) or [])
        named = dict(getattr(e, "named_arguments", None) or {})
        for callee, recv in candidates(e, frame):
            if getattr(callee, "graph", None) is None:
                continue
            enqueue(callee, bind(callee, recv, args, named, frame), lib_of(callee), frame_depth[0] + 1)

    def modifiers_of(f, frame, depth):
        for name, margs in (getattr(f, "modifier_invocations", None) or []):
            mod = ccf.get_function_cfg(name) if ccf is not None else None
            if mod is None and an is not None and ccf is not None:
                mod = an._find_in_ancestors(ccf, name)
            if mod is None or getattr(mod, "function_type", None) != "modifier":
                continue
            enqueue(mod, bind(mod, None, list(margs or []), {}, frame), None, depth + 1)

    frame_depth = [0]
    enqueue(fcfg, {p: (p, "Param") for p in params}, lib_of(fcfg), 0)
    while queue:
        f, frame, depth = queue.pop(0)
        frame_depth[0] = depth
        for n in f.graph.nodes():
            for e in _ap_node_exprs(n):
                scan(e, frame)
        modifiers_of(f, frame, depth)

    # ── intent 가 참조하는 진입 값 (2026-10-23, M8) ───────────────────
    #   함수가 읽지 않고 intent 만 참조하는 상태변수도 진입 값이 있어야 한다 — 버그가 "함수가 그 변수를 갱신하지 않는다"
    #   이면 그 변수는 @Post 에만 나온다(SwordCrowdsale `weiRaised(Entry) > weiRaised(Exit)`). 슬롯이 없으면 진입 값이 ⊤ 라
    #   Entry/Exit 가 같은 값이라는 것을 도메인이 모른다. 대상: 상태변수(스칼라·접근 경로)·전역. 지역변수는 입력이 아니다.
    _AP = _re.compile(r"(?<![\w$.])([A-Za-z_]\w*)((?:\[[^\[\]]+\]|\.[A-Za-z_]\w*)*)")
    _ok_index = set(params) | set(statevars) | {"msg", "block", "tx", "this", "address", "true", "false"}
    for text in intent_texts or ():
        t = _re.sub(r"\(\s*(?:Entry|Exit|Before|After)\s*\)", "", text or "")
        if _re.search(r"address\s*\(\s*this\s*\)\s*\.\s*balance", t):
            globals_used.add("address(this).balance")
        for m in _AP.finditer(t):
            root, tail = m.group(1), m.group(2).replace(" ", "")
            if root in ("msg", "block", "tx"):
                mm = _re.match(r"\.([A-Za-z_]\w*)", tail)
                if mm:
                    globals_used.add(f"{root}.{mm.group(1)}")
                continue
            if root not in statevars:
                continue
            if not tail:
                scalars.add(root)
                continue
            if not all(_idents_in(ix) <= _ok_index for ix in _index_texts(root + tail)):
                continue                       # 인덱스가 지역변수 등 — 진입 시점 입력 경로가 아니다
            compound.setdefault(root + tail, "StateVar")

    # ── 슬롯 만들기 ───────────────────────────────────────────────
    def root_obj(label, cls):
        r = label_root_name(label)
        return statevars.get(r) if cls == "StateVar" else param_objs.get(r)

    def emit_path(label, cls, obj, depth=0):
        """경로 슬롯 + 구조체면 멤버 leaf 로 펼침."""
        kind = "StateVar" if cls == "StateVar" else "Param"
        if isinstance(obj, StructVariable) and depth < 3:
            for mname, mobj in obj.members.items():
                emit_path(f"{label}.{mname}", cls, mobj, depth + 1)
        if obj is None:
            vk = ("interval", False, False, 256)
        else:
            vk = _valuekind_of(getattr(obj, "typeInfo", None), type(obj).__name__)
        add(kind, label, vk)

    seen_keys: set = set()

    def add(kind, key, vk):
        if key in seen_keys:
            return
        seen_keys.add(key)
        v, opaque, signed, bits = vk
        idx = _index_texts(key) if kind in ("StateVar", "Param") else []
        slots.append(DebugSlot(kind=kind, key=key, label=key, valueKind=v,
                               opaque=bool(opaque), signed=signed, bits=bits,
                               specializable=bool(idx),
                               requiresIndex=any(_idents_in(t) & target_locals for t in idx)))

    # 1) 파라미터 — 타입대로, 복합이면 멤버 leaf 까지
    for p in params:
        po = param_objs.get(p)
        if isinstance(po, StructVariable):
            emit_path(p, "Param", po)
        else:
            add("Param", p, _valuekind_of(getattr(po, "typeInfo", None), type(po).__name__, p) if po is not None
                else ("interval", False, False, 256))

    # 2) 상태변수 스칼라 — 타깃 본문 텍스트 참조 ∪ footprint 참조. 인터페이스 핸들은 주소 값 슬롯
    compound_roots = {label_root_name(l) for l, c in compound.items() if c == "StateVar"}
    for name, var in statevars.items():
        if name in compound_roots and not isinstance(var, StructVariable) and \
                getattr(getattr(var, "typeInfo", None), "typeCategory", None) in ("mapping", "array"):
            continue
        if not (name in scalars or (func_src and _referenced(name, func_src))):
            continue
        if isinstance(var, StructVariable):
            emit_path(name, "StateVar", var)
            continue
        add("StateVar", name, _valuekind_of(getattr(var, "typeInfo", None), type(var).__name__, name))

    # 3) 접근 경로 슬롯
    for label, cls in compound.items():
        r, accs = _parse_label(label)
        ro = root_obj(label, cls)
        if not isinstance(ro, (StructVariable, MappingVariable, ArrayVariable)):
            continue                    # 스칼라 루트의 멤버(`to.balance` 등)는 저장소 경로가 아니다
        emit_path(label, cls, _navigate(ro, accs))

    # 4) 글로벌 — 기존 등록(usage_sites) ∪ footprint 에서 쓰인 것
    globs = (getattr(ccf, "globals", {}) if ccf is not None else {}) or {}
    for g, gvar in (globs.items() if hasattr(globs, "items") else ((x, None) for x in globs)):
        sites = getattr(gvar, "usage_sites", None)
        if (func_name in sites) if sites else _referenced(g, func_src):
            globals_used.add(g)
    for g in sorted(globals_used):
        gvar = globs.get(g) if hasattr(globs, "get") else None
        add("GlobalVar", g, _valuekind_of(getattr(gvar, "typeInfo", None), type(gvar).__name__, g))

    # 5) @IReturn — 스캔 ∪ registry. 튜플 반환이면 원소별 `[k]`
    _handles = _interface_handles(fcfg, interface_names)
    for key in getattr(fcfg, "ireturn_registry", {}) or {}:
        label = ".".join(str(x) for x in key if x)
        ireturn_labels.add(label)
        if label not in ireturn_meta and len(key) >= 2:      # 입력으로 들어온 키 = (핸들 이름, 함수 이름, 접근 체인)
            tn = ext_roots.get(key[0]) or _handles.get(key[0]) or \
                (getattr(fcfg, "interface_var_types", None) or {}).get(key[0])
            if tn:
                ireturn_meta[label] = (tn, key[1])

    def ireturn_rejected(label: str) -> bool:
        """IReturn 슬롯이 **아닌가**: 받는 쪽 타입(부모 포함)이 그 이름의 view/pure 함수를 선언하지 않았다.
        (2026-10-23, M5) 예전엔 fail-open(못 찾으면 통과)이라 using-for 라이브러리 함수
        `ERC20(token).safeTransfer(..)`(62_H_10) 가 IReturn 슬롯이 됐다 — 그 타입의 멤버가 아니므로 외부 호출이 아니다."""
        fcs = ireturn_cands(label)
        if not fcs:
            return True
        return all(getattr(c, "mutability", None) not in ("view", "pure") for c in fcs)

    def ireturn_cands(label):
        meta = ireturn_meta.get(label)
        if meta is None:
            return []
        tn, fn = meta
        out, seen, todo = [], set(), [tn]
        while todo:                                  # 인터페이스 상속 체인(IVaultGovernance ← ILpIssuerGovernance)
            nm = todo.pop(0)
            if nm in seen:
                continue
            seen.add(nm)
            cfg = contract_cfgs.get(nm)
            if cfg is None:
                continue
            sub = (getattr(cfg, "functions", {}) or {}).get(fn) or {}
            out.extend(sub.values() if isinstance(sub, dict) else [sub])
            todo.extend(getattr(cfg, "parent_contracts", None) or [])
        return out

    def ret_struct_def(t):
        """struct 반환 타입의 정의 — 선언한 인터페이스(structOwner) 기준."""
        if getattr(t, "typeCategory", None) != "struct":
            return None
        owner, name = getattr(t, "structOwner", None), getattr(t, "structTypeName", None) or ""
        cfg = contract_cfgs.get(owner) if owner else None
        return (getattr(cfg, "structDefs", None) or {}).get(name.split(".")[-1]) if cfg is not None else None

    def add_ireturn(label, t, depth=0):
        """(2026-10-23, M6) struct 반환은 멤버 leaf 까지 펼친다 — `p = vg.delayedStrategyParams(n); p.managementFee` 처럼
        반환값을 통째로 받으면 접근식에 멤버가 없어 struct 전체 슬롯 하나만 생겼다(58_H_02). leaf 값 종류는 타입대로
        (예전엔 언제나 interval — 주소 반환도)."""
        sd = ret_struct_def(t)
        if sd is not None and depth < 4 and isinstance(getattr(sd, "members", None), dict):
            for m, mt in sd.members.items():
                add_ireturn(f"{label}.{m}", mt, depth + 1)
            return
        add("IReturn", label, _valuekind_of(t, "", label) if t is not None else ("interval", True, None, None))

    for label in sorted(ireturn_labels):
        if ireturn_rejected(label):
            continue
        cands = ireturn_cands(label)
        arity = max((len(getattr(c, "return_types", []) or []) for c in cands), default=1)
        if arity > 1 and not label.endswith("]"):
            for k in range(arity):
                add("IReturn", f"{label}[{k}]", ("interval", True, None, None))
            continue
        # 반환 타입 → 라벨의 호출 뒤 멤버 체인(`().a.b`)을 따라간 타입. 그 뒤에 또 호출이 있으면 타입을 정하지 않는다.
        rts = list(getattr(cands[0], "return_types", []) or []) if cands else []
        t = rts[0] if len(rts) == 1 else None
        tail = label.split("()", 1)[1] if "()" in label else ""
        if t is not None and _re.fullmatch(r"(?:\.[A-Za-z_]\w*)*", tail):
            for m in [x for x in tail.split(".") if x]:
                sd = ret_struct_def(t)
                t = sd.members.get(m) if sd is not None and isinstance(sd.members, dict) else None
                if t is None:
                    break
        else:
            t = None
        add_ireturn(label, t)
    return slots


def _is_condition_header(code: str) -> bool:
    """body 를 가진 조건 헤더(if/while/for/else-if) — modify 시 in-place(body 보존) 대상.
    require/assert(body 없음)·단순 statement 는 delete+add 로 처리하므로 제외."""
    t = code.strip()
    return ("{" in t) and t.startswith(
        ("if ", "if(", "while ", "while(", "for ", "for(", "else if", "} else if"))


def _op_line(op) -> int:
    if op is None:
        return -1
    return op["startLine"] if isinstance(op, dict) else op.start


def _op_where(op) -> str:
    if op is None:
        return "?"
    if isinstance(op, dict):
        return f"line {op['startLine']} `{op['code'].strip()[:60]}`"
    return f"{op.event} line {op.start} ({op.kind}) `{op.code.strip()[:60]}`"


class IncrementalSurgeryError(RuntimeError):
    """증분 편집(CFG 수술) 실패 또는 미지원 — fail loud (2026-10-22 사용자 결정: 전체 재빌드 폴백 폐기).
    변경은 후보(세션 사본)에만 적용되므로 실패해도 마지막 정상 상태는 그대로다(EngineSession._run_transaction)."""


def _is_risky_op(op) -> bool:
    """증분 수술 **미지원** 편집 — try-catch / do-while(통삭제 전제) + break/continue 삭제·수정.
    (예전엔 full-rebuild 안전망으로 보냈다. 지금은 IncrementalSurgeryError 로 거부한다.)"""
    t = op.code.strip()
    if op.kind in ("break", "continue", "catch"):
        return True
    return t.startswith(("do ", "do{", "try ", "try{", "} catch", "catch"))


def _values_key(debug_values: dict[str, RangeValue], scope: Optional["Scope"] = None) -> str:
    """(타깃 함수, debug 값 묶음) → states 캐시 키. evaluate 가 심고 verify 가 찾는다.
    ★ (Phase 1a) 스코프 포함 — 예전 키는 값만이라, 다른 함수를 같은 값으로 돌린 states 를 재사용했다."""
    sc = (scope.contract, scope.function) if scope is not None else ("", "")
    return repr((sc, sorted((k, tuple(sorted(v.items()))) for k, v in debug_values.items())))


# ─────────────────────────────────────────────────────────────────────────────
# Root A — 타겟 분석 세션 (결정론 코어)
# ─────────────────────────────────────────────────────────────────────────────


class EngineSession:
    """타겟 1개에 대한 분석 세션.

    내부: SolidityAnalyzer 1개 보유(Analyzer/SolidityAnalyzer.py:13) +
          프로젝트 deps 1회 로드(Dependencies.main.load_project_deps — CLI 와 공용).
          debug 적용은 DebugBatchManager(Analyzer/DebugUnitAnalyzer.py:33) snapshot/restore 로 감쌈.
    """

    def __init__(self, deps_root: Optional[str] = None, exclude: tuple[str, ...] = ()) -> None:
        from Analyzer.SolidityAnalyzer import SolidityAnalyzer
        from Analyzer.DebugUnitAnalyzer import DebugBatchManager

        self._sa = SolidityAnalyzer()
        self._ca = self._sa.contract_analyzer
        self._batch = DebugBatchManager(self._ca, self._ca.snapman)
        # states 보존: interpret_function_cfg_for_debug 내부 restore(Engine.py) 직전에
        #   Engine 이 계산된 states fcfg 를 freeze 하도록 게이트 on.
        self._ca._capture_debug_fcfg = True

        self._scope: Optional[Scope] = None
        # (2026-10-28, 6차 검토 C9) overload 지정용 타깃 파라미터 타입. None 이면 이름만으로 고른다
        #   (= 먼저 등록된 overload). `_last_resolution` 에 무엇이 선택됐는지 남는다.
        self._target_param_types: Optional[list[str]] = None
        self._last_resolution: Optional[dict] = None
        self._slots: dict[str, DebugSlot] = {}  # key → slot (evaluate 가 kind 조회)
        self._last_debug_values: dict[str, RangeValue] = {}  # 마지막 적용 debug 값(verify 재해석용)
        self._last_value_errors: list[Diagnostic] = []       # 마지막 debug 값 입력 오류(Fix A)
        self._last_intent_errors: list[Diagnostic] = []      # 마지막 intent 파싱/처리 오류(Fix B, crash 대신)
        self._intents: list[IntentItem] = []                 # 현재 등록된 intent
        # values_version → freeze 된 fcfg (Stage1 states 캐시). 키는 debug 값 직렬화 해시.
        self._states_cache: dict[str, object] = {}
        self._prev_records: list[dict] = []  # B1 증분 diff 기준 (마지막 적용 records)
        # 프로젝트 단위 deps 로드(@IReturn/cross-contract 해소) — CLI(main.load_dependencies)와 같은 로더.
        #   2026-10-22: DepSpec 이름으로 전역 objectfile 에서 선별 로드하던 방식 폐기(전역 공용 트리 삭제).
        #   deps_root 가 없으면 의존성 없이 동작(타깃이 의존성을 참조하면 분석 시점에 명시적 오류).
        self._deps_root = deps_root
        self._deps_exclude = tuple(exclude)
        # (2026-10-22) 에디터의 **현재** 소스 오류. 비어 있지 않으면 엔진 CFG 는 마지막 정상(committed) 버전이고
        #   현재 소스와 다르다 → evaluate/verify 거부. 성공한 업데이트에서 비운다.
        self._editor_errors: list[Diagnostic] = []
        self._load_deps()
        # (2026-10-22) 의존성만 올린 빈 세션 — **직접 바꾸지 않고 사본만 만든다**. 컨트랙트 수준 선언을 지우거나 고친
        #   업데이트는 이 사본 위에서 현재 소스 전체를 새로 분석한다(_run_transaction(fresh=True)).
        self._pristine = (self._sa, self._batch)
        self._sa, self._batch = self._copy_pair(self._pristine)
        self._ca = self._sa.contract_analyzer

    def close(self) -> None:
        """세션 리소스 해제. (현재는 참조 해제만.)"""
        self._states_cache.clear()
        self._scope = None

    # ── 내부 드라이버 (simulate_inputs 의 코드-라인 경로, 콘솔 print 제거) ──────────

    def _apply_code_record(self, code: str, s: int, e: int, ev: str,
                           close_before: bool = False) -> None:
        from Utils.Helper import ParserHelpers
        from Utils.YulHelper import YulParserHelpers
        from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor
        from Analyzer.EnhancedYulVisitor import EnhancedYulVisitor

        # 프론트 event("update") → 엔진("modify") 매핑
        ev = "modify" if ev == "update" else ev
        self._sa.update_code(s, e, code, ev, close_before)

        if not code.strip():
            return
        ctx = self._ca.get_current_context_type()
        if ctx == "assembly":
            tree = YulParserHelpers.generate_parse_tree(code)
            EnhancedYulVisitor(self._ca).visit(tree)
        else:
            tree = ParserHelpers.generate_parse_tree(code, ctx, True)
            EnhancedSolidityVisitor(self._ca).visit(tree)

    def _select_overload(self, ccf, func: str, param_types: Optional[list[str]]):
        """이름이 같은 FunctionCFG 들 중 하나를 고르고, **어떻게 골랐는지**를 함께 돌려준다.

        `Utils/CFG` 의 저장 구조는 `name → {signature → FunctionCFG}` 라 overload 를 이미 구분하지만,
        `get_function_cfg(name)` 을 param_types 없이 부르면 `next(iter(...))` 로 **먼저 등록된 하나**가
        조용히 선택된다. 호출자가 모호성을 감지할 수 있도록 후보 목록을 항상 싣는다."""
        sub = dict((getattr(ccf, "functions", {}) or {}).get(func) or {})
        if len(sub) <= 1:
            return ccf.get_function_cfg(func), {
                "function": func, "signature": next(iter(sub), None),
                "candidates": list(sub), "selected_by": "unique" if sub else "fallback"}
        if param_types is None:
            sig, fcfg = next(iter(sub.items()))
            return fcfg, {"function": func, "signature": sig, "candidates": list(sub),
                          "selected_by": "first_registered_ambiguous"}
        want = _norm_sig(param_types)
        for sig, fcfg in sub.items():
            if _norm_sig(getattr(fcfg, "parameter_types", None) or []) == want:
                return fcfg, {"function": func, "signature": sig, "candidates": list(sub),
                              "selected_by": "param_types"}
        # 타입 어휘가 달라 못 맞춘 경우라도 인자 개수로 유일하게 좁혀지면 그걸로 본다.
        by_arity = [(s, f) for s, f in sub.items()
                    if len(getattr(f, "parameter_types", None) or []) == len(param_types)]
        if len(by_arity) == 1:
            sig, fcfg = by_arity[0]
            return fcfg, {"function": func, "signature": sig, "candidates": list(sub),
                          "selected_by": "arity"}
        raise ValueError(f"target function '{func}{want}' matches no overload — "
                         f"candidates: {', '.join(sub) or '(none)'}")

    def _resolve_fcfg(self, contract: Optional[str], func: Optional[str],
                      param_types: Optional[list[str]] = None):
        """타겟 FunctionCFG 회수. contract/func 미지정 시 단일 후보를 추정.

        ★ (2026-10-28, 6차 검토 C9) 예전엔 이름만 넘겨 overload 중 먼저 등록된 하나가 **조용히**
          선택됐다. 데이터셋 실측(DAppSCAN 옵션 2′): T1 2,602 중 26개 이름에 56개 정의가 몰려 있어
          30개 정의는 이름만으로 도달할 수 없고, 결과가 엉뚱한 시그니처에 귀속된다.
          `param_types`(없으면 세션에 설정된 `_target_param_types`)로 시그니처를 지정할 수 있다."""
        if param_types is None:
            param_types = self._target_param_types
        cfgs = self._ca.contract_cfgs
        # (2026-10-23) 명시한 컨트랙트·함수가 없으면 오류. 예전엔 fcfg=None 을 돌려 evaluate/verify 가 **빈 결과**(노드 0개)를
        #   냈다 — 케이스 메타데이터의 컨트랙트 이름이 틀린 51_H_02·60_H_01 이 오류 없이 판정 None 이었다.
        if contract is not None and contract not in cfgs:
            raise ValueError(f"target contract '{contract}' is not defined in the analyzed source")
        if contract is not None and func is not None:
            ccf0 = cfgs[contract]
            if ccf0.get_function_cfg(func) is None:
                raise ValueError(f"target function '{contract}.{func}' is not defined")
        if contract is None:
            # 의존성으로 로드된 ifc_/lib_/con_ 제외하고, 입력으로 빌드된 컨트랙트 우선.
            candidates = [c for c in cfgs if c not in getattr(self._ca, "interface_names", set())]
            contract = candidates[-1] if candidates else (next(iter(cfgs), None))
        ccf = cfgs.get(contract)
        if ccf is None:
            self._last_resolution = None
            return contract, func, None
        if func is None:
            names = [n for n, _ in ccf.iter_all_functions()] if hasattr(ccf, "iter_all_functions") else list(getattr(ccf, "functions", {}))
            func = names[-1] if names else None
        if not func:
            self._last_resolution = None
            return contract, func, None
        fcfg, res = self._select_overload(ccf, func, param_types)
        self._last_resolution = {"contract": contract, **res}
        return contract, func, fcfg

    def target_resolution(self) -> Optional[dict]:
        """마지막 타깃 해석 기록 — `{contract, function, signature, candidates, selected_by}`.
        `selected_by == "first_registered_ambiguous"` 면 overload 가 여럿인데 이름만으로 골랐다는 뜻이다
        (실험 하네스가 이 경우를 결과에 표시해야 한다)."""
        return self._last_resolution

    def _set_context(self, fcfg, contract: str, func: str) -> None:
        """타겟 함수 컨텍스트 수동 설정 (어노테이션을 소스에 삽입하지 않음 = 라인 밀림 없음)."""
        self._ca.current_target_contract = contract
        self._ca.current_target_function = func
        self._ca.current_target_function_param_types = getattr(fcfg, "parameter_types", None)
        self._ca.current_target_function_cfg = fcfg
        self._ca.current_edit_event = "add"
        body_lines = [n.src_line for n in fcfg.graph.nodes() if getattr(n, "src_line", None)]
        line = max(body_lines) if body_lines else 1
        self._ca.current_start_line = line
        self._sa.current_start_line = line

    def _attach_intents(self, fcfg, intents: list[IntentItem]) -> None:
        """intent 를 live CFG 노드에 등록(파싱→process_during/process_post). 매번 fresh.
        @During = 라인의 노드, @Post = exit 노드. 소스 삽입 없음(컨텍스트 수동)."""
        from Utils.Helper import ParserHelpers
        from Analyzer.EnhancedSolidityVisitor import EnhancedSolidityVisitor

        # 기존 intent 비우기 (노드 + exit)
        for n in fcfg.graph.nodes():
            if hasattr(n, "intents"):
                n.intents = []
        # Fix B: intent 하나의 파싱/처리 실패가 전체 요청을 500 으로 죽이지 않게 per-intent 격리.
        #   (debug 값 경로 _queue_debug_values 와 대칭 — 실패는 diagnostic 으로 수집·skip.)
        errors: list[Diagnostic] = []
        for it in intents:
            kw = "@During" if it.scopeKind == "During" else "@Post"
            if it.line is not None:
                self._ca.current_start_line = it.line
            code = f"{kw} {it.text}"
            try:
                tree = ParserHelpers.generate_parse_tree(code, "intentUnit")
                v = EnhancedSolidityVisitor(self._ca)
                v.intent_target = fcfg          # (2026-10-22) @Post = 이 함수의 EXIT, @During = 이 함수 안의 그 줄 노드
                v.visit(tree)
            except Exception as e:  # 파싱실패(문법위반)·처리실패 모두 흡수
                errors.append(Diagnostic(line=(it.line or 0),
                                         message=f"intent 무시됨 ({kw} {it.text}): {e}"))
        self._last_intent_errors = errors

    def _queue_debug_values(self, debug_values: dict[str, RangeValue]) -> int:
        """debug 값 → 캐논 어노테이션 라인 → batch. 적용 개수 반환.
        빈 경계는 슬롯 타입으로 치환(Fix B), 파싱 실패는 _last_value_errors 에 수집(Fix A, crash 대신)."""
        from Utils.Helper import ParserHelpers
        self._batch.reset()
        applied = 0
        errors: list[Diagnostic] = []
        # (2026-10-23) 디버그 입력은 **동시에** 성립하는 진입 제약이다 — 인덱스 슬롯(`m[i]`)이 위치를 고를 때 선택자(`i`)의
        #   입력값이 이미 놓여 있어야 한다. 접근 경로가 얕은 것부터(`[` 개수) 놓아 입력 순서와 무관하게 만든다.
        for key, val in sorted((debug_values or {}).items(), key=lambda kv: kv[0].count("[")):
            slot = self.match_slot(key)
            if _value_text(val) is None or (val.get("t") == "interval" and not str(val.get("lo", "")).strip()
                                            and not str(val.get("hi", "")).strip()):
                continue                                  # 빈 칸 = 입력 안 함
            if slot is None:
                errors.append(Diagnostic(line=0, message=f"{key}: 이 함수의 디버그 슬롯이 아니다", severity="error"))
                continue
            if slot.key == key and slot.requiresIndex:
                errors.append(Diagnostic(line=0, severity="error",
                                         message=f"{key}: 인덱스가 함수 지역변수라 진입 시점 값이 없다 — 구체 인덱스로 지정"))
                continue
            text, err = _debug_value_text(val, slot)
            if err is not None:
                errors.append(Diagnostic(line=(slot.line if slot and slot.line else 0),
                                         message=f"{key}: {err}", severity="error"))
                continue
            if text is None:
                continue
            kw = _KIND_KW.get(slot.kind if slot else "Param", "@Param")
            line = f"{kw} {key} = {text}"
            # (2026-10-25) 입력 문법으로 읽을 수 없는 키는 그 슬롯의 입력 오류 — 예전엔 파서가 출력만 하고 진행해 visitor 가 죽었다.
            syn = ParserHelpers.annotation_syntax_errors(line)
            if syn:
                errors.append(Diagnostic(line=(slot.line if slot and slot.line else 0), severity="error",
                                         message=f"{key}: 디버그 입력 문법으로 읽을 수 없는 키다 ({'; '.join(syn)})"))
                continue
            self._batch.add(line)
            applied += 1
        self._last_value_errors = errors
        return applied

    def _interpret(self):
        """현재 _last_debug_values + _intents 로 1회 해석(P1) → freeze 된 states fcfg 반환.
        intent 는 노드에 attach 돼 해석 중 inline 검증 → analysis_per_line 에 verdict 기록.
        debug 값이 하나도 없으면 (None, None) — states/verdict 없음(프론트는 auto-fill 유도)."""
        sc = self._scope or Scope(contract="", function="")
        contract, func, fcfg = self._resolve_fcfg(sc.contract or None, sc.function or None)
        if fcfg is None:
            return None, None
        self._scope = Scope(contract=contract or "", function=func or "")
        self._set_context(fcfg, contract, func)
        self._attach_intents(fcfg, self._intents)

        applied = self._queue_debug_values(self._last_debug_values)
        if applied == 0:
            return fcfg, None  # 값 없음 → 해석 안 함

        self._ca._frozen_debug_fcfg = None
        self._batch.flush()  # interpret(P1): states 계산 + inline intent 검증 → (restore 직전 freeze)
        frozen = getattr(self._ca, "_frozen_debug_fcfg", None)
        if frozen is None:
            # fail-loud: freeze 안 됐으면 states 없음 → baseline 으로 덮지 않고 에러.
            raise RuntimeError(
                "interpret: 계산된 states freeze 실패(_frozen_debug_fcfg=None). "
                "_capture_debug_fcfg 게이트 확인.")
        return fcfg, frozen

    # ── 결정론 코어 ──────────────────────────────────────────────────────────

    def set_target_param_types(self, param_types: Optional[list[str]]) -> None:
        """타깃 overload 를 시그니처로 못박는다(6차 검토 C9). `None` 이면 이름만으로 고른다
        (= 먼저 등록된 overload). 세션 상태라 명시적으로 지워야 하므로, 요청마다 타깃을 싣는
        호출자(HTTP 서버·실험 하네스)는 **매 요청 호출**해 이전 타깃의 값이 남지 않게 한다."""
        self._target_param_types = list(param_types) if param_types is not None else None

    def analyze(self, records: list[Record],
                contract: Optional[str] = None, func: Optional[str] = None,
                intents: Optional[list] = None,
                param_types: Optional[list[str]] = None) -> Structure:
        """편집 delta records 를 누적 적용 → CFG 구조 + 빈 debug 슬롯. 값/상태 없음. [코드 편집 트리거]

        입력 모델 (delta fragment, full-slice 아님):
          * records = 최신 편집 delta (Monaco onChange → (startLine,endLine,code,event) 어댑터 산출).
            키 입력 = 작은 fragment, paste = multi-line record 여러 개. 세션이 누적 코드를 보유.
          * 통일 경로: slice_solidity(Dependencies/main.py:53) record 형식과 동일 → update_code 증분 적용.
            = SolQDebug 증분("quick") 강점 유지. full-slice-per-change 는 그 강점을 버리므로 안 함.

        syntax gate (누적 전체 기준):
          * delta 적용 후 *합쳐진 전체 코드*를 ANTLR(Parser/Solidity.g4) 파스. 실패 시
            Structure.diagnostics 채우고 이전 valid 구조 유지(표시만). solc 게이트는 보류(Diagnostic 참고).

        실배선:
          * records 를 update_code(SolidityAnalyzer.py:40) 경로로 적용해 visitor 가 CFG 구성.
          * CFG 구조 추출: line_info[ln]["cfg_nodes"], CFGNode flags/cond_type/edges(Utils/CFG.py).
          * debug_slots 도출(값 이전, 구조적):
              StateVar = 상태변수 선언, Param = 함수 파라미터,
              GlobalVar = static 화이트리스트, IReturn = 인터페이스 호출 statement 등장.
          * 이 단계 nodes.state 는 빈 dict (값은 evaluate 가 채움).
        주의: 어노테이션 라인은 records 에 없음. 슬롯 line 부착점은 CFGNode.src_line 으로 프론트가 유도.
        """
        # fail-loud: 드라이버 에러는 그대로 전파. 유저 코드 syntax 검증은 별도(추후 ANTLR
        #   게이트가 누적 코드 파스 실패를 diagnostics 로 산출 — 버그 숨기는 catch-all 아님).
        def apply(cur):
            for rec in records:
                cur["op"] = rec
                self._apply_code_record(rec["code"], rec["startLine"], rec["endLine"], rec["event"],
                                        rec.get("closeBefore", False))
        if intents is not None:
            self._intents = list(intents)       # (2026-10-23, M8) 슬롯 도출이 intent 가 참조하는 진입 값도 본다
        if param_types is not None:
            self.set_target_param_types(param_types)
        errs = self._run_transaction(apply)
        if errs:
            # 사용자 소스 오류 — 아무것도 반영하지 않는다(committed = 빈 세션). 다음 update 가 [] 와 diff 한다.
            self._editor_errors = errs
            return self.structure(contract, func, errs)
        self._prev_records = list(records)  # B1 증분 diff 기준 (= committed records)
        self._editor_errors = []
        # 코드 변경 → 이전 states 캐시 전부 무효화 (analyze→evaluate→verify 체인 종속).
        self._states_cache.clear()
        return self.structure(contract, func, [])

    def analyze_source(self, source: str, contract: Optional[str] = None,
                       func: Optional[str] = None, intents: Optional[list] = None,
                       param_types: Optional[list[str]] = None) -> Structure:
        """전체 소스 → slice → analyze. 슬라이스 단계의 입력 전제 위반(SourceError)도 진단으로 돌려준다."""
        from Dependencies.main import slice_solidity
        if param_types is not None:
            self.set_target_param_types(param_types)
        try:
            records = slice_solidity(source)
        except SourceError as ex:
            self._editor_errors = [Diagnostic(line=ex.line if ex.line is not None else -1,
                                              message=str(ex), category="semantic")]
            return self.structure(contract, func, self._editor_errors)
        return self.analyze(records, contract=contract, func=func, intents=intents)

    def set_intents(self, intents: list, contract: Optional[str] = None, func: Optional[str] = None) -> Structure:
        """(2026-10-23, M8) intent 만 바뀌었을 때 — CFG 는 그대로, 슬롯만 다시 도출한다(intent 가 참조하는 진입 값 슬롯)."""
        self._intents = list(intents or [])
        return self.structure(contract, func, list(getattr(self, "_editor_errors", []) or []))

    def structure(self, contract: Optional[str] = None, func: Optional[str] = None,
                  diagnostics: Optional[list] = None) -> Structure:
        """현재 세션 상태(fcfg)를 Structure 로 직렬화. analyze/update 후 공용."""
        if diagnostics and contract is not None and contract not in self._ca.contract_cfgs:
            # 소스 오류로 아무것도 반영되지 않은 상태(빈 committed) — 대상이 없는 이유는 진단이 설명한다
            self._scope = Scope(contract=contract, function=func or "")
            return Structure(scope=self._scope, cfg=CFG(entry="ENTRY", exit="EXIT"),
                             debug_slots=[], diagnostics=list(diagnostics))
        contract, func, fcfg = self._resolve_fcfg(contract, func)
        self._scope = Scope(contract=contract or "", function=func or "")
        diags = diagnostics or []
        if fcfg is None:
            return Structure(scope=self._scope, cfg=CFG(entry="ENTRY", exit="EXIT"),
                             debug_slots=[], diagnostics=diags)
        cfg = _serialize_cfg(fcfg, self._ca.recorder, with_states=False,
                             code_lines=self._sa.full_code_lines)
        ccf = self._ca.contract_cfgs.get(contract) if contract else None
        # 이 함수의 소스 텍스트만 슬라이스(상태변수/글로벌 사용 판정용) — 함수 CFG 라인 스팬 기준.
        body_lines = [n.src_line for n in fcfg.graph.nodes() if getattr(n, "src_line", None)]
        lo_ln, hi_ln = (min(body_lines), max(body_lines)) if body_lines else (0, 1 << 30)
        func_src = "\n".join(r.get("code", "") for r in self._prev_records
                             if lo_ln <= r.get("startLine", -1) <= hi_ln)
        slots = _derive_debug_slots(fcfg, ccf, func_src, func or "",
                                    interface_names=getattr(self._ca, "interface_names", None),
                                    contract_cfgs=getattr(self._ca, "contract_cfgs", None), an=self._ca,
                                    intent_texts=[getattr(i, "text", "") for i in (getattr(self, "_intents", None) or [])])
        self._slots = {s.key: s for s in slots}
        self._slot_locals = _declared_locals(fcfg) - set(getattr(fcfg, "parameters", []) or [])
        return Structure(scope=self._scope, cfg=cfg, debug_slots=slots, diagnostics=diags)

    def match_slot(self, key: str) -> Optional[DebugSlot]:
        """디버그 입력 키 → 슬롯. 슬롯 키 그대로이거나, 경로 슬롯의 **인덱스 자리만** 바꾼 키면 그 템플릿 슬롯
        (2026-10-23 사용자 결정 — 진입 시점 이름 허용). 바꾼 인덱스 = 타깃 함수 **진입 시점에 부작용 없이 평가되는 식**:
        리터럴·파라미터·상태변수·전역·형 변환과 그 접근식(`[0]`, `[_token]`, `[address(vaderPairs[0])]`). 지역변수(진입 시점에
        값이 없다)·형 변환이 아닌 호출·`++`/대입은 거부. 의미: 그 식의 **진입 시점 값**이 고르는 위치에 대한 제약이다
        (프로그램이 뒤에 선택자를 바꿔도 제약은 원래 위치에 남는다 — Storage 의 선택자 무효화). 순수성은 값을 놓을 때
        엔진이 실제 식으로 다시 검사한다(Storage.resolve → 부수효과면 오류)."""
        slots = getattr(self, "_slots", {}) or {}
        if key in slots:
            return slots[key]
        root, accs = _parse_label(key)
        if root is None:
            return None
        locs = getattr(self, "_slot_locals", set()) or set()
        for slot in slots.values():
            if not slot.specializable:
                continue
            r2, a2 = _parse_label(slot.key)
            if r2 != root or len(a2) != len(accs):
                continue
            if all(k1 == k2 and (k1 != "member" or x1 == x2) and (k1 != "index" or _entry_index_ok(x1, locs))
                   for (k1, x1), (k2, x2) in zip(accs, a2)):
                return slot
        return None

    # ── 함수 목록(아웃라인) ────────────────────────────────────────────────
    def _function_span(self, fcfg) -> Optional[tuple[int, int]]:
        """함수의 `function ... {` 선언 라인 ~ 닫는 `}` 라인.

        시작 = entry 노드가 등록된 라인(`line_info[ln]["cfg_nodes"]`).
        끝   = **실제 소스 텍스트의 중괄호 균형**으로 찾는다.

        ★ `line_info` 의 open/close 카운트를 쓰면 안 된다: slice 가 `} else {` 의 닫는
          괄호를 `closeBefore` 로 소비해 **어느 라인에도 close 로 기록되지 않는다**
          (실측: `else {` 라인이 open=1, close=0). 그래서 balance 가 1씩 부풀어
          함수 끝을 지나쳐 다음 함수까지 삼켰다. full_code_lines 원문을 세면 정확하다.

        ★★ 예전에는 `full_code_lines` 에 `} else {` 가 `else {` 로 들어있어(닫는 괄호는 record 의
          `closeBefore` 로 이동) 여기서 close 1개를 되돌려줬다. Phase 4b 부터 저장소가 `} else {`
          원문을 보관하므로(`SolidityAnalyzer._stored_text`) 그대로 센다.

        한계: 문자열 리터럴·블록 주석 안의 중괄호는 구분하지 않는다(라인 주석은 제거).
              아웃라인 용도라 허용 가능하며, 어긋나도 최대 span 으로 폴백한다.
        """
        li = self._sa.line_info or {}
        entry = fcfg.entry_node
        start = None
        for ln, info in li.items():
            if entry in (info.get("cfg_nodes") or []):
                start = ln
                break
        if start is None:
            return None
        lines = self._sa.full_code_lines or {}
        max_ln = max(lines.keys()) if lines else start
        bal = 0
        opened = False
        for ln in range(start, max_ln + 1):
            text = lines.get(ln, "").split("//", 1)[0]   # 라인 주석 제거
            bal += text.count("{") - text.count("}")
            if bal > 0:
                opened = True
            elif opened:
                return (start, ln)
        return (start, max_ln)

    def outline(self) -> list[FunctionOutline]:
        """이 소스에 있는 디버그 가능한 함수 전체를 열거.

        `analyze` 는 스코프와 무관하게 record 를 전부 적용하므로(스코프는 `structure()`
        에서만 쓰인다) 세션 하나로 모든 컨트랙트·함수를 열거할 수 있다.

        제외: ① interface 컨트랙트의 함수(본문이 없어 디버그 대상 아님)
              ② 본문 노드가 없는 함수(선언만 있는 경우)
        오버로딩: 프론트 세션 키가 "contract.func" 라 이름당 하나만 낸다(첫 시그니처).
        """
        out: list[FunctionOutline] = []
        iface = set(getattr(self._ca, "interface_names", None) or ())
        lines = self._sa.full_code_lines or {}
        for cname, ccf in (self._ca.contract_cfgs or {}).items():
            if cname in iface:
                continue
            for fname, subs in (getattr(ccf, "functions", {}) or {}).items():
                for sig, fcfg in subs.items():
                    body = [n for n in fcfg.graph.nodes()
                            if getattr(n, "src_line", None) is not None]
                    if not body:
                        continue
                    span = self._function_span(fcfg)
                    if span is None:
                        continue
                    lo, hi = span
                    src = "\n".join(lines.get(ln, "") for ln in range(lo, hi + 1))
                    out.append(FunctionOutline(contract=cname, name=fname, signature=sig,
                                               startLine=lo, endLine=hi, source=src))
                    break  # 이름당 첫 시그니처만
        out.sort(key=lambda f: (f.contract, f.startLine))
        return out

    # ── B1 증분 편집 (§5.6 5단계) ───────────────────────────────────────────
    def update(self, new_source: str,
               contract: Optional[str] = None, func: Optional[str] = None) -> dict:
        """새 풀소스로 증분 갱신. SyntaxValid 게이트 → record-space diff → 변경분만 적용.

        적용 순서 = modify(무이동) → delete(아래부터) → add(위부터) 로 라인 좌표 정합 유지.
        invalid 면 {"ok": False, diagnostics} 반환하고 상태 유지(잘못된 부분삭제 CFG 손상 차단).
        """
        from incremental import diff_records, syntax_valid
        from Dependencies.main import slice_solidity

        ok, diags = syntax_valid(new_source)
        if not ok:
            self._editor_errors = [Diagnostic(line=d["line"], message=d["message"]) for d in diags]
            return {"ok": False, "diagnostics": diags}

        try:
            new_records = slice_solidity(new_source)
        except SourceError as ex:          # 입력 전제 위반(한 입력 = 한 문장) — 소스 오류로 진단
            self._editor_errors = [Diagnostic(line=ex.line if ex.line is not None else -1,
                                              message=str(ex), category="semantic")]
            return {"ok": False, "diagnostics": [asdict(d) for d in self._editor_errors]}
        ops = diff_records(self._prev_records, new_records)
        mods = [o for o in ops if o.event == "modify"]
        dels = sorted((o for o in ops if o.event == "delete"), key=lambda o: o.start, reverse=True)
        adds = sorted((o for o in ops if o.event == "add"), key=lambda o: o.start)

        # (2026-10-22, 사용자 결정) **컨트랙트 수준 선언**(상태변수·struct/멤버·enum/멤버·event·modifier·함수 헤더 등)의
        #   삭제·수정은 증분 수술 대신 현재 소스 전체를 새로 분석한다. 이런 선언은 여러 함수의 해석(변수 환경 주입·
        #   타입)에 쓰이는데, 수술은 CFG 노드만 떼어 내고 등록된 정의는 지우지 않았다(struct P 를 지워도 f(P) 가 옛 P 로
        #   정상 처리됨). 새로 분석하면 지워진 선언을 참조하는 곳이 SourceError 로 드러나고 남는 정의도 없다.
        #   함수 본문 문장 편집과 선언 추가는 증분 그대로.
        if any(self._is_declaration_level(o.start) for o in (dels + mods)):
            def rebuild(cur):
                for rec in new_records:
                    cur["op"] = rec
                    self._apply_code_record(rec["code"], rec["startLine"], rec["endLine"], rec["event"],
                                            rec.get("closeBefore", False))
            errs = self._run_transaction(rebuild, fresh=True)
            if errs:
                self._editor_errors = errs
                return {"ok": False, "diagnostics": [asdict(d) for d in errs]}
            self._prev_records = list(new_records)
            self._editor_errors = []
            self._states_cache.clear()
            return {"ok": True, "diagnostics": []}

        # (2026-10-22) fail loud — 예전엔 미지원 구조(try/do-while/catch/break/continue)는 full-rebuild 로,
        #   수술 중 예외도 full-rebuild 로 덮었다(수술 버그가 결과에 드러나지 않음). 지금은 둘 다 오류다.
        #   미지원 판정은 수술 시작 **전**에 하므로 이 경우 세션 상태는 그대로다.
        risky = [o for o in (dels + mods) if _is_risky_op(o)]
        if risky:
            o = risky[0]
            raise IncrementalSurgeryError(
                f"증분 편집 미지원: {o.event} line {o.start} ({o.kind}) `{o.code.strip()[:60]}` — "
                f"이 구조의 CFG 수술은 아직 구현되지 않았다 (세션 상태는 변경 전 그대로)")
        def apply(cur):
            for o in mods:
                cur["op"] = o; self._apply_modify(o)
            for o in dels:
                cur["op"] = o; self._apply_delete(o.start, o.end, o.kind)
            for o in adds:
                cur["op"] = o; self._apply_code_record(o.code, o.start, o.end, "add", o.close_before)
        errs = self._run_transaction(apply)
        if errs:
            self._editor_errors = errs
            return {"ok": False, "diagnostics": [asdict(d) for d in errs]}
        self._prev_records = list(new_records)      # committed records 는 성공했을 때만 갱신
        self._editor_errors = []
        self._states_cache.clear()
        return {"ok": True, "diagnostics": []}

    # ── (2026-10-22) 업데이트 트랜잭션 ─────────────────────────────────────────
    #   한 번의 analyze/update 가 만드는 연산 전체를 **후보(세션 사본)** 에 적용하고, 전부 성공하면 후보로 교체한다.
    #   실패하면 후보를 버린다 → 마지막 정상(committed) 상태는 한 번도 바뀌지 않는다. 그래서
    #     · 사용자 소스 오류(SourceError) = 진단으로 돌려주고 세션은 계속 쓴다(편집 중 흔한 상태).
    #     · 그 밖의 예외 = 엔진 내부 실패 — 숨기지 않고 올린다(전체 재빌드로 덮지 않는다). 세션은 오염되지 않는다.
    #   사본은 deepcopy 이되 의존성 CFG(로드 후 불변)는 공유한다 — 측정상 6~10ms (의존성까지 복사하면 수백 ms).
    _DECL_BLOCKS = ("contract", "abstract contract", "interface", "library", "struct", "enum")

    def _is_declaration_level(self, line: int) -> bool:
        """committed 소스에서 line 이 함수 본문 밖(파일·컨트랙트·struct·enum 블록 직속)인가."""
        ln = self._ca._enclosing_open_brace_line(line, inclusive=False)
        if ln is None:
            return True
        ctx = self._ca.determine_top_level_context(self._sa.full_code_lines.get(ln, ""))
        return ctx in self._DECL_BLOCKS

    @staticmethod
    def _copy_pair(pair):
        """(SolidityAnalyzer, DebugBatchManager) deepcopy — 의존성 CFG(로드 후 불변, from_dependency)는 공유."""
        import copy
        ca = pair[0].contract_analyzer
        shared = {}
        for v in list(ca.contract_cfgs.values()) + list(ca.library_cfgs.values()):
            if getattr(v, "from_dependency", False):
                shared[id(v)] = v
        return copy.deepcopy(pair, shared)

    def _run_transaction(self, apply_fn, fresh: bool = False) -> list:
        """fresh=False: 현재(committed) 세션 사본에 증분 적용. fresh=True: 의존성만 올린 빈 세션 사본에서 시작
        (컨트랙트 수준 선언의 삭제·수정 — 그 선언을 참조하던 곳까지 새로 해석해야 하므로 현재 소스 전체를 다시 분석)."""
        committed = (self._sa, self._ca, self._batch)
        cand_sa, cand_batch = self._copy_pair(self._pristine if fresh else (self._sa, self._batch))
        self._sa, self._ca, self._batch = cand_sa, cand_sa.contract_analyzer, cand_batch
        cur: dict = {"op": None}
        try:
            apply_fn(cur)
        except SourceError as ex:
            self._sa, self._ca, self._batch = committed
            return [Diagnostic(line=ex.line if ex.line is not None else _op_line(cur["op"]),
                               message=str(ex), category="semantic")]
        except Exception as ex:
            self._sa, self._ca, self._batch = committed
            raise IncrementalSurgeryError(
                f"증분 분석 실패: {_op_where(cur['op'])}: {type(ex).__name__}: {ex} "
                f"— 이 변경은 반영되지 않았다(직전 정상 상태 유지)") from ex
        return []

    def _load_deps(self) -> None:
        if self._deps_root is not None:
            from Dependencies.main import load_project_deps
            load_project_deps(self._sa, self._ca, self._deps_root, self._deps_exclude, verbose=False)

    def _apply_delete(self, start: int, end: int, kind: str) -> None:
        """CFG surgery(delete_cfg_range, line_info 사용) → 소스 라인 장부(update_code delete) 순.

        ★ 수술 대상 fcfg 는 반드시 현재 타겟 스코프로 지정. 생략하면 _resolve_fcfg 폴백이
          "컨트랙트의 마지막 함수"를 집어 **엉뚱한 CFG 를 수술**한다 → 타겟 함수엔 지운 문장
          노드가 그대로 남고(유령 노드) 소스 장부만 줄어 소스/CFG 가 어긋난다.
        """
        sc = self._scope or Scope(contract="", function="")
        _, _, fcfg = self._resolve_fcfg(sc.contract or None, sc.function or None)
        if fcfg is not None:
            self._ca.builder.delete_cfg_range(fcfg=fcfg, start_line=start, kind=kind)
        self._sa.update_code(start, end, "", "delete")

    def _apply_modify(self, op) -> None:
        """조건 헤더 변경 = in-place(body 보존); 단순 statement = delete+add."""
        if _is_condition_header(op.code):
            # update_code(modify)+visitor → process_* modify 분기에서 condition_expr in-place swap
            self._apply_code_record(op.code, op.start, op.end, "modify", op.close_before)
        else:
            self._apply_delete(op.start, op.end, op.kind)
            self._apply_code_record(op.code, op.start, op.end, "add", op.close_before)

    def evaluate(self, debug_values: dict[SlotKey, RangeValue],
                 contract: Optional[str] = None, func: Optional[str] = None) -> CfgWithStates:
        """debug 값 seed → fixpoint → CFG-with-states. [값 변경 트리거]

        실배선:
          * debug_values 를 캐논 텍스트로 직렬화(serialize.ts 형식) 후
            process_*_for_debug(ContractAnalyzer.py:2710~2876)로 등록.
          * snapshot → Engine.fixpoint(Interpreter/Engine.py:409) → states 가 CFGNode 에 채워짐.
          * ★ DebugBatchManager.flush(restore) 로 값 초기화되기 **직전**에
            CFGNode.variables/before_envs(Utils/CFG.py:47-48)를
            RecordManager 직렬화(Analyzer/RecordManager.py:292-325)로 떠서 CfgWithStates 로 복사.
          * 반환은 불변 스냅샷. 엔진 객체는 리셋된 채 다음 run 대기.
          * top_vars: ⊤(top) 으로 평가된 변수 수집(provenance 는 현재 None).
        """
        self._require_valid_source()
        if contract or func:
            sc = self._scope or Scope(contract="", function="")
            self._scope = Scope(contract=contract or sc.contract, function=func or sc.function)
        self._last_debug_values = debug_values

        fcfg, frozen = self._interpret()
        # 값 입력 오류(Fix A) + intent 파싱 오류(Fix B) — 모든 반환 경로에 부착
        diags = list(self._last_value_errors) + list(self._last_intent_errors)
        if fcfg is None:
            return CfgWithStates(scope=self._scope or Scope("", ""),
                                 cfg=CFG(entry="ENTRY", exit="EXIT"), diagnostics=diags)
        if frozen is None:
            # 값 없음(또는 전부 미입력/오류) → 구조만. 프론트는 슬롯 입력/오류표시.
            cfg = _serialize_cfg(fcfg, self._ca.recorder, with_states=False,
                             code_lines=self._sa.full_code_lines)
            return CfgWithStates(scope=self._scope, cfg=cfg, top_vars=[], diagnostics=diags)

        self._states_cache[_values_key(debug_values, self._scope)] = frozen

        # ★ N4. callee 상태(S1 포착) → callee CFG 맵 + 노드별 CalleeRef.
        #   `fcfg`(live)로 호출 지점을 스캔하고, `frozen`(states 스냅샷)으로 CFG 를 직렬화한다.
        _ccf = (self._ca.contract_cfgs or {}).get(self._scope.contract)
        _ext = _external_call_roots(_ccf, getattr(self._ca, "interface_names", None),
                                    getattr(self._ca, "contract_cfgs", None))
        callee_cfgs, node_callees = _build_callees(frozen, self._ca, _ext)
        cfg = _serialize_cfg(frozen, self._ca.recorder, with_states=True,
                             code_lines=self._sa.full_code_lines,
                             node_callees=node_callees)
        top_vars = _collect_top_vars(frozen, scope=self._scope.function)
        return CfgWithStates(scope=self._scope, cfg=cfg, callees=callee_cfgs,
                             top_vars=top_vars, diagnostics=diags)

    def _require_valid_source(self) -> None:
        """(2026-10-22) 에디터의 현재 소스에 오류가 있으면 디버깅 거부 — 엔진 CFG 는 마지막 정상 버전이라
        현재 코드에 대한 결과가 아니다."""
        if self._editor_errors:
            first = self._editor_errors[0]
            raise SourceError(f"현재 코드에 오류가 있어 디버깅할 수 없음 — L{first.line}: {first.message}",
                              line=first.line)

    def verify(self, intents: list[IntentItem],
               contract: Optional[str] = None, func: Optional[str] = None,
               debug_values: Optional[dict[str, RangeValue]] = None) -> list[VerdictItem]:
        """intent 판정 → verdict. [intent 변경 트리거]

        Stage 1(inline verify): intent 를 노드에 등록 후 debug 값으로 재해석 →
          해석 중 inline 검증이 analysis_per_line 에 verdict 기록 → 그걸 VerdictItem 으로 변환.
          (Stage 2 에서 보존 states 에 guardian 사후 호출하는 standalone P2 로 재해석 제거 예정.)

        소프트 게이트(결정 22): debug 값이 없으면 verdict 없음([]) — 프론트가 auto-fill 유도.

        ★ 세션은 "소스의 캐시"일 뿐 클라이언트 상태의 주인이 아니다: debug 값은 항상 호출자가
          싣고 온 것(debug_values)을 쓴다. 서버 기억(_last_debug_values)에 기대면, 값을 persist
          하지 않는 프론트(세션성, decision #32)가 재접속했을 때 **이전 생애의 유령 값**으로
          verdict 가 나와 위 소프트 게이트가 무력화된다.
        """
        self._require_valid_source()
        if contract or func:
            sc = self._scope or Scope(contract="", function="")
            self._scope = Scope(contract=contract or sc.contract, function=func or sc.function)
        if debug_values is not None:
            self._last_debug_values = debug_values
        self._intents = intents or []

        if not self._last_debug_values:
            return []  # debug 컨텍스트 없음 → auto-fill 유도

        # Stage 2 standalone P2: 이번 값으로 계산된 보존 states 가 있으면 재해석 없이 사후 검증.
        #   (직전 evaluate 의 키가 아니라 "이번 요청 값"의 키로 조회 — Apply 흐름에선 동일 키라 적중.)
        frozen = self._states_cache.get(_values_key(self._last_debug_values, self._scope))
        if frozen is not None:
            return self._verify_standalone(frozen, self._intents)

        # fallback(Stage 1 inline): states 캐시 없으면 전체 재해석.
        self._ca.analysis_per_line.clear()
        _, frozen = self._interpret()
        if frozen is None:
            return []
        return _extract_verdict(self._ca)

    def _verify_standalone(self, frozen_fcfg, intents: list[IntentItem]) -> list[VerdictItem]:
        """Stage 2: 보존 states(frozen_fcfg)에 guardian 사후 호출 → **재해석 없이** verdict.
        intent 파싱은 live fcfg 에 attach(파서 재사용) 후 node 이름으로 frozen 에 미러링.
        frozen states 는 읽기만(불변) → 캐시 재사용 유효."""
        sc = self._scope or Scope(contract="", function="")
        _, _, live_fcfg = self._resolve_fcfg(sc.contract or None, sc.function or None)
        if live_fcfg is None:
            return []
        self._set_context(live_fcfg, sc.contract, sc.function)
        self._attach_intents(live_fcfg, intents)

        # live node.intents → frozen node.intents (uid 매칭 — frozen 은 live 의 pickle 사본이라 uid 가 같다.
        #   예전 이름 매칭은 줄 이동으로 이름이 중복되면 intent 를 다른 노드에 붙였다). exit 노드(post) 포함.
        live_by_uid = {n.uid: list(getattr(n, "intents", []) or [])
                       for n in live_fcfg.graph.nodes()}
        for fn in frozen_fcfg.graph.nodes():
            fn.intents = list(live_by_uid.get(fn.uid, []))

        engine = self._ca.engine
        prev = getattr(self._ca, "current_target_function_cfg", None)
        self._ca.current_target_function_cfg = frozen_fcfg
        engine._record_enabled = True
        self._ca.analysis_per_line.clear()
        try:
            for node in frozen_fcfg.graph.nodes():
                if getattr(node, "intents", None):
                    engine._process_node_intents(node, node.variables, node.src_line)
            engine._process_post_annotations(frozen_fcfg)
        finally:
            self._ca.current_target_function_cfg = prev
        return _extract_verdict(self._ca)


# ─────────────────────────────────────────────────────────────────────────────
# 결합 export — send_report_to_front(print) 대체. 프론트 AnalysisResult 계약으로 직렬화.
# ─────────────────────────────────────────────────────────────────────────────


class AnalysisResultDict(TypedDict):
    """frontend/src/types/result.ts AnalysisResult 와 동일. analyzeClient 가 그대로 소비."""
    scope: dict
    cfg: dict
    verdict: list[dict]
    top_vars: list[dict]
    intents: list[dict]
    debugSlots: list[dict]


def to_analysis_result(
    structure: Structure,
    states: CfgWithStates,
    verdict: list[VerdictItem],
    intents: list[IntentItem],
) -> AnalysisResultDict:
    """3단계 산출 + intent 를 프론트 1개 객체로 합성(states.cfg 를 navigation substrate 로 사용).

    ContractAnalyzer.send_report_to_front(print만, ContractAnalyzer.py:2934)를 대체하는 출력층.
    """
    # states 가 있으면 그 cfg(=navigation substrate, states 채워짐), 없으면 구조 cfg.
    cfg = states.cfg if (states and states.cfg.nodes) else structure.cfg
    scope = (states.scope if states else None) or structure.scope
    top_vars = states.top_vars if states else []
    return {
        "scope": asdict(scope),
        "cfg": _cfg_to_dict(cfg),
        # N4: Step Into 대상 callee 들의 CFG. 키 = CFGNode.callees[].key
        "callees": {k: _cfg_to_dict(v) for k, v in ((states.callees if states else None) or {}).items()},
        "verdict": [asdict(v) for v in verdict],
        "top_vars": [asdict(t) for t in top_vars],
        "intents": [asdict(i) for i in intents],
        "debugSlots": [asdict(s) for s in structure.debug_slots],
    }


def analyze_once(
    records: list[Record],
    debug_values: Optional[dict[SlotKey, RangeValue]] = None,
    intents: Optional[list[IntentItem]] = None,
    deps_root: Optional[str] = None,
    exclude: tuple[str, ...] = (),
) -> AnalysisResultDict:
    """세션 없이 fresh 1회 통짜 분석(L2 실험·격리 실행용). [인터랙티브는 3콜, 실험은 이 1콜]

    내부: EngineSession(deps_root, exclude) 만들어 analyze → evaluate → verify → to_analysis_result 후 close.
    인터랙티브 캐시 계층(dirty 신호)이 필요 없는 배치/격리 시나리오 전용.
    """
    sess = EngineSession(deps_root, exclude)
    try:
        structure = sess.analyze(records)
        states = sess.evaluate(debug_values or {})
        verdict = sess.verify(intents or [])
        return to_analysis_result(structure, states, verdict, intents or [])
    finally:
        sess.close()


# ─────────────────────────────────────────────────────────────────────────────
# 생성 보조 (solver/LLM) — 표면 분리, 내부 generator 공유. async/느림. L2 originality.
# ─────────────────────────────────────────────────────────────────────────────


def suggest_value(session: EngineSession, slot: DebugSlot) -> Suggestion:
    """opaque 슬롯(@IReturn 등)에 그럴듯한 값 제안. target 없는 단일-슬롯 생성.

    derivable → solver, opaque(외부값) → LLM. 무엇이 나은지는 L2 실험이 결정(backend-agnostic).
    """
    raise NotImplementedError("suggest_value")


def reach(session: EngineSession, target: Union[str, IntentItem]) -> dict[SlotKey, RangeValue]:
    """특정 path(노드 id) 또는 intent 가 걸린 path 를 live(비-⊥)로 만드는 debug 값 합성.

    = target 있는 값 생성(witness). path condition 만족 = solver 주도(derivable),
      외부값 결부 시 LLM 보조. suggest_value 와 generator 공유, 목표만 다름.
    """
    raise NotImplementedError("reach")


def cover(session: EngineSession, scope: Optional[Scope] = None) -> list[dict[SlotKey, RangeValue]]:
    """전 분기를 커버하는 debug 입력 집합 생성. reach 를 분기마다 반복. [후순위]"""
    raise NotImplementedError("cover")


def suggest_intent(
    source: str,
    comments: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> list[IntentItem]:
    """변수 관계 + 자연어 주석 + 알려진 numeric-logic-error 취약점 패턴 → intent 후보 제안.

    입력이 코드 records 가 아니라 **원문 소스(주석 포함)** + 패턴 DB 라서 코어와 분리된 별 surface.
    LLM 주도.
    """
    raise NotImplementedError("suggest_intent")


# ─────────────────────────────────────────────────────────────────────────────
# Root B — Dependency 관리 (생애주기: 첨부 1회 빌드 → pkl 캐시 영속)
# ─────────────────────────────────────────────────────────────────────────────


class DependencyStore:
    """첨부 .sol 의존성을 slice → analyze_file → pkl 로 빌드/캐시.

    실배선: Dependencies/main.py slice_solidity(:53), analyze_file(→ ifc_/lib_/con_*.pkl).
            임의 첨부 순서 해석엔 자동 topo-sort 필요(현재 _con_order 하드코딩, 미구현).
    """

    def build(self, name: str, source: str) -> DepInfo:
        """.sol 원문 → slice → analyze_file → ifc_/lib_/con_*.pkl 저장. building → ready."""
        raise NotImplementedError("DependencyStore.build")

    def list(self) -> list[DepInfo]:
        """빌드된 의존성 목록."""
        raise NotImplementedError("DependencyStore.list")

    def remove(self, name: str) -> None:
        """의존성 pkl 제거."""
        raise NotImplementedError("DependencyStore.remove")
