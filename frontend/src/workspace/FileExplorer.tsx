import { useRef } from 'react'
import { useStore } from '../state/store'

export default function FileExplorer() {
  const files = useStore((s) => s.files)
  const activeFile = useStore((s) => s.activeFile)
  const setActiveFile = useStore((s) => s.setActiveFile)
  const newFile = useStore((s) => s.newFile)
  const openFile = useStore((s) => s.openFile)
  const deleteFile = useStore((s) => s.deleteFile)
  const fileInput = useRef<HTMLInputElement>(null)

  const onNew = () => {
    const name = window.prompt('새 파일 이름', 'New.sol')
    if (name) newFile(name)
  }

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = () => openFile(f.name, String(reader.result))
    reader.readAsText(f)
    e.target.value = '' // 같은 파일 재선택 허용
  }

  return (
    <div className="explorer-section">
      <div className="panel-title">Files</div>
      {files.map((f) => (
        <div
          key={f.path}
          className={f.path === activeFile ? 'file-row active' : 'file-row'}
          onClick={() => setActiveFile(f.path)}
        >
          <span className="fname">{f.path}</span>
          <span
            className="del"
            title="삭제"
            onClick={(e) => {
              e.stopPropagation()
              deleteFile(f.path)
            }}
          >
            ✕
          </span>
        </div>
      ))}
      <div className="explorer-actions">
        <button className="mini-btn" onClick={onNew}>
          + new
        </button>
        <button className="mini-btn" onClick={() => fileInput.current?.click()}>
          ↥ open
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".sol"
          style={{ display: 'none' }}
          onChange={onPick}
        />
      </div>
    </div>
  )
}
