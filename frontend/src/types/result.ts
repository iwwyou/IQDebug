// engine_api 출력 스키마 (mock·실엔진 공통 계약)
// 이 타입들의 합집합 = engine_api `analyze()`가 반환해야 하는 것.

export type NodeFlag =
  | 'condition'
  | 'branch_then'
  | 'branch_else'
  | 'merge'
  | 'fixpoint_evaluation' // loop fixpoint 노드
  | 'loop_exit'
  | 'loop_body'
  | 'loop_init'  // (K1) for 헤더의 init 블록 — 효과는 for_cond 의 IN 에 포함
  | 'loop_incr'  // (K1) for 헤더의 incr 블록 — back edge 쪽
  | 'exit'
  | 'call'    // N4: 이 노드에 함수 호출이 있다
  | 'opaque'  // N4: 그 호출로는 들어갈 수 없다 (외부 호출)

export type EdgeKind = 'normal' | 'true' | 'false' | 'back_edge'

/** (K3) 루프 탈출 경로의 종류. */
export type LoopExitKind = 'normal' | 'break' | 'return' | 'revert' | 'other'

/** (K3) 루프 헤더에서 이 루프가 끝날 수 있는 한 가지 경로. */
export interface LoopExit {
  kind: LoopExitKind
  /** 이 경로를 만든 노드 uid (루프 조건 / break 문 / return 문) */
  from_: string
  /** 도착 노드 uid — loop exit 노드, 또는 RETURN/ERROR sink */
  to: string
  /** false = 이 경로는 ⊥ 다(엔진이 join 에서 제외). 후보로 제시하면 안 된다. */
  feasible: boolean
  /** 그 경로**만**의 상태 (join 되기 전) */
  state: Record<string, string>
}

export interface CFGNode {
  /** 노드 uid — 생성 시 한 번 정해지고 증분 편집(줄 이동)에도 바뀌지 않는다 (2026-10-22). */
  id: string
  /** 엔진 노드 이름(`Assign_4`) — 사람이 읽는 라벨일 뿐 식별자가 아니다(줄 이동 시 중복 가능). */
  name?: string
  src_line: number
  /** 화면 표시·breakpoint 줄. if/else-if join 은 체인을 닫는 `}` 줄, 그 외는 src_line (U3). */
  display_line: number
  flags: NodeFlag[]
  cond_type?: string // if | else_if | require | while | for | do_while | try | catch
  /** 실행 **후**(OUT) 상태: 변수명 → 직렬화된 추상값 ("[lo,hi]" | "address(⊤)" | "top" | ...) */
  state: Record<string, string>
  /**
   * 실행 **전**(IN) 상태 — 커서의 기본 표시("이 줄 실행 직전"). join·분기 노드는 state 와 같다.
   * 루프 헤더는 루프 밖에서 들어오는 상태(back-edge 제외). 루프 본문 문장은 비어 있다(루프에 들어가지 않음).
   */
  in_state: Record<string, string>
  /** merge 노드면 join에 기여한 predecessor 노드 id들 (backward 양쪽 하이라이트용) */
  join_from?: string[]
  /**
   * 루프 헤더 노드면 "루프 진입 전 ↔ 고정점 수렴 후 값이 **바뀐** 변수"만.
   * 루프 안에서 새로 선언된 변수는 포함되지 않는다(진입 전에 없었으므로).
   * decision #4 가 iteration 별 stepping 을 금지하므로, step forward 는 루프를 통째로
   * 접고(N2) 그 효과를 이 delta 하나로 보여준다.
   */
  loop_delta?: Record<string, string>
  /**
   * (K3) 루프 헤더 노드면 이 루프가 **끝날 수 있는 경로들**.
   * 루프 본문은 탐색에서 블랙박스이므로(양방향 진입 금지) 헤더가 탈출 경로의 유일한 요약 지점이다.
   * `state` 는 그 경로만의 환경 — loop-exit 노드의 `in_state` 는 모든 경로의 join 이라
   * "조건 실패로 탈출" 과 "break 로 탈출" 을 구별할 수 없다.
   */
  loop_exits?: LoopExit[]
  /** N4: 이 노드의 호출들. enterable 한 것은 `AnalysisResult.callees[key]` 에 CFG 가 있다. */
  callees?: CalleeRef[]
}

/** N4: 이 노드에서 일어나는 함수 호출 하나. */
export interface CalleeRef {
  /** `AnalysisResult.callees` 의 키 = "<호출노드>#<컨트랙트>.<함수>". opaque 면 빈 문자열. */
  key: string
  /** "Math.mulDiv" — UI 표시용 */
  label: string
  /** false = opaque. 외부 호출은 전부 false (callee 코드가 타입으로 정해지지 않음) */
  enterable: boolean
  contract?: string | null
  name?: string | null
  /** enterable=false 인 이유. 화면에 그대로 띄운다. */
  reason?: string | null
  /**
   * callee 소스 범위. 같은 파일 callee 는 `store.functions` 로도 찾을 수 있고,
   * 의존성 pkl callee 는 **그 dep 파일 기준** 라인이라 `depContents` 에 맞다.
   */
  start_line?: number | null
  end_line?: number | null
}

export interface CFGEdge {
  from: string
  to: string
  kind: EdgeKind
}

export interface CFG {
  entry: string
  exit: string
  return_exit?: string
  error_exit?: string
  nodes: CFGNode[]
  edges: CFGEdge[]
}

export type Verdict = 'Satisfied' | 'Warning' | 'Violated' | 'Unreachable'

// 위반 영역. **출력공간**(비교되는 값의 구간)이지 입력 파라미터 구간이 아니다 —
// 입력공간 역전파/분할은 decision #40 (B) 몫. warning·단일 절(during)일 때만 존재.
// 경계는 2^256 까지 가능 → 문자열(엔진 IntervalValue 와 동일 규약).
export interface FalseRegions {
  lowerViolation: [string, string] | null // intent 하한 아래로 벗어난 구간
  upperViolation: [string, string] | null // intent 상한 위로 벗어난 구간
  overlapZone: [string, string] | null // 겹치는 불확정 구간
}

export interface VerdictItem {
  line: number
  node: string
  type: string
  status: Verdict
  risk: number // 0..10
  region: [number, number]
  message: string
  falseRegions?: FalseRegions | null // 없으면 undefined/null
}

export interface TopVar {
  var: string
  kind: string
  node: string
  src_line: number
  scope: string
  provenance: string | null // ⊤ 출처 태깅 (현재 엔진엔 없음 → null)
}

export type DebugKind = 'StateVar' | 'Param' | 'GlobalVar' | 'IReturn'

// 값 입력 종류 — 슬롯의 타입이 결정 (numeric→interval, bool→bool, address→symbolicAddress)
export type ValueKind = 'interval' | 'bool' | 'address' | 'array' | 'bytes' | 'enum'

// 사용자가 입력하는 값 (debugValue 캐논으로 직렬화됨)
export type RangeValue =
  | { t: 'interval'; lo: string; hi: string }
  | { t: 'bool'; v: 'true' | 'false' | 'any' }
  | { t: 'address'; id: number } // symbolicAddress N
  | { t: 'none' }

export interface Suggestion {
  value: RangeValue
  backend: 'llm' | 'solver' | 'hybrid' // 2번 결정 보류 → 추상화
  confidence: number
}

// ★ 분석이 주는 "debuggable slot" — 사용자는 값만 채움.
//   엔진이 상태변수/파라미터/글로벌/인터페이스호출을 알고 슬롯 생성.
export interface DebugSlot {
  kind: DebugKind
  key: string // 캐논 타겟: "balance" | "a" | "msg.sender" | "token.balanceOf()"
  label: string // 표시용
  valueKind: ValueKind
  opaque?: boolean // @IReturn/외부 등 도출 불가
  line?: number // debug는 대체로 함수-스코프 → 보통 생략
  suggestion?: Suggestion // ghost 제안 (LLM/solver)
  signed?: boolean | null // interval 빈 경계 치환용 (엔진 Fix B)
  bits?: number | null
  /** (2026-10-23) 인덱스 자리가 있는 경로 — 구체 인덱스로 바꾼 키(`poolInfo[1]`)로도 입력 가능 */
  specializable?: boolean
  /** 인덱스가 함수 지역변수(루프 i 등) — 진입 시점 값이 없어 이 키 그대로는 입력 불가, 구체 인덱스로만 */
  requiresIndex?: boolean
}

export type DepStatus = 'ready' | 'building' | 'error'

export interface DepItem {
  name: string
  kind: 'interface' | 'library' | 'contract'
  status: DepStatus
}

export interface IntentItem {
  scopeKind: 'During' | 'Post'
  line?: number
  text: string
}

export interface AnalysisResult {
  scope: { contract: string; function: string }
  cfg: CFG
  /**
   * N4: Step Into 대상 callee 들의 CFG(상태 포함). 키 = `CalleeRef.key`.
   * ★ **호출 지점별로 분리**된다 — 같은 callee 를 두 곳에서 부르면 별개 엔트리다
   *   (엔진은 함수당 FunctionCFG 가 하나이고 복귀 시 원복하므로, 호출 지점마다 따로 떠낸다).
   */
  callees?: Record<string, CFG>
  verdict: VerdictItem[]
  top_vars: TopVar[]
  intents: IntentItem[]
  /** 분석이 노출하는 debuggable slots (사용자는 값만 채움) */
  debugSlots: DebugSlot[]
}
