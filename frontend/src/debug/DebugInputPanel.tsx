import { useState } from 'react'
import { useStore, keyOf } from '../state/store'
import type { AnalysisResult, DebugKind, DebugSlot, RangeValue } from '../types/result'
import { emptyValue, slotToAnnotation } from './serialize'
import { differentSelector, indexesOf, matchesTemplate, parseLabel, withIndexes } from './slotTemplate'

/** (2026-11-27) 블록 안 템플릿이 이 수 이상이면 처음엔 접고, 펼쳐도 앞의 이만큼만(+ 입력·오류 있는 것) 보인다 */
const COLLAPSE_AT = 8

// ★ 슬롯-주도: 분석이 슬롯(상태변수/파라미터/글로벌/IReturn)을 주고, 사용자는 값만 채움.
//   StateVar=상태변수 선언 시, Param=함수 파라미터, GlobalVar=static 화이트리스트,
//   IReturn=인터페이스 호출 statement 등장 시 자동 생성. (전부 함수-스코프)
export default function DebugInputPanel({ result }: { result: AnalysisResult }) {
  const activeContract = useStore((s) => s.activeContract)
  const activeFunction = useStore((s) => s.activeFunction)
  const debugInputs = useStore((s) => s.debugInputs)
  const setDebugValue = useStore((s) => s.setDebugValue)
  const removeDebugValue = useStore((s) => s.removeDebugValue)
  const renameDebugKey = useStore((s) => s.renameDebugKey)
  const applyDebug = useStore((s) => s.applyDebug)
  const analyzing = useStore((s) => s.analyzing)

  const valueErrors = useStore((s) => s.valueErrors)

  const apply = () => applyDebug(activeFunction)

  const fkey = keyOf(activeContract, activeFunction)
  const values = debugInputs[fkey] ?? {}

  const groups: DebugKind[] = ['StateVar', 'Param', 'GlobalVar', 'IReturn']
  const bySlot = (k: DebugKind) => result.debugSlots.filter((s) => s.kind === k)

  const valueOf = (slot: DebugSlot): RangeValue => values[slot.key] ?? emptyValue(slot)

  // (2026-10-23) 구체 인덱스로 지정한 입력 — 슬롯 키가 아닌 입력 키를 템플릿 슬롯 아래에 둔다
  const slotKeys = new Set(result.debugSlots.map((s) => s.key))
  const extraKeys = Object.keys(values).filter((k) => !slotKeys.has(k))
  const specOf = (slot: DebugSlot) => extraKeys.filter((k) => matchesTemplate(k, slot))
  const orphans = extraKeys.filter((k) => !result.debugSlots.some((s) => matchesTemplate(k, s)))
  const addSpecialized = (slot: DebugSlot) => {
    // 인덱스 자리를 0, 1, … 로 채운 새 키 (이미 있으면 다음 번호). 값은 비워 둔다(none = 미입력).
    for (let n = 0; ; n++) {
      const k = withIndexes(slot.key, indexesOf(slot.key).map(() => String(n)))
      if (!(k in values) && !slotKeys.has(k)) {
        setDebugValue(activeFunction, k, { t: 'none' })
        return
      }
    }
  }

  return (
    <div
      style={{ marginTop: 14 }}
      onKeyDown={(e) => {
        // 어느 입력 필드에서든 Enter → 전체 값 한 번에 적용(run).
        if (e.key === 'Enter') {
          e.preventDefault()
          apply()
        }
      }}
    >
      <div className="panel-title">Debug Input (슬롯 자동 · 값만 입력)</div>

      {result.appliedInputs === 0 && valueErrors.length === 0 && (
        <div className="cell-note" role="status">입력 없이 실행했다 — 모든 진입 값을 ⊤(모름)로 두었다.</div>
      )}

      {valueErrors.length > 0 && (
        <div className="value-errors">
          {valueErrors.map((e, i) => (
            <div key={i} className="value-error">⚠ {e.message}</div>
          ))}
        </div>
      )}

      {groups.map((g) => {
        const slots = bySlot(g)
        if (slots.length === 0) return null
        // (2026-11-27) 같은 컨테이너(첫 index 가 고르는 mapping·배열)의 슬롯은 한 블록으로 묶는다(ROADMAP 결정 3·4·12)
        const plain = slots.filter((s) => !s.container)
        const blocks = new Map<string, DebugSlot[]>()
        for (const s of slots) if (s.container) blocks.set(s.container, [...(blocks.get(s.container) ?? []), s])
        const rows = plain.map((slot) => (
          <div key={slot.key}>
            <SlotRow
              slot={slot}
              value={valueOf(slot)}
              onChange={(v) => setDebugValue(activeFunction, slot.key, v)}
              onSpecialize={slot.specializable ? () => addSpecialized(slot) : undefined}
            />
            {specOf(slot).map((k) => (
              <SpecializedRow
                key={k}
                slot={slot}
                entryKey={k}
                value={values[k]}
                onChange={(v) => setDebugValue(activeFunction, k, v)}
                onRename={(to) => renameDebugKey(activeFunction, k, to)}
                onRemove={() => removeDebugValue(activeFunction, k)}
              />
            ))}
          </div>
        ))
        for (const [c, bs] of blocks) {
          rows.push(
            <ContainerBlock
              key={`blk:${c}`}
              container={c}
              slots={bs}
              values={values}
              extraKeys={extraKeys}
              slotCells={result.slotCells ?? {}}
              errors={valueErrors.map((e) => e.message)}
              onChange={(k, v) => setDebugValue(activeFunction, k, v)}
              onRename={(k, to) => renameDebugKey(activeFunction, k, to)}
              onRemove={(k) => removeDebugValue(activeFunction, k)}
              onSpecialize={addSpecialized}
            />,
          )
        }
        // 전역(block/msg/tx)은 자주 안 건드리므로 접이식(기본 접힘) — State/Locals 전역 fold 와 일관.
        if (g === 'GlobalVar') {
          return (
            <details key={g} className="globals dbg-globals">
              <summary>@GlobalVar (block/msg/tx) · {slots.length}</summary>
              {rows}
            </details>
          )
        }
        return (
          <div key={g} className="dbg-group">
            <div className="dbg-group-title">@{g}</div>
            {rows}
          </div>
        )
      })}

      {orphans.length > 0 && (
        <div className="dbg-group">
          <div className="dbg-group-title">슬롯이 아닌 입력 (적용 시 오류)</div>
          {orphans.map((k) => (
            <div key={k} className="input-row">
              <span className="var bad" title={k}>{k}</span>
              <button className="spec-btn" onClick={() => removeDebugValue(activeFunction, k)}>×</button>
            </div>
          ))}
        </div>
      )}

      <button className="apply-btn" onClick={apply} disabled={analyzing}>
        {analyzing ? '적용 중…' : '▶ 적용 (Enter)'}
      </button>

      {analyzing && (
        <div className="analyzing-bar" role="status" aria-live="polite">
          <div className="analyzing-track">
            <div className="analyzing-fill" />
          </div>
          <span className="analyzing-label">백엔드 분석 중…</span>
        </div>
      )}

      <div style={{ fontSize: 11, color: '#666', marginTop: 6 }}>
        값 타입은 슬롯이 결정 (numeric→interval, bool→bool, address→symbolicAddress).
        interval 은 <b>한쪽을 비우면 무제약</b>(예: <code>[5, ]</code> = ≥5, <code>[ , 10]</code> = ≤10).
        <br />인덱스가 있는 경로는 <b>+ 원소</b>로 특정 원소(예: <code>pegObservations[0]</code>)에 값을 줄 수 있다.
        인덱스가 함수 지역변수(루프 <code>i</code> 등)인 경로는 진입 시점 값이 없으므로 원소를 지정해야만 입력된다.
        <br />mapping 은 <b>변수 방식</b>(코드의 <code>m[x]</code> 그대로 — 진입 시점 <code>x</code> 가 고르는 칸)과
        <b>값 방식</b>(칸을 구체값으로 추가) 중 하나로 입력한다. 동적 배열은 원소 목록이고 길이는 입력한 원소 수다.
        <br />storage 파라미터는 가리킬 곳을 고른다. 비우면 상태변수와 겹치지 않는 별도 칸이다.
        <br />값 입력 후 <b>적용</b>을 눌러야 실행됩니다. breakpoint 있으면 그 지점까지, 없으면 함수 끝(EXIT)에서 정지.
      </div>
    </div>
  )
}

function SlotRow({
  slot,
  value,
  onChange,
  onSpecialize,
}: {
  slot: DebugSlot
  value: RangeValue
  onChange: (v: RangeValue) => void
  onSpecialize?: () => void
}) {
  const sugg = slot.suggestion
  return (
    <div>
      <div className="input-row">
        <span className="var" title={slot.label}>
          {slot.label}
        </span>
        {slot.requiresIndex ? (
          <span className="needs-index" title="인덱스가 함수 지역변수라 진입 시점 값이 없다">원소 지정 필요</span>
        ) : (
          <ValueInput slot={slot} value={value} onChange={onChange} />
        )}
        {slot.opaque && <span className="badge opaque">opaque</span>}
        {slot.ireturnSites && slot.ireturnSites > 1 && (
          <span className="badge opaque" title="이 입력은 키에 맞는 호출 모두의 반환값 범위다">
            호출 {slot.ireturnSites}곳
          </span>
        )}
        {onSpecialize && (
          <button className="spec-btn" title="특정 원소(구체 인덱스)에 값 지정" onClick={onSpecialize}>
            + 원소
          </button>
        )}
      </div>
      {sugg && (
        <div className="ghost">
          ⇠ {preview(sugg.value)} ({sugg.backend}, {Math.round(sugg.confidence * 100)}%)
          <span className="accept" onClick={() => onChange(sugg.value)}>
            [Tab 수락]
          </span>
        </div>
      )}
    </div>
  )
}

/** 구체 인덱스 입력 행 — 경로의 인덱스 자리만 편집(나머지는 템플릿 고정). */
function SpecializedRow({
  slot,
  entryKey,
  value,
  onChange,
  onRename,
  onRemove,
}: {
  slot: DebugSlot
  entryKey: string
  value: RangeValue
  onChange: (v: RangeValue) => void
  onRename: (to: string) => void
  onRemove: () => void
}) {
  const idx = indexesOf(entryKey)
  const tmpl = indexesOf(slot.key)
  const other = differentSelector(entryKey, slot)
  return (
    <div>
    <div className="input-row spec-row">
      <span className="spec-key" title={entryKey}>
        <span className="badge custom">custom</span>
        {idx.map((x, n) => (
          <input
            key={n}
            className="num idx"
            defaultValue={x}
            title={`인덱스 ${n + 1} (소스: ${tmpl[n]})`}
            onBlur={(e) => {
              const next = idx.slice()
              next[n] = e.target.value.trim() || tmpl[n]
              onRename(withIndexes(slot.key, next))
            }}
          />
        ))}
      </span>
      <ValueInput slot={slot} value={value} onChange={onChange} />
      <button className="spec-btn" title="이 원소 입력 제거" onClick={onRemove}>
        ×
      </button>
    </div>
    {other && (
      <div className="spec-note">
        선택한 인덱스는 원본 코드의 인덱스와 다릅니다. 이 입력은 지정한 진입 저장 위치에 적용되며, 실제 코드의 접근 위치와
        항상 같다는 뜻은 아닙니다.
      </div>
    )}
    </div>
  )
}

function ValueInput({
  slot,
  value,
  onChange,
}: {
  slot: DebugSlot
  value: RangeValue
  onChange: (v: RangeValue) => void
}) {
  if (slot.valueKind === 'interval') {
    const v = value.t === 'interval' ? value : { t: 'interval' as const, lo: '', hi: '' }
    return (
      <span className="interval-in">
        [
        <input
          className="num"
          value={v.lo}
          placeholder="lo"
          onChange={(e) => onChange({ t: 'interval', lo: e.target.value, hi: v.hi })}
        />
        ,
        <input
          className="num"
          value={v.hi}
          placeholder="hi"
          onChange={(e) => onChange({ t: 'interval', lo: v.lo, hi: e.target.value })}
        />
        ]
      </span>
    )
  }
  if (slot.valueKind === 'bool') {
    const v = value.t === 'bool' ? value.v : 'any'
    return (
      <select value={v} onChange={(e) => onChange({ t: 'bool', v: e.target.value as 'true' | 'false' | 'any' })}>
        <option value="true">true</option>
        <option value="false">false</option>
        <option value="any">any</option>
      </select>
    )
  }
  if (slot.valueKind === 'address') {
    const id = value.t === 'address' ? value.id : 0
    return (
      <span className="interval-in">
        symbolicAddress
        <input
          className="num"
          value={String(id)}
          onChange={(e) => onChange({ t: 'address', id: Number(e.target.value) || 0 })}
        />
      </span>
    )
  }
  if (slot.valueKind === 'location') {
    return <LocationInput slot={slot} value={value} onChange={onChange} />
  }
  if (slot.valueKind === 'enum') {
    // (2026-11-27) 멤버 하나를 고른다. 비우면 모름(모든 멤버)
    const v = value.t === 'enum' ? value.v : ''
    const members = slot.enumMembers ?? []
    return members.length > 0 ? (
      <select value={v} onChange={(e) => onChange({ t: 'enum', v: e.target.value })}>
        <option value="">(모름)</option>
        {members.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    ) : (
      <input
        className="num"
        value={v}
        placeholder="멤버 이름"
        onChange={(e) => onChange({ t: 'enum', v: e.target.value })}
      />
    )
  }
  // array/bytes/enum 은 advanced (후순위)
  return <span style={{ color: '#666', fontSize: 11 }}>({slot.valueKind} — advanced)</span>
}

/** (2026-11-27) 리터럴 index 만 쓴 슬롯(`xs[1]`)은 이미 구체 칸 — 방식과 무관하게 바로 입력한다 (엔진과 같은 판정) */
const TYPE_WORD = /^(address|payable|bool|string|bytes\d*|u?int\d*)$/
const literalSlot = (s: DebugSlot) =>
  indexesOf(s.key).every((x) => (x.match(/(?<![\w$])[A-Za-z_$][\w$]*/g) ?? []).every((n) => TYPE_WORD.test(n)))

/** 키의 첫 index 를 숫자로 (배열 길이 계산용). 아니면 null. */
function firstIndex(k: string): number | null {
  const x = indexesOf(k)[0]
  const n = x !== undefined && /^\d+$/.test(x.trim()) ? Number(x) : NaN
  return Number.isInteger(n) ? n : null
}

/** (2026-11-27) 한 컨테이너(mapping·배열)의 슬롯 블록. */
function ContainerBlock({
  container,
  slots,
  values,
  extraKeys,
  slotCells,
  errors,
  onChange,
  onRename,
  onRemove,
  onSpecialize,
}: {
  container: string
  slots: DebugSlot[]
  values: Record<string, RangeValue>
  extraKeys: string[]
  slotCells: Record<string, string>
  errors: string[]
  onChange: (k: string, v: RangeValue) => void
  onRename: (k: string, to: string) => void
  onRemove: (k: string) => void
  onSpecialize: (slot: DebugSlot) => void
}) {
  const kind = slots[0].containerKind
  const isMap = kind === 'mapping'
  const entered = (k: string) => values[k] !== undefined && values[k].t !== 'none'
  const specOf = (s: DebugSlot) => extraKeys.filter((k) => matchesTemplate(k, s))
  const valueEntries = slots.flatMap(specOf)
  const varEntries = slots.filter((s) => !literalSlot(s) && entered(s.key)).map((s) => s.key)
  const canVar = isMap && slots.some((s) => s.varMode)
  const [modePick, setMode] = useState<'var' | 'value' | null>(null)
  const [more, setMore] = useState(false)
  // 입력이 있으면 그 방식, 없으면 사용자가 고른 방식, 그것도 없으면 가능할 때 변수 방식
  const mode: 'var' | 'value' =
    varEntries.length > 0 ? 'var' : valueEntries.length > 0 ? 'value' : (modePick ?? (canVar ? 'var' : 'value'))
  const errOf = (k: string) => errors.find((m) => m.startsWith(`${k}:`))
  const blockErr = errors.find((m) => m.startsWith(`${container}:`))
  const why = slots.find((s) => s.varModeReason)?.varModeReason

  // 동적 배열 길이 = 입력한 가장 큰 첫 index + 1 (엔진 finalize_entry_arrays 와 같은 규칙)
  let length: number | null = null
  if (kind === 'array') {
    length = 0
    const keys = [...valueEntries, ...slots.filter((s) => literalSlot(s) && entered(s.key)).map((s) => s.key)]
    for (const k of keys) {
      const i = firstIndex(k)
      if (i !== null && entered(k)) length = Math.max(length, i + 1)
    }
  } else if (kind === 'static-array') length = slots[0].containerLength ?? null

  const important = (s: DebugSlot) => entered(s.key) || specOf(s).length > 0 || !!errOf(s.key)
  const shown = more || slots.length <= COLLAPSE_AT ? slots : slots.filter((s, i) => i < COLLAPSE_AT || important(s))
  const hidden = slots.length - shown.length
  const startOpen = slots.length < COLLAPSE_AT || slots.some(important) || !!blockErr

  const addElement = () => {
    // 다음 원소 = index `length` — 첫 템플릿의 첫 index 자리에 놓는다
    const s = slots[0]
    const k = withIndexes(s.key, indexesOf(s.key).map((x, n) => (n === 0 ? String(length ?? 0) : x)))
    if (!(k in values)) onChange(k, { t: 'none' })
    else onSpecialize(s)
  }

  return (
    <details className={`dbg-block${blockErr ? ' has-error' : ''}`} open={startOpen}>
      <summary>
        <span className="blk-name">{container}</span>
        <span className="badge kind">{isMap ? 'mapping' : kind === 'array' ? '동적 배열' : '정적 배열'}</span>
        {length !== null && (
          <span className="blk-len" title={kind === 'array' ? '진입 길이 = 입력한 원소 수 (가장 큰 index + 1)' : '선언 길이'}>
            length = {length}
          </span>
        )}
        <span className="blk-count">
          {slots.length}칸{valueEntries.length > 0 ? ` · 입력 ${valueEntries.length}` : ''}
        </span>
      </summary>
      {blockErr && <div className="value-error">⚠ {blockErr.slice(container.length + 1).trim()}</div>}
      {isMap && (
        <div className="mode-toggle" role="radiogroup" aria-label={`${container} 입력 방식`}>
          <button
            className={mode === 'var' ? 'on' : ''}
            disabled={!canVar || valueEntries.length > 0}
            title={
              !canVar
                ? (why ?? '변수 방식을 쓸 수 없다')
                : valueEntries.length > 0
                  ? '값 방식 입력을 먼저 지운다'
                  : '코드의 index 식 그대로 — 진입 시점 값이 고르는 칸'
            }
            onClick={() => setMode('var')}
          >
            변수 방식
          </button>
          <button
            className={mode === 'value' ? 'on' : ''}
            disabled={varEntries.length > 0}
            title={varEntries.length > 0 ? '변수 방식 입력을 먼저 지운다' : '칸을 구체값으로 추가한다(추가하지 않은 칸 = ⊤)'}
            onClick={() => setMode('value')}
          >
            값 방식
          </button>
          {!canVar && why && <span className="mode-why">{why}</span>}
        </div>
      )}
      {shown.map((slot) => {
        const direct = literalSlot(slot) || (isMap && mode === 'var' && !!slot.varMode)
        const cell = slotCells[slot.key]
        const err = errOf(slot.key)
        return (
          <div key={slot.key} className={err ? 'row-error' : ''}>
            {direct ? (
              <SlotRow slot={slot} value={values[slot.key] ?? emptyValue(slot)} onChange={(v) => onChange(slot.key, v)} />
            ) : (
              <div className="input-row">
                <span className="var tmpl" title={slot.label}>
                  {slot.label}
                </span>
                {isMap && mode === 'var' ? (
                  <span className="needs-index">{slot.varModeReason ?? '변수 방식 불가'}</span>
                ) : (
                  <button className="spec-btn" onClick={() => onSpecialize(slot)}>
                    {isMap ? '+ 칸 추가' : '+ 원소'}
                  </button>
                )}
              </div>
            )}
            {cell && direct && <div className="cell-note">지금 → {cell}</div>}
            {err && <div className="value-error">⚠ {err.slice(slot.key.length + 1).trim()}</div>}
            {!direct &&
              specOf(slot).map((k) => {
                const e = errOf(k)
                return (
                  <div key={k}>
                    <SpecializedRow
                      slot={slot}
                      entryKey={k}
                      value={values[k]}
                      onChange={(v) => onChange(k, v)}
                      onRename={(to) => onRename(k, to)}
                      onRemove={() => onRemove(k)}
                    />
                    {e && <div className="value-error">⚠ {e.slice(k.length + 1).trim()}</div>}
                  </div>
                )
              })}
          </div>
        )
      })}
      {hidden > 0 && (
        <button className="spec-btn more" onClick={() => setMore(true)}>
          더 보기 ({hidden})
        </button>
      )}
      {kind === 'array' && (
        <button className="spec-btn add-elem" onClick={addElement}>
          + 원소 추가
        </button>
      )}
    </details>
  )
}

/** (2026-11-27) 타깃 storage 파라미터가 가리킬 곳 — 후보(`poolInfo[]`)를 고르고 `[]` 자리에 진입 시점 index 식을 넣는다. */
function LocationInput({
  slot,
  value,
  onChange,
}: {
  slot: DebugSlot
  value: RangeValue
  onChange: (v: RangeValue) => void
}) {
  const opts = slot.locationOptions ?? []
  const hints = slot.locationIndexHints ?? {}
  const path = value.t === 'location' ? value.path : ''
  // 현재 경로가 어느 후보인가 — index 자리를 비운 모양이 같다
  const shape = (k: string) => withIndexes(k, indexesOf(k).map(() => ''))
  const tmpl = path ? (opts.find((o) => shape(o) === shape(path)) ?? '') : ''
  const idx = path && tmpl ? indexesOf(path) : []
  const set = (t: string, xs: string[]) =>
    onChange(t ? { t: 'location', path: withIndexes(t, indexesOf(t).map((_, n) => xs[n] ?? '')) } : { t: 'none' })
  return (
    <span className="loc-in">
      <select value={tmpl} onChange={(e) => set(e.target.value, [])} title="이 storage 파라미터가 가리킬 곳">
        <option value="">(별도 칸)</option>
        {opts.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      {tmpl &&
        parseLabel(tmpl)
          .accs.filter((a) => a.kind === 'index')
          .map((_, n) => {
            // 키 타입이 맞는 진입 이름 제안 (자동으로 채우지 않는다)
            const listId = `loc-hint-${slot.key}-${n}`
            const sugg = hints[tmpl]?.[n] ?? []
            return (
              <span key={n}>
                <input
                  className="num idx"
                  value={idx[n] ?? ''}
                  placeholder={sugg.length ? `예: ${sugg[0]}` : `index ${n + 1}`}
                  title="진입 시점에 정해지는 식 — 파라미터·상태변수·전역·리터럴"
                  list={sugg.length ? listId : undefined}
                  onChange={(e) => {
                    const next = idx.slice()
                    next[n] = e.target.value
                    set(tmpl, next)
                  }}
                />
                {sugg.length > 0 && (
                  <datalist id={listId}>
                    {sugg.map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                )}
              </span>
            )
          })}
    </span>
  )
}

function preview(v: RangeValue): string {
  return v.t === 'interval' ? `[${v.lo},${v.hi}]` : v.t === 'bool' ? v.v : v.t === 'address' ? `addr ${v.id}` : '-'
}

// (참고) 직렬화 확인용 — 실엔진 연결 시 사용
export { slotToAnnotation }
