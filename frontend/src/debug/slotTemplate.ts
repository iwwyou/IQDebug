// (2026-10-23) 디버그 슬롯의 구체 인덱스 지정 — 엔진 `engine_api._parse_label` / `EngineSession.match_slot` 과 같은 규칙.
//   기본 슬롯은 소스에 쓴 접근식 그대로(`poolInfo[_pid]`), 사용자가 원하면 인덱스 자리를 구체값으로 바꾼 키
//   (`poolInfo[1]`)로 입력한다. 최종 검증(바꾼 인덱스가 지역변수를 쓰지 않는가 등)은 엔진이 한다.
import type { DebugSlot } from '../types/result'

export type Accessor = { kind: 'member' | 'index' | 'call'; text: string }

/** 라벨 → 루트 + 접근자. 괄호 균형으로 자른다. */
export function parseLabel(label: string): { root: string | null; accs: Accessor[] } {
  const m = /^[A-Za-z_$][\w$]*/.exec(label)
  if (!m) return { root: null, accs: [] }
  const accs: Accessor[] = []
  let i = m[0].length
  while (i < label.length) {
    const c = label[i]
    if (c === '.') {
      const m2 = /^[A-Za-z_$][\w$]*/.exec(label.slice(i + 1))
      if (!m2) break
      accs.push({ kind: 'member', text: m2[0] })
      i += 1 + m2[0].length
    } else if (c === '[' || c === '(') {
      let depth = 0
      let j = i
      for (; j < label.length; j++) {
        if (label[j] === '[' || label[j] === '(') depth++
        else if (label[j] === ']' || label[j] === ')') {
          depth--
          if (depth === 0) break
        }
      }
      accs.push({ kind: c === '[' ? 'index' : 'call', text: label.slice(i + 1, j) })
      i = j + 1
    } else break
  }
  return { root: m[0], accs }
}

const idents = (t: string) => new Set(t.match(/(?<![\w$])[A-Za-z_$][\w$]*/g) ?? []) // 0x1a 의 x1a 제외

/** 키가 이 템플릿 슬롯의 인덱스 자리만 바꾼 것인가 (화면 그룹핑용 — 허용 여부의 최종 판정은 엔진 `match_slot`). */
export function matchesTemplate(key: string, slot: DebugSlot): boolean {
  if (!slot.specializable || key === slot.key) return false
  const a = parseLabel(key)
  const b = parseLabel(slot.key)
  if (a.root === null || a.root !== b.root || a.accs.length !== b.accs.length) return false
  return a.accs.every((x, n) => x.kind === b.accs[n].kind && (x.kind === 'index' || x.text === b.accs[n].text))
}

/** 소스와 **다른 선택자**를 쓴 원소 지정인가 — 소스 인덱스가 진입 시점 값(지역변수 아님)인데 새 이름을 들여온 경우.
 *  (`[index]` → `[0]` 처럼 지역 인덱스를 구체화하는 건 의도된 사용이라 해당 없음.) */
export function differentSelector(key: string, slot: DebugSlot): boolean {
  if (slot.requiresIndex) return false
  const a = indexesOf(key)
  const b = indexesOf(slot.key)
  return a.some((x, n) => {
    const allowed = idents(b[n] ?? '')
    return [...idents(x)].some((id) => !allowed.has(id))
  })
}

/** 템플릿 키에서 n 번째 인덱스 자리만 바꾼 키. */
export function withIndexes(slotKey: string, indexes: string[]): string {
  const { root, accs } = parseLabel(slotKey)
  if (root === null) return slotKey
  let k = 0
  let out = root
  for (const a of accs) {
    if (a.kind === 'member') out += `.${a.text}`
    else if (a.kind === 'call') out += `(${a.text})`
    else out += `[${indexes[k++] ?? a.text}]`
  }
  return out
}

export function indexesOf(key: string): string[] {
  return parseLabel(key).accs.filter((a) => a.kind === 'index').map((a) => a.text)
}
