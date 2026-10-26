import { useEffect } from 'react'
import { useStore } from './state/store'
import WorkspacePanel from './workspace/WorkspacePanel'
import DebugView from './debug/DebugView'

export default function App() {
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const analyzing = useStore((s) => s.analyzing)
  const runAnalysis = useStore((s) => s.runAnalysis)

  // 최초 1회 활성 함수 분석 (백엔드 live 분석 흉내)
  useEffect(() => {
    void runAnalysis()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">IQDebug</span>
        <div className="tabs">
          <button
            className={view === 'workspace' ? 'tab active' : 'tab'}
            onClick={() => setView('workspace')}
          >
            Workspace
          </button>
          <button
            className={view === 'debug' ? 'tab active' : 'tab'}
            onClick={() => setView('debug')}
          >
            Debug
          </button>
        </div>
        <span className={analyzing ? 'live on' : 'live'}>
          {analyzing ? '● analyzing…' : '○ live'}
        </span>
      </header>

      {view === 'workspace' ? <WorkspacePanel /> : <DebugView />}
    </div>
  )
}
