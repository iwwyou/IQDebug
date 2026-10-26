import { useStore, keyOf } from '../state/store'
import type { Verdict } from '../types/result'

// 함수별 verdict 마커: 백엔드 live 분석 결과(results)를 구독.
// "분석은 live, 표시는 화면이 정함"의 워크스페이스측 증명.
function worstVerdict(statuses: Verdict[]): Verdict | 'none' {
  if (statuses.includes('Violated')) return 'Violated'
  if (statuses.includes('Warning')) return 'Warning'
  if (statuses.includes('Satisfied')) return 'Satisfied'
  return 'none'
}

export default function FunctionList() {
  const functions = useStore((s) => s.functions)
  const results = useStore((s) => s.results)
  const openDebug = useStore((s) => s.openDebug)

  return (
    <div className="fn-list">
      <div className="panel-title">Functions</div>
      {functions.map((f) => {
        const res = results[keyOf(f.contract, f.name)]
        const mark = res ? worstVerdict(res.verdict.map((v) => v.status)) : 'none'
        const glyph =
          mark === 'Violated' ? '●' : mark === 'Warning' ? '⚠' : mark === 'Satisfied' ? '○' : '·'
        return (
          <div className="fn-row" key={f.name}>
            <span className={`marker ${mark}`}>{glyph}</span>
            <span style={{ flex: 1 }}>{f.name}</span>
            <button className="debug-btn" onClick={() => openDebug(f.contract, f.name)}>
              Debug ▷
            </button>
          </div>
        )
      })}
      <div style={{ marginTop: 12, fontSize: 11, color: '#666' }}>
        마커 = 백엔드 edit-time 분석 결과. 클릭하면 그 함수 디버그 뷰로.
      </div>
    </div>
  )
}
