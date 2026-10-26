import { useStore, keyOf } from '../state/store'
import { useNavigation } from './useNavigation'
import CodePane from './CodePane'
import DebuggerControls from './DebuggerControls'
import StateLocals from './StateLocals'
import CallStack from './CallStack'
import VerdictPanel from './VerdictPanel'
import DebugInputPanel from './DebugInputPanel'
import IntentPanel from './IntentPanel'

export default function DebugView() {
  const activeContract = useStore((s) => s.activeContract)
  const activeFunction = useStore((s) => s.activeFunction)
  const results = useStore((s) => s.results)
  const functions = useStore((s) => s.functions)

  const analyzeError = useStore((s) => s.analyzeError)
  const syntaxErrors = useStore((s) => s.syntaxErrors)
  const depContents = useStore((s) => s.depContents)
  const result = results[keyOf(activeContract, activeFunction)]
  const fnMeta = functions.find((f) => f.name === activeFunction)
  const nav = useNavigation(result)

  // ★ N4. 코드 창은 **현재 프레임**의 소스를 보여준다. 조달 순서:
  //   ① 타겟 프레임 → outline 이 준 fnMeta
  //   ② callee 가 같은 파일 → store.functions (outline 이 컨트랙트×함수를 전부 열거한다)
  //   ③ callee 가 의존성 → depContents[컨트랙트] 를 CalleeRef 의 라인 범위로 절취
  //      (pkl 의 라인 번호는 **그 dep 파일 기준**이라 업로드한 원문과 맞는다)
  //   ④ 둘 다 없으면 소스 없음 안내 — 상태 패널은 그대로 동작한다
  const frame = nav.stack[nav.stack.length - 1]
  const pane = (() => {
    if (!frame || frame.cfgKey === null) {
      return { source: fnMeta?.source ?? '', startLine: fnMeta?.startLine, note: '' }
    }
    const fm = functions.find((f) => f.contract === frame.contract && f.name === frame.name)
    if (fm) return { source: fm.source, startLine: fm.startLine, note: '' }
    const dep = frame.contract ? depContents[frame.contract] : undefined
    if (dep && frame.startLine && frame.endLine) {
      // CRLF 도 섞일 수 있어 정규식으로 자른다(업로드된 원문은 줄끝이 보존된다).
      const lines = dep.split(/\r?\n/)
      return {
        source: lines.slice(frame.startLine - 1, frame.endLine).join('\n'),
        startLine: frame.startLine,
        note: '',
      }
    }
    return {
      source: '',
      startLine: frame.startLine ?? 1,
      note: `${frame.label} 의 소스가 없습니다 — 의존성 패널에 해당 .sol 을 올리면 표시됩니다. (상태는 아래 패널에서 볼 수 있습니다)`,
    }
  })()

  if (!result) {
    return (
      <div className="debug" style={{ display: 'block', padding: 24, color: '#888' }}>
        분석 결과 없음 — {activeContract}.{activeFunction} (mock에 미등록이거나 분석중)
      </div>
    )
  }

  return (
    <div className="debug">
      {/* 상단 가로 툴바 = 디버거 컨트롤 */}
      <div className="dtoolbar">
        <DebuggerControls nav={nav} />
      </div>

      {analyzeError && (
        <div className="analyze-error" title={analyzeError}>
          ⚠ 분석 실패: {analyzeError}
        </div>
      )}

      {/* 문법 오류 상태에선 아래 CFG/값이 "직전 유효 코드"의 것이다 — 최신으로 오인하면 안 됨 */}
      {syntaxErrors.length > 0 && (
        <div className="analyze-error" title={syntaxErrors.map((e) => `L${e.line}: ${e.message}`).join('\n')}>
          ⚠ 코드 오류 {syntaxErrors.length}건 — 아래 결과는 직전 정상 코드 기준이며 디버깅(Apply)은 막혀 있습니다
        </div>
      )}

      {/* 좌측 = 코드 (현재 프레임의 함수만 표시 · 라인넘버는 파일-절대) */}
      <div className="dpanel code">
        {pane.note ? (
          <div className="pane-note">{pane.note}</div>
        ) : (
          <CodePane source={pane.source} startLine={pane.startLine} nav={nav} result={result} />
        )}
      </div>

      {/* 우측 사이드바 = 세로 3분할 */}
      <div className="dright">
        <div className="dpanel intent-sec">
          <div className="dsec-title">Intent &amp; Verdict</div>
          <VerdictPanel result={result} nav={nav} />
          <IntentPanel />
        </div>
        <div className="dpanel">
          <div className="dsec-title">Debug Input</div>
          <DebugInputPanel result={result} />
        </div>
        <div className="dpanel">
          <div className="dsec-title">Call Stack</div>
          <CallStack nav={nav} />
          <div className="dsec-title" style={{ marginTop: 10 }}>State &amp; Locals</div>
          <StateLocals nav={nav} result={result} />
        </div>
      </div>
    </div>
  )
}
