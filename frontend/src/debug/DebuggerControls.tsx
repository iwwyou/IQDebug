import type { NavState } from './useNavigation'

// 전부 로컬 navigation — 엔진 호출 0.
export default function DebuggerControls({ nav }: { nav: NavState }) {
  const cfg = nav.activeCfg
  const label = (id: string) => {
    const n = cfg.nodes.find((x) => x.id === id)
    if (!n) return id
    const where = n.display_line >= 0 ? `L${n.display_line}` : '함수 끝'
    const kind = n.flags.includes('merge') ? ' (합류)' : n.flags.includes('loop_exit') ? ' (루프 후)' : ''
    return `${where}${kind}`
  }
  return (
    <div>
      <div className="panel-title">Debugger</div>
      <div className="ctrl-row">
        <button onClick={nav.restart} title="restart — 함수 첫 줄로">⏮ restart</button>
        <button onClick={nav.stepBackward} title="step backward (provenance)">⟲ back</button>
        <button onClick={nav.stepForward} title="step forward">⟳ fwd</button>
        <button onClick={nav.stepInto} title="step into — 같은 줄에서 반복하면 평가 순서대로 다음 호출">↘ into</button>
        <button onClick={nav.stepOut} title="step out — 호출 지점으로">↗ out</button>
        <button onClick={nav.continueRun} title="continue — 다음 breakpoint(호출 안 포함) 또는 함수 끝">▷ continue</button>
      </div>
      <div className="ctrl-row">
        <button onClick={nav.prevBreakpoint} title="previous breakpoint">|◀ prev bp</button>
        <button onClick={nav.nextBreakpoint} title="next breakpoint">next bp ▶|</button>
        <button onClick={nav.prevIntent} title="previous intent">◀ prev intent</button>
        <button onClick={nav.nextIntent} title="next intent">next intent ▶</button>
      </div>
      {/* Select Successor / Predecessor — feasible 후보가 여럿일 때만 */}
      {nav.succCandidates.length > 1 && (
        <div className="ctrl-row">
          <span style={{ fontSize: 11, color: '#888' }}>select successor:</span>
          {nav.succCandidates.map((id) => (
            <button key={id} onClick={() => nav.selectSuccessor(id)} disabled={id === nav.cursor}>
              → {label(id)}
            </button>
          ))}
        </div>
      )}
      {nav.predCandidates.length > 1 && (
        <div className="ctrl-row">
          <span style={{ fontSize: 11, color: '#888' }}>select predecessor:</span>
          {nav.predCandidates.map((id) => (
            <button key={id} onClick={() => nav.selectPredecessor(id)} disabled={id === nav.cursor}>
              ← {label(id)}
            </button>
          ))}
        </div>
      )}
      <div style={{ fontSize: 11, color: '#666' }}>
        gutter 클릭 = breakpoint · 표시 상태 = 커서 줄 실행 전 · 루프는 통째로 접는다
      </div>
    </div>
  )
}
