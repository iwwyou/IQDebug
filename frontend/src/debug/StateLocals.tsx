import type { AnalysisResult } from '../types/result'
import type { NavState } from './useNavigation'
import { isDead, isLoopHead, whyUnreachable } from './useNavigation'

// 커서 노드의 추상상태 + 도달 경로. 커서 이동 시 갱신(엔진 호출 없음).
// ★ (2026-10-22) 표시 상태 = "이 노드 실행 직전" = 엔진이 계산한 in_state(IN).
//   예전엔 선행 노드의 out-state 로 흉내 냈는데, join(선행 여럿)에서 정의되지 않아 join 에 착지를 막아야 했다.
//   엔진 worklist 가 선행 join·scope 정리 직후의 환경을 저장하므로 join·loop exit 에도 착지할 수 있다.
//   함수 EXIT 만 "최종 상태"(IN == OUT).
const isGlobal = (k: string) =>
  k.startsWith('block.') || k.startsWith('msg.') || k.startsWith('tx.')

export default function StateLocals({ nav, result }: { nav: NavState; result: AnalysisResult }) {
  const node = nav.node
  const flags = node?.flags ?? []
  // ★ N4. **활성 프레임**의 CFG 를 본다 — callee 프레임에서 타겟 CFG 를 뒤지면 노드를
  //   못 찾아 pre-state/path 가 전부 비어버린다.
  const cfg = nav.activeCfg

  // entry→cursor 단순 경로 (첫 선행을 따라 역추적, 표시 줄 기준)
  const path: number[] = []
  if (node) {
    let cur: string | undefined = node.id
    const seen = new Set<string>()
    while (cur && !seen.has(cur)) {
      seen.add(cur)
      const n = cfg.nodes.find((x) => x.id === cur)
      if (n && (path.length === 0 || path[0] !== n.display_line)) path.unshift(n.display_line)
      const e = cfg.edges.find((x) => x.to === cur && x.kind !== 'back_edge')
      cur = e?.from
    }
  }

  const displayState: Record<string, string> = node
    ? (node.id === cfg.exit ? node.state : node.in_state)
    : {}
  let stateNote = ''
  let unreachableNote = ''
  if (node) {
    if (node.id === cfg.exit) stateNote = '최종 상태 (함수 종료)'
    else if (flags.includes('merge')) stateNote = '분기 합류 — 모든 분기 상태를 합친 값'
    else if (flags.includes('loop_exit')) stateNote = '루프 종료 후 상태'
    else if (isLoopHead(node)) stateNote = '루프 진입 전 상태 (루프 밖에서 들어오는 값)'
    else if (flags.includes('condition')) stateNote = '조건 평가 전'
    else stateNote = '이 줄 실행 전'
    // ★ why-unreachable 은 조작이 아니라 출력 — 도달 불가면 원인이 된 조건 분기를 알려준다.
    if (isDead(cfg, node.id)) {
      const c = whyUnreachable(cfg, node.id)
      unreachableNote = c
        ? `도달 불가(⊥) — L${c.display_line} 조건의 이 분기는 현재 입력에서 성립할 수 없습니다.`
        : '도달 불가(⊥)'
    }
  }

  const valClass = (v: string) =>
    v.includes('⊤') || v === 'top' ? 'v top' : v.includes('-') ? 'v neg' : 'v'

  // ★ N2+N3. 보여줄 루프 fold 가 있나.
  //   - `nav.foldedLoopHead`: 직전 step forward 가 접고 넘어온 루프 (커서는 그 loop exit)
  //   - 커서가 루프 헤더 자체: 다음 step 이 접을 루프 (`self`)
  //   두 경우 다 헤더 노드의 `loop_delta` 를 읽는다. 구조 단계(analyze)엔 delta 가 없다.
  const loopHeadNode =
    (nav.foldedLoopHead ? cfg.nodes.find((n) => n.id === nav.foldedLoopHead) : undefined) ??
    (isLoopHead(node) ? node : undefined)
  const loopFold = loopHeadNode
    ? { node: loopHeadNode, delta: loopHeadNode.loop_delta, self: !nav.foldedLoopHead }
    : undefined

  const entries = Object.entries(displayState)
  const realEntries = entries.filter(([k]) => !isGlobal(k))
  const globalEntries = entries.filter(([k]) => isGlobal(k))

  return (
    <div style={{ marginTop: 14 }}>
      <div className="panel-title">
        State &amp; Locals
        {node && (
          <span style={{ color: '#666', marginLeft: 8 }}>
            @ {node.display_line >= 0 ? `L${node.display_line}` : '함수 경계'} ({node.name ?? node.id})
          </span>
        )}
      </div>
      {stateNote && <div className="state-note">{stateNote}</div>}
      {unreachableNote && <div className="state-note" style={{ color: '#d16969' }}>{unreachableNote}</div>}
      {nav.message && <div className="state-note" style={{ color: '#c98a3a' }}>{nav.message}</div>}
      {flags.length > 0 && (
        <div style={{ fontSize: 11, color: '#c586c0', marginBottom: 6 }}>{flags.join(', ')}</div>
      )}
      {node && entries.length === 0 && <div style={{ color: '#666' }}>(빈 상태)</div>}
      {node && entries.length > 0 && realEntries.length === 0 && (
        <div style={{ color: '#666' }}>(로컬/상태 변수 없음 — 전역만)</div>
      )}
      {realEntries.map(([k, v]) => (
        <div className="kv" key={k}>
          <span>{k}</span>
          <span className={valClass(v)}>{v}</span>
        </div>
      ))}

      {globalEntries.length > 0 && (
        <details className="globals">
          <summary>전역 변수 (block/msg/tx) · {globalEntries.length}</summary>
          {globalEntries.map(([k, v]) => (
            <div className="kv" key={k}>
              <span>{k}</span>
              <span className={valClass(v)}>{v}</span>
            </div>
          ))}
        </details>
      )}

      <div className="path">
        path: {path.filter((l) => l >= 0).map((l) => `L${l}`).join(' → ')}
      </div>
      {/* ★ N2+N3. 루프를 접고 넘어왔으면, 그 루프가 "한 일" = 수렴 후 바뀐 변수만 보여준다.
          iteration 을 밟게 하지 않는 대신 이 delta 가 루프의 효과를 대표한다(decision #4). */}
      {loopFold && (
        <div className="loop-fold">
          <div className="loop-fold-title">
            루프 L{loopFold.node.display_line} ({loopFold.node.cond_type}) 를 접었습니다
            {loopFold.self ? ' — 다음 step 은 루프 이후로' : ''}
          </div>
          {loopFold.delta === undefined ? (
            <div className="loop-fold-none">(아직 값이 없음 — Apply 후 표시)</div>
          ) : Object.keys(loopFold.delta).length === 0 ? (
            <div className="loop-fold-none">수렴 후 바뀐 변수 없음 (루프가 0회 또는 무효과)</div>
          ) : (
            <>
              <div className="loop-fold-sub">고정점 수렴 후 값이 바뀐 변수 (모든 iteration 성립)</div>
              {Object.entries(loopFold.delta).map(([k, v]) => (
                <div className="kv" key={k}>
                  <span>{k}</span>
                  <span className={valClass(v)}>{v}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}
