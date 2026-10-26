"""인덱스 저장소(mapping / array) 의미론 — 2026-10-23.

디버그 입력은 소스의 접근식(`m[a]`, `arr[i]`) 에 값을 준다. 그 의미는 "이 실행에서 선택자 a 가 고르는 위치의 진입 값" 이다
(∀σ ⊨ D: σ(m)[σ(a)] ∈ v). 이 모듈은 그 이름 기반 입력을 유지하면서 read / write / alias 를 sound 하게 처리한다.

  키 해석 resolve()  : 선택자 식 → Concrete(구체 키) · Symbolic(순수한 선택자 식) · Range(부수효과 있는 식) · Bottom
  선택 select()      : Concrete → 구체 셀(summary 에서 구체화) · Symbolic → 이 프레임의 선택자 셀(없으면 겹칠 수 있는 셀들과
                       summary 의 join 으로 만든다) · Range → 저장하지 않는 가상 셀(같은 join)
  쓰기               : 선택한 위치는 strong update(update_left_var 가 함), 끝난 뒤 propagate() 가
                       겹칠 수 있는 **다른** 셀에 weak update, 선택자가 미확정이면 summary 에도 weak update
  진입 제약 assume   : 슬롯이 고르는 셀에 값을 놓는다(쓰기 아님 — 서로 weak update 하지 않는다). 선택자 셀이면 summary 에도 join
  무효화             : 선택자가 읽는 변수에 대입하면 그 셀을 버린다(바인딩만 — 쓰기 효과는 이미 다른 셀·summary 에 있다).
                       callee 진입/복귀 때 그 함수 프레임의 셀도 버린다.

불변식: 구체화하지 않은 모든 위치의 실제 값 ∈ summary. 그래서 선택자 셀을 버려도, 새 셀을 summary 에서 만들어도 sound 하다.

호출 대응(2026-10-23): callee 파라미터가 호출자의 순수한 선택자 식을 그대로 받았으면(`_mint(foreignAsset)` → `asset`),
callee 안의 `m[asset]` 은 호출자 프레임의 `m[foreignAsset]` 과 **같은 셀**이다(파라미터를 다시 대입하기 전까지 must-alias).
그래서 타깃 진입 이름으로 준 디버그 입력이 callee 접근에도 그대로 쓰인다(engine_api 슬롯 도출이 같은 번역을 한다).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.Interval import IntegerInterval, UnsignedIntegerInterval, BoolInterval
from Domain.AddressSet import AddressSet
from Domain.BytesSet import BytesSet
from Domain.Variable import Variables, ArrayVariable, MappingVariable, StructVariable, rename_tree
from Domain.IR import Expression
from Utils.Helper import VariableEnv

_INTERVALS = (IntegerInterval, UnsignedIntegerInterval, BoolInterval)
_ADDR_KEY = re.compile(r"AddressSet\(\{([\d, ]*)\}\)")
# 형 변환처럼 쓰는 호출 — 순수하다
_CAST_FUNCS = re.compile(r"(address|payable|bool|string|bytes\d*|u?int\d*)$")
_IMPURE_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>=", "++", "--", "delete"}
# 디버그 입력(intentUnit) 의 접근식 문맥 → 프로그램 식 문맥 (같은 평가기를 쓰기 위해)
_DEBUG_CTX = {"IntentIndexAccess": "IndexAccessContext", "IntentMemberAccess": "MemberAccessContext",
              "VarRefIndexAccess": "IndexAccessContext", "VarRefMemberAccess": "MemberAccessContext",
              "VarRefBase": "IdentifierExpContext"}


class KeyRes:
    __slots__ = ("kind", "key", "vals", "deps", "scope")

    def __init__(self, kind, key=None, vals=None, deps=frozenset(), scope=None):
        self.kind = kind      # "concrete" | "symbolic" | "range" | "bottom"
        self.key = key        # concrete: 셀 키 문자열(배열은 십진 인덱스) · symbolic: 선택자 식 텍스트
        self.vals = vals      # 키의 추상값 (alias 판정)
        self.deps = deps      # symbolic: 선택자 식이 읽는 변수 이름들
        self.scope = scope    # symbolic: 선택자가 속한 프레임(함수) — 호출 대응이면 호출자 프레임


def is_composite(v):
    return isinstance(v, (StructVariable, ArrayVariable, MappingVariable))


class Storage:

    def __init__(self, an: "ContractAnalyzer"):
        self.an = an
        self.dep_names: set[str] = set()     # 어떤 선택자 셀이든 의존한 적 있는 이름 (무효화 빠른 경로)
        self._assume_pending: list = []      # assume 로 고른 선택자 셀 — 값이 놓인 뒤 summary 에 join
        # 호출 프레임 스택: (함수 이름, {파라미터: (호출자 프레임의 선택자 텍스트, 그 프레임, 의존 변수)})
        self.frames: list = []

    @property
    def ev(self):
        return self.an.evaluator

    def _scope(self):
        return self.an.current_target_function

    # ───────────────────────────── 호출 대응 ─────────────────────────────
    def _aliases(self):
        if self.frames and self.frames[-1][0] == self._scope():
            return self.frames[-1][1]
        return {}

    def _alias_of(self, arg):
        """호출 인자 식 → 호출자 프레임 기준 선택자 (텍스트, 프레임, 의존 변수). 순수하지 않으면 None."""
        if not isinstance(arg, Expression):
            return None
        al = self._aliases()
        if arg.identifier is not None and arg.base is None and arg.function is None and arg.identifier in al:
            return al[arg.identifier]
        deps = _pure_deps(arg)
        if deps is None or deps & al.keys():
            return None                         # 대응을 이어 받을 수 없는 혼합식 — 대응 없음(sound, 비정밀)
        return (str(arg), self._scope(), deps)

    def callee_aliases(self, fcfg, call_expr, kind):
        """invoke_callee 가 부른다(호출자 프레임에서) — 파라미터마다 호출자 선택자 대응."""
        params = list(getattr(fcfg, "parameters", []) or [])
        if call_expr is None or not params:
            return {}
        args = list(getattr(call_expr, "arguments", None) or [])
        named = dict(getattr(call_expr, "named_arguments", None) or {})
        exprs = [None] * len(params)
        off = 0
        if kind == "library" and len(params) == len(args) + len(named) + 1:
            fn = call_expr.function                # using-for 수신자 `x.f(...)` → 첫 파라미터
            exprs[0] = getattr(fn, "base", None) if fn is not None else None
            off = 1
        for i, a in enumerate(args):
            if off + i < len(exprs):
                exprs[off + i] = a
        for name, a in named.items():
            if name in params:
                exprs[params.index(name)] = a
        out = {}
        for p, a in zip(params, exprs):
            t = self._alias_of(a)
            if t is not None:
                out[p] = t
        return out

    def push_frame(self, fn_name, aliases):
        self.frames.append((fn_name, dict(aliases or {})))

    def pop_frame(self):
        self.frames.pop()

    # ───────────────────────────── 키 해석 ─────────────────────────────
    def resolve(self, c, index_expr: Expression, variables, *, debug=False) -> KeyRes:
        expr = _program_expr(index_expr) if debug else index_expr
        v = self.ev.evaluate_expression(expr, variables, None, None)
        if isinstance(v, Variables) and not is_composite(v):
            v = v.value
        v = self._coerce_key(c, v)
        if _is_bottom(v):
            return KeyRes("bottom", vals=v)
        key = _concrete_key(v)
        if key is not None:
            return KeyRes("concrete", key=key, vals=_point(v))
        deps = _pure_deps(expr)
        if deps is None:
            return KeyRes("range", vals=v)
        if expr.identifier is not None and expr.base is None and expr.function is None:
            al = self._aliases().get(expr.identifier)
            if al is not None:                    # 호출자 선택자와 같은 위치 (호출 대응)
                return KeyRes("symbolic", key=al[0], vals=v, deps=al[2], scope=al[1])
        return KeyRes("symbolic", key=str(index_expr), vals=v, deps=deps, scope=self._scope())

    @staticmethod
    def _coerce_key(c, v):
        """키 타입으로 맞춘다 — address 키 매핑에 정수(리터럴) → 주소 집합 (DebugInitializer._resolve_mapping_key 와 같은 규칙)."""
        if isinstance(c, MappingVariable):
            kt = c.typeInfo.mappingKeyType
            if getattr(kt, "elementaryTypeName", None) == "address":
                if isinstance(v, int) and not isinstance(v, bool):
                    return AddressSet(ids={v})
                if isinstance(v, _INTERVALS):
                    if v.is_bottom():
                        return AddressSet(ids=set())
                    if v.min_value == v.max_value:
                        return AddressSet(ids={v.min_value})
                    return AddressSet.top()
        return v

    # ───────────────────────────── 셀 열거 · alias ─────────────────────────────
    @staticmethod
    def cells(c):
        """(kind, key, obj, vals) — 구체 셀과 선택자 셀 전부."""
        out = []
        if isinstance(c, MappingVariable):
            for k, obj in c.mapping.items():
                meta = c.sym.get(k)
                out.append(("sym", k, obj, meta["vals"]) if meta else ("con", k, obj, _key_point(k)))
        else:
            for i, obj in sorted(c.elements.items()):
                out.append(("con", str(i), obj, i))
            for k, obj in c.sym_cells.items():
                out.append(("sym", k, obj, c.sym[k]["vals"]))
        return out

    @staticmethod
    def _has_unmaterialized(c, res: KeyRes) -> bool:
        """res 가 고를 수 있는 위치 중 구체 셀이 없는 위치가 있는가 (없으면 summary 를 join 할 필요가 없다)."""
        if isinstance(c, ArrayVariable):
            # (2026-10-23, 희소 배열) 정적 배열도 구체화되지 않은 원소가 있다 — res 범위 안 위치 수 > 구체 셀 수 이면 True
            hi = _hi(res.vals)
            n = c.static_length()
            if n is not None:
                hi = n - 1 if hi is None else min(hi, n - 1)
            if hi is None:
                return True
            lo = max(0, getattr(res.vals, "min_value", 0) if isinstance(res.vals, _INTERVALS) else 0)
            if hi < lo:
                return False
            have = sum(1 for k in c.elements if lo <= k <= hi)
            return have < hi - lo + 1
        v = res.vals
        if isinstance(v, _INTERVALS) and v.max_value - v.min_value < 64:
            have = {k for k in c.mapping if k not in c.sym}
            return any(str(n) not in have for n in range(v.min_value, v.max_value + 1))
        if isinstance(v, AddressSet) and not v.is_top:
            have = {k for k in c.mapping if k not in c.sym}
            return any(str(AddressSet(ids={n})) not in have for n in v.ids)
        return True

    def _joined(self, c, res: KeyRes, label):
        """res 가 고를 수 있는 모든 위치 값의 join — 겹칠 수 있는 셀들 + (구체화 안 된 위치가 있으면) summary."""
        parts = [obj for _k, _key, obj, vals in self.cells(c) if _may_equal(res.vals, vals)]
        if not parts or self._has_unmaterialized(c, res):
            parts.append(c.summary if c.summary is not None else c.fresh_default())
        out = VariableEnv.copy_single_variable(parts[0])
        for p in parts[1:]:
            out = VariableEnv._merge_values(out, p, "join")
            out = VariableEnv.copy_single_variable(out)
        rename_tree(out, f"{c.identifier}[{label}]")
        return out

    # ───────────────────────────── 선택 ─────────────────────────────
    def select(self, c, res: KeyRes, *, create=True):
        """res 가 고르는 셀 객체. Range/Bottom 이면 저장하지 않는 가상 셀."""
        if res.kind == "bottom":
            return c.fresh_entry("⊥") if isinstance(c, MappingVariable) else c.fresh_element("⊥")
        if res.kind == "concrete":
            if isinstance(c, MappingVariable):
                return c.get_or_create(res.key)
            idx = int(res.key)
            if idx < 0:
                raise IndexError(f"negative index {idx} for array '{c.identifier}'")
            if not c.in_static_range(idx):
                # 정적 배열 범위 밖 = 이 상태에선 revert(Panic 0x32). 경로 차단은 Phase 5 — 지금은 저장하지 않는 ⊤ 셀.
                return c._create_new_array_element(idx)
            return c.get_or_create_element(idx)
        if res.kind == "symbolic":
            store = c.mapping if isinstance(c, MappingVariable) else c.sym_cells
            meta = c.sym.get(res.key)
            if meta is not None and res.key in store:
                if meta["scope"] == res.scope and meta["deps"] == res.deps:
                    return store[res.key]
                # 같은 텍스트지만 다른 프레임(callee 의 같은 이름 파라미터 등) — 같은 위치라는 근거가 없다
                return self._joined(c, res, res.key)
            obj = self._joined(c, res, res.key)
            if create:
                store[res.key] = obj
                c.sym[res.key] = {"scope": res.scope, "deps": res.deps, "vals": res.vals}
                self.dep_names |= res.deps
            return obj
        return self._joined(c, res, "?")

    # ───────────────────────────── 읽기 ─────────────────────────────
    def read(self, c, index_expr, variables):
        res = self.resolve(c, index_expr, variables)
        return self.select(c, res)

    # ───────────────────────────── 쓰기 후 전파 ─────────────────────────────
    def propagate(self, c, res: KeyRes, obj, path, variables):
        """선택한 셀 obj 아래 `path` 위치에 쓰기가 끝난 뒤: 겹칠 수 있는 다른 셀의 **같은 path 위치** ⊔= 쓴 값,
        미확정 선택자면 summary 의 같은 위치에도. (다른 셀 C 와 같은 위치인 실행에선 C 의 그 위치 = 쓴 값, 아닌 실행에선 그대로
        → join 이 정확한 합집합이다. path 밖의 필드는 이 대입이 쓰지 않았으므로 건드리지 않는다.)"""
        if res.kind == "bottom":
            return
        written = self._follow(obj, path, variables)
        for kind, _key, cell, vals in self.cells(c):
            if cell is obj:
                continue
            if res.kind == "concrete" and kind == "con":
                continue                                  # 서로 다른 구체 키는 겹치지 않는다
            if _may_equal(res.vals, vals):
                self._weak_write(cell, path, written, variables)
        if res.kind in ("symbolic", "range") and self._has_unmaterialized(c, res):
            if c.summary is None:
                c.summary = c.fresh_default()
            self._weak_write(c.summary, path, written, variables)

    def _follow(self, obj, path, variables):
        for kind, x in path:
            if kind == "member":
                obj = obj.members[x]
            else:
                obj = self.select(obj, self.resolve(obj, x, variables))
        return obj

    def _weak_write(self, cell, path, written, variables):
        """cell 아래 path 위치 ⊔= written. 중간 인덱스 단계는 그 컨테이너 안에서도 겹치는 셀·summary 로 전파한다."""
        if not path:
            _join_into(cell, written)
            return
        kind, x = path[0]
        if kind == "member":
            self._weak_write(cell.members[x], path[1:], written, variables)
            return
        res = self.resolve(cell, x, variables)
        sub = self.select(cell, res)
        self._weak_write(sub, path[1:], written, variables)
        self.propagate(cell, res, sub, path[1:], variables)

    # ───────────────────────────── 진입 제약 (디버그 입력) ─────────────────────────────
    def assume_select(self, c, index_expr, variables, label):
        res = self.resolve(c, index_expr, variables, debug=True)
        if res.kind == "range":
            raise ValueError(f"디버그 입력 `{label}`: 인덱스 `{index_expr}` 는 부수효과가 있는 식이라 위치를 정할 수 없다")
        if res.kind == "bottom":
            raise ValueError(f"디버그 입력 `{label}`: 인덱스 `{index_expr}` 의 값이 ⊥ 이다")
        # (2026-10-23) 정적 배열의 **존재하지 않는 위치**에 놓은 입력 = 잘못된 디버깅 입력(그런 진입 상태가 없다).
        #   예전엔 저장하지 않는 가상 셀에 써서 입력이 조용히 사라졌다. 인덱스 값 일부만 범위 밖인 경우는 미결정이라 그대로 둔다.
        if isinstance(c, ArrayVariable) and not c.typeInfo.isDynamicArray:
            n = c.static_length()
            lo = (int(res.key) if res.kind == "concrete"
                  else getattr(res.vals, "min_value", None) if isinstance(res.vals, _INTERVALS) else None)
            if lo is not None and lo >= n:
                raise ValueError(f"잘못된 디버깅 입력 `{label}`: 인덱스 `{index_expr}` 가 길이 {n} 인 정적 배열 "
                                 f"`{c.identifier}` 의 범위 밖이다 (그런 위치가 없다)")
        obj = self.select(c, res)
        if res.kind == "symbolic":
            self._assume_pending.append((c, res, obj))
        return obj

    def after_assume(self):
        """값을 놓은 뒤: 선택자 셀의 값을 summary 에도 join — '구체화 안 된 위치 ∈ summary' 불변식(선택자가 구체화 안 된 위치를
        고를 수 있으므로). summary 가 ⊤(D5)이면 아무것도 바뀌지 않는다."""
        pend, self._assume_pending = self._assume_pending, []
        for c, res, obj in pend:
            if self._has_unmaterialized(c, res):
                if c.summary is None:
                    c.summary = c.fresh_default()
                _join_into(c.summary, obj)

    # ───────────────────────────── 무효화 ─────────────────────────────
    def on_assign(self, variables, name):
        """변수 name 에 대입 — name 을 읽는 선택자 셀의 바인딩을 버린다 (모든 프레임; 과하게 버리는 것은 sound).
        호출 대응도 끊는다: 현재 프레임의 파라미터 name 을 다시 대입했거나, 대응이 name 에 의존하면."""
        if self.frames:
            top = self._aliases()
            top.pop(name, None)
            for _fn, al in self.frames:
                for p in [p for p, t in al.items() if name in t[2]]:
                    del al[p]
        if name not in self.dep_names:
            return
        for c in _containers(variables):
            for k in [k for k, m in c.sym.items() if name in m["deps"]]:
                _drop_cell(c, k)

    def purge_scope(self, variables, scope):
        """callee 진입/복귀 — 그 함수 프레임에서 만든 선택자 셀을 버린다(같은 이름의 다음 호출이 낡은 셀을 잡지 않게)."""
        for c in _containers(variables):
            for k in [k for k, m in c.sym.items() if m["scope"] == scope]:
                _drop_cell(c, k)


# ═════════════════════════════ 헬퍼 ═════════════════════════════
def _drop_cell(c, k):
    c.sym.pop(k, None)
    if isinstance(c, MappingVariable):
        c.mapping.pop(k, None)
    else:
        c.sym_cells.pop(k, None)


def _containers(variables):
    seen, out = set(), []

    def walk(v):
        if id(v) in seen:
            return
        seen.add(id(v))
        if isinstance(v, MappingVariable):
            out.append(v)
            for e in list(v.mapping.values()):
                walk(e)
            if v.summary is not None:
                walk(v.summary)
        elif isinstance(v, ArrayVariable):
            out.append(v)
            for e in list(v.elements.values()):
                walk(e)
            for e in list(v.sym_cells.values()):
                walk(e)
            if v.summary is not None:
                walk(v.summary)
        elif isinstance(v, StructVariable):
            for e in v.members.values():
                walk(e)

    for v in (variables or {}).values():
        walk(v)
    return out


def _join_into(cell, obj):
    """cell ⊔= obj (제자리 — cell 을 참조하는 쪽이 그대로 새 값을 본다). 식별자는 cell 것을 유지."""
    if is_composite(cell):
        merged = VariableEnv._merge_values(cell, obj, "join")
        merged = VariableEnv.copy_single_variable(merged)
        ident = cell.identifier
        cell.__dict__.update(merged.__dict__)
        rename_tree(cell, ident)
    else:
        cell.value = VariableEnv._merge_values(cell.value, getattr(obj, "value", obj), "join")


def _program_expr(e):
    """디버그 입력(intentUnit) 식을 프로그램 식 문맥으로 바꾼 사본."""
    if not isinstance(e, Expression):
        return e
    import copy
    n = copy.copy(e)
    n.context = _DEBUG_CTX.get(e.context, e.context)
    for f in ("left", "right", "base", "index", "expression", "function", "condition", "true_expr", "false_expr"):
        setattr(n, f, _program_expr(getattr(e, f)))
    if e.arguments:
        n.arguments = [_program_expr(a) for a in e.arguments]
    if e.elements:
        n.elements = [_program_expr(a) for a in e.elements]
    return n


def _pure_deps(e):
    """선택자 식이 순수하면 그것이 읽는 변수 이름들, 아니면 None (Range 로 처리)."""
    if e is None:
        return frozenset()
    if not isinstance(e, Expression):
        return frozenset()
    if e.operator in _IMPURE_OPS:
        return None
    if e.literal is not None:
        return frozenset()
    if e.identifier is not None and e.base is None and e.function is None:
        return frozenset({e.identifier})
    if e.function is not None:
        if not (e.function.identifier and _CAST_FUNCS.match(e.function.identifier)):
            return None
        parts = [_pure_deps(a) for a in (e.arguments or [])]
    elif e.member is not None and e.base is not None:
        if VariableEnv.is_global_expr(e):
            return frozenset()                      # msg.sender 등 — 한 프레임 안에서 바뀌지 않는다
        parts = [_pure_deps(e.base)]
    else:
        parts = [_pure_deps(getattr(e, f)) for f in ("left", "right", "base", "index", "expression",
                                                     "condition", "true_expr", "false_expr")
                 if getattr(e, f) is not None]
        parts += [_pure_deps(a) for a in (e.elements or [])]
        if not parts:
            return None                             # 모르는 형태 — 순수하다고 가정하지 않는다
    if any(p is None for p in parts):
        return None
    return frozenset().union(*parts)


def _is_bottom(v):
    if isinstance(v, _INTERVALS):
        return v.is_bottom()
    if isinstance(v, AddressSet):
        return not v.is_top and not v.ids
    if isinstance(v, BytesSet):
        return not v.is_top and not v.values
    return v is None


def _concrete_key(v):
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, _INTERVALS):
        return str(v.min_value) if v.min_value == v.max_value else None
    if isinstance(v, AddressSet):
        return str(v) if v.is_singleton() else None
    if isinstance(v, BytesSet):
        return str(v) if v.is_singleton() else None
    if isinstance(v, str) and not v.startswith("symbol"):
        return v                                    # 문자열 리터럴 키
    return None


def _point(v):
    """구체 키의 추상값 (alias 판정용)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, _INTERVALS):
        return v.min_value
    return v


def _key_point(k):
    """기존 구체 키 문자열 → alias 판정용 값. 모르는 형태는 None(= 겹칠 수 있다고 본다)."""
    if re.fullmatch(r"-?\d+", k):
        return int(k)
    m = _ADDR_KEY.fullmatch(k)
    if m:
        ids = {int(x) for x in m.group(1).replace(" ", "").split(",") if x}
        return AddressSet(ids=ids)
    return None


def _hi(v):
    if isinstance(v, int):
        return v
    if isinstance(v, _INTERVALS):
        return v.max_value
    return None


def _may_equal(a, b):
    """두 키 추상값이 같은 구체 키를 가리킬 수 있는가. 판정 못 하면 True(sound)."""
    if a is None or b is None:
        return True
    if isinstance(a, int) and not isinstance(a, bool):
        a = UnsignedIntegerInterval(a, a, 256) if a >= 0 else IntegerInterval(a, a, 256)
    if isinstance(b, int) and not isinstance(b, bool):
        b = UnsignedIntegerInterval(b, b, 256) if b >= 0 else IntegerInterval(b, b, 256)
    if isinstance(a, _INTERVALS) and isinstance(b, _INTERVALS):
        if a.is_bottom() or b.is_bottom():
            return False
        return not (a.max_value < b.min_value or b.max_value < a.min_value)
    if isinstance(a, AddressSet) and isinstance(b, AddressSet):
        if a.is_top or b.is_top:
            return True
        return bool(a.ids & b.ids)
    if isinstance(a, BytesSet) and isinstance(b, BytesSet):
        if a.is_top or b.is_top:
            return True
        return bool(set(a.values) & set(b.values))
    if isinstance(a, str) and isinstance(b, str) and not a.startswith("symbol") and not b.startswith("symbol"):
        return a == b
    return True
