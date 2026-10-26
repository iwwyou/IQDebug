import copy
from typing import Dict, Any

from antlr4 import *
from Parser.SolidityLexer  import SolidityLexer
from Parser.SolidityParser import SolidityParser
from antlr4.error.ErrorListener import ErrorListener, ConsoleErrorListener
from Utils.Errors import AnnotationSyntaxError

from Domain.Variable import (Variables, ArrayVariable, GlobalVariable,
                             StructVariable, MappingVariable, EnumVariable)
from Domain.Interval import *   # ← 딱 이 정도만 있으면 됨
from Domain.AddressSet import AddressSet
from Domain.Type import SolType
from Domain.IR import Expression

class ParserHelpers:
    # --------------------------- 컨텍스트 → 파싱 규칙 매핑
    _CTX_MAP: dict[str, str] = {
        'contract':'interactiveSourceUnit', 'abstract contract':'interactiveSourceUnit',
        'library':'interactiveSourceUnit',
        'interface':'interactiveSourceUnit', 'enum':'interactiveSourceUnit',
        'struct':'interactiveSourceUnit',   'functionDefinition':'interactiveSourceUnit',
        'constructor':'interactiveSourceUnit', 'fallback':'interactiveSourceUnit',
        'receive':'interactiveSourceUnit',  'event':'interactiveSourceUnit',
        'error':'interactiveSourceUnit',    'modifier':'interactiveSourceUnit',
        'stateVariableDeclaration':'interactiveSourceUnit',
        'constantVariableDeclaration': 'interactiveSourceUnit',
        'usingDirective':'interactiveSourceUnit',
        'enumMember':'interactiveEnumUnit',
        'structMember':'interactiveStructUnit',
        'fileLevelStruct':'interactiveSourceUnit',
        'fileLevelStructMember':'interactiveStructUnit',
        'fileLevelEnum':'interactiveSourceUnit',
        'fileLevelEnumMember':'interactiveEnumUnit',
        'fileLevelTypeAlias':'interactiveSourceUnit',

        'simpleStatement':'interactiveBlockUnit', 'if':'interactiveBlockUnit',
        'for':'interactiveBlockUnit',     'while':'interactiveBlockUnit',
        'do':'interactiveBlockUnit',      'try':'interactiveBlockUnit',
        'return':'interactiveBlockUnit',  'break':'interactiveBlockUnit',
        'continue':'interactiveBlockUnit','emit':'interactiveBlockUnit',
        'unchecked':'interactiveBlockUnit',

        'doWhileWhile':'interactiveDoWhileUnit',

        'intentUnit':'intentUnit',  # @Param, @During, @Post etc.
        'catch':'interactiveCatchClauseUnit',
        'else_if':'interactiveIfElseUnit', 'else':'interactiveIfElseUnit',

        'IntentUnit':'intentUnit'
    }

    # --------------------------- map → 규칙 문자열
    @staticmethod
    def map_context_type(ctx_type: str) -> str|None:
        return ParserHelpers._CTX_MAP.get(ctx_type)

    # --------------------------- 파싱
    @staticmethod
    def annotation_syntax_errors(src: str) -> list:
        """어노테이션 한 줄(intentUnit)의 문법 오류 목록 — 빈 리스트면 문법에 맞다. (2026-10-25)"""
        errors = []

        class _Collect(ErrorListener):
            def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
                errors.append(f"{column}: {msg}")

        lexer = SolidityLexer(InputStream(src))
        lexer.removeErrorListeners()
        lexer.addErrorListener(_Collect())
        parser = SolidityParser(CommonTokenStream(lexer))
        parser.removeErrorListeners()
        parser.addErrorListener(_Collect())
        parser.intentUnit()
        return errors

    @staticmethod
    def generate_parse_tree(src: str, ctx_type: str, verbose=False):
        # (2026-10-25) 어노테이션은 문법 오류가 있으면 해석하지 않는다 — 복구된 트리는 입력의 일부가 빠진 다른 문장이다.
        if ParserHelpers.map_context_type(ctx_type) == 'intentUnit':
            errs = ParserHelpers.annotation_syntax_errors(src)
            if errs:
                raise AnnotationSyntaxError(src, errs)
        input_stream  = InputStream(src)
        lexer         = SolidityLexer(input_stream)
        token_stream  = CommonTokenStream(lexer)
        parser        = SolidityParser(token_stream)

        # ── ① 에러 리스너 부착 ───────────────────────────────
        if verbose:
            # 기본 ConsoleErrorListener 제거
            parser.removeErrorListeners()

            # ▶ 한 줄짜리 익명 ErrorListener
            parser.addErrorListener(
                type(
                    "InlineErr", (ErrorListener,), {
                        "syntaxError": lambda self, recognizer, offendingSymbol,
                                              line, column, msg, e:
                        print(f"[ANTLR] {line}:{column} {msg}")
                    }
                )()
            )
        else:
            # 최소한 기본 오류 출력은 유지하고 싶다면
            parser.removeErrorListeners()
            parser.addErrorListener(ConsoleErrorListener.INSTANCE)

        rule = ParserHelpers.map_context_type(ctx_type)

        match rule:
            case 'interactiveStructUnit':     return parser.interactiveStructUnit()
            case 'interactiveEnumUnit':       return parser.interactiveEnumUnit()
            case 'interactiveBlockUnit':      return parser.interactiveBlockUnit()
            case 'interactiveDoWhileUnit':    return parser.interactiveDoWhileUnit()
            case 'interactiveIfElseUnit':     return parser.interactiveIfElseUnit()
            case 'interactiveCatchClauseUnit':return parser.interactiveCatchClauseUnit()
            case 'intentUnit':                 return parser.intentUnit()
            case _:                           return parser.interactiveSourceUnit()

class VariableEnv:
    """
    변수 환경(dict[str, Variables])을 deep-copy / 비교 / merge 하는 공통 유틸.
    * Engine, Semantics 모두 같은 로직을 필요로 하므로 여기로 끌어올렸다.
    """
    _GLOBAL_BASES = {"block", "msg", "tx"}

    # public façade --------------------------------------------------------
    join_variables_simple     = staticmethod(lambda l, r:
        VariableEnv._merge_by_mode(l, r, "join"))
    join_variables_with_widening = staticmethod(lambda l, r:
        VariableEnv._merge_by_mode(l, r, "widen"))
    narrow_variables          = staticmethod(lambda old, new:
        VariableEnv._merge_by_mode(old, new, "narrow"))

    # ─────────────────────────────────────────── 복사 / 비교
    @staticmethod
    def copy_single_variable(v: "Variables") -> "Variables":
        """
        단일 변수를 deep-copy (Array / Struct / Mapping 서브-클래스 유지)
        """
        if isinstance(v, ArrayVariable):
            new_arr = ArrayVariable(
                identifier=v.identifier,
                base_type=v.typeInfo.arrayBaseType,            # 타입 메타데이터는 공유(불변)
                array_length=v.typeInfo.arrayLength,
                is_dynamic=v.typeInfo.isDynamicArray,
                scope=v.scope,
                struct_defs=v.struct_defs,
                enum_defs=v.enum_defs
            )
            new_arr.elements = {i: VariableEnv.copy_single_variable(e) for i, e in v.elements.items()}
            VariableEnv._copy_indexed_state(new_arr, v)
            new_arr.sym_cells = VariableEnv.copy_variables(v.sym_cells)
            new_arr.length = v.length.copy() if v.length is not None else None
            return new_arr

        if isinstance(v, StructVariable):
            new_st = StructVariable(
                identifier=v.identifier,
                struct_type=v.typeInfo.structTypeName,
                scope=v.scope
            )
            new_st.members = VariableEnv.copy_variables(v.members)
            return new_st

        if isinstance(v, MappingVariable):
            new_mp = MappingVariable(
                identifier=v.identifier,
                key_type=v.typeInfo.mappingKeyType,            # 타입 메타데이터는 공유(불변)
                value_type=v.typeInfo.mappingValueType,
                scope=v.scope,
                struct_defs=v.struct_defs,
                enum_defs=v.enum_defs
            )
            new_mp.mapping = VariableEnv.copy_variables(v.mapping)
            VariableEnv._copy_indexed_state(new_mp, v)
            return new_mp

        # ★ (Phase 3b, 성능) 일반 Variables: 값만 새로 만들고 타입 메타데이터(typeInfo)는 공유한다.
        #   elementary 변수의 typeInfo 를 제자리에서 바꾸는 코드는 없다(배열/구조체/매핑·생성자만 자기 객체를 바꾼다).
        #   예전 `copy.deepcopy(v)` 는 SolType 까지 매번 깊은 복사해 fixpoint 비용의 대부분을 차지했다
        #   (web3bugs_3_H_05 프로파일: 전체 1138s 중 deepcopy 1046s).
        if type(v) in (Variables, GlobalVariable, EnumVariable):
            nv = type(v).__new__(type(v))
            nv.__dict__ = dict(v.__dict__)
            nv.value = VariableEnv._copy_value(v.value)
            if type(v) is GlobalVariable:
                nv.default_value = VariableEnv._copy_value(v.default_value)
                nv.debug_override = VariableEnv._copy_value(v.debug_override)
            return nv

        return copy.deepcopy(v)

    @staticmethod
    def _copy_indexed_state(dst, src):
        """(2026-10-23) 선택자 셀 메타·summary 복사 (Domain/Variable.py `_init_indexed_storage` 참고)."""
        dst.sym = {k: dict(m) for k, m in src.sym.items()}
        dst.summary = VariableEnv.copy_single_variable(src.summary) if src.summary is not None else None

    @staticmethod
    def adopt_storage(dst, src):
        """dst(Array/Mapping)의 저장소 상태를 src 것으로 통째로 바꾼다 — 식별자·스코프는 dst 유지.
        callee 복귀 되쓰기·참조 인자 되쓰기·통짜 재할당이 공유한다(필드를 하나씩 옮기면 새 필드가 빠진다)."""
        if isinstance(dst, MappingVariable):
            dst.mapping = VariableEnv.copy_variables(src.mapping)
        else:
            dst.elements = {i: VariableEnv.copy_single_variable(e) for i, e in src.elements.items()}
            dst.sym_cells = VariableEnv.copy_variables(src.sym_cells)
            dst.length = src.length.copy() if src.length is not None else None
        VariableEnv._copy_indexed_state(dst, src)

    @staticmethod
    def _copy_value(val):
        """추상값 하나의 독립 사본 (Interval·AddressSet·BytesSet 는 가볍게, 나머지는 deepcopy)."""
        from Domain.BytesSet import BytesSet
        if val is None or isinstance(val, (str, int, bool)):
            return val
        if isinstance(val, (IntegerInterval, UnsignedIntegerInterval, BoolInterval)):
            nv = val.copy()
            if len(val.__dict__) > 3:      # 덧붙은 태그(있다면)도 보존
                nv.__dict__.update({k: x for k, x in val.__dict__.items()
                                    if k not in ("min_value", "max_value", "type_length")})
            return nv
        if isinstance(val, AddressSet):
            nv = AddressSet(ids=val.ids, is_top=val.is_top)
            nv._cast_interface = getattr(val, "_cast_interface", None)
            return nv
        if isinstance(val, BytesSet):
            return BytesSet(values=val.values, is_top=val.is_top, byte_size=val.byte_size)
        return copy.deepcopy(val)

    @staticmethod
    def copy_variables(src: Dict[str, "Variables"]) -> Dict[str, "Variables"]:
        """
        deep-copy 하되 Array / Struct / Mapping 의 서브-클래스를 유지한다.
        (원래 Engine.copy_variables 와 동일 로직)
        """
        dst: Dict[str, "Variables"] = {}

        for name, v in src.items():
            dst[name] = VariableEnv.copy_single_variable(v)

        return dst

    @staticmethod
    def deep_clone_variable(var_obj, new_name: str):
        """
        var_obj   : 복제할 원본 변수 객체
        new_name  : 로컬 변수로 선언될 새 이름

        반환      : var_obj 와 동일한 타입의 “독립적인” 복사본
        """
        import copy
        new_var = copy.deepcopy(var_obj)  # 깊은 복사
        new_var.identifier = new_name  # 최상위 이름 교체

        # ── Struct 내부 멤버 식별자 업데이트 ──────────────────────
        if hasattr(new_var, "members"):  # StructVariable
            for m in new_var.members.values():
                tail = m.identifier.split('.', 1)[-1]  # 기존 “a.b.c”
                m.identifier = f"{new_name}.{tail}"

        # ── 배열 요소 식별자 업데이트 ─────────────────────────────
        if isinstance(new_var, ArrayVariable):
            for i, e in new_var.elements.items():
                e.identifier = f"{new_name}[{i}]"

        # ── 매핑 value 식별자 업데이트 ───────────────────────────
        if hasattr(new_var, "mapping"):  # MappingVariable
            for k, v in new_var.mapping.items():
                v.identifier = f"{new_name}[{k}]"

        return new_var

    @staticmethod
    def variables_equal(a: Dict[str, "Variables"] | None,
                        b: Dict[str, "Variables"] | None) -> bool:
        """
        Engine.variables_equal 와 동일.  (간단화 버전)
        """
        if a is None or b is None:
            return a is b
        if a.keys() != b.keys():
            return False

        for k in a:
            v1, v2 = a[k], b[k]
            if type(v1) is not type(v2):
                return False

            if not VariableEnv._single_equal(v1, v2):
                return False
        return True

    @staticmethod
    def _single_equal(v1, v2) -> bool:
        """변수 하나 비교. (2026-10-23) 복합 타입을 **먼저** 본다 — 예전엔 Struct/Mapping 이 `.value`(항상 None)를 가져서
        leaf 비교(None == None)로 빠져 **내용을 전혀 비교하지 않았다**. fixpoint 가 mapping·struct 의 변화만 있는 후속 노드를
        다시 계산하지 않아(Engine._run_worklist 의 changed_succ) 루프 안의 저장소 쓰기가 반영되지 않을 수 있었다."""
        if type(v1) is not type(v2):
            return False
        if not isinstance(v1, Variables):              # 원소 자리에 값이 그대로 들어 있는 경우
            return v1.equals(v2) if hasattr(v1, "equals") else v1 == v2
        if isinstance(v1, ArrayVariable):
            return (VariableEnv._compare_array_elements(v1, v2)
                    and VariableEnv._indexed_meta_equal(v1, v2)
                    and VariableEnv.variables_equal(v1.sym_cells, v2.sym_cells)
                    and VariableEnv._opt_value_equal(v1.length, v2.length))
        if isinstance(v1, MappingVariable):
            return (VariableEnv.variables_equal(v1.mapping, v2.mapping)
                    and VariableEnv._indexed_meta_equal(v1, v2))
        if isinstance(v1, StructVariable):
            return VariableEnv.variables_equal(v1.members, v2.members)
        if hasattr(v1.value, "equals"):
            return type(v1.value) == type(v2.value) and v1.value.equals(v2.value)
        return v1.value == v2.value

    @staticmethod
    def _indexed_meta_equal(c1, c2) -> bool:
        if c1.sym.keys() != c2.sym.keys():
            return False
        if (c1.summary is None) != (c2.summary is None):
            return False
        return c1.summary is None or VariableEnv._single_equal(c1.summary, c2.summary)

    @staticmethod
    def _opt_value_equal(a, b) -> bool:
        if a is None or b is None:
            return a is b
        return a.equals(b)

    @staticmethod
    def _compare_array_elements(a1, a2) -> bool:
        """(2026-10-23, 희소 배열) 원소 비교 = 키 합집합에서 의미상 값(Read) 비교 — 한쪽만 구체화했어도 값이 같으면 같다."""
        for k in set(a1.elements) | set(a2.elements):
            if not VariableEnv._single_equal(a1.read_element(k), a2.read_element(k)):
                return False
        return True

    @staticmethod
    def env_equal(a: dict[str, Variables] | None,
                   b: dict[str, Variables] | None) -> bool:
        return VariableEnv.variables_equal(a or {}, b or {})

    # ─────────────────────────────────────────── join / widen / narrow
    @staticmethod
    def _merge_values(v1: Any, v2: Any, mode: str = "join") -> Any:

        def _should_widen(val):  # 작은 헬퍼
            return isinstance(val, (IntegerInterval, UnsignedIntegerInterval, BoolInterval))

        # ⓪ None 방어: 한쪽이 None이면 다른 쪽 반환 (초기화 안 된 branch)
        if v1 is None:
            return v2
        if v2 is None:
            return v1

        # ① primitive -----------------------------------------------------
        if not isinstance(v1, (Variables, ArrayVariable, StructVariable,
                               MappingVariable, EnumVariable)):
            # ★ 문자열(symbolic 값)은 str.join 등 호출 방지
            if isinstance(v1, str):
                return v1 if v1 == v2 else f"symbolic{mode.capitalize()}({v1},{v2})"
            if mode == "widen" and not _should_widen(v1):
                return VariableEnv._merge_values(v1, v2, "join")
            if hasattr(v1, mode):
                return getattr(v1, mode)(v2)
            return f"symbolic{mode.capitalize()}({v1},{v2})"

        # ② 래퍼 타입 but 서로 다른 클래스 → symbolic*
        if type(v1) is not type(v2):
            return f"symbolic{mode.capitalize()}({v1},{v2})"

        # ③ leaf Variables / EnumVariable
        if isinstance(v1, (Variables, EnumVariable)) and \
           not isinstance(v1, (ArrayVariable, StructVariable, MappingVariable)):
            new = copy.copy(v1)
            # ★ v1.value가 Variables인 경우 방어 처리 (잘못된 초기화 감지)
            if isinstance(v1.value, Variables):
                new.value = v1.value  # 그대로 유지
            else:
                new.value = VariableEnv._merge_values(v1.value, v2.value, mode)
            return new

        # ④ Array
        #   (2026-10-23) 구체화한 원소 수가 다르면 짧은 쪽의 나머지를 **그쪽 summary 에서** 구체화해 join 한다.
        #   예전엔 긴 쪽을 그대로 돌려줘(짧은 쪽 경로의 값이 빠짐) unsound 였다. 원소 수 ≠ 길이 — 길이는 따로 join.
        if isinstance(v1, ArrayVariable):
            #   (희소 배열) 키 합집합에서 Read₁(k) ⊔ Read₂(k) — 한쪽에 구체화되지 않은 원소는 그쪽 D(summary)에서 만든다.
            new_arr = copy.copy(v1)
            new_arr.elements = {k: VariableEnv._merge_values(v1.read_element(k), v2.read_element(k), mode)
                                for k in sorted(set(v1.elements) | set(v2.elements))}
            new_arr.sym_cells, new_arr.sym = VariableEnv._merge_sym_cells(v1, v2, v1.sym_cells, v2.sym_cells, mode)
            new_arr.summary = VariableEnv._merge_summary(v1, v2, mode)
            if v1.length is None and v2.length is None:
                new_arr.length = None
            else:
                from Domain.Variable import array_length
                new_arr.length = VariableEnv._merge_values(array_length(v1), array_length(v2), mode)
            return new_arr

        # ⑤ Struct
        if isinstance(v1, StructVariable):
            new_st = copy.copy(v1)
            new_st.members = {}
            for m in v1.members.keys() | v2.members.keys():
                if m in v1.members and m in v2.members:
                    new_st.members[m] = VariableEnv._merge_values(v1.members[m], v2.members[m], mode)
                else:
                    new_st.members[m] = copy.copy(v1.members.get(m, v2.members.get(m)))
            return new_st

        # ⑥ Mapping
        #   (2026-10-23) 한쪽에만 있는 구체 키는 다른 쪽에선 **구체화되지 않은 위치**(값 ∈ 그쪽 summary)다 — 그것과 join.
        #   예전엔 있는 쪽 값을 그대로 가져가 다른 경로의 값(⊤ 등)이 빠졌다(unsound).
        if isinstance(v1, MappingVariable):
            new_map = copy.copy(v1)
            con1 = {k: x for k, x in v1.mapping.items() if k not in v1.sym}
            con2 = {k: x for k, x in v2.mapping.items() if k not in v2.sym}
            new_map.mapping = {}
            for k in con1.keys() | con2.keys():
                a = con1[k] if k in con1 else v1.fresh_entry(k)
                b = con2[k] if k in con2 else v2.fresh_entry(k)
                new_map.mapping[k] = VariableEnv._merge_values(a, b, mode)
            cells, new_map.sym = VariableEnv._merge_sym_cells(
                v1, v2, {k: v1.mapping[k] for k in v1.sym}, {k: v2.mapping[k] for k in v2.sym}, mode)
            new_map.mapping.update(cells)
            new_map.summary = VariableEnv._merge_summary(v1, v2, mode)
            return new_map

        return f"symbolic{mode.capitalize()}({v1},{v2})"

    @staticmethod
    def _merge_sym_cells(c1, c2, cells1, cells2, mode):
        """선택자 셀 join — 양쪽 모두 **같은 바인딩**(프레임·의존 변수)으로 가진 셀만 남긴다. 한쪽에만 있으면 그 경로에선
        선택자가 다시 대입돼 바인딩이 끊긴 것이므로 버린다(값은 이미 다른 셀·summary 에 반영돼 있다 — Storage.py 불변식)."""
        cells, sym = {}, {}
        for k in cells1.keys() & cells2.keys():
            m1, m2 = c1.sym[k], c2.sym[k]
            if m1["scope"] != m2["scope"] or m1["deps"] != m2["deps"]:
                continue
            cells[k] = VariableEnv._merge_values(cells1[k], cells2[k], mode)
            sym[k] = {"scope": m1["scope"], "deps": m1["deps"],
                      "vals": VariableEnv._merge_values(m1["vals"], m2["vals"], "join")}
        return cells, sym

    @staticmethod
    def _merge_summary(c1, c2, mode):
        if c1.summary is None and c2.summary is None:
            return None
        a = c1.summary if c1.summary is not None else c1.fresh_default()
        b = c2.summary if c2.summary is not None else c2.fresh_default()
        return VariableEnv._merge_values(a, b, mode)

    @staticmethod
    def _merge_by_mode(left, right, mode: str):
        if left is None:
            return VariableEnv.copy_variables(right or {})
        if not right:
            return VariableEnv.copy_variables(left)

        res = VariableEnv.copy_variables(left)
        for name, r_var in (right or {}).items():
            if name in res:
                try:
                    res[name] = VariableEnv._merge_values(res[name], r_var, mode)
                except AttributeError as e:
                    l_val = getattr(res[name], 'value', '?')
                    r_val = getattr(r_var, 'value', '?')
                    raise AttributeError(
                        f"merge '{name}': left.value={l_val} ({type(l_val).__name__}), "
                        f"right.value={r_val} ({type(r_val).__name__}) - {e}"
                    ) from e
            else:
                res[name] = VariableEnv.copy_variables({name: r_var})[name]
        return res

    @staticmethod
    def diff_changed(old_env: dict[str, Variables],
                     new_env: dict[str, Variables]) -> dict[str, Variables]:

        from Analyzer.RecordManager import RecordManager  # serialize 재사용
        rm = RecordManager()  # helper only

        def _flat(env: dict[str, Variables]) -> dict[str, str]:
            out = {}
            for k, v in env.items():
                # 방어 코드: v가 Variables 객체가 아니면 건너뛰기
                if not hasattr(v, 'identifier'):
                    continue
                rm._flatten_var(v, v.identifier, out)
            return out

        old_flat = _flat(old_env) if old_env else {}
        new_flat = _flat(new_env) if new_env else {}

        changed = {}
        for path, new_val in new_flat.items():
            old_val = old_flat.get(path)
            if old_val is None:
                # 이전에 없던 변수는 제외 (루프 내부 새 변수는 loopDelta에 포함 안 함)
                continue
            # old_val과 new_val은 이미 _flatten_var에서 직렬화된 문자열이므로 직접 비교
            if old_val != new_val:
                changed[path] = new_val  # path 는 "a[3].x" 같은 키
        return changed

    @staticmethod
    def is_interval(x) -> bool:
        """Integer / UnsignedInteger 계열인지 판단 (AddressSet은 제외)"""
        return isinstance(x, (IntegerInterval, UnsignedIntegerInterval))

    @staticmethod
    def top_from_soltype(sol_t: SolType, struct_defs: dict = None, enum_defs: dict = None,
                         identifier: str = ""):
        """SolType → top-valued domain object 생성 (모든 타입 지원)"""
        from Domain.BytesSet import BytesSet
        if struct_defs is None:
            struct_defs = {}
        if enum_defs is None:
            enum_defs = {}

        if sol_t.typeCategory == "array":
            arr = ArrayVariable(
                identifier=identifier,
                base_type=sol_t.arrayBaseType,
                array_length=sol_t.arrayLength,
                is_dynamic=sol_t.isDynamicArray,
                struct_defs=struct_defs,
                enum_defs=enum_defs,
            )
            arr.initialize_default_by_base_type()
            return arr

        if sol_t.typeCategory == "mapping":
            return MappingVariable(
                identifier=identifier,
                key_type=sol_t.mappingKeyType,
                value_type=sol_t.mappingValueType,
                struct_defs=struct_defs,
                enum_defs=enum_defs,
            )

        if sol_t.typeCategory == "struct":
            sv = StructVariable(
                identifier=identifier,
                struct_type=sol_t.structTypeName,
            )
            if sol_t.structTypeName in struct_defs:
                sv.initialize_struct(struct_defs[sol_t.structTypeName],
                                     struct_defs=struct_defs, enum_defs=enum_defs)
            return sv

        if sol_t.typeCategory == "enum":
            ev = EnumVariable(
                identifier=identifier,
                enum_type=sol_t.enumTypeName,
            )
            if sol_t.enumTypeName in enum_defs:
                defn = enum_defs[sol_t.enumTypeName]
                ev.members = {m: i for i, m in enumerate(defn.members)}
                ev.valueIndex = 0
                ev.value = defn.members[0]
            return ev

        if sol_t.typeCategory == "function":          # (2026-10-23) 함수 포인터 = ⊤ (Domain/FunctionRef.py)
            from Domain.FunctionRef import FunctionRef
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = FunctionRef.top()
            return v

        if sol_t.typeCategory == "interface":
            ifc_name = getattr(sol_t, 'interfaceName', None)
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = AddressSet.top()
            if ifc_name:
                v.value._cast_interface = ifc_name
            return v

        # elementary
        et = sol_t.elementaryTypeName or ""
        if et.startswith("int"):
            bits = sol_t.intTypeLength or 256
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = IntegerInterval.top(bits)
            return v
        if et.startswith("uint"):
            bits = sol_t.intTypeLength or 256
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = UnsignedIntegerInterval.top(bits)
            return v
        if et == "bool":
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = BoolInterval.top()
            return v
        if et == "address":
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = AddressSet.top()
            return v
        if et.startswith("bytes") and len(et) > 5:
            byte_size = int(et[5:])
            v = Variables(identifier=identifier)
            v.typeInfo = sol_t
            v.value = BytesSet.top(byte_size)
            return v
        # string / bytes 등
        v = Variables(identifier=identifier)
        v.typeInfo = sol_t
        v.value = f"symbol_{identifier}"
        return v


    @staticmethod
    def convert_int_to_bool_interval(int_interval):
        """
        간단히 [0,0] => BoolInterval(0,0),
             [1,1] => BoolInterval(1,1)
             그외 => BoolInterval(0,1)
        """
        if int_interval.is_bottom():
            return BoolInterval(None, None)
        if int_interval.min_value == 0 and int_interval.max_value == 0:
            return BoolInterval(0, 0)  # always false
        elif int_interval.min_value == 1 and int_interval.max_value == 1:
            return BoolInterval(1, 1)  # always true
        else:
            return BoolInterval(0, 1)  # unknown

    @staticmethod
    def is_global_expr(expr: Expression) -> bool:
        """
        Expression 이 block.xxx / msg.xxx / tx.xxx 형태인지 검사.
        """
        return (
                expr.member is not None  # x.y 형태
                and expr.base is not None
                and getattr(expr.base, "identifier", None) in VariableEnv._GLOBAL_BASES
        )