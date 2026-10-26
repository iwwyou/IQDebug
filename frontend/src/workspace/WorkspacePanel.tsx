import { useEffect, useRef } from 'react'
import Editor from '@monaco-editor/react'
import { useStore } from '../state/store'
import { registerSolidity } from '../editor/solidity'
import FileExplorer from './FileExplorer'
import DepsSidebar from './DepsSidebar'
import FunctionList from './FunctionList'

// 디바운스: 편집 → 잠시 후 분석 트리거 (백엔드 live 분석 흉내)
let timer: ReturnType<typeof setTimeout> | undefined

export default function WorkspacePanel() {
  const files = useStore((s) => s.files)
  const activeFile = useStore((s) => s.activeFile)
  const updateActiveContent = useStore((s) => s.updateActiveContent)
  const syntaxErrors = useStore((s) => s.syntaxErrors)

  const content = files.find((f) => f.path === activeFile)?.content ?? ''

  // /analyze diagnostics → Monaco marker(squiggle).
  // 이걸 안 그리면 증분 편집 게이트(SyntaxValid)가 편집을 거부해도 화면상 아무 변화가 없어
  // "편집했는데 조용히 무시되는" 상태가 된다 — 실제로 그 버그를 오래 못 봤던 원인.
  const editorRef = useRef<any>(null)
  const monacoRef = useRef<any>(null)

  useEffect(() => {
    const ed = editorRef.current
    const monaco = monacoRef.current
    const model = ed?.getModel()
    if (!monaco || !model) return

    monaco.editor.setModelMarkers(
      model,
      'iqdebug',
      syntaxErrors.map((e) => {
        // line 0/범위밖 진단(파일 전역 오류)은 1행에 붙인다 — 안 그리면 마커가 사라진다.
        const ln = Math.min(Math.max(e.line || 1, 1), model.getLineCount())
        return {
          startLineNumber: ln,
          endLineNumber: ln,
          startColumn: 1,
          endColumn: model.getLineMaxColumn(ln),
          message: e.message,
          severity: monaco.MarkerSeverity.Error,
        }
      }),
    )
  }, [syntaxErrors, activeFile])

  return (
    <div className="workspace">
      <div className="left-sidebar">
        <FileExplorer />
        <DepsSidebar />
      </div>

      <div className="editor-wrap">
        {/* squiggle 이 화면 밖일 수 있으므로 배너로도 알린다 — "편집이 반영 안 됨"이 핵심 정보 */}
        {syntaxErrors.length > 0 && (
          <div className="analyze-error" title={syntaxErrors.map((e) => `L${e.line}: ${e.message}`).join('\n')}>
            ⚠ 코드 오류 {syntaxErrors.length}건 — 이 편집은 분석에 반영되지 않았습니다 (직전 정상 상태 유지)
          </div>
        )}
        {activeFile ? (
          <Editor
            height="100%"
            path={activeFile} /* 파일별 모델 → undo 스택 분리 */
            language="solidity"
            theme="iqdebug-dark"
            beforeMount={registerSolidity}
            onMount={(ed, monaco) => {
              editorRef.current = ed
              monacoRef.current = monaco
            }}
            value={content}
            onChange={(val) => {
              if (timer) clearTimeout(timer)
              timer = setTimeout(() => updateActiveContent(val ?? ''), 400)
            }}
            options={{ minimap: { enabled: false }, fontSize: 13 }}
          />
        ) : (
          <div style={{ padding: 24, color: '#888' }}>파일 없음 — + new 로 생성</div>
        )}
      </div>

      <FunctionList />
    </div>
  )
}
