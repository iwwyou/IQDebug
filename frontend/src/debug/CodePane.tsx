import { useEffect, useRef } from 'react'
import Editor from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import { registerSolidity } from '../editor/solidity'
import { useStore, keyOf } from '../state/store'
import type { AnalysisResult } from '../types/result'
import type { NavState } from './useNavigation'
import { canBreakpointOnLine, isLineUnreachable } from './useNavigation'

interface Props {
  source: string // 함수-로컬 소스 (에디터 내부 라인 1 = 함수 첫 줄)
  nav: NavState
  result: AnalysisResult
  startLine?: number // 함수의 컨트랙트-절대 시작 라인 → gutter/좌표 오프셋
}

// 함수만 표시하되 라인넘버는 컨트랙트-절대(#2). 엔진 src_line/verdict/intent 는 모두 절대라서
// 에디터 내부 로컬라인(1..N)과 offset=(startLine-1) 로 변환한다:
//   내부 = 절대 - offset,  gutter 표시 = 내부 + offset = 절대.
export default function CodePane({ source, nav, result, startLine }: Props) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null)
  const monacoRef = useRef<any>(null)
  const decoRef = useRef<string[]>([])

  const offset = (startLine ?? 1) - 1
  const toLocal = (abs: number) => abs - offset

  // onMount 콜백은 1회만 실행 → 클로저가 초기 result/offset/nav 를 캡처(함수 전환 시 stale).
  // ref 로 항상 최신값을 참조해 breakpoint 판정/토글이 어긋나지 않게 한다.
  const offsetRef = useRef(offset)
  offsetRef.current = offset
  const navRef = useRef(nav)
  navRef.current = nav

  const activeContract = useStore((s) => s.activeContract)
  const activeFunction = useStore((s) => s.activeFunction)
  const intents = useStore((s) => s.intents)
  const intentLines = (intents[keyOf(activeContract, activeFunction)] ?? [])
    .map((it) => it.line)
    .filter((l): l is number => l !== undefined)

  // ★ N4. 모든 노드 조회는 **활성 프레임의 CFG** 기준이다 — callee 프레임이면 callee CFG.
  const cfg = nav.activeCfg
  const cfgRef = useRef(cfg)
  cfgRef.current = cfg

  const cursorLine = nav.node?.display_line
  const predLines = nav.highlightedPreds
    .map((id) => cfg.nodes.find((n) => n.id === id)?.display_line)
    .filter((l): l is number => l !== undefined)
  // ★ N1. forward 분기의 다른 feasible successor 들 (커서가 간 첫째는 cursor-line 이 덮는다)
  const succLines = nav.highlightedSuccs
    .map((id) => cfg.nodes.find((n) => n.id === id)?.display_line)
    .filter((l): l is number => l !== undefined)
  const violatedLines = result.verdict
    .filter((v) => v.status === 'Violated')
    .flatMap((v) => [v.region[0], v.region[1]])
  // ★ N2+N3. 접고 넘어온 루프의 헤더 라인 — "여기를 통째로 지나왔다" 를 보이게 한다.
  //   커서는 루프 **이후** 에 있으므로, 표시가 없으면 루프를 건너뛴 사실이 화면에서 사라진다.
  const foldedNode = nav.foldedLoopHead
    ? cfg.nodes.find((n) => n.id === nav.foldedLoopHead)
    : undefined
  const foldedLine = foldedNode && foldedNode.display_line >= 0 ? foldedNode.display_line : undefined
  const foldedHover = foldedNode?.loop_delta
    ? Object.entries(foldedNode.loop_delta)
        .map(([k, v]) => `- \`${k}\` → \`${v}\``)
        .join('\n')
    : null

  useEffect(() => {
    const ed = editorRef.current
    const monaco = monacoRef.current
    if (!ed || !monaco) return
    const lineCount = ed.getModel()?.getLineCount() ?? 0

    const decos: editor.IModelDeltaDecoration[] = []
    // 절대라인 → 내부라인 변환 + 함수 범위(1..lineCount) 밖은 스킵.
    const push = (abs: number, options: editor.IModelDecorationOptions) => {
      const l = toLocal(abs)
      if (l < 1 || l > lineCount) return
      decos.push({ range: new monaco.Range(l, 1, l, 1), options })
    }

    if (cursorLine && cursorLine >= 0) {
      push(cursorLine, { isWholeLine: true, className: 'cursor-line', glyphMarginClassName: 'cursor-glyph' })
    }
    predLines.forEach((l) => push(l, { isWholeLine: true, className: 'pred-line' }))
    succLines.forEach((l) => push(l, { isWholeLine: true, className: 'succ-line' }))
    if (foldedLine !== undefined) {
      push(foldedLine, {
        isWholeLine: true,
        className: 'loop-fold-line',
        hoverMessage: {
          value: foldedHover
            ? `**루프를 접어 지나갔습니다** — 고정점 수렴 후 바뀐 값:\n\n${foldedHover}`
            : '**루프를 접어 지나갔습니다** — iteration 별로 멈추지 않습니다 (상태는 고정점 하나).',
        },
      })
    }
    violatedLines.forEach((l) => push(l, { isWholeLine: true, className: 'violated-line' }))
    // ★ N5. 도달 불가(⊥) 라인의 breakpoint 는 다른 글리프 + 설명 — Continue 가 멈추지 않는다.
    nav.breakpoints.forEach((l) =>
      push(
        l,
        isLineUnreachable(cfg, l)
          ? {
              glyphMarginClassName: 'bp-glyph-unreachable',
              glyphMarginHoverMessage: {
                value: '도달 불가 (⊥) — abstract interpretation 이 이 지점을 도달 불가로 판정했습니다. Continue/next bp 가 멈추지 않습니다.',
              },
            }
          : { glyphMarginClassName: 'bp-glyph' }
      )
    )
    intentLines.forEach((l) =>
      push(l, { glyphMarginClassName: 'intent-glyph', glyphMarginHoverMessage: { value: '@During' } })
    )
    decoRef.current = ed.deltaDecorations(decoRef.current, decos)
  }, [cursorLine, predLines.join(','), succLines.join(','), foldedLine, violatedLines.join(','), nav.breakpoints, intentLines.join(','), cfg, offset])

  return (
    <Editor
      height="100%"
      language="solidity"
      theme="iqdebug-dark"
      beforeMount={registerSolidity}
      value={source}
      onMount={(ed, monaco) => {
        editorRef.current = ed
        monacoRef.current = monaco
        // glyph margin 클릭 → breakpoint 토글 (내부라인 → 절대라인으로 저장)
        //   단, 구조적 라인(}=merge/loop_exit/φ/exit)에는 걸 수 없음 → 무시(pre-state 모호 회피).
        ed.onMouseDown((e) => {
          if (e.target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN) {
            const ln = e.target.position?.lineNumber
            if (!ln) return
            const abs = ln + offsetRef.current
            if (canBreakpointOnLine(cfgRef.current, abs)) navRef.current.toggleBreakpoint(abs)
          }
        })
      }}
      options={{
        readOnly: true,
        glyphMargin: true,
        minimap: { enabled: false },
        fontSize: 13,
        // gutter 는 컨트랙트-절대 라인 표시 (내부 n + offset)
        lineNumbers: (n: number) => String(n + offset),
      }}
    />
  )
}
