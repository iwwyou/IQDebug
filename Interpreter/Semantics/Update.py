from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                         # 타입 검사 전용
     from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.Interval import *
from Domain.AddressSet import AddressSet, address_manager
from Domain.Variable import Variables, ArrayVariable, MappingVariable, StructVariable, EnumVariable
from Domain.IR import Expression
from Utils.Helper import VariableEnv
from Interpreter.Semantics.Evaluation import coerce_to_declared


def _path_below(top, node):
    """top(대입 LHS) 에서 node(인덱스 접근 식) 아래로 내려가는 접근 경로 — [("member", 이름) | ("index", 인덱스 식)].
    node 의 셀에서 이 경로를 따라간 위치가 이번 대입이 쓴 곳이다."""
    def walk(e):
        if e is node:
            return []
        if e is None:
            return None
        if e.context == "TupleExpressionContext":
            for el in (e.elements or []):
                r = walk(el)
                if r is not None:
                    return r
            return None
        if e.base is None:
            return None
        r = walk(e.base)
        if r is None:
            return None
        if e.context == "IndexAccessContext":
            return r + [("index", e.index)]
        if e.member is not None:
            return r + [("member", e.member)]
        return None
    path = walk(top)
    if path is None:
        raise ValueError(f"internal: index access {node} not found under LHS {top}")
    return path


def _lhs_roots(expr):
    """대입 LHS 가 바꾸는 뿌리 변수 이름들 (선택자 셀 무효화용)."""
    if expr is None:
        return []
    if expr.context == "TupleExpressionContext":
        return [r for e in (expr.elements or []) for r in _lhs_roots(e)]
    while expr.base is not None and expr.identifier is None:
        expr = expr.base
    return [expr.identifier] if expr.identifier else []


class Update :

    def __init__(self, an: "ContractAnalyzer"):
        self.an = an
        # (2026-10-23) 대입 하나(최상위 update_left_var) 동안 고른 인덱스 셀 — 쓰기가 끝난 뒤 alias 전파(Storage.propagate)
        self._depth = 0
        self._pending: list = []
        self._assume = False
        self._resolved = ([], None, None)

    @property
    def ev(self):
        return self.an.evaluator

    @staticmethod
    def _assign_whole_composite(dst, src) -> bool:
        """
        `dst`(기존 Array/Struct/MappingVariable)를 `src`와 같은 계열
        composite로 통짜 재할당(예: `arr = new T[](n)`, `arr[i] = StructLit`,
        `map[k] = OtherMap`)할 때, dst의 identifier/scope는 유지한 채 실제
        내부 상태(elements/members/mapping)만 src 것으로 교체한다.
        (leaf용 `_apply_to_leaf`로 보내면 `.value`에 composite 객체가 통째로
        얹히고 dst의 원래 내부 상태는 그대로 남아 다음 접근이 깨짐 —
        web3bugs_35_H_08/35_H_12에서 발견. StructVariable 등은 Variables의
        서브클래스라서 `isinstance(x, Variables)` 체크만으론 leaf와 구분 안 됨,
        composite 여부를 먼저 판별해야 함.)
        반환값: 교체를 수행했으면 True, dst/src 계열이 안 맞아 못 했으면 False.

        ★ src.elements/members/mapping을 그대로 참조 공유하지 않고 deep-copy함
        (`VariableEnv.copy_single_variable` 재사용) — `b = a;`처럼 두 변수 다
        계속 살아있는 경우 참조를 공유하면 한쪽을 고치면 다른 쪽도 같이
        바뀌는 aliasing 버그가 생김(기존 MappingVariable 쪽 struct-entry
        재할당 코드가 이미 member별 deep-copy를 하던 것과 같은 이유).
        """
        if isinstance(dst, ArrayVariable) and isinstance(src, ArrayVariable):
            dst.typeInfo = copy.deepcopy(src.typeInfo)
            VariableEnv.adopt_storage(dst, src)
            if src.struct_defs:
                dst.struct_defs = src.struct_defs
            if src.enum_defs:
                dst.enum_defs = src.enum_defs
            return True
        if isinstance(dst, StructVariable) and isinstance(src, StructVariable):
            dst.members = VariableEnv.copy_variables(src.members)
            return True
        if isinstance(dst, MappingVariable) and isinstance(src, MappingVariable):
            VariableEnv.adopt_storage(dst, src)
            return True
        return False

    def update_left_var(self, expr, rVal, operator, variables, callerObject=None, callerContext=None,
                        log:bool=False, line_no:int=None, top_expr=None, assume: bool = False):
        """대입 하나. (2026-10-23) 최상위 호출이 끝나면(LHS 경로 전체에 값을 쓴 뒤)
          · 고른 인덱스 셀마다 겹칠 수 있는 다른 셀·summary 에 weak update (Storage.propagate — 안쪽 단계부터)
          · LHS 의 뿌리 변수를 읽는 선택자 셀의 바인딩을 버린다 (Storage.on_assign)
        assume=True 는 조건 refine — 값을 좁힐 뿐 저장이 아니므로 전파·무효화하지 않고, 미확정 위치(Range)는 건드리지 않는다.
        rVal=None(객체 찾기만, resolve_lhs_expr)이면 전파는 호출자가 값을 바꾼 뒤 `flush_resolved_write()` 로 한다."""
        if self._depth:
            return self._update_left_var(expr, rVal, operator, variables, callerObject, callerContext,
                                         log, line_no, top_expr)
        saved = (self._pending, self._assume)
        self._pending, self._assume = [], assume
        self._depth += 1
        try:
            out = self._update_left_var(expr, rVal, operator, variables, callerObject, callerContext,
                                        log, line_no, top_expr)
            if rVal is None:
                self._resolved = (self._pending, variables, expr)
            elif not assume:
                self._finish_write(self._pending, variables, expr)
            return out
        finally:
            self._depth -= 1
            self._pending, self._assume = saved

    def _finish_write(self, pending, variables, lhs):
        for c, res, obj, node in reversed(pending):
            # 이 인덱스 단계 아래로 LHS 가 실제로 쓴 경로(`s[a].v` 면 .v) — alias 셀에는 그 위치에만 weak update
            self.an.storage.propagate(c, res, obj, _path_below(lhs, node), variables)
        for root in _lhs_roots(lhs):
            self.an.storage.on_assign(variables, root)

    def flush_resolved_write(self):
        """resolve_lhs_expr 로 찾은 객체를 호출자가 직접 바꾼 뒤(delete 등) — 그 쓰기의 전파·무효화."""
        pending, variables, lhs = self._resolved
        self._resolved = ([], None, None)
        if variables is not None:
            self._finish_write(pending, variables, lhs)

    def _update_left_var(self, expr, rVal, operator, variables, callerObject=None, callerContext=None,
                         log:bool=False, line_no:int=None, top_expr=None):
        """
        log: True이면 recording 활성화
        line_no: recording할 라인 번호 (None이면 self.an.current_start_line 사용)
        top_expr: recording할 때 사용할 최상위 LHS expression (None이면 expr 사용)
        """
        # 최상위 호출에서는 top_expr이 None이므로 expr을 사용
        if top_expr is None:
            top_expr = expr

        if log:
            actual_line = line_no if line_no is not None else self.an.current_start_line

        # ── ① 글로벌이면 갱신 금지 ─────────────────────────
        if callerObject is None and callerContext is None and VariableEnv.is_global_expr(expr):
            return None

        if expr.context == "IndexAccessContext":
            return self.update_left_var_of_index_access_context(expr, rVal, operator, variables,
                                                                callerObject, callerContext, log, line_no, top_expr)
        elif expr.context == "MemberAccessContext":
            return self.update_left_var_of_member_access_context(expr, rVal, operator, variables,
                                                                 callerObject, callerContext, log, line_no, top_expr)

        elif expr.context == "IdentifierExpContext":
            return self.update_left_var_of_identifier_context(expr, rVal, operator, variables,
                                                              callerObject, callerContext, log, line_no, top_expr)
        elif expr.context == "LiteralExpContext":
            return self.update_left_var_of_literal_context(expr, rVal, operator, variables,
                                                           callerObject, callerContext, log, line_no, top_expr)
        elif expr.context == "IntentIndexAccess":
            return self.update_left_var_of_intent_index_access_context(expr, rVal, operator, variables,
                                                                                 callerObject, callerContext, log)
        elif expr.context == "IntentMemberAccess":
            return self.update_left_var_of_intent_member_access_context(expr, rVal, operator, variables,
                                                                                  callerObject, callerContext, log)

        # ★ LHS가 튜플 대입(`(a, b, c) = (...)` 혹은 `(a, b, c) = f()`)인 경우 —
        # 예전엔 이 context를 처리하는 분기 자체가 없어서(elements를 쓰지
        # left/right를 안 씀) 조용히 return None으로 빠지며 대입 전체가
        # no-op이었음(web3bugs_52_H_23의 named-return 튜플 대입에서 발견 —
        # 재해석 로그에 이 줄 자체가 아예 안 찍혔음). rVal(리스트/튜플)과
        # expr.elements(빈 슬롯은 None, _split_tuple_slots가 채움)를 위치별로
        # 짝지어 각각 재귀 업데이트.
        elif expr.context == "TupleExpressionContext":
            if len(expr.elements or []) == 1:
                # 원소 하나 = 괄호식 `(x)` — 안쪽 식이 대상이다(refine 이 `(a * b) > 0` 의 좌변을 넘기는 경우 포함)
                return self._update_left_var(expr.elements[0], rVal, operator, variables,
                                            callerObject, callerContext, log, line_no, top_expr)
            if not isinstance(rVal, (list, tuple)):
                # ★ (Phase 3b, D4) 예전엔 우변이 튜플 값이 아니면 대입 전체를 조용히 건너뛰었다.
                raise ValueError(f"tuple assignment: right-hand side is not a tuple value ({type(rVal).__name__})")
            for idx, target in enumerate(expr.elements):
                if target is None or idx >= len(rVal):
                    continue
                self._update_left_var(target, rVal[idx], operator, variables,
                                      None, None, log, line_no, top_expr)
            return None

        # ★ 위 named-context 어디에도 안 걸리는 인덱스 표현식(예: cast
        # `uint256(EnumType.Member)`, 단항 `~x` 등) — update_left_var_of_binary_exp_context
        # 는 이름과 달리 expr.left/right를 전혀 안 쓰고 expr 전체를 그냥
        # evaluate_expression으로 평가해서 인덱스로 쓰므로, binary expression이
        # 아니어도 안전하게 재사용 가능. 이전엔 `expr.left/right가 둘 다 있을 때만`
        # 이 폴백을 태워서 cast 표현식(.expression/.typeName만 있고 .left/.right는
        # 없음)이 인덱스로 쓰이면 조용히 None을 리턴하고 대입 자체가 사라졌음
        # (web3bugs_70_H_04, `totalLiquidityWeight[uint256(Paths.VADER)] = ...`에서 발견).
        elif (expr.left is not None and expr.right is not None) or \
                (callerObject is not None and callerContext == "IndexAccessContext"):
            return self.update_left_var_of_binary_exp_context(expr, rVal, operator, variables,
                                                              callerObject, callerContext, log)

        return None

    def update_left_var_of_binary_exp_context(
            self,
            expr: Expression,
            r_val,
            operator: str,
            variables: dict[str, Variables],
            caller_object=None,
            caller_context=None,
            log:bool=False
    ):
        """
        rebalanceCount % 10 처럼 BinaryExp(%) 가 IndexAccess 의 인덱스로
        쓰일 때 호출된다.
        """

        # IndexAccess 의 인덱스로 호출된 경우가 아니면 아무 것도 하지 않음
        if caller_object is None or caller_context != "IndexAccessContext":
            return None  # 🔸 더 내려갈 대상 없음

        # 1) 인덱스 식 abstract-eval → int 또는 Interval
        idx_val = self.ev.evaluate_expression(expr, variables, None, None)

        # ── ① singleton Interval ⇒ int 확정
        if isinstance(idx_val, (IntegerInterval, UnsignedIntegerInterval)) \
                and idx_val.min_value == idx_val.max_value:
            idx_val = idx_val.min_value

        # ── ② 확정 int ────────────────────────────────────────────────
        if isinstance(idx_val, int):
            target = self._touch_index_entry(caller_object, idx_val)
            if r_val is None:
                return target

            new_val = self.compound_assignment(target.value, r_val, operator)
            self._patch_var_with_new_value(target, new_val)

            if log :
                # Note: line_no는 함수 파라미터가 아니라 로컬 변수로 사용
                actual_line = self.an.current_start_line
                # 🔸 즉시 기록
                self.an.recorder.record_assignment(
                    line_no=actual_line,
                    expr=expr,
                    var_obj=target,
                    base_obj=caller_object,
                )
            return None  # 더 내려갈 대상이 없으므로 None 반환

        # ── ③ Interval 범위 [l, r] ───────────────────────────────────
        if isinstance(idx_val, (IntegerInterval, UnsignedIntegerInterval)):
            l, r = idx_val.min_value, idx_val.max_value

            # ---- 배열(ArrayVariable) --------------------------------
            if isinstance(caller_object, ArrayVariable):
                # (a) 동적 배열 – 전체-쓰기(<unk>)로 추상화
                if caller_object.typeInfo.isDynamicArray:
                    if log :
                        self.an.recorder.record_assignment(
                            line_no=self.an.current_start_line,
                            expr=expr,
                            var_obj=caller_object,
                            base_obj=caller_object,
                        )
                    return None

                # (b) 정적 배열 – l..r 패치
                decl_len = caller_object.typeInfo.arrayLength or 0
                if r >= decl_len:
                    raise IndexError(
                        f"Index [{l},{r}] out of range for static array "
                        f"'{caller_object.identifier}' (decl len={decl_len})"
                    )
                if r_val is None:
                    return caller_object

                for i in range(l, r + 1):
                    elem = caller_object.get_or_create_element(i)
                    nv = self.compound_assignment(elem.value, r_val, operator)
                    self._patch_var_with_new_value(elem, nv)

                if log :
                    self.an.recorder.record_assignment(
                        line_no=self.an.current_start_line,
                        expr=expr,
                        var_obj=caller_object,
                        base_obj=caller_object,
                    )
                return None

            # ---- 매핑(MappingVariable) ------------------------------
            if isinstance(caller_object, MappingVariable):
                # Top interval인 경우: symbolic key로 entry 반환
                if l == 0 and r >= 2**255:  # Top interval
                    # 단일 entry가 있으면 반환
                    if len(caller_object.mapping) == 1:
                        entry = list(caller_object.mapping.values())[0]
                        return entry
                    # 없으면 symbolic key로 생성
                    symbolic_key = f"symbolic_index_{id(expr)}"
                    if symbolic_key not in caller_object.mapping:
                        caller_object.mapping[symbolic_key] = caller_object.get_or_create(symbolic_key)
                    return caller_object.mapping[symbolic_key]

                if r_val is None:
                    return caller_object

                for i in range(l, r + 1):
                    k = str(i)
                    if k in caller_object.mapping:
                        entry = caller_object.mapping[k]
                        nv = self.compound_assignment(entry.value, r_val, operator)
                        self._patch_var_with_new_value(entry, nv)

                if log :
                    self.an.recorder.record_assignment(
                        line_no=self.an.current_start_line,
                        expr=expr,
                        var_obj=caller_object,
                        base_obj=caller_object,
                    )
                return None

        # Interval 도 int 도 아니면 (아직 심볼릭) – 아무 것도 못 함
        return None

    def update_left_var_of_index_access_context(self, expr, rVal, operator, variables,
                                                callerObject=None, callerContext=None,
                                                log:bool=False, line_no:int=None, top_expr=None):
        # base expression에 대한 재귀
        base_obj = self._update_left_var(expr.base, rVal, operator, variables,
                                        None, "IndexAccessContext", log, line_no, top_expr)

        # (2026-10-23) mapping / array 는 Storage 로 — 읽기(Evaluation.evaluate_index_access_context)와 같은 키 해석·선택
        if isinstance(base_obj, (MappingVariable, ArrayVariable)):
            return self._update_indexed(base_obj, expr, rVal, operator, variables, callerContext,
                                        log, line_no, top_expr)

        # index expression에 대한 재귀
        return self._update_left_var(expr.index, rVal, operator, variables,
                                     base_obj, "IndexAccessContext", log, line_no, top_expr)

    def _update_indexed(self, c, expr, rVal, operator, variables, callerContext, log, line_no, top_expr):
        """c[expr.index] 대입(또는 더 깊은 LHS 의 중간 단계). 고른 셀에 값을 쓰고(strong), 셀을 _pending 에 남겨
        최상위 대입이 끝난 뒤 alias 전파를 받게 한다."""
        if not c.struct_defs or not c.enum_defs:
            all_structs, all_enums = self.an.get_full_struct_enum_defs()
            c.struct_defs = {**all_structs, **c.struct_defs}
            c.enum_defs = {**all_enums, **c.enum_defs}
        storage = self.an.storage
        res = storage.resolve(c, expr.index, variables)
        if self._assume and res.kind in ("range", "bottom"):
            return None                      # 미확정 위치는 좁힐 대상이 없다
        cell = storage.select(c, res)
        if not self._assume:
            self._pending.append((c, res, cell, expr))
        # 더 깊은 접근(m[i].x / a[i][j])의 중간 단계이거나 객체 찾기만 하는 호출 → 셀을 돌려준다
        if callerContext is not None or rVal is None:
            return cell
        if isinstance(cell, (ArrayVariable, StructVariable, MappingVariable)):
            if not self._assign_whole_composite(cell, rVal):
                raise ValueError(f"cannot assign {type(rVal).__name__} to composite '{cell.identifier}'")
            if log:
                self.an.recorder.record_assignment(
                    line_no=line_no if line_no is not None else self.an.current_start_line,
                    expr=top_expr if top_expr is not None else expr, var_obj=cell, base_obj=c)
            return None
        self._write_leaf(cell, rVal, operator, c, log, line_no, top_expr, expr)
        return None

    def update_left_var_of_member_access_context(
            self, expr, rVal, operator, variables,
            callerObject=None, callerContext=None,
            log:bool=False, line_no:int=None, top_expr=None):

        # ① 먼저 base 부분을 재귀-업데이트
        base_obj = self._update_left_var(expr.base, rVal, operator,
                                        variables, None, "MemberAccessContext", log, line_no, top_expr)
        member = expr.member

        # ────────────────────────────────────────────────
        # ② 〈글로벌 멤버〉가 매핑의 키로 쓰인 경우
        #      · balances[msg.sender]         (1-단계)
        #      · allowed[msg.sender][_from]   (2-단계)
        # ────────────────────────────────────────────────
        if VariableEnv.is_global_expr(expr) and isinstance(callerObject, MappingVariable):
            full_name = f"{expr.base.identifier}.{member}"  # "msg.sender"

            if not callerObject.struct_defs or not callerObject.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

            # global var의 실제 값으로 key 결정 (identifier 경로와 동일)
            key = full_name  # fallback
            if full_name in variables:
                gv_val = variables[full_name].value
                if isinstance(gv_val, AddressSet):
                    if gv_val.is_singleton():
                        key = str(gv_val)  # "AddressSet({101})"
                elif hasattr(gv_val, "min_value"):
                    if gv_val.min_value == gv_val.max_value:
                        key = str(gv_val.min_value)

            # (1) 엔트리 없으면 생성
            if key not in callerObject.mapping:
                callerObject.mapping[key] = callerObject.get_or_create(key)

            entry = callerObject.mapping[key]
            # struct/array/mapping이면 바로 반환 (외부 member access가 이어짐)
            if isinstance(entry, (StructVariable, ArrayVariable, MappingVariable)):
                return entry

            # (2-B) leaf 에 값 대입 중이면 여기서 patch
            # ★ F33. resolve-only 호출을 걸러야 한다.
            #   `resolve_lhs_expr` 는 "순수히 변수 객체만 찾는다" 는 규약으로
            #   `update_left_var(expr, None, None, ...)` 를 부른다(handle_delete 가 사용).
            #   형제 분기들(L163/207/240/416/525/570)은 전부 `if r_val is None: return ...`
            #   가드를 갖는데 **여기만 빠져 있었다** → `compound_assignment(..., None)` 이
            #   `Unsupported compound-assignment operator: None` 으로 터졌다.
            #   실측: `delete _admins[msg.sender];` / `delete _bal[msg.sender];`
            if rVal is None:
                return entry
            if hasattr(entry, "value"):
                self._store(entry, self.compound_assignment(entry.value, rVal, operator))

            if log :
                actual_line = line_no if line_no is not None else self.an.current_start_line
                self.an.recorder.record_assignment(
                    line_no=actual_line,
                    expr=top_expr if top_expr else expr,
                    var_obj=entry,
                    base_obj=callerObject,
                )
            return None

        if isinstance(base_obj, MappingVariable):
            # ① base expression 이 IndexAccess 면 → index 식에서 키 추출
            key = None
            base_exp = expr.base  # levels[i]   에서   expr.base == IndexAccess
            if getattr(base_exp, "context", "") == "IndexAccessContext":
                idx_exp = base_exp.index
                # 식별자 인덱스  (levels[i]  →  "i")
                if getattr(idx_exp, "context", "") == "IdentifierExpContext":
                    key = idx_exp.identifier
                # 숫자 / 주소 literal 인덱스
                elif getattr(idx_exp, "context", "") == "LiteralExpContext":
                    key = str(idx_exp.literal)
                # MemberAccess 인덱스 (newLockedStake.prevID → evaluate해서 값 추출)
                elif getattr(idx_exp, "context", "") == "MemberAccessContext":
                    eval_result = self.ev.evaluate_expression(idx_exp, variables, None, None)
                    if isinstance(eval_result, (IntegerInterval, UnsignedIntegerInterval)):
                        if eval_result.min_value == eval_result.max_value:
                            key = str(eval_result.min_value)
                        else:
                            # Top interval: symbolic key 사용
                            key = f"{idx_exp.base.identifier}.{idx_exp.member}"
                    # Fallback: identifier로 사용
                    if key is None:
                        key = f"{idx_exp.base.identifier}.{idx_exp.member}"

            # ② ①에서 못 뽑았고, 매핑에 엔트리 하나뿐이면 그걸 그대로 사용
            if key is None and len(base_obj.mapping) == 1:
                key, _ = next(iter(base_obj.mapping.items()))

            # ③ key를 여전히 못 찾았으면 에러
            if key is None:
                raise ValueError(f"Cannot resolve mapping key from expression")

            # ── 엔트리 가져오거나 생성
            if key not in base_obj.mapping:
                base_obj.mapping[key] = base_obj.get_or_create(key)

            nested = base_obj.mapping[key]
            base_obj = nested  # 이후 Struct 처리로 fall-through

        if isinstance(base_obj, ArrayVariable):
            # (1) .length  ─ Read-only.  LHS 로 올 수 없으므로 rVal 는 None
            if member == "length":
                from Domain.Variable import array_length
                return array_length(base_obj)      # (2026-10-23) 구체화한 원소 수가 아니라 배열 길이

            # (2) .push / .pop 은 LHS 로 오지 않으므로 여기서 무시
            return base_obj  # 깊은 체이닝 대비 그대로 전달

            # ② base 가 StructVariable 인지 확인
        if not isinstance(base_obj, StructVariable):
            raise ValueError(f"Member access on non-struct '{base_obj.identifier}'")

        # ③ 멤버 존재 확인
        if member not in base_obj.members:
            raise ValueError(f"Member '{member}' not in struct '{base_obj.identifier}'")

        nested = base_obj.members[member]

        if isinstance(nested, (StructVariable, ArrayVariable, MappingVariable)):
            # 더 깊은 member access가 이어질 수 있으므로 그대로 반환
            return nested

        # ── elementary / enum ──────────────────────────────
        if isinstance(nested, (Variables, EnumVariable)):
            # IndexAccessContext에서 호출되었으면 mapping key로 사용
            if callerContext == "IndexAccessContext" and isinstance(callerObject, MappingVariable):
                idx_val = nested.value
                # Interval → key 변환
                if isinstance(idx_val, (IntegerInterval, UnsignedIntegerInterval)):
                    if idx_val.min_value == idx_val.max_value:
                        key = str(idx_val.min_value)
                    else:
                        # Top interval: 단일 entry 반환 또는 symbolic key 생성
                        if len(callerObject.mapping) == 1:
                            return list(callerObject.mapping.values())[0]
                        key = f"symbolic_{nested.identifier}"
                else:
                    # Non-interval: symbolic key
                    key = f"symbolic_{nested.identifier}"

                # Entry 가져오거나 생성
                if key not in callerObject.mapping:
                    callerObject.mapping[key] = callerObject.get_or_create(key)
                return callerObject.mapping[key]

            # 일반 IndexAccessContext (Array 등)
            if callerContext == "IndexAccessContext":
                return nested

            if rVal is None:
                return nested

            self._store(nested, self.compound_assignment(nested.value, rVal, operator))

            if log :
                actual_line = line_no if line_no is not None else self.an.current_start_line
                self.an.recorder.record_assignment(
                    line_no=actual_line,
                    expr=top_expr if top_expr else expr,
                    var_obj=nested,
                    base_obj=base_obj,
                )
            return None

        raise ValueError(f"Unexpected member-type '{type(nested).__name__}'")

    # Interpreter/Semantics/Update.py
    # … (생략) …

    def update_left_var_of_literal_context(
            self,
            expr: Expression,
            r_val,
            operator: str,
            variables: dict[str, Variables],
            caller_object: Variables | ArrayVariable | MappingVariable | None = None,
            caller_context=None,
            log: bool = False,
            line_no: int = None,
            top_expr = None
    ):
        # ───────────────────────────── 준비 ─────────────────────────────
        lit = expr.literal  # 예: 123, 0x1a, true …
        lit_str = str(lit)
        if caller_object is None:
            raise ValueError(f"Literal '{lit_str}' cannot appear standalone on LHS")

        # ── literal  ➜  Interval/원시값 변환 ────────────────────────────
        def _to_interval(ref_var: Variables, text: str):
            # RHS(r_val)이 이미 Interval 이면 그대로
            if VariableEnv.is_interval(r_val):
                return r_val

            # 숫자 ----------------------------------------------------------------
            if text.startswith(('-', '0x')) or text.isdigit():
                v = int(text, 0)
                et = ref_var.typeInfo.elementaryTypeName
                b = ref_var.typeInfo.intTypeLength or 256
                return (
                    IntegerInterval(v, v, b) if et.startswith("int")
                    else UnsignedIntegerInterval(v, v, b)
                )

            # 불리언 --------------------------------------------------------------
            if text in ("true", "false"):
                return BoolInterval(1, 1) if text == "true" else BoolInterval(0, 0)

            # 20-byte 주소 hex ----------------------------------------------------
            if text.startswith("0x") and len(text) <= 42:
                v = int(text, 16)
                return UnsignedIntegerInterval(v, v, 160)

            # bytes/string 등 – 심볼릭 그대로
            return text

        # ========================================================================
        # 1) ArrayVariable  ─ arr[ literal ]
        # ========================================================================
        if isinstance(caller_object, ArrayVariable):
            if not lit_str.isdigit():
                return None  # 비정수 인덱스면 상위에서 처리

            idx = int(lit_str)
            if idx < 0:
                raise IndexError(f"Negative index {idx} for array '{caller_object.identifier}'")

            # 동적 배열이면 부족한 요소는 padding — base type에 맞는 real
            # element(struct/nested-array/mapping/enum 포함)를 만드는
            # `_create_new_array_element`를 통해서(struct 등을 무조건 raw
            # 심볼 문자열로 바텀아웃시키면 그 다음 leaf-업데이트에서
            # elementaryTypeName=None 크래시남, web3bugs_35_H_08/35_H_12에서 발견)
            # (2026-10-23, 희소 배열) 그 칸만 구체화(정적 배열은 선언 길이로 범위 검사)
            elem = caller_object.get_or_create_element(idx)

            # ── (a) nested composite (struct/array/map) 먼저 체크 ──────────
            # (StructVariable/ArrayVariable/MappingVariable 모두 Variables의
            # 서브클래스라서 순서를 안 바꾸면 아래 leaf 분기가 먼저 걸려버려
            # composite 원소 통짜 재할당(arr[i] = StructLit)이나 체이닝
            # (arr[i].field = x)이 전부 깨짐 — web3bugs_35_H_08/35_H_12에서 발견)
            if isinstance(elem, (ArrayVariable, StructVariable, MappingVariable)):
                if self._assign_whole_composite(elem, r_val):
                    if log:
                        self.an.recorder.record_assignment(
                            line_no=self.an.current_start_line,
                            expr=expr,
                            var_obj=elem,
                            base_obj=caller_object,
                        )
                    return None
                return elem  # 타입 안 맞음 → 체이닝 계속(예: arr[i].field = x)

            # ── (b) leaf 스칼라(elementary / enum) ────────────────────────
            if isinstance(elem, (Variables, EnumVariable)):
                if r_val is None:
                    return elem

                new_iv = _to_interval(elem, lit_str)
                self._store(elem, self.compound_assignment(elem.value, new_iv, operator))

                if log:
                    self.an.recorder.record_assignment(
                        line_no=self.an.current_start_line,
                        expr=expr,
                        var_obj=elem,
                        base_obj=caller_object,
                    )
                return None  # 더 내려갈 대상 없음

            return None

        # ========================================================================
        # 2) MappingVariable  ─ map[ literal ]
        # ========================================================================
        if isinstance(caller_object, MappingVariable):
            if not caller_object.struct_defs or not caller_object.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                caller_object.struct_defs = {**all_structs, **caller_object.struct_defs}
                caller_object.enum_defs = {**all_enums, **caller_object.enum_defs}

            key = lit_str  # 매핑 키는 문자열 그대로
            entry = caller_object.mapping.setdefault(key, caller_object.get_or_create(key))

            # ── (a) nested composite 먼저 체크 (순서 이유는 위 ArrayVariable
            # 분기 주석 참고 — 같은 이유로 여기도 leaf 체크보다 앞서야 함) ──
            if isinstance(entry, (ArrayVariable, StructVariable, MappingVariable)):
                if self._assign_whole_composite(entry, r_val):
                    if log:
                        self.an.recorder.record_assignment(
                            line_no=self.an.current_start_line,
                            expr=expr,
                            var_obj=entry,
                            base_obj=caller_object,
                        )
                    return None
                return entry  # 타입 안 맞음 → 체이닝 계속

            # ── (b) leaf 스칼라 ───────────────────────────────────────────
            if isinstance(entry, (Variables, EnumVariable)):
                if r_val is None:
                    return entry

                new_iv = _to_interval(entry, lit_str)
                self._store(entry, self.compound_assignment(entry.value, new_iv, operator))

                if log:
                    self.an.recorder.record_assignment(
                        line_no=self.an.current_start_line,
                        expr=expr,
                        var_obj=entry,
                        base_obj=caller_object,
                    )
                return None

            return None

        # ========================================================================
        # 3) 기타(Struct 등) – 현재 설계 범위 밖
        # ========================================================================
        raise ValueError(
            f"Literal context '{lit_str}' not handled for '{type(caller_object).__name__}'"
        )

    def _write_leaf(self, var_obj, r_val, operator, caller_object, log, actual_line_no, top_expr, record_expr):
        """leaf 변수에 compound-assignment 적용 + Recorder 호출 (식별자·인덱스 대입 공용)"""
        if actual_line_no is None:
            actual_line_no = self.an.current_start_line
        # ★ var_obj가 이미 Interval인 경우 (배열 원소가 직접 Interval로 저장된 경우)
        # 이는 join된 결과로, 직접 처리할 수 없으므로 skip
        if VariableEnv.is_interval(var_obj):
            return

        # (a) r_val → Interval/AddressSet 변환(필요 시)
        conv_val = r_val

        # ★ AddressSet 직접 처리
        if isinstance(r_val, AddressSet):
            conv_val = r_val  # AddressSet은 그대로 사용
        elif not VariableEnv.is_interval(r_val) and isinstance(var_obj, Variables):
            et = var_obj.typeInfo.elementaryTypeName
            bits = var_obj.typeInfo.intTypeLength or 256

            # ① 숫자(int, bool) ------------------------------------------------
            if isinstance(r_val, bool):
                conv_val = BoolInterval(int(r_val), int(r_val))
            elif isinstance(r_val, int):
                conv_val = (
                    IntegerInterval(r_val, r_val, bits) if et.startswith("int")
                    else UnsignedIntegerInterval(r_val, r_val, bits)
                )

            # ② 문자열 리터럴(hex, dec, true/false) ----------------------------
            elif isinstance(r_val, str) and (r_val.lstrip("-").isdigit() or r_val.startswith("0x")):
                n = int(r_val, 0)
                conv_val = (
                    IntegerInterval(n, n, bits) if et.startswith("int")
                    else UnsignedIntegerInterval(n, n, bits)
                )
            elif r_val in ("true", "false"):
                conv_val = BoolInterval(1, 1) if r_val == "true" else BoolInterval(0, 0)

        # (b) 실제 값 패치 (operator가 None이 아닐 때만)
        if operator is not None:
            # ★ AddressSet의 경우 compound_assignment를 거치지 않고 직접 할당
            if isinstance(conv_val, AddressSet):
                if operator == '=':
                    var_obj.value = conv_val
                else:
                    raise ValueError(f"AddressSet does not support compound operator: {operator}")
            else:
                self._store(var_obj, self.compound_assignment(var_obj.value, conv_val, operator))

        # (c) 기록 (log가 True이고 operator가 None이 아닐 때)
        if log and operator is not None:
            # top_expr을 사용하여 최상위 LHS expression 기록
            actual_record_expr = top_expr if top_expr is not None else record_expr
            self.an.recorder.record_assignment(
                line_no=actual_line_no,
                expr=actual_record_expr,
                var_obj=var_obj,
                base_obj=caller_object,
            )

    def update_left_var_of_identifier_context(
            self,
            expr: Expression,
            r_val,  # Interval | int | str | …
            operator: str,
            variables: dict[str, Variables],
            caller_object: Variables | ArrayVariable | StructVariable | MappingVariable | None = None,
            caller_context: str | None = None,
            log: bool = False,
            line_no: int = None,
            top_expr = None
    ):
        ident = expr.identifier

        # line_no를 헬퍼 함수에서 접근하기 위해 미리 결정
        actual_line_no = line_no if line_no is not None else self.an.current_start_line

        # ────────────────────────── 내부 헬퍼 ──────────────────────────
        def _apply_to_leaf(var_obj: Variables | EnumVariable, record_expr: Expression):
            self._write_leaf(var_obj, r_val, operator, caller_object, log, actual_line_no, top_expr, record_expr)


        # ======================================================================
        # 1)  caller_object 가 **ArrayVariable** 인 경우  arr[i] = …
        # ======================================================================
        if isinstance(caller_object, ArrayVariable):
            if ident not in variables:
                raise ValueError(f"Index identifier '{ident}' not found.")

            idx_iv = variables[ident].value  # Interval | ⊥

            # ⊥이면 전체-쓰기 추상화
            if VariableEnv.is_interval(idx_iv) and idx_iv.is_bottom():
                return caller_object

            # Interval 범위가 너무 크면 전체-쓰기 추상화 (widening된 경우)
            MAX_CONCRETE_INDICES = 20
            if VariableEnv.is_interval(idx_iv) and idx_iv.min_value != idx_iv.max_value:
                range_size = idx_iv.max_value - idx_iv.min_value + 1
                if range_size > MAX_CONCRETE_INDICES:
                    return caller_object

                # ★ 범위가 작으면 각 인덱스에 할당 (over-approximation)
                for idx in range(idx_iv.min_value, idx_iv.max_value + 1):
                    if idx < 0:
                        continue  # 음수 인덱스는 skip

                    if not caller_object.in_static_range(idx):
                        continue  # 정적 배열 범위 밖
                    elem = caller_object.get_or_create_element(idx)      # (희소 배열) 그 칸만 구체화
                    if isinstance(elem, (StructVariable, ArrayVariable, MappingVariable)):
                        # composite는 처리 불가, 추상화
                        return caller_object

                    # leaf 업데이트 (join)
                    _apply_to_leaf(elem, expr)

                return None

            # Singleton interval: 정확한 인덱스 하나
            if not (VariableEnv.is_interval(idx_iv) and idx_iv.min_value == idx_iv.max_value):
                raise ValueError(f"Array index '{ident}' must resolve to interval.")

            idx = idx_iv.min_value
            if idx < 0:
                raise IndexError(f"Negative index {idx} for array '{caller_object.identifier}'")

            # (2026-10-23, 희소 배열) 그 칸만 구체화 — 앞쪽 원소를 채우지 않는다(구체화 안 된 원소 = D).
            #   정적 배열은 선언 길이로 범위 검사(IndexError).
            elem = caller_object.get_or_create_element(idx)
            if isinstance(elem, (StructVariable, ArrayVariable, MappingVariable)):
                # 통짜 재할당(arr[i] = StructLit 등)이면 내부 상태 교체,
                # 아니면(체이닝 중) 그대로 반환해 더 내려감
                if self._assign_whole_composite(elem, r_val):
                    if log:
                        self.an.recorder.record_assignment(
                            line_no=actual_line_no,
                            expr=top_expr if top_expr is not None else expr,
                            var_obj=elem,
                            base_obj=caller_object,
                        )
                    return None
                return elem  # composite – 더 내려감
            _apply_to_leaf(elem, expr)  # leaf 업데이트 + 기록
            return None

        # ======================================================================
        # 2)  caller_object 가 **StructVariable**  – s.x = …
        # ======================================================================
        if isinstance(caller_object, StructVariable):
            if ident not in caller_object.members:
                raise ValueError(f"Struct '{caller_object.identifier}' has no member '{ident}'")
            mem = caller_object.members[ident]
            if isinstance(mem, (StructVariable, ArrayVariable, MappingVariable)):
                if self._assign_whole_composite(mem, r_val):
                    if log:
                        self.an.recorder.record_assignment(
                            line_no=actual_line_no,
                            expr=top_expr if top_expr is not None else expr,
                            var_obj=mem,
                            base_obj=caller_object,
                        )
                    return None
                return mem
            _apply_to_leaf(mem, expr)  # leaf
            return None

        # ======================================================================
        # 3)  caller_object 가 **MappingVariable**  – map[key] = …
        # ======================================================================
        if isinstance(caller_object, MappingVariable):
            if not caller_object.struct_defs or not caller_object.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                caller_object.struct_defs = {**all_structs, **caller_object.struct_defs}
                caller_object.enum_defs = {**all_enums, **caller_object.enum_defs}

            # ── (1) ident 가 변수로 선언돼 있을 때 ──────────────────────────
            if ident in variables:
                key_var = variables[ident]
                val = getattr(key_var, "value", key_var)

                # ★ AddressSet: singleton이면 str(val)을 key로 사용
                if isinstance(val, AddressSet):
                    if val.is_singleton():
                        key_str = str(val)  # "AddressSet({1})"
                    else:
                        key_str = ident  # TOP/multi → identifier

                # --- bytes / symbolic => 그대로 식별자 사용 -------
                elif getattr(key_var.typeInfo, "elementaryTypeName", "").startswith("bytes") \
                        or not VariableEnv.is_interval(val):
                    key_str = ident

                # --- int/uint singleton interval ----------------------------
                elif VariableEnv.is_interval(val):
                    iv = val

                    if not iv.is_bottom() and iv.min_value == iv.max_value:
                        key_str = str(iv.min_value)
                    else:
                        # 범위 / ⊥  → 전체-쓰기 추상화 유지
                        return caller_object
                else:
                    key_str = ident  # fallback
            # ── (2) ident 가 리터럴(바로 account처럼) ----------------------
            else:
                raise ValueError (f"Key '{ident}' not found.")

            entry = caller_object.get_or_create(key_str)

            # ── composite 타입 처리 ────────────────────────────────────
            if isinstance(entry, (StructVariable, ArrayVariable, MappingVariable)):
                # r_val이 같은 타입의 composite이면 복사
                if r_val is not None and type(r_val) == type(entry):
                    if isinstance(entry, StructVariable) and isinstance(r_val, StructVariable):
                        # Struct 멤버별 복사
                        for member_name, member_val in r_val.members.items():
                            if member_name in entry.members:
                                entry.members[member_name] = VariableEnv.copy_single_variable(member_val)

                        if log:
                            self.an.recorder.record_assignment(
                                line_no=actual_line_no,
                                expr=top_expr if top_expr is not None else expr,
                                var_obj=entry,
                                base_obj=caller_object,
                            )
                        return None
                return entry  # composite (deep access가 이어질 경우)

            # ★ F33. resolve-only 호출은 객체만 돌려준다 (위 member-access 분기와 같은 이유).
            #   이 가드가 없어 `delete _admins[who];`(식별자가 매핑 키)가
            #   `_apply_to_leaf` 후 None 을 반환해 `LHS cannot be resolved.` 로 터졌다.
            if r_val is None:
                return entry
            _apply_to_leaf(entry, expr)  # leaf + 기록
            return None

        # ======================================================================
        # 4)  caller_object 가 스칼라(Variables / EnumVariable) – 단순 ident
        # ======================================================================
        if isinstance(caller_object, (Variables, EnumVariable)):
            _apply_to_leaf(caller_object, expr)
            return None

        # ======================================================================
        # 5)  상위 객체 없음  (top-level ident)  ─  a = …
        # ======================================================================
        if caller_context in ("IndexAccessContext", "MemberAccessContext"):
            # 상위 composite 의 base 식별자 해석 단계 – 객체 반환만
            if ident in variables:
                return variables[ident]
            if ident in ("block", "tx", "msg"):
                return ident
            enum_defs = self.an.contract_cfgs[self.an.current_target_contract].enumDefs
            if ident in enum_defs:
                return enum_defs[ident]
            if ident in self.an.library_cfgs:
                # LibraryName.Member 접근의 base (e.g. Perpetuals.Side.Long) - Evaluation.py의
                # isLibrary 처리와 동일한 marker를 반환해 이후 .Member 단계가 이어질 수 있게 함
                return {"isLibrary": True, "libraryName": ident}
            raise ValueError(f"Identifier '{ident}' not declared.")

        # 일반 로컬/상태 변수 직접 갱신
        if ident not in variables:
            raise ValueError(f"Variable '{ident}' not declared.")
        tgt = variables[ident]

        # ★ 통짜 재할당: arr = new T[](n) / s = SomeStruct({...}) / m = ... 처럼
        # tgt가 composite(Array/Struct/Mapping)이고 r_val도 같은 계열 composite면
        # 아래 `_apply_to_leaf`(스칼라용, `.value`에 통째로 얹어버림)로 보내면
        # 안 됨 — r_val이 이미 올바르게 구성한 내부 상태(elements/members/mapping)가
        # 통째로 버려지고 tgt는 예전 상태(예: 선언 시점의 빈 배열) 그대로 남아
        # 이후 인덱스/멤버 접근이 깨짐(web3bugs_35_H_08/35_H_12에서 발견).
        if self._assign_whole_composite(tgt, r_val):
            if log:
                self.an.recorder.record_assignment(
                    line_no=actual_line_no,
                    expr=top_expr if top_expr is not None else expr,
                    var_obj=tgt,
                    base_obj=caller_object,
                )
            return None

        if not isinstance(tgt, (Variables, EnumVariable)):
            raise ValueError(f"Assignment to non-scalar '{ident}' requires member/index access.")
        if r_val is None:
            return tgt

        _apply_to_leaf(tgt, expr)
        return None

    def update_left_var_of_intent_index_access_context(self, expr: Expression,
                                                                 rVal,
                                                                 operator,
                                                                 variables: dict[str, Variables],
                                                                 callerObject=None, callerContext=None,
                                                        log:bool=False):
        # base
        base_obj = self._update_left_var(
            expr.base, rVal, operator, variables,
            None, "IntentIndexAccess", log
        )
        # index
        return self._update_left_var(
            expr.index, rVal, operator, variables, base_obj, "IntentIndexAccess", log
        )

    def update_left_var_of_intent_member_access_context(self, expr: Expression,
                                                                  rVal,
                                                                  operator,
                                                                  variables: dict[str, Variables],
                                                                  callerObject=None, callerContext=None,
                                                         log:bool=False):

        # ① 먼저 base 부분을 재귀-업데이트
        base_obj = self._update_left_var(expr.base, rVal, operator,
                                                 variables, None, "IntentMemberAccess", log)
        member = expr.member

        if member is not None:
            if VariableEnv.is_global_expr(expr) and isinstance(callerObject, MappingVariable):
                key = f"{expr.base.identifier}.{member}"  # "msg.sender"

                # 엔트리가 없으면 새로 만든다
                if key not in callerObject.mapping:
                    callerObject.mapping[key] = callerObject.get_or_create(key)

                entry = callerObject.mapping[key]

                # ① 더 깊은 IndexAccess 가 이어질 때는 객체 그대로 반환
                if callerContext == "IntentIndexAccess":
                    return entry  # allowed[msg.sender] 의 결과

                # ② leaf 읽기(Intent이므로 값 패치는 하지 않음)
                return entry  # Variables / EnumVariable / Array…

            if not isinstance(base_obj, StructVariable):
                raise ValueError(f"[Warn] member access on non-struct '{base_obj.identifier}'")
            m = base_obj.members.get(member)
            if m is None:
                raise ValueError(f"[Warn] struct '{base_obj.identifier}' has no member '{member}'")

            nested = base_obj.members[member]

            if isinstance(nested, (StructVariable, ArrayVariable, MappingVariable)):
                # 더 깊은 member access가 이어질 수 있으므로 그대로 반환
                return nested
            elif isinstance(nested, (Variables, EnumVariable)):
                if rVal is not None:
                    self._patch_var_with_new_value(m, rVal)
                return m

        raise ValueError(f"Unexpected member-type '{member}'")

    # ---------------------------------------------------------------------------
    #  읽기-전용  LHS resolver
    #   • *어떤 값도* 수정하지 않는다.
    #   • 찾은 객체(Variables / ArrayVariable …)를 그대로 반환하거나
    #     더 내려갈 composite 객체를 반환한다.
    #   • 기존 _resolve_and_update_expr 의 "update 파트"를 모두 제거한 버전.
    # ---------------------------------------------------------------------------
    def resolve_lhs_expr(
            self,
            expr: Expression,
            variables: dict[str, Variables],
            caller_object=None,
            caller_context: str | None = None,
            log: bool = False
    ):
        """纯粹히 '변수 객체'를 찾아서 돌려준다. (값 패치는 전혀 하지 않음)"""

        return self.update_left_var(expr, None, None, variables, caller_object, caller_context, log)

    def _touch_index_entry(self, container, idx: int):
        """배열/매핑에서 idx 번째 엔트리를 가져오거나 필요 시 생성"""
        if isinstance(container, ArrayVariable):
            return container.get_or_create_element(idx)

        if isinstance(container, MappingVariable):
            # MappingVariable 이 알아서 value 객체를 만들어 줌
            return container.get_or_create(str(idx))

        raise TypeError(
            f"_touch_index_entry: unsupported container type {type(container).__name__}"
        )

    def _store(self, var_obj, new_val):
        """leaf 변수에 값 기록 — 선언 타입으로 암묵 변환(`coerce_to_declared`, Phase 4b)."""
        var_obj.value = coerce_to_declared(new_val, getattr(var_obj, "typeInfo", None),
                                           f"value assigned to '{getattr(var_obj, 'identifier', '?')}'")

    def compound_assignment(self, left_interval, right_interval, operator):
        """
        +=, -=, <<= … 등 복합 대입 연산자의 interval 계산.
        한쪽이 ⊥(bottom) 이면 결과도 ⊥ 로 전파한다.
        """

        # 0) 단순 대입인 '='
        if operator == '=':
            return right_interval

        # 1) ⊥-전파용 로컬 헬퍼 ――――――――――――――――――――――――――――
        def _arith_safe(l, r, fn):
            """
            l·r 중 하나라도 bottom ⇒ bottom 그대로 반환
            아니면 fn(l, r) 실행
            """
            if l.is_bottom() or r.is_bottom():
                return l.bottom(getattr(l, "type_length", 256))
            return fn(l, r)

        # 2) 연산자 → 동작 매핑 ―――――――――――――――――――――――――――――――
        mapping = {
            '+=': lambda l, r: _arith_safe(l, r, lambda a, b: a.add(b)),
            # (Phase 2) unchecked 블록에서는 unsigned 뺄셈이 wrap 한다
            '-=': lambda l, r: _arith_safe(l, r, lambda a, b: (
                a.subtract(b, wrapping=self.an.engine._unchecked)
                if isinstance(a, UnsignedIntegerInterval) else a.subtract(b))),
            '*=': lambda l, r: _arith_safe(l, r, lambda a, b: a.multiply(b)),
            '/=': lambda l, r: _arith_safe(l, r, lambda a, b: a.divide(b)),
            '%=': lambda l, r: _arith_safe(l, r, lambda a, b: a.modulo(b)),
            '|=': lambda l, r: _arith_safe(l, r, lambda a, b: a.bitwise('|', b)),
            '^=': lambda l, r: _arith_safe(l, r, lambda a, b: a.bitwise('^', b)),
            '&=': lambda l, r: _arith_safe(l, r, lambda a, b: a.bitwise('&', b)),
            '<<=': lambda l, r: _arith_safe(l, r, lambda a, b: a.shift(b, '<<')),
            '>>=': lambda l, r: _arith_safe(l, r, lambda a, b: a.shift(b, '>>')),
            '>>>=': lambda l, r: _arith_safe(l, r, lambda a, b: a.shift(b, '>>>')),
        }

        # 3) 실행
        try:
            return mapping[operator](left_interval, right_interval)
        except KeyError:
            raise ValueError(f"Unsupported compound-assignment operator: {operator}")

    def _fill_array(self, arr: ArrayVariable, py_val: list):
        """
        arr.elements 를 py_val(list) 내용으로 완전히 교체
        – 1-D · multi-D 모두 재귀로 채움
        – 숫자   → Integer / UnsignedInteger Interval( [n,n] )
        – Bool   → BoolInterval
        – str('symbolicAddress …') → 160-bit interval
        – list   → nested ArrayVariable
        """
        arr.elements = {}  # 새로 만들기
        baseT = arr.typeInfo.arrayBaseType

        def _make_elem(eid: str, raw):
            # list  →  하위 ArrayVariable 재귀
            if isinstance(raw, list):
                sub = ArrayVariable(
                    identifier=eid, base_type=baseT,
                    array_length=len(raw), is_dynamic=True,
                    scope=arr.scope
                )
                self._fill_array(sub, raw)
                return sub

            # symbolicAddress …
            if isinstance(raw, str) and raw.startswith("symbolicAddress"):
                nid = int(raw.split()[1])
                # ★ AddressSet으로 처리
                addr_set = AddressSet(ids={nid})
                return Variables(eid, addr_set, scope=arr.scope, typeInfo=baseT)

            # 숫자  /  Bool  → Interval
            if isinstance(raw, (int, bool)):
                if baseT.elementaryTypeName.startswith("uint"):
                    bits = getattr(baseT, "intTypeLength", 256)
                    val = UnsignedIntegerInterval(raw, raw, bits)
                elif baseT.elementaryTypeName.startswith("int"):
                    bits = getattr(baseT, "intTypeLength", 256)
                    val = IntegerInterval(raw, raw, bits)
                else:  # bool
                    val = BoolInterval(int(raw), int(raw))
                return Variables(eid, val, scope=arr.scope, typeInfo=baseT)

            # bytes / string 심볼
            return Variables(eid, f"symbol_{eid}", scope=arr.scope, typeInfo=baseT)

        for i, raw in enumerate(py_val):
            elem_id = f"{arr.identifier}[{i}]"
            arr.elements[i] = _make_elem(elem_id, raw)

        # ───────── 값 덮어쓰기 (debug 주석용) ────────────────────────────────────

    def _apply_new_value_to_variable(self, var_obj: Variables, new_value):
        """
        new_value 유형
          • IntegerInterval / UnsignedIntegerInterval / BoolInterval
          • 단일 int / bool
          • 'symbolicAddress N'  (str)
          • 기타 str   (symbolic tag)
        """
        # 0) 이미 Interval 객체면 그대로
        if VariableEnv.is_interval(new_value) or isinstance(new_value, BoolInterval):
            self._store(var_obj, new_value)
            return

        # 1) elementary 타입 확인
        if not (var_obj.typeInfo and var_obj.typeInfo.elementaryTypeName):
            print(f"[Info] _apply_new_value_to_variable: skip non-elementary '{var_obj.identifier}'")
            return

        etype = var_obj.typeInfo.elementaryTypeName
        bits = var_obj.typeInfo.intTypeLength or 256

        # ---- int / uint ---------------------------------------------------
        if etype.startswith("int"):
            iv = (
                new_value
                if isinstance(new_value, IntegerInterval)
                else IntegerInterval(int(new_value), int(new_value), bits)
            )
            var_obj.value = iv

        elif etype.startswith("uint"):
            uv = (
                new_value
                if isinstance(new_value, UnsignedIntegerInterval)
                else UnsignedIntegerInterval(int(new_value), int(new_value), bits)
            )
            var_obj.value = uv

        # ---- bool ---------------------------------------------------------
        elif etype == "bool":
            if isinstance(new_value, str) and new_value.lower() == "any":
                var_obj.value = BoolInterval.top()
            elif isinstance(new_value, (bool, int)):
                b = bool(new_value)
                var_obj.value = BoolInterval(int(b), int(b))
            else:
                var_obj.value = new_value  # already BoolInterval

        # ---- address ------------------------------------------------------
        elif etype == "address":
            # ★ AddressSet 기반 처리
            if isinstance(new_value, AddressSet):
                var_obj.value = new_value
            elif isinstance(new_value, UnsignedIntegerInterval):
                # Interval → AddressSet 변환
                if new_value.is_bottom():
                    var_obj.value = AddressSet.bot()
                elif new_value.min_value == new_value.max_value:
                    var_obj.value = AddressSet(ids={new_value.min_value})
                else:
                    var_obj.value = AddressSet.top()
            elif isinstance(new_value, str) and new_value.startswith("symbolicAddress"):
                nid = int(new_value.split()[1])
                var_obj.value = AddressSet(ids={nid})
            elif isinstance(new_value, int):
                var_obj.value = AddressSet(ids={new_value})
            else:  # 임의 문자열 → TOP
                var_obj.value = AddressSet.top()

        # ---- fallback -----------------------------------------------------
        else:
            print(f"[Warning] _apply_new_value_to_variable: unhandled type '{etype}'")
            var_obj.value = new_value

    def _patch_var_with_new_value(self, var_obj, new_val):
        """
        • ArrayVariable 인 경우 list 가 오면 _fill_array 로 교체
        • 그 외는 _apply_new_value_to_variable 로 기존 처리
        """
        if isinstance(var_obj, ArrayVariable) and isinstance(new_val, list):
            self._fill_array(var_obj, new_val)
        else:
            self._apply_new_value_to_variable(var_obj, new_val)

# ---------------------------------------------------------------------------
#  디버그 주석(@GlobalVar / @StateVar / @Param) 전용 헬퍼
# ---------------------------------------------------------------------------
    def _snapshot_once(self, var_obj):
        """처음 보는 객체면 스냅샷 매니저에 등록."""
        if id(var_obj) not in self.an.snapman.store:
            self.an.snapman.register(var_obj, self.an.ser)

    def _bind_if_address(self, var_obj):
        """address 형이면 심볼릭-ID ↔ 변수 바인딩."""
        if getattr(var_obj.typeInfo, "elementaryTypeName", None) == "address":
            # ★ AddressSet 기반 바인딩
            if isinstance(var_obj.value, AddressSet):
                address_manager.bind_var(var_obj.identifier, var_obj.value)

    # -------------- public API ---------------------------------------------
    def apply_debug_directive(
        self,
        *,
        scope: str,                    # "global" | "state" | "local"
        lhs_expr: Expression,
        value,
        variables: dict[str, Variables],   # state-var 테이블 or fcfg.related_variables
        edit_event: str,                  # "add" | "modify" | "delete"
    ):
        """
        • resolve_lhs_expr() 으로 객체를 찾고
        • 스냅샷 + 값 패치 + 주소 바인딩 + Recorder 기록까지 한-큐
        """
        target = self.resolve_lhs_expr(lhs_expr, variables)
        if target is None:
            raise ValueError(f"LHS cannot be resolved to a {scope} variable.")

        # ① snapshot & restore ---------------------------------------------
        self._snapshot_once(target)
        if edit_event == "delete":
            self.an.snapman.restore(target, self.an.de)
            return                          # 롤백만 하고 끝
        elif edit_event not in ("add", "modify"):
            raise ValueError(f"unknown edit_event {edit_event!r}")

        # ② 값 패치 ---------------------------------------------------------
        self._patch_var_with_new_value(target, value)

        # ③ 주소-ID 바인딩 ---------------------------------------------------
        self._bind_if_address(target)

        # ④ Recorder 기록 제거 -----------------------------------------------
        #   디버그 주석은 초기값 설정이므로 기록 불필요
        #   실제 assignment는 재해석 시 자동으로 기록됨
