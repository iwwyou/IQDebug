import { useState } from 'react'
import { useStore, keyOf } from '../state/store'
import type { IntentItem } from '../types/result'

// intent를 코드에 안 적고 패널에서 작성 — lineRef로 부착(코드 삽입/밀림 없음).
// @During = 라인 부착(코드 라인 옆), @Post = 함수 entry/exit(라인 불필요).
export default function IntentPanel() {
  const activeContract = useStore((s) => s.activeContract)
  const activeFunction = useStore((s) => s.activeFunction)
  const intents = useStore((s) => s.intents)
  const addIntent = useStore((s) => s.addIntent)
  const removeIntent = useStore((s) => s.removeIntent)

  const key = keyOf(activeContract, activeFunction)
  const list = intents[key] ?? []

  const [kind, setKind] = useState<IntentItem['scopeKind']>('During')
  const [line, setLine] = useState('')
  const [text, setText] = useState('')

  const onAdd = () => {
    if (!text.trim()) return
    const item: IntentItem = {
      scopeKind: kind,
      text: text.trim(),
      ...(kind === 'During' && line ? { line: Number(line) } : {}),
    }
    addIntent(key, item)
    setText('')
    setLine('')
  }

  return (
    <div>
      <div className="panel-title">Intent (코드 밖 · lineRef 부착)</div>

      <div className="intent-list">
        {list.length === 0 && <span style={{ color: '#666' }}>(없음)</span>}
        {list.map((it, i) => (
          <span className="intent-line" key={i}>
            <span className="tag">
              @{it.scopeKind}
              {it.line ? ` L${it.line}` : ''}
            </span>
            {it.text}
            <span className="del" onClick={() => removeIntent(key, i)} title="삭제">
              ✕
            </span>
          </span>
        ))}
      </div>

      <div className="intent-form">
        <select value={kind} onChange={(e) => setKind(e.target.value as IntentItem['scopeKind'])}>
          <option value="During">@During</option>
          <option value="Post">@Post</option>
        </select>
        {kind === 'During' && (
          <input
            className="line-in"
            placeholder="line"
            value={line}
            onChange={(e) => setLine(e.target.value)}
          />
        )}
        <input
          className="text-in"
          placeholder={kind === 'During' ? 'before: bal >= a' : 'changed(balance)'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onAdd()}
        />
        <button className="mini-btn" onClick={onAdd}>
          add
        </button>
      </div>
    </div>
  )
}
