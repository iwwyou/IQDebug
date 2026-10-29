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

  // ★ (D3, 2026-10-29) 변화 집합 Δ(n) = { x | IN(n)(x) ≠ OUT(n)(x) }.
  //   커서는 "이 줄 실행 직전" 이고, 거기에 **이 지점이 무엇을 바꾸는가** 를 인라인으로 덧붙인다.
  //   `phase`(pre/post) 좌표를 도입하지 않은 이유: φ=post 는 환경 전체를 바꿔 무엇이 달라졌는지
  //   사용자가 찾아야 하는데, Δ 는 바뀐 것만 짚고 `IN` + Δ 로 `OUT` 이 복원되므로 정보가 더 많다.
  //   그리고 설정 `C` 에 좌표가 늘지 않는다(모든 operation 에 "φ := pre" 를 서술할 필요가 없다).
  //
  //   Δ 가 비지 않는 노드는 **IN ≠ OUT 인 노드**뿐이다 — 실측상 문장 노드와 루프 헤더. 조건·merge·
  //   loop_exit·EXIT 은 문장을 갖지 않아 IN = OUT 이라 자동으로 빈다(= "분기 생성 문장 제외" 를
  //   손으로 관리할 필요가 없다). 루프 본문 노드는 착지 대상이 아니다.
  //
  //   호출 지점의 Δ 는 **반환값 바인딩과 상태변수 부수효과를 모두** 담는다 → Step Out 으로 복귀한
  //   직후 "호출이 무엇을 했는가" 가 커서를 옮기지 않고 보인다(커서를 옮기면 그 줄의 남은 호출로
  //   Step Into 할 수 없다 — 호출 보유 줄의 21.7%가 호출 2개 이상).
  //   루프 헤더의 Δ 는 `loop_delta` 를 포함한다(그쪽은 수렴 후 값만, Δ 는 진입 전 값까지).
  const delta: Record<string, [string | undefined, string | undefined]> = {}
  if (node && Object.keys(node.in_state).length > 0) {
    for (const k of new Set([...Object.keys(node.in_state), ...Object.keys(node.state)])) {
      if (node.in_state[k] !== node.state[k]) delta[k] = [node.in_state[k], node.state[k]]
    }
  }
  const hasCall = flags.includes('call')
  const deltaNote = isLoopHead(node)
    ? '변화 (루프 진입 전 → 고정점 수렴 후)'
    : hasCall ? '변화 (호출 전 → 호출 후)' : '변화 (실행 전 → 실행 후)'

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
      {Object.keys(delta).length > 0 && (
        <div className="state-note" style={{ color: '#9cdcfe' }}>
          {deltaNote} · {Object.keys(delta).length}개
        </div>
      )}
      {node && entries.length === 0 && <div style={{ color: '#666' }}>(빈 상태)</div>}
      {node && entries.length > 0 && realEntries.length === 0 && (
        <div style={{ color: '#666' }}>(로컬/상태 변수 없음 — 전역만)</div>
      )}
      {/* 바뀌는 변수는 `이전 → 이후` 로 인라인 표시한다 — 별도 블록을 두지 않는 이유는
          전체 환경 **맥락 안에서** 보는 것이 맞고(변수가 20개여도 화살표 줄만 눈에 들어온다),
          같은 변수가 두 번 나오지 않기 때문이다. */}
      {realEntries.map(([k, v]) => {
        const d = delta[k]
        return (
          <div className={'kv' + (d ? ' changed' : '')} key={k}>
            <span>{k}</span>
            <span className={valClass(v)}>
              {d ? (
                <>
                  <span className="kv-was">{d[0] ?? '—'}</span>
                  <span className="kv-arrow"> → </span>
                  <span className={valClass(d[1] ?? '')}>{d[1] ?? '—'}</span>
                </>
              ) : v}
            </span>
          </div>
        )
      })}
      {/* IN 에 없고 OUT 에만 있는 이름(이 줄에서 선언된 변수)은 위 목록에 안 나오므로 따로 보여준다. */}
      {Object.entries(delta)
        .filter(([k]) => !isGlobal(k) && !(k in displayState))
        .map(([k, d]) => (
          <div className="kv changed" key={`new-${k}`}>
            <span>{k}</span>
            <span>
              <span className="kv-was">{d[0] ?? '—'}</span>
              <span className="kv-arrow"> → </span>
              <span className={valClass(d[1] ?? '')}>{d[1] ?? '—'}</span>
            </span>
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

      {/* ★ (2026-10-29) 라벨 정정. 이 경로는 (i) 사용자가 **실제로 탐색한 경로가 아니고**
          (첫 선행을 따라 역추적하므로 else 로 왔어도 then 을 지나온 것처럼 그려진다),
          (ii) 경로를 그려 보여주는 행위 자체가 "상태가 그 경로에 조건화되어 있다"고 암시한다.
          추상 상태를 조건화하는 것은 **노드의 구문적 위치**(분기 본문은 그 조건으로 refine 된다)이고,
          사용자가 수행한 operation 열이 아니다 — 같은 노드는 어떤 경로로 도달해도 같은 상태다.
          그래서 `JumpToLine` 이 건전하다. */}
      <div className="path">
        가능한 경로 하나(예시): {path.filter((l) => l >= 0).map((l) => `L${l}`).join(' → ')}
      </div>
      <div className="state-note" style={{ color: '#666' }}>
        위 경로는 예시입니다 — 표시 상태는 <b>이 지점</b>의 값이고, 어떤 경로로 도달했는지에
        따라 달라지지 않습니다.
      </div>
      {/* ★ N2+N3. 루프를 접고 넘어왔으면, 그 루프가 "한 일" = 수렴 후 바뀐 변수만 보여준다.
          iteration 을 밟게 하지 않는 대신 이 delta 가 루프의 효과를 대표한다(decision #4). */}
      {loopFold && (
        <div className="loop-fold">
          <div className="loop-fold-title">
            루프 L{loopFold.node.display_line} ({loopFold.node.cond_type}) 를 접었습니다
            {loopFold.self ? ' — 다음 step 은 루프 이후로' : ''}
          </div>
          {/* (D3) 커서가 **루프 헤더에** 있으면 위 Δ 가 이미 같은 것을 보여준다 — 그쪽은 진입 전
              값까지 있어서 `loop_delta`(수렴 후 값만) 를 포함한다. 그래서 중복을 피해 생략한다.
              커서가 **접고 넘어온 loop exit** 에 있을 때만 필요하다(그 노드는 IN = OUT 이라 Δ 가 빈다). */}
          {!loopFold.self && (
            loopFold.delta === undefined ? (
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
            )
          )}

          {/* ★ (K3, 2026-10-29) 루프 탈출 경로 요약.
              루프 본문은 탐색에서 블랙박스이므로(양방향 진입 금지) 헤더가 "이 루프가 어떻게
              끝날 수 있는가" 의 유일한 요약 지점이다. loop exit 노드의 상태는 모든 경로의
              **join** 이라 조건 실패 탈출과 break 탈출을 구별할 수 없었다 — 여기서는 엔진이
              join 하기 **전**의 경로별 환경을 그대로 보여준다(추가 계산 없음).
              `feasible=false` 인 경로는 엔진이 ⊥ 로 판정해 join 에서 제외한 것이다. */}
          {loopFold.node.loop_exits && loopFold.node.loop_exits.length > 0 && (
            <>
              <div className="loop-fold-sub">이 루프가 끝나는 경로</div>
              {loopFold.node.loop_exits.map((le, i) => {
                const label =
                  le.kind === 'normal' ? '조건 실패로 탈출'
                    : le.kind === 'break' ? 'break 로 탈출'
                      : le.kind === 'return' ? 'return 으로 함수 종료'
                        : le.kind === 'revert' ? 'revert (require/assert 실패)'
                          : '그 밖의 경로'
                const vars = Object.entries(le.state).filter(([k]) => !isGlobal(k))
                return (
                  <div key={`${le.kind}-${le.from_}-${i}`} style={{ marginBottom: 4 }}>
                    <div style={{ fontSize: 11, color: le.feasible ? '#9cdcfe' : '#d16969' }}>
                      {label}
                      {le.feasible ? '' : ' — 현재 입력에서는 불가능(⊥)'}
                    </div>
                    {le.feasible && vars.map(([k, v]) => (
                      <div className="kv" key={k} style={{ paddingLeft: 10 }}>
                        <span>{k}</span>
                        <span className={valClass(v)}>{v}</span>
                      </div>
                    ))}
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}
