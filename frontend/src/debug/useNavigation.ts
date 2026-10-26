// ★ navigation은 전적으로 로컬 — 이미 받은 Result(cfg) 위 커서 이동.
//   step/back/into/out/continue 어느 것도 엔진을 호출하지 않음.
//   (이게 "quick" 정체성의 구조적 근거.)
//
// (2026-10-22 재구성) 합의된 탐색 규칙
//   · 커서 = "이 노드 실행 직전". 표시 상태는 엔진이 내려준 in_state(IN). 노드 식별은 uid(`id`).
//   · 착지 노드(landable): 문장·조건·join(merge)·loop exit·함수 EXIT.
//     착지하지 않는 노드: ENTRY, 분기 더미(then/else 블록 시작), 루프 φ, 루프 본문 전체, RETURN/ERROR sink,
//     그리고 **바로 다음이 또 join 인 join**(else-if 체인의 안쪽 join — 연속 join 은 한 step 으로 접는다, U3).
//   · 루프에는 들어가지 않는다: 루프 헤더(Before = 루프 밖에서 들어오는 상태) → loop exit(루프 후 상태).
//   · 분기: Step Forward 는 첫 feasible 후보로 가고 나머지를 하이라이트, Select Successor/Predecessor 로 고른다.
//     Select 는 분기의 첫 문장에 착지한다(더미를 건너뜀).
//   · Step Into: 엔진 평가 순서대로 — 같은 노드에서 Into 를 반복하면 다음 callee 로 들어간다. 외부 호출은 opaque.
//   · Continue: callee 안의 breakpoint 까지 내려간다. breakpoint 키 = (소스 파일, 줄) (U6).
//     feasible 분기 여러 개에 서로 다른 breakpoint 가 있으면 분기점에서 멈추고 고르게 한다(U2).
//   · 루프 본문 줄에는 breakpoint 를 걸 수 없다(U5). Step Over Branch 는 이번 범위에서 제외(F3).

import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../state/store'
import type { AnalysisResult, CalleeRef, CFG, CFGNode } from '../types/result'
import { backwardCands, firstLanding, forwardCands, has, isLandable, isLoopHead, navIndex } from './navGraph'
export { canBreakpointOnLine, isDead, isLineUnreachable, isLoopHead, whyUnreachable } from './navGraph'


// ★ N4. 호출 스택 한 칸. `cfgKey === null` 이 타겟 함수 프레임이다.
//   callee 프레임은 `result.callees[cfgKey]` 의 CFG 위에서 움직인다.
//   부모 프레임의 `node` 는 **호출 지점에 그대로 남겨 둔다** → Step Out 은 pop 만 하면 된다.
export interface Frame {
  cfgKey: string | null
  node: string
  label: string                 // "Pool.withdraw" / "Math.mulDiv"
  contract?: string | null
  name?: string | null
  startLine?: number | null     // callee 소스 범위 (같은 파일이면 store.functions 로도 찾는다)
  endLine?: number | null
  /** 이 프레임의 소스 파일 키 — '' = 편집 중인 파일, `dep:<컨트랙트>` = 의존성 파일 (breakpoint 키, U6) */
  fileKey: string
  /** 이 노드에서 다음 Step Into 가 들어갈 callee 순번 (엔진 평가 순서). 커서가 움직이면 0. */
  calleeIdx: number
}

// 안전장치. 재귀 처리(감지·표시)는 **보류**이고(백로그 ⑨), 이건 "UI 가 끝없이 내려가지
// 않게" 하는 상한일 뿐이다. 루프 중첩과 달리 호출은 재귀면 무한히 깊어질 수 있다.
export const MAX_CALL_DEPTH = 16

const bpKey = (fileKey: string, line: number) => `${fileKey}:${line}`

export interface NavState {
  cursor: string // 현재 노드 id (uid)
  node: CFGNode | undefined
  highlightedPreds: string[] // 다중 후보 backward — Select Predecessor 대상
  highlightedSuccs: string[] // 다중 후보 forward — 직전 step 이 고른 것 외의 형제 후보 하이라이트
  /** Select Successor 후보: 직전 step 의 형제 후보들, 아니면 커서에서 갈 수 있는 feasible 후보(2개 이상일 때). */
  succCandidates: string[]
  /** Select Predecessor 후보 (같은 규칙, backward). */
  predCandidates: string[]
  /** 직전 step 이 접고 넘어온 루프 헤더 id (커서가 그 loop exit 에 있을 때). */
  foldedLoopHead: string | undefined
  /** 사용자에게 보일 안내 (Continue 가 분기점에서 멈춘 이유 등). */
  message: string
  stack: Frame[]
  /** 현재 프레임의 CFG — succ/pred/state 가 모두 이것 위에서 움직인다. */
  activeCfg: CFG
  /** 현재 프레임(=파일)의 breakpoint 줄. */
  breakpoints: Set<number>
  stepForward: () => void
  stepBackward: () => void
  selectSuccessor: (id: string) => void
  selectPredecessor: (id: string) => void
  stepInto: () => void
  stepOut: () => void
  continueRun: () => void
  nextBreakpoint: () => void
  prevBreakpoint: () => void
  nextIntent: () => void
  prevIntent: () => void
  restart: () => void
  /** Call Stack 패널에서 프레임을 눌러 그 깊이까지 되돌아가기 (0 = 타겟). */
  popToFrame: (index: number) => void
  jumpToLine: (line: number) => void
  toggleBreakpoint: (line: number) => void
}

const EMPTY_CFG: CFG = { entry: '', exit: '', nodes: [], edges: [] }

// Continue 탐색 결과의 "멈출 곳": 이 프레임의 노드, 또는 이 노드의 i 번째 callee 안.
type Stop = { node: string; enter?: { idx: number; inner: Stop[] } }

export function useNavigation(result: AnalysisResult | undefined): NavState {
  const functions = useStore((s) => s.functions)
  const rootLabel = result ? `${result.scope.contract}.${result.scope.function}` : '(target)'
  const rootFrame = (node?: string): Frame => ({
    cfgKey: null, node: node ?? result?.cfg.entry ?? '', label: rootLabel, fileKey: '', calleeIdx: 0,
  })
  const [stack, setStack] = useState<Frame[]>(() => [rootFrame()])
  const [highlightedPreds, setHighlightedPreds] = useState<string[]>([])
  const [highlightedSuccs, setHighlightedSuccs] = useState<string[]>([])
  const [foldedLoopHead, setFoldedLoopHead] = useState<string | undefined>(undefined)
  const [message, setMessage] = useState('')
  // ★ U6. breakpoint 키 = (소스 파일, 줄). 프레임별이 아니라 파일별이라 Continue 가 callee 안에서도 멈춘다.
  const [bps, setBps] = useState<Set<string>>(new Set())

  const top = stack[stack.length - 1]
  const cursor = top?.node ?? ''
  const cfgOf = (key: string | null): CFG =>
    (key ? result?.callees?.[key] : result?.cfg) ?? EMPTY_CFG
  const activeCfg: CFG = cfgOf(top?.cfgKey ?? null)
  const ix = useMemo(() => navIndex(activeCfg), [activeCfg])
  const node = ix.byId.get(cursor)

  const fileKeyOf = (contract?: string | null, name?: string | null): string =>
    functions.some((f) => f.contract === contract && f.name === name) ? '' : `dep:${contract ?? '?'}`

  const breakpoints = useMemo(() => {
    const out = new Set<number>()
    const pre = `${top?.fileKey ?? ''}:`
    bps.forEach((k) => { if (k.startsWith(pre)) out.add(Number(k.slice(pre.length))) })
    return out
  }, [bps, top?.fileKey])

  const isBp = (cfg: CFG, fileKey: string, id: string): boolean => {
    const n = navIndex(cfg).byId.get(id)
    return !!n && n.display_line >= 0 && isLandable(cfg, n) && bps.has(bpKey(fileKey, n.display_line))
  }

  const clearMarks = () => {
    setHighlightedPreds([]); setHighlightedSuccs([]); setFoldedLoopHead(undefined); setMessage('')
  }
  /** 현재 프레임 안에서 이동. 부모 프레임의 node 는 호출 지점에 그대로 남는다(Step Out = pop). */
  const go = (id: string | undefined, opts: { preds?: string[]; succs?: string[]; folded?: string; msg?: string } = {}) => {
    if (!id) return
    setStack((prev) => {
      if (prev.length === 0) return prev
      const next = prev.slice()
      next[next.length - 1] = { ...next[next.length - 1], node: id, calleeIdx: 0 }
      return next
    })
    setHighlightedPreds(opts.preds ?? [])
    setHighlightedSuccs(opts.succs ?? [])
    setFoldedLoopHead(opts.folded)
    setMessage(opts.msg ?? '')
  }

  const enterableRefs = (n: CFGNode | undefined) =>
    (n?.callees ?? []).filter((r) => r.enterable && r.key && result?.callees?.[r.key])

  // ───────────────────── Continue 탐색 (U2·U6) ─────────────────────
  // stops(cfg, id) = id 부터(자기 포함) 각 feasible 경로에서 **처음** 만나는 멈출 곳들.
  //   자기 자신이 breakpoint 면 그곳, 아니면 이 노드의 callee 들(평가 순서) 안의 breakpoint,
  //   아니면 후속들의 합집합. 루프에는 들어가지 않으므로(헤더 forward = exit) 그래프는 DAG 다.
  const stopsMemo = new Map<string, Stop[]>()
  const calleeStops = (r: CalleeRef, depth: number): Stop[] => {
    if (depth >= MAX_CALL_DEPTH) return []
    const cfg = cfgOf(r.key)
    return stopsFrom(cfg, fileKeyOf(r.contract, r.name), cfg.entry, depth, false)
  }
  function stopsFrom(cfg: CFG, fileKey: string, id: string, depth: number, includeSelf: boolean, fromCallee = 0): Stop[] {
    const memoKey = `${fileKey}|${id}|${includeSelf}|${fromCallee}|${depth}|${cfg.entry}`
    const hit = stopsMemo.get(memoKey)
    if (hit) return hit
    stopsMemo.set(memoKey, []) // 순환 방지(구조상 DAG 이지만 방어)
    let res: Stop[] = []
    const n = navIndex(cfg).byId.get(id)
    if (includeSelf && isBp(cfg, fileKey, id)) {
      res = [{ node: id }]
    } else {
      const refs = (n?.callees ?? []).filter((r) => r.enterable && r.key && result?.callees?.[r.key])
      for (let i = fromCallee; i < refs.length && res.length === 0; i++) {
        const inner = calleeStops(refs[i], depth + 1)
        if (inner.length > 0) res = [{ node: id, enter: { idx: i, inner } }]
      }
      if (res.length === 0) {
        const seen = new Set<string>()
        for (const c of forwardCands(cfg, id)) {
          for (const s of stopsFrom(cfg, fileKey, c, depth, true)) {
            const k = s.enter ? `${s.node}#${s.enter.idx}` : s.node
            if (!seen.has(k)) { seen.add(k); res.push(s) }
          }
        }
      }
    }
    stopsMemo.set(memoKey, res)
    return res
  }

  /** Stop 하나를 실제 프레임 스택으로 펼친다(callee 안이면 프레임을 쌓는다). */
  const applyStop = (base: Frame[], s: Stop): Frame[] => {
    const frames = base.slice()
    const cur = frames[frames.length - 1]
    if (!s.enter) {
      frames[frames.length - 1] = { ...cur, node: s.node, calleeIdx: 0 }
      return frames
    }
    const cfg = cfgOf(cur.cfgKey)
    const refs = enterableRefs(navIndex(cfg).byId.get(s.node))
    const r = refs[s.enter.idx]
    frames[frames.length - 1] = { ...cur, node: s.node, calleeIdx: s.enter.idx + 1 }
    const ccfg = cfgOf(r.key)
    frames.push({
      cfgKey: r.key, node: ccfg.entry, label: r.label, contract: r.contract, name: r.name,
      startLine: r.start_line, endLine: r.end_line, fileKey: fileKeyOf(r.contract, r.name), calleeIdx: 0,
    })
    // callee 안의 첫 멈출 곳 — 하나면 그곳, 여럿이면 callee 의 분기점까지
    return resolveStops(frames, s.enter.inner)
  }

  /** 후보 stop 들 → 프레임 스택. 하나면 그곳. 여럿이면 **갈라지는 분기점**에서 멈춘다(U2). */
  const resolveStops = (frames: Frame[], ss: Stop[]): Frame[] => {
    if (ss.length === 1) return applyStop(frames, ss[0])
    // 여럿: 현재 프레임에서 공통 경로를 따라가 후보가 갈라지는 첫 노드를 찾는다
    const f = frames[frames.length - 1]
    const cfg = cfgOf(f.cfgKey)
    let cur = f.node
    for (let i = 0; i < 10000; i++) {
      const cands = forwardCands(cfg, cur).filter((c) => stopsFrom(cfg, f.fileKey, c, frames.length, true).length > 0)
      if (cands.length !== 1) break
      cur = cands[0]
    }
    const out = frames.slice()
    out[out.length - 1] = { ...f, node: cur, calleeIdx: 0 }
    return out
  }

  /** 현재 위치(스택) 다음의 멈출 곳으로. 없으면 null. callee 를 다 지나면 부모로 돌아가 이어서 찾는다. */
  const findNext = (frames: Frame[]): { frames: Frame[]; branch: boolean } | null => {
    let fs = frames.slice()
    while (fs.length > 0) {
      const f = fs[fs.length - 1]
      const cfg = cfgOf(f.cfgKey)
      const ss = stopsFrom(cfg, f.fileKey, f.node, fs.length, false, f.calleeIdx)
      if (ss.length > 0) {
        const out = resolveStops(fs, ss)
        return { frames: out, branch: ss.length > 1 }
      }
      if (fs.length === 1) return null
      fs = fs.slice(0, -1) // callee 끝 — 부모의 호출 지점(calleeIdx 는 이미 다음 callee)에서 이어서
    }
    return null
  }

  const landOn = (frames: Frame[], branch: boolean) => {
    setStack(frames)
    const f = frames[frames.length - 1]
    const cfg = cfgOf(f.cfgKey)
    const succs = branch ? forwardCands(cfg, f.node) : []
    setHighlightedPreds([]); setHighlightedSuccs(succs.length > 1 ? succs : []); setFoldedLoopHead(undefined)
    setMessage(branch && succs.length > 1
      ? '여러 feasible 분기에 서로 다른 breakpoint 가 있습니다 — Select Successor 로 갈 분기를 고르세요.'
      : '')
  }

  // ★ Apply(evaluate) 마다 커서를 "run" 결과 위치로 재배치 (decision #32):
  //   breakpoint 있으면 entry 에서 Continue 한 곳, 없으면 EXIT(최종상태). 호출 스택은 타겟 하나로 리셋.
  const applyTick = useStore((s) => s.applyTick)
  const runRef = useRef<() => void>(() => {})
  runRef.current = () => {
    if (!result) return
    const base = [rootFrame(result.cfg.entry)]
    const hit = findNext(base)
    if (hit) landOn(hit.frames, hit.branch)
    else { setStack([rootFrame(result.cfg.exit)]); clearMarks() }
  }
  // 결과가 바뀔 때: Apply 신호면 run, 아니면(코드 편집으로 구조만 바뀜) 프레임들이 아직 유효한지 확인.
  //   uid 라 편집 후에도 남은 노드는 그대로다 — 사라진 노드/callee 를 가리키는 프레임이 있으면 타겟 첫 착지로.
  const lastTick = useRef(0)
  useEffect(() => {
    if (!result) return
    if (applyTick !== lastTick.current) {
      lastTick.current = applyTick
      if (applyTick !== 0) { runRef.current(); return }
    }
    const valid = stack.every((f) => {
      const cfg = f.cfgKey ? result.callees?.[f.cfgKey] : result.cfg
      return !!cfg && navIndex(cfg).byId.has(f.node)
    })
    if (!valid) { setStack([rootFrame(firstLanding(result.cfg))]); clearMarks() }
  }, [result, applyTick])

  const loopFoldFrom = (from: string, to: string): string | undefined =>
    isLoopHead(ix.byId.get(from)) && has(ix.byId.get(to), 'loop_exit') ? from : undefined

  const fc = forwardCands(activeCfg, cursor)
  const bc = backwardCands(activeCfg, cursor)
  const succCandidates = highlightedSuccs.length > 1 ? highlightedSuccs : fc.length > 1 ? fc : []
  const predCandidates = highlightedPreds.length > 1 ? highlightedPreds : bc.length > 1 ? bc : []

  // 검증 결과 줄 목록 (Next/Prev Intent) — 타겟 프레임 기준
  const intentLines = (): number[] =>
    [...new Set((result?.verdict ?? []).map((v) => v.line).filter((l) => l >= 0))].sort((a, b) => a - b)
  const landingAtLine = (cfg: CFG, line: number): string | undefined =>
    cfg.nodes.find((n) => n.display_line === line && isLandable(cfg, n))?.id

  return {
    cursor,
    node,
    highlightedPreds,
    highlightedSuccs,
    succCandidates,
    predCandidates,
    foldedLoopHead,
    message,
    stack,
    activeCfg,
    breakpoints,

    stepForward: () => {
      const cands = forwardCands(activeCfg, cursor)
      if (cands.length === 0) {
        setMessage(cursor === activeCfg.exit ? '함수 끝입니다.' : '이후 모든 경로가 도달 불가(⊥)입니다.')
        return
      }
      go(cands[0], { succs: cands.length > 1 ? cands : [], folded: loopFoldFrom(cursor, cands[0]) })
    },
    stepBackward: () => {
      const cands = backwardCands(activeCfg, cursor)
      if (cands.length === 0) return
      go(cands[0], { preds: cands.length > 1 ? cands : [] })
    },
    selectSuccessor: (id) => {
      const cands = forwardCands(activeCfg, cursor)
      // 하이라이트된 후보에서 고른 경우: 커서는 이미 첫 후보로 갔을 수 있다 → 형제 후보로 이동 허용
      if (cands.includes(id) || highlightedSuccs.includes(id)) go(id, { folded: loopFoldFrom(cursor, id) })
    },
    selectPredecessor: (id) => {
      if (backwardCands(activeCfg, cursor).includes(id) || highlightedPreds.includes(id)) go(id)
    },

    // ★ N4. callee CFG 로 하강(push). 같은 노드에서 반복하면 **엔진 평가 순서대로** 다음 callee 로.
    stepInto: () => {
      const refs = enterableRefs(node)
      const i = top?.calleeIdx ?? 0
      if (i >= refs.length) {
        setMessage(refs.length === 0
          ? ((node?.callees ?? []).length > 0 ? '들어갈 수 있는 호출이 없습니다 (외부 호출은 opaque).' : '이 줄에는 호출이 없습니다.')
          : '이 줄의 호출을 모두 지났습니다.')
        return
      }
      if (stack.length >= MAX_CALL_DEPTH) return // 안전 상한 (재귀 처리는 보류)
      const r = refs[i]
      const ccfg = cfgOf(r.key)
      setStack((prev) => {
        const next = prev.slice()
        next[next.length - 1] = { ...next[next.length - 1], calleeIdx: i + 1 }
        next.push({
          cfgKey: r.key, node: firstLanding(ccfg), label: r.label, contract: r.contract, name: r.name,
          startLine: r.start_line, endLine: r.end_line, fileKey: fileKeyOf(r.contract, r.name), calleeIdx: 0,
        })
        return next
      })
      clearMarks()
    },

    // ★ N4. caller 로 복귀(pop) — 호출 지점 노드로 돌아온다.
    stepOut: () => {
      if (stack.length <= 1) return
      setStack((prev) => prev.slice(0, -1))
      clearMarks()
    },

    continueRun: () => {
      const hit = findNext(stack)
      if (hit) { landOn(hit.frames, hit.branch); return }
      // breakpoint 없음 → 함수 끝(타겟 EXIT)
      setStack([rootFrame(result?.cfg.exit)])
      clearMarks()
    },
    // Continue 와 달리 breakpoint 가 없으면 이동하지 않는다.
    nextBreakpoint: () => {
      const hit = findNext(stack)
      if (hit) landOn(hit.frames, hit.branch)
      else setMessage('이후에 도달 가능한 breakpoint 가 없습니다.')
    },
    // 뒤쪽 breakpoint — 현재 프레임 안에서 선행을 거슬러 처음 만나는 것들. 여럿이면 하이라이트.
    prevBreakpoint: () => {
      const f = top
      if (!f) return
      const found: string[] = []
      const seen = new Set<string>()
      const walk = (id: string) => {
        for (const p of backwardCands(activeCfg, id)) {
          if (seen.has(p)) continue
          seen.add(p)
          if (isBp(activeCfg, f.fileKey, p)) { if (!found.includes(p)) found.push(p) }
          else walk(p)
        }
      }
      walk(cursor)
      if (found.length === 0) { setMessage('앞쪽에 breakpoint 가 없습니다.'); return }
      go(found[0], { preds: found.length > 1 ? found : [] })
    },

    nextIntent: () => {
      const cur = stack.length === 1 ? (node?.display_line ?? -1) : -1
      const ln = intentLines().find((l) => l > cur)
      const id = ln !== undefined && result ? landingAtLine(result.cfg, ln) : undefined
      if (id) { setStack([rootFrame(id)]); clearMarks() } else setMessage('다음 intent 가 없습니다.')
    },
    prevIntent: () => {
      const cur = stack.length === 1 ? (node?.display_line ?? Infinity) : Infinity
      const ln = [...intentLines()].reverse().find((l) => l < cur)
      const id = ln !== undefined && result ? landingAtLine(result.cfg, ln) : undefined
      if (id) { setStack([rootFrame(id)]); clearMarks() } else setMessage('이전 intent 가 없습니다.')
    },
    restart: () => {
      if (!result) return
      setStack([rootFrame(firstLanding(result.cfg))])
      clearMarks()
    },

    popToFrame: (index) => {
      if (index < 0 || index >= stack.length - 1) return
      setStack((prev) => prev.slice(0, index + 1))
      clearMarks()
    },

    // 타겟 파일 줄로 이동(Verdict 패널 등) — 호출 스택은 타겟 하나로.
    jumpToLine: (line) => {
      const id = result ? landingAtLine(result.cfg, line) : undefined
      if (id) { setStack([rootFrame(id)]); clearMarks() } else setMessage(`L${line} 에 착지할 노드가 없습니다.`)
    },

    toggleBreakpoint: (line) => {
      const k = bpKey(top?.fileKey ?? '', line)
      setBps((prev) => {
        const cur = new Set(prev)
        if (cur.has(k)) cur.delete(k)
        else cur.add(k)
        return cur
      })
    },
  }
}
