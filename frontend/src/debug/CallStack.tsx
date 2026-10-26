import type { NavState } from './useNavigation'
import { MAX_CALL_DEPTH } from './useNavigation'

// ★ N4. 호출 스택 = `nav.stack` 그대로다. 별도 자료구조가 아니라 **navigation 의 상태 자체**라
//   Step Into/Out 과 항상 일관된다. 아래로 갈수록 깊고, 눌러서 그 깊이로 되돌아간다.
export default function CallStack({ nav }: { nav: NavState }) {
  const node = nav.node
  // 현재 노드에서 들어갈 수 있는/없는 호출들 — Step Into 가 무엇을 할지 미리 보여준다.
  const refs = node?.callees ?? []
  const enterable = refs.filter((r) => r.enterable)
  const opaque = refs.filter((r) => !r.enterable)
  const atCap = nav.stack.length >= MAX_CALL_DEPTH

  return (
    <div className="callstack">
      {nav.stack.map((f, i) => {
        const isTop = i === nav.stack.length - 1
        const n = isTop ? nav.node : undefined
        return (
          <div
            key={`${f.cfgKey ?? 'root'}-${i}`}
            className={'cs-frame' + (isTop ? ' active' : '')}
            style={{ paddingLeft: 6 + i * 10 }}
            onClick={() => !isTop && nav.popToFrame(i)}
            title={isTop ? '현재 프레임' : '클릭하면 이 프레임으로 돌아갑니다'}
          >
            <span className="cs-depth">{i === 0 ? '▣' : '↳'}</span>
            <span className="cs-label">{f.label}</span>
            {isTop && n && <span className="cs-at">{n.display_line >= 0 ? `@ L${n.display_line}` : "@ 함수 경계"}</span>}
          </div>
        )
      })}

      {enterable.length > 0 && (
        <div className="cs-hint">
          Step Into 가능: {enterable.map((r) => r.label).join(', ')}
          {enterable.length > 1 && ' (평가 순서대로 — Into 를 반복하면 다음 호출)'}
          {atCap && ` — ⚠ 깊이 상한 ${MAX_CALL_DEPTH} 도달`}
        </div>
      )}
      {opaque.map((r, i) => (
        <div className="cs-opaque" key={i} title={r.reason ?? undefined}>
          ⊘ {r.label} — 들어갈 수 없음
          {r.reason && <div className="cs-reason">{r.reason}</div>}
        </div>
      ))}
    </div>
  )
}
