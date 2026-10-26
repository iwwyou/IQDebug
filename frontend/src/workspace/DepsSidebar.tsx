import { useRef } from 'react'
import { useStore } from '../state/store'

export default function DepsSidebar() {
  const deps = useStore((s) => s.deps)
  const attachDep = useStore((s) => s.attachDep)
  const fileInput = useRef<HTMLInputElement>(null)

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = () => {
      // 파일명에서 확장자 제거 → dep 이름. 실엔진이면 이 content로 slice→analyze_file→pkl.
      const name = f.name.replace(/\.sol$/i, '')
      attachDep(name, String(reader.result))
    }
    reader.readAsText(f)
    e.target.value = ''
  }

  return (
    <div className="explorer-section">
      <div className="panel-title">Dependencies</div>
      {deps.map((d) => (
        <div className="dep-row" key={d.name}>
          <span className="name">{d.name}</span>
          <span className="kind status">{d.kind}</span>
          <span className={`status ${d.status}`}>
            {d.status === 'ready' ? '✓' : d.status === 'building' ? '⧗' : '✕'}
          </span>
        </div>
      ))}
      <div className="explorer-actions">
        <button className="mini-btn" onClick={() => fileInput.current?.click()}>
          + attach .sol
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".sol"
          style={{ display: 'none' }}
          onChange={onPick}
        />
      </div>
      <div style={{ fontSize: 10, color: '#666', marginTop: 6 }}>
        첨부 = building→ready (pkl 빌드는 엔진 연결 시)
      </div>
    </div>
  )
}
