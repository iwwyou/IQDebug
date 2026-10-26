// 탐색 그래프 규칙 (순수 함수) — React 와 무관해 단독 테스트 가능. useNavigation 이 사용한다.
// 규칙 요약은 useNavigation.ts 머리말 참조.

import type { CFG, CFGEdge, CFGNode } from '../types/result'

// ───────────────────────── 인덱스 (CFG 당 1회) ─────────────────────────
// 별도 navigation graph 를 만들지 않는다 — 받은 CFG 에 조회용 인덱스만 붙인다.
interface NavIndex {
  byId: Map<string, CFGNode>
  succ: Map<string, CFGEdge[]>   // back_edge 제외
  pred: Map<string, CFGEdge[]>   // back_edge 제외
}
const idxCache = new WeakMap<CFG, NavIndex>()
export function navIndex(cfg: CFG): NavIndex {
  let ix = idxCache.get(cfg)
  if (ix) return ix
  ix = { byId: new Map(), succ: new Map(), pred: new Map() }
  for (const n of cfg.nodes) ix.byId.set(n.id, n)
  for (const e of cfg.edges) {
    if (e.kind === 'back_edge') continue // fixpoint 로 흡수 — navigation 에서 숨김
    if (!ix.succ.has(e.from)) ix.succ.set(e.from, [])
    ix.succ.get(e.from)!.push(e)
    if (!ix.pred.has(e.to)) ix.pred.set(e.to, [])
    ix.pred.get(e.to)!.push(e)
  }
  idxCache.set(cfg, ix)
  return ix
}

export const has = (n: CFGNode | undefined, f: string) => !!n && (n.flags as string[]).includes(f)
export const isLoopHead = (n: CFGNode | undefined): boolean =>
  has(n, 'condition') && ['while', 'for', 'do_while'].includes(n?.cond_type ?? '')

/** 도달 불가(⊥) — 엔진 `_is_bottom_env`("하나라도 BOTTOM")와 같은 기준. require 실패 sink 도 죽은 곳. */
export function isDead(cfg: CFG, id: string | undefined): boolean {
  if (!id) return true
  if (id === cfg.error_exit) return true
  const n = navIndex(cfg).byId.get(id)
  if (!n) return true
  const env = Object.keys(n.in_state).length > 0 ? n.in_state : n.state
  return Object.values(env).some((v) => v.includes('⊥'))
}

/** 루프 헤더의 forward 는 loop exit(false) 하나뿐 — 루프 본문에 들어가지 않는다. */
function fwdEdges(cfg: CFG, id: string): CFGEdge[] {
  const ix = navIndex(cfg)
  const es = ix.succ.get(id) ?? []
  return isLoopHead(ix.byId.get(id)) ? es.filter((e) => e.kind === 'false') : es
}

/** 다음이 또 join 인 join — 연속 join 은 마지막 하나로 접는다(else-if 체인의 안쪽 join). */
function isCollapsedJoin(cfg: CFG, n: CFGNode): boolean {
  if (!has(n, 'merge')) return false
  const out = fwdEdges(cfg, n.id)
  return out.length === 1 && has(navIndex(cfg).byId.get(out[0].to), 'merge')
}

export function isLandable(cfg: CFG, n: CFGNode | undefined): boolean {
  if (!n) return false
  if (n.id === cfg.entry) return false
  if (has(n, 'exit')) return n.id === cfg.exit // RETURN/ERROR sink 는 착지 안 함, 함수 EXIT 는 "최종 상태"
  if (has(n, 'fixpoint_evaluation') || has(n, 'loop_body')) return false
  if ((has(n, 'branch_then') || has(n, 'branch_else')) && !has(n, 'loop_exit')) return false // 분기 더미
  if (isCollapsedJoin(cfg, n)) return false
  return true
}

/** 한 edge 를 따라가 처음 만나는 착지 노드 (비착지 노드는 통과). 죽은 곳이면 undefined. */
function landFwd(cfg: CFG, first: string, seen = new Set<string>()): string | undefined {
  let cur: string | undefined = first
  while (cur && !seen.has(cur)) {
    seen.add(cur)
    // return 문 뒤의 RETURN sink 는 EXIT 로 이어지는 엣지가 없다 — 정상 반환 = 함수 끝(EXIT)
    if (cur === cfg.return_exit) return cfg.exit
    if (isDead(cfg, cur)) return undefined
    const n = navIndex(cfg).byId.get(cur)
    if (isLandable(cfg, n)) return cur
    const out: CFGEdge[] = fwdEdges(cfg, cur).filter((e) => !isDead(cfg, e.to))
    // 비착지 노드(더미·φ·접힌 join)에서 갈라지는 일은 구조상 없다(분기는 조건 노드 = 착지) — 있으면 버그로 드러낸다.
    if (out.length > 1) throw new Error(`navigation: non-landable node '${n?.name ?? cur}' has ${out.length} successors`)
    cur = out[0]?.to
  }
  return undefined
}

/** 커서에서 Step Forward 후보들 (feasible, 착지 노드, 엔진 edge 순서 = true 먼저). */
export function forwardCands(cfg: CFG, from: string): string[] {
  const out: string[] = []
  for (const e of fwdEdges(cfg, from)) {
    const l = landFwd(cfg, e.to)
    if (l && !out.includes(l)) out.push(l)
  }
  return out
}

/** 커서에서 Step Backward 후보들 (feasible 선행의 착지 노드). */
export function backwardCands(cfg: CFG, from: string): string[] {
  const ix = navIndex(cfg)
  const out: string[] = []
  const walk = (id: string, seen: Set<string>) => {
    if (seen.has(id)) return
    seen.add(id)
    for (const e of ix.pred.get(id) ?? []) {
      const p = e.from
      if (isDead(cfg, p) && p !== cfg.entry) continue
      const pn = ix.byId.get(p)
      if (isLandable(cfg, pn)) {
        if (!out.includes(p)) out.push(p)
      } else if (p !== cfg.entry) walk(p, seen)
    }
  }
  const seen = new Set<string>()
  walk(from, seen)
  // EXIT 의 선행에는 return 문이 없다(return → RETURN sink) — RETURN 의 선행도 EXIT 의 선행으로 본다
  if (from === cfg.exit && cfg.return_exit) walk(cfg.return_exit, seen)
  return out
}

/** 함수 첫 착지 노드 (entry 다음). */
export function firstLanding(cfg: CFG): string {
  return forwardCands(cfg, cfg.entry)[0] ?? cfg.exit
}

/** 이 줄에 breakpoint 를 걸 수 있나 — 착지 노드가 있는 줄만 (루프 본문 줄은 불가, U5). */
export const canBreakpointOnLine = (cfg: CFG, line: number): boolean =>
  cfg.nodes.some((n) => n.display_line === line && isLandable(cfg, n))

/** 그 줄의 착지 노드가 전부 도달 불가(⊥) 인가. */
export const isLineUnreachable = (cfg: CFG, line: number): boolean => {
  const on = cfg.nodes.filter((n) => n.display_line === line && isLandable(cfg, n))
  return on.length > 0 && on.every((n) => isDead(cfg, n.id))
}

/** 왜 도달 불가인가 — 죽은 구간을 거슬러 올라가 처음 만나는 살아 있는 조건 노드(그 분기가 ⊥). */
export function whyUnreachable(cfg: CFG, id: string): CFGNode | undefined {
  const ix = navIndex(cfg)
  const seen = new Set<string>()
  const stack = [id]
  while (stack.length) {
    const cur = stack.pop()!
    if (seen.has(cur)) continue
    seen.add(cur)
    for (const e of ix.pred.get(cur) ?? []) {
      if (!isDead(cfg, e.from)) {
        const pn = ix.byId.get(e.from)
        if (has(pn, 'condition')) return pn
      } else stack.push(e.from)
    }
  }
  return undefined
}

