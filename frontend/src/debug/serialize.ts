// 슬롯 + 값 → g4 캐논 어노테이션 텍스트.
// engine_api는 이 텍스트만 파싱해 lineRef(debug는 함수 body top)에 등록 — 코드 삽입/밀림 없음.
// Parser/Solidity.g4 의 debugInput / debugValue 규칙과 1:1.

import type { DebugSlot, RangeValue } from '../types/result'

export function valueToText(v: RangeValue): string {
  switch (v.t) {
    case 'interval':
      return `[${v.lo},${v.hi}]`
    case 'bool':
      return v.v // true | false | any
    case 'address':
      return `symbolicAddress ${v.id}`
    case 'none':
      return ''
  }
}

export function slotToAnnotation(slot: DebugSlot, value: RangeValue): string {
  const val = valueToText(value)
  if (!val) return ''
  switch (slot.kind) {
    case 'StateVar':
      return `// @StateVar ${slot.key} = ${val}`
    case 'Param':
      return `// @Param ${slot.key} = ${val}`
    case 'GlobalVar':
      return `// @GlobalVar ${slot.key} = ${val}`
    case 'IReturn':
      // slot.key 예: "token.balanceOf()" → 그대로 캐논 형태
      return `// @IReturn ${slot.key} = ${val}`
  }
}

// 기본값: 슬롯 타입별 빈 값
export function emptyValue(slot: DebugSlot): RangeValue {
  switch (slot.valueKind) {
    case 'interval':
      return { t: 'interval', lo: '', hi: '' }
    case 'bool':
      return { t: 'bool', v: 'any' }
    case 'address':
      return { t: 'address', id: 0 }
    default:
      return { t: 'none' }
  }
}
