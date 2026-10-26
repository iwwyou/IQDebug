import type { AnalysisResult, FalseRegions } from '../types/result'
import type { NavState } from './useNavigation'

// 위반 영역(출력공간) 표시. decision #40: 이건 "비교되는 값"이 깨지는 구간이지 아직 입력값이
// 아니다 → 라벨에 (출력값 기준) 명시. 입력공간 분할 생성은 (B)에서.
function FalseRegionsView({ fr }: { fr: FalseRegions }) {
  const parts: string[] = []
  if (fr.lowerViolation) parts.push(`하한↓ [${fr.lowerViolation[0]}, ${fr.lowerViolation[1]}]`)
  if (fr.upperViolation) parts.push(`상한↑ [${fr.upperViolation[0]}, ${fr.upperViolation[1]}]`)
  if (fr.overlapZone) parts.push(`불확정 [${fr.overlapZone[0]}, ${fr.overlapZone[1]}]`)
  if (parts.length === 0) return null
  return (
    <div className="false-regions" style={{ fontSize: 11, color: '#c98a3a', marginTop: 3 }}>
      위반 영역(출력값 기준): {parts.join(' · ')}
    </div>
  )
}

export default function VerdictPanel({ result, nav }: { result: AnalysisResult; nav: NavState }) {
  return (
    <div>
      <div className="panel-title">Verdict</div>
      {result.verdict.length === 0 && <div style={{ color: '#6ac46a' }}>● Satisfied</div>}
      {result.verdict.map((v, i) => (
        <div
          key={i}
          className={`verdict-item ${v.status}`}
          onClick={() => nav.jumpToLine(v.line)}
          title="클릭 → 위반 지점으로 점프"
        >
          <span className="risk">{v.risk}/10</span>
          <div>
            <b>{v.status}</b> · {v.type} · L{v.line}
          </div>
          <div className="msg">{v.message}</div>
          {v.falseRegions && <FalseRegionsView fr={v.falseRegions} />}
        </div>
      ))}
      {result.top_vars.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12 }}>
          <span style={{ color: '#888' }}>⊤ vars: </span>
          {result.top_vars.map((t) => (
            <span key={t.var} className="v top" style={{ marginRight: 8 }}>
              {t.var}
              {t.provenance ? ` (${t.provenance})` : ' (출처 미태깅)'}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
