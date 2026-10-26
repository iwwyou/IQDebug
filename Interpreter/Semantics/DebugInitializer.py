from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.Variable import Variables, ArrayVariable, StructVariable, MappingVariable, EnumVariable
from Domain.Type import SolType
from Domain.Interval import Interval, IntegerInterval, BoolInterval, UnsignedIntegerInterval
from Domain.AddressSet import AddressSet
from Domain.IR import Expression

from Utils.Helper import VariableEnv
from Interpreter.Semantics.Storage import is_composite as is_composite_var


def _coerce_debug(value, var):
    """(Phase 4b) 디버그 입력 값을 변수의 선언 타입으로 암묵 변환. 예전엔 `@StateVar x = [8,8]` 이 선언 타입(uint16 등)과 무관하게
    256비트로 들어가, 이후 대입으로 선언 폭이 된 값과 join 할 때 폭 불일치로 터지거나 256비트에서 계산됐다.
    선언 타입 범위를 벗어난 입력은 사용자 입력 오류로 예외."""
    from Interpreter.Semantics.Evaluation import coerce_to_declared
    return coerce_to_declared(value, getattr(var, "typeInfo", None),
                              f"debug value for '{getattr(var, 'identifier', '?')}'")

class DebugInitializer:
    """
    디버깅 전용 변수 초기화/업데이트 클래스.
    Update.py와 달리 mapping entry가 없으면 강제로 생성하는 등 디버깅에 특화된 동작.
    """

    def __init__(self, analyzer: "ContractAnalyzer"):
        self.an = analyzer
        self._assumed: dict = {}     # 이번 디버깅 회차에 값을 놓은 셀 id → (슬롯, 값)  — 같은 위치 입력 모순 검사

    def begin_batch(self):
        """디버깅 1회(flush) 시작 — DebugUnitAnalyzer.flush 가 부른다."""
        self._assumed = {}

    def _assume_indexed(self, container, expr, variables):
        """(2026-10-23) 디버그 입력의 인덱스 단계 — 진입 제약으로 셀을 고른다(Storage.assume_select). 프로그램 쓰기가 아니다.
        컨테이너를 **고르기 전에** 스냅샷에 등록한다: 새 키 엔트리·선택자 셀·summary 변화까지 flush 후 restore 로 되돌리기 위해
        (예전엔 leaf 만 등록돼 새로 만든 엔트리가 mapping 에 남았다)."""
        self._snapshot_once_for_debug(container)
        if not container.struct_defs or not container.enum_defs:
            all_structs, all_enums = self.an.get_full_struct_enum_defs()
            container.struct_defs = {**all_structs, **container.struct_defs}
            container.enum_defs = {**all_enums, **container.enum_defs}
        return self.an.storage.assume_select(container, expr.index, variables, label=str(expr))

    def resolve_lhs_expr_for_debug(
            self,
            expr: Expression,
            variables: dict[str, Variables],
            caller_object=None,
            caller_context: str | None = None
    ):
        """
        디버깅 전용 LHS 표현식 해석.
        일반 프로그램과 달리 mapping entry가 없어도 강제로 생성함.
        """
        return self._update_left_var_for_debug(expr, None, None, variables, caller_object, caller_context)

    def _update_left_var_for_debug(self, expr, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        디버깅 전용 변수 업데이트. Update.py의 update_left_var와 유사하지만 디버깅에 특화됨.
        """

        if expr.context == "IndexAccessContext":
            return self._update_left_var_of_index_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "MemberAccessContext":
            return self._update_left_var_of_member_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "IdentifierExpContext":
            return self._update_left_var_of_identifier_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "LiteralExpContext":
            return self._update_left_var_of_literal_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "IntentIndexAccess":
            return self._update_left_var_of_intent_index_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "IntentMemberAccess":
            return self._update_left_var_of_intent_member_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "VarRefBase":
            # VarRefBase: visitVarRef에서 생성되는 단순 변수 참조
            return self._update_left_var_of_varref_base_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "VarRefMemberAccess":
            # VarRefMemberAccess: varRef의 .member 접근 (IntentMemberAccess와 동일 구조)
            return self._update_left_var_of_intent_member_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "VarRefIndexAccess":
            # VarRefIndexAccess: varRef의 [index] 접근 (IntentIndexAccess와 동일 구조)
            return self._update_left_var_of_intent_index_access_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)
        elif expr.context == "TypeConversion":
            # TypeConversion이 mapping key로 사용되는 경우 (예: balances[address(this)])
            return self._update_left_var_of_type_conversion_context_for_debug(
                expr, rVal, operator, variables, callerObject, callerContext)

        return None

    def _update_left_var_of_intent_index_access_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        IntentIndexAccess 컨텍스트 처리 (디버깅 전용)
        allowed[_from][msg.sender] 같은 중첩 매핑 접근 처리
        """
        # base 객체 찾기
        base_obj = self._update_left_var_for_debug(
            expr.base, rVal, operator, variables, None, "IntentIndexAccess")

        if base_obj is None:
            return None
        if isinstance(base_obj, (MappingVariable, ArrayVariable)):
            return self._assume_indexed(base_obj, expr, variables)

        # index 처리 - callerObject를 base_obj로, callerContext를 IntentIndexAccess로 전달
        result = self._update_left_var_for_debug(
            expr.index, rVal, operator, variables, base_obj, "IntentIndexAccess")
        return result

    def _update_left_var_of_identifier_context_for_debug(
            self, expr: Expression, rVal, operator, variables, caller_object=None, caller_context=None):
        """
        Identifier 컨텍스트 처리 (디버깅 전용)
        """
        ident = expr.identifier

        # ======================================================================
        # 0) caller_context가 IntentIndexAccess이고 ident가 글로벌 변수인 경우
        #    (예: block, tx, msg)
        # ======================================================================
        if caller_context == "IntentIndexAccess" and ident in ("block", "tx", "msg"):
            # 글로벌 객체는 그대로 반환 (다음 MemberAccess에서 처리)
            return ident

        # ======================================================================
        # 1) caller_object가 ArrayVariable인 경우 - arr[variable_index]
        # ======================================================================
        if isinstance(caller_object, ArrayVariable):
            if not caller_object.struct_defs or not caller_object.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                caller_object.struct_defs = {**all_structs, **caller_object.struct_defs}
                caller_object.enum_defs = {**all_enums, **caller_object.enum_defs}

            if ident not in variables:
                # 디버깅에서는 변수가 없어도 리터럴로 처리 시도
                if ident.isdigit():
                    idx = int(ident)
                    try:
                        if caller_object.typeInfo.isDynamicArray:
                            return caller_object.get_or_create_element(idx)
                        else:
                            if caller_object.in_static_range(idx):
                                return caller_object.get_or_create_element(idx)
                        return None
                    except (IndexError, ValueError):
                        return None
                return None

            idx_var = variables[ident]
            iv = getattr(idx_var, "value", None)

            # 인덱스가 확정값인 경우
            if VariableEnv.is_interval(iv) and not iv.is_bottom() and iv.min_value == iv.max_value:
                idx = iv.min_value
                if idx < 0:
                    return None
                try:
                    if caller_object.typeInfo.isDynamicArray:
                        return caller_object.get_or_create_element(idx)
                    else:
                        if caller_object.in_static_range(idx):
                            return caller_object.get_or_create_element(idx)
                    return None
                except (IndexError, ValueError):
                    return None

            # 불확정 인덱스인 경우 - 디버깅에서는 일단 None 반환
            return None

        # ======================================================================
        # 2) caller_object가 MappingVariable인 경우
        # ======================================================================
        elif isinstance(caller_object, MappingVariable):
            # ── ① key 결정 ──────────────────────────────────
            if ident in variables:  # ident == 변수명
                key_var = variables[ident]
                val = getattr(key_var, "value", key_var)

                # ★ AddressSet: singleton이면 str(val)을 key로 사용
                if isinstance(val, AddressSet):
                    if val.is_singleton():
                        key_str = str(val)  # "AddressSet({1})"
                    else:
                        key_str = key_var.identifier  # TOP/multi → identifier
                elif hasattr(val, "min_value"):
                    if val.min_value == val.max_value:  # 숫자·bool 싱글톤
                        key_str = str(val.min_value)
                    else:
                        key_str = key_var.identifier  # 여전히 TOP
                else:
                    key_str = key_var.identifier  # string·bool 등
            else:
                # ───── 디버깅 전용: 리터럴/특수 expression을 key로 변환 ──────
                key_str = self._resolve_mapping_key(ident, caller_object, variables)

            # ── ③ 매핑 엔트리 가져오거나 생성 ─────────────
            if not caller_object.struct_defs or not caller_object.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                caller_object.struct_defs = {**all_structs, **caller_object.struct_defs}
                caller_object.enum_defs = {**all_enums, **caller_object.enum_defs}

            entry = caller_object.get_or_create(key_str)
            if isinstance(entry, (StructVariable, ArrayVariable, MappingVariable)):
                return entry
            else:
                return entry  # Variables 객체 반환

        # ======================================================================
        # 3) caller_object가 StructVariable인 경우
        # ======================================================================
        elif isinstance(caller_object, StructVariable):
            if ident not in caller_object.members:
                # 디버깅에서는 struct 멤버가 없어도 시도는 해봄 (실제로는 정의에 따라)
                return None

            member = caller_object.members[ident]
            if isinstance(member, Variables):
                return member
            else:
                return member  # ArrayVariable, StructVariable, MappingVariable

        # ======================================================================
        # 4) 상위 객체 없음 (top-level ident) - 일반 변수 찾기
        # ======================================================================
        if caller_context in (
                "IndexAccessContext", "MemberAccessContext", "IntentIndexAccess"):
            # base에 대한 접근
            if ident in variables:
                return variables[ident]  # MappingVariable, ArrayVariable 등을 반환
            elif ident in ("block", "tx", "msg"):
                # 글로벌 객체는 문자열로 반환 (MemberAccess에서 처리)
                return ident
            else:
                # 디버깅에서는 없어도 일단 None 반환 (에러 발생시키지 않음)
                return None

        # 일반적인 변수 접근
        if ident in variables:
            return variables[ident]
        else:
            return None

    def _update_left_var_of_index_access_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        IndexAccess 컨텍스트 처리 (디버깅 전용) - arr[i], mapping[key] 등
        """
        # base 객체 찾기
        base_obj = self._update_left_var_for_debug(
            expr.base, rVal, operator, variables, None, "IndexAccessContext")

        if base_obj is None:
            return None
        if isinstance(base_obj, (MappingVariable, ArrayVariable)):
            return self._assume_indexed(base_obj, expr, variables)

        # index 처리
        result = self._update_left_var_for_debug(
            expr.index, rVal, operator, variables, base_obj, "IndexAccessContext")
        return result

    def _update_left_var_of_member_access_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        MemberAccess 컨텍스트 처리 (디버깅 전용) - struct.field, msg.sender 등
        """
        from Utils.Helper import VariableEnv

        # base 객체 찾기
        base_obj = self._update_left_var_for_debug(
            expr.base, rVal, operator, variables, None, "MemberAccessContext")

        if base_obj is None:
            return None

        member_name = expr.member

        # ======================================================================
        # 1) 글로벌 멤버 (msg.sender, tx.origin 등)가 매핑 키로 사용되는 경우
        #    예: allowed[_from][msg.sender], balances[msg.sender]
        # ======================================================================
        if VariableEnv.is_global_expr(expr) and isinstance(callerObject, MappingVariable):
            full_name = f"{expr.base.identifier}.{member_name}"  # "msg.sender"

            if not callerObject.struct_defs or not callerObject.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

            # global var의 실제 값으로 key 결정
            key = full_name  # fallback
            if full_name in variables:
                gv_val = variables[full_name].value
                if isinstance(gv_val, AddressSet):
                    if gv_val.is_singleton():
                        key = str(gv_val)  # "AddressSet({101})"
                elif hasattr(gv_val, "min_value"):
                    if gv_val.min_value == gv_val.max_value:
                        key = str(gv_val.min_value)

            # 엔트리 없으면 생성
            if key not in callerObject.mapping:
                callerObject.mapping[key] = callerObject.get_or_create(key)

            entry = callerObject.mapping[key]

            # IntentIndexAccess나 IndexAccessContext에서 호출된 경우
            # 더 깊은 접근이 이어질 수 있으므로 객체 반환
            if callerContext in ("IntentIndexAccess", "IndexAccessContext"):
                return entry

            # leaf 변수에 값 대입하는 경우
            if rVal is not None and hasattr(entry, "value"):
                entry.value = _coerce_debug(rVal, entry)

            return entry

        # ======================================================================
        # 2) base가 글로벌 문자열인 경우 (ident 단계에서 "msg" 반환된 경우)
        # ======================================================================
        if isinstance(base_obj, str) and base_obj in ("block", "tx", "msg"):
            # 글로벌 멤버 접근 - callerObject가 MappingVariable이면 키로 사용
            if isinstance(callerObject, MappingVariable):
                full_name = f"{base_obj}.{member_name}"

                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

                key = full_name
                if full_name in variables:
                    gv_val = variables[full_name].value
                    if isinstance(gv_val, AddressSet):
                        if gv_val.is_singleton():
                            key = str(gv_val)
                    elif hasattr(gv_val, "min_value"):
                        if gv_val.min_value == gv_val.max_value:
                            key = str(gv_val.min_value)

                if key not in callerObject.mapping:
                    callerObject.mapping[key] = callerObject.get_or_create(key)

                return callerObject.mapping[key]

            # 그 외에는 None 반환 (글로벌 값 자체는 변수가 아님)
            return None

        # ======================================================================
        # 3) StructVariable인 경우
        # ======================================================================
        if isinstance(base_obj, StructVariable):
            if member_name not in base_obj.members:
                # 디버깅에서는 멤버가 없어도 생성 시도 (실제로는 struct 정의에 따라)
                # 여기서는 일단 None 반환하고, 필요하면 struct 정의를 확인해서 생성
                return None
            return base_obj.members[member_name]

        # ======================================================================
        # 4) ArrayVariable의 length 접근 등
        # ======================================================================
        if isinstance(base_obj, ArrayVariable):
            if member_name == "length":
                # length는 변수가 아니므로 None 반환 (값만 필요한 경우)
                return None
            return None

        return None

    def _update_left_var_of_literal_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        Literal 컨텍스트 처리 (디버깅 전용) - 숫자나 문자열 리터럴
        """
        lit = expr.literal

        # caller_object가 있는 경우 (배열 인덱스나 매핑 키로 사용)
        if callerObject is not None:
            # ArrayVariable인 경우
            if isinstance(callerObject, ArrayVariable):
                if not lit.lstrip("-").isdigit():
                    return None  # 디버깅에서도 배열 인덱스는 숫자여야 함
                idx = int(lit)
                if idx < 0:
                    return None
                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}
                # 동적 배열이면 확장 가능, 정적 배열이면 범위 체크
                try:
                    if callerObject.typeInfo.isDynamicArray:
                        # 동적 배열은 필요하면 확장
                        elem = callerObject.get_or_create_element(idx)
                        return elem
                    else:
                        # 정적 배열은 범위 내에서만
                        if callerObject.in_static_range(idx):
                            return callerObject.get_or_create_element(idx)
                        return None
                except (IndexError, ValueError):
                    return None

            # MappingVariable인 경우
            elif isinstance(callerObject, MappingVariable):
                if not callerObject.struct_defs or not callerObject.enum_defs:
                    all_structs, all_enums = self.an.get_full_struct_enum_defs()
                    callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                    callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

                # ★ 리터럴 키도 identifier-context 분기(위 159번째 줄)와 동일하게
                # _resolve_mapping_key로 변환 — 매핑의 key type이 address면
                # `AddressSet({N})` 형태로 맞춰줘야 실제 evaluator가 나중에
                # 계산하는 키와 일치함. 예전엔 원본 텍스트를 그대로 키로 써서
                # `twapData[1]`처럼 address-keyed 매핑을 bare 정수로 seed하면
                # 실제 조회 키(`AddressSet({1})`)와 안 맞아 조용히 다른 엔트리를
                # 만들었음(web3bugs_70_H_04/45_H_01 등에서 발견).
                key = self._resolve_mapping_key(lit, callerObject, variables)
                # 디버깅에서는 키가 없어도 생성
                entry = callerObject.get_or_create(key)
                return entry

        # caller_context가 있는 경우 (base/index 해석용)
        if callerContext in ("IndexAccessContext", "MemberAccessContext"):
            return lit  # 리터럴 값 그대로 반환

        return None

    def _update_left_var_of_intent_member_access_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        IntentMemberAccess 컨텍스트 처리 (디버깅 전용)
        디버그 주석에서 사용되는 멤버 접근: allowed[_from][msg.sender] 형태

        callerObject: 외부 매핑 객체 (예: allowed[_from])
        callerContext: 호출 컨텍스트 (예: "IntentIndexAccess")
        """
        member = expr.member

        # ======================================================================
        # 1) 글로벌 멤버가 매핑 키로 사용되는 경우: allowed[_from][msg.sender]
        # ======================================================================
        if VariableEnv.is_global_expr(expr) and isinstance(callerObject, MappingVariable):
            full_name = f"{expr.base.identifier}.{member}"  # "msg.sender"

            if not callerObject.struct_defs or not callerObject.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
                callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

            # global var의 실제 값으로 key 결정
            key = full_name
            if full_name in variables:
                gv_val = variables[full_name].value
                if isinstance(gv_val, AddressSet):
                    if gv_val.is_singleton():
                        key = str(gv_val)
                elif hasattr(gv_val, "min_value"):
                    if gv_val.min_value == gv_val.max_value:
                        key = str(gv_val.min_value)

            # 엔트리가 없으면 새로 만든다
            if key not in callerObject.mapping:
                callerObject.mapping[key] = callerObject.get_or_create(key)

            entry = callerObject.mapping[key]

            # ① 더 깊은 IndexAccess 가 이어질 때는 객체 그대로 반환
            if callerContext == "IntentIndexAccess":
                return entry

            # ② leaf 읽기
            return entry

        # ======================================================================
        # 2) 일반적인 경우: base 객체를 먼저 찾고 멤버 접근
        # ======================================================================
        # ① 먼저 base 부분을 재귀-업데이트
        base_obj = self._update_left_var_for_debug(
            expr.base, rVal, operator, variables, None, "IntentMemberAccess")

        if base_obj is None:
            return None

        if member is not None:
            # ======================================================================
            # 3) StructVariable인 경우
            # ======================================================================
            if isinstance(base_obj, StructVariable):
                m = base_obj.members.get(member)
                if m is None:
                    # 디버깅에서는 멤버가 없어도 시도 (실제로는 정의에 따라)
                    return None

                nested = base_obj.members[member]

                if isinstance(nested, (StructVariable, ArrayVariable, MappingVariable)):
                    # 더 깊은 member access가 이어질 수 있으므로 그대로 반환
                    return nested
                elif isinstance(nested, (Variables, EnumVariable)):
                    if rVal is not None:
                        nested.value = _coerce_debug(rVal, nested)
                    return nested

        return None

    def _update_left_var_of_varref_base_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        VarRefBase 컨텍스트 처리 (디버깅 전용) - visitVarRef에서 생성되는 단순 변수 참조
        """
        ident = expr.identifier

        if ident in variables:
            return variables[ident]
        else:
            return None

    def apply_debug_directive_enhanced(
        self,
        *,
        scope: str,
        lhs_expr: Expression,
        value,
        variables: dict[str, Variables],
        edit_event: str,
    ):
        """
        개선된 디버깅 지시어 적용. mapping entry가 없어도 강제로 생성함.

        특별 처리:
        - `array.length = [N, N]` → 동적 배열의 크기를 N개로 초기화
        """

        # ★ 특별 케이스: 동적 배열의 .length 설정
        if (lhs_expr.context in ("MemberAccessContext", "IntentMemberAccess", "VarRefMemberAccess") and
            lhs_expr.member == "length"):

            # base 객체 찾기
            base_obj = self.resolve_lhs_expr_for_debug(lhs_expr.base, variables)

            if isinstance(base_obj, ArrayVariable) and base_obj.typeInfo.isDynamicArray:
                # ① 스냅샷 먼저 생성 (복원을 위해)
                self._snapshot_once_for_debug(base_obj)

                if edit_event == "delete":
                    # 삭제: 원래 상태로 복원
                    self.an.snapman.restore(base_obj, self.an.de)
                    return
                elif edit_event not in ("add", "modify"):
                    raise ValueError(f"unknown edit_event {edit_event!r}")

                # value에서 크기 추출
                target_length = self._extract_array_length_from_value(value)
                if target_length is not None:
                    # ② 동적 배열을 target_length 크기로 초기화
                    self._initialize_dynamic_array_to_length(base_obj, target_length)
                    return
                else:
                    raise ValueError(f"Invalid length value for dynamic array: {value}")

        # 일반적인 변수 처리
        target = self.resolve_lhs_expr_for_debug(lhs_expr, variables)
        if target is None:
            # More detailed error message for debugging
            try:
                expr_repr = f"{lhs_expr.context}"
                if hasattr(lhs_expr, 'identifier') and lhs_expr.identifier is not None:
                    expr_repr += f" (identifier: {lhs_expr.identifier})"
                if hasattr(lhs_expr, 'member') and lhs_expr.member is not None:
                    expr_repr += f" (member: {lhs_expr.member})"
                if hasattr(lhs_expr, 'base') and lhs_expr.base is not None:
                    base_id = getattr(lhs_expr.base, 'identifier', None)
                    expr_repr += f" (base: {base_id})"
                if hasattr(lhs_expr, 'index') and lhs_expr.index is not None:
                    idx_id = getattr(lhs_expr.index, 'identifier', None)
                    expr_repr += f" (index: {idx_id})"
            except:
                expr_repr = "unknown"

            # (2026-10-23) 슬롯을 제공해 놓고 값을 버리면 안 된다 — 입력 오류로 올린다(예전엔 경고만 찍고 건너뛰었다)
            raise ValueError(f"디버그 입력 `{lhs_expr}` 을 놓을 위치를 찾지 못했다: {expr_repr}")

        # ① snapshot & restore
        self._snapshot_once_for_debug(target)
        if edit_event == "delete":
            self.an.snapman.restore(target, self.an.de)
            return
        elif edit_event not in ("add", "modify"):
            raise ValueError(f"unknown edit_event {edit_event!r}")

        # ② 값 패치
        self._patch_var_with_new_value_for_debug(target, value)
        self._check_same_location(target, lhs_expr)
        self.an.storage.after_assume()

        # ③ 주소-ID 바인딩
        self._bind_if_address_for_debug(target)

        # ④ Recorder 기록 제거
        #   디버그 주석은 초기값 설정이므로 기록 불필요
        #   실제 assignment는 재해석 시 자동으로 기록됨

    def _check_same_location(self, target, lhs_expr):
        """(2026-10-23) 두 슬롯이 같은 위치(같은 셀)를 고르면(예: i=[3,3] 일 때 `m[i]` 와 `m[3]`) 두 제약을 모두 만족해야 한다 —
        값의 meet. 비면 입력 모순(그런 진입 상태가 없다)."""
        if is_composite_var(target) or not hasattr(target, "value"):
            return
        label = str(lhs_expr)
        prev = self._assumed.get(id(target))
        if prev is not None and prev[0] != label:
            cur = target.value
            if hasattr(cur, "meet") and type(cur) is type(prev[1]):
                met = cur.meet(prev[1])
                if met.is_bottom():
                    raise ValueError(f"디버그 입력 모순: `{prev[0]}` 와 `{label}` 는 같은 위치인데 값이 겹치지 않는다 "
                                     f"({prev[1]} / {cur})")
                target.value = met
        self._assumed[id(target)] = (label, target.value)

    def _snapshot_once_for_debug(self, target_var: Variables):
        """
        디버깅용 스냅샷 생성 - 처음 보는 객체면 스냅샷 매니저에 등록
        """
        if hasattr(self.an, 'snapman') and hasattr(self.an, 'ser'):
            if id(target_var) not in self.an.snapman.store:
                self.an.snapman.register(target_var, self.an.ser)

    def _patch_var_with_new_value_for_debug(self, target_var, new_value):
        """
        디버깅용 변수 값 패치
        """
        from Domain.Variable import Variables as VarClass
        from Domain.Interval import UnsignedIntegerInterval, IntegerInterval

        # ArrayVariable 처리: value가 리스트이고 배열 요소 값들인 경우
        if isinstance(target_var, ArrayVariable) and isinstance(new_value, list):
            # 배열 요소 초기화
            base_type = target_var.typeInfo.arrayBaseType
            if target_var.typeInfo.isDynamicArray:
                target_var.typeInfo.arrayLength = len(new_value)
                target_var.length = UnsignedIntegerInterval(len(new_value), len(new_value), 256)
            elif target_var.static_length() != len(new_value):
                # (2026-10-23) 정적 배열의 길이는 선언으로 정해진다 — 다른 길이의 값 목록은 그런 진입 상태가 없다
                raise ValueError(f"잘못된 디버깅 입력 `{target_var.identifier}`: 길이 {target_var.static_length()} 인 "
                                 f"정적 배열에 원소 {len(new_value)} 개")
            target_var.elements = {}

            for idx, val in enumerate(new_value):
                # Variables 객체 생성
                elem_id = f"{target_var.identifier}[{idx}]"
                elem = VarClass(identifier=elem_id, scope=target_var.scope)
                elem.typeInfo = base_type

                # 값 설정
                if isinstance(val, list) and len(val) == 2:
                    min_val, max_val = val
                    if base_type.elementaryTypeName.startswith('uint'):
                        bits = base_type.intTypeLength or 256
                        elem.value = UnsignedIntegerInterval(min_val, max_val, bits)
                    elif base_type.elementaryTypeName.startswith('int'):
                        bits = base_type.intTypeLength or 256
                        elem.value = IntegerInterval(min_val, max_val, bits)
                    else:
                        elem.value = val
                elif isinstance(val, int):
                    if base_type.elementaryTypeName.startswith('uint'):
                        bits = base_type.intTypeLength or 256
                        elem.value = UnsignedIntegerInterval(val, val, bits)
                    elif base_type.elementaryTypeName.startswith('int'):
                        bits = base_type.intTypeLength or 256
                        elem.value = IntegerInterval(val, val, bits)
                    else:
                        elem.value = val
                else:
                    elem.value = val

                target_var.elements[idx] = elem
            return

        # EnumVariable: _parse_debug_value가 만든 "Member" / "Enum.Member" /
        # "Library.Enum.Member" 형태의 문자열을 실제 멤버 인덱스로 해석해야 한다.
        # EnumVariable도 Variables의 서브클래스라 아래의 일반 isinstance(target_var, VarClass)
        # 분기에 걸리긴 하지만, 그 분기는 valueIndex를 계산하지 않고 문자열을 그대로
        # .value에 박아버려서 (엄밀히는 동작하지도 실패하지도 않는 반쪽짜리 상태) enum
        # 비교/평가 코드가 기대하는 형태가 안 된다 - 그래서 이 분기를 먼저 확인한다.
        if isinstance(target_var, EnumVariable) and isinstance(new_value, str):
            member_name = new_value.rsplit(".", 1)[-1]
            if member_name not in target_var.members:
                raise ValueError(
                    f"Debug annotation value '{new_value}' for enum '{target_var.typeInfo.enumTypeName}': "
                    f"member '{member_name}' not found (known members: {list(target_var.members.keys())}). "
                    f"target_var.members is populated from the enum's own EnumDefinition - if it's empty, "
                    f"the enum type likely wasn't resolved (e.g. an unresolved cross-library qualified type)."
                )
            target_var.valueIndex = target_var.members[member_name]
            target_var.value = member_name
            return

        if isinstance(target_var, VarClass):
            # 리스트 형태 값 처리 [1000,1000] -> UnsignedIntegerInterval(1000, 1000)
            if isinstance(new_value, list) and len(new_value) == 2:
                min_val, max_val = new_value
                if hasattr(target_var, 'typeInfo') and target_var.typeInfo:
                    et = getattr(target_var.typeInfo, 'elementaryTypeName', '')
                    bits = getattr(target_var.typeInfo, 'intTypeLength', 256) or 256

                    if et.startswith('uint'):
                        from Domain.Interval import UnsignedIntegerInterval
                        target_var.value = UnsignedIntegerInterval(min_val, max_val, bits)
                    elif et.startswith('int'):
                        from Domain.Interval import IntegerInterval
                        target_var.value = IntegerInterval(min_val, max_val, bits)
                    elif et == 'bool':
                        from Domain.Interval import BoolInterval
                        target_var.value = BoolInterval(min_val, max_val)
                    else:
                        target_var.value = new_value
                else:
                    target_var.value = new_value
            else:
                target_var.value = _coerce_debug(new_value, target_var)

    def _update_left_var_of_type_conversion_context_for_debug(
            self, expr: Expression, rVal, operator, variables, callerObject=None, callerContext=None):
        """
        TypeConversion이 mapping key로 사용되는 경우 처리 (예: balances[address(this)])
        evaluator로 expression을 평가하여 key를 결정하고 mapping entry를 반환
        """
        if not isinstance(callerObject, MappingVariable):
            return None

        key_val = self.an.evaluator.evaluate_expression(expr, variables, None, None)
        if key_val is None:
            return None

        if isinstance(key_val, AddressSet):
            key_str = str(key_val)
        elif hasattr(key_val, "min_value") and key_val.min_value == key_val.max_value:
            key_str = str(key_val.min_value)
        else:
            key_str = str(key_val)

        if not callerObject.struct_defs or not callerObject.enum_defs:
            all_structs, all_enums = self.an.get_full_struct_enum_defs()
            callerObject.struct_defs = {**all_structs, **callerObject.struct_defs}
            callerObject.enum_defs = {**all_enums, **callerObject.enum_defs}

        return callerObject.get_or_create(key_str)

    def _resolve_mapping_key(self, ident: str, caller_object, variables: dict) -> str:
        """mapping key 문자열을 evaluator와 동일한 방식으로 resolve.
        address(this) → AddressSet({1}), 숫자 → AddressSet({N}) or str(N), 등"""
        key_type = getattr(caller_object.typeInfo, 'mappingKeyType', None)
        key_type_name = getattr(key_type, 'elementaryTypeName', None) if key_type else None

        if key_type_name == "address":
            # address(this) → symbolic address 1
            if ident == "address(this)":
                return str(AddressSet(ids={self.an.addr_mgr.make_symbolic_address(1, "this").ids.copy().pop()}))
            # 숫자 literal → AddressSet
            try:
                addr_id = int(ident)
                return str(AddressSet(ids={addr_id}))
            except ValueError:
                pass
            # global variable (msg.sender 등) — MemberAccess에서 이미 처리되므로 여기 도달 안 함
            # 변수명인 경우 variables에서 조회
            if ident in variables:
                val = getattr(variables[ident], "value", None)
                if isinstance(val, AddressSet) and val.is_singleton():
                    return str(val)
            return ident

        # address가 아닌 key type
        try:
            return str(int(ident))
        except ValueError:
            return ident

    def _bind_if_address_for_debug(self, target_var: Variables):
        """
        디버깅용 주소 바인딩 (필요한 경우)
        """
        if isinstance(target_var, Variables) and hasattr(target_var, 'typeInfo'):
            et = getattr(target_var.typeInfo, 'elementaryTypeName', '')
            if et == 'address':
                # 주소 타입이면 심볼릭 매니저에 바인딩 가능
                # 여기서는 간단히 패스
                pass

    def _record_usage_for_debug(self, lhs_expr: Expression, target_var: Variables, scope: str, edit_event: str):
        """
        디버깅용 사용 기록
        """
        if hasattr(self.an, 'recorder'):
            # RecordManager가 있으면 기록
            try:
                self.an.recorder.record_debug_variable_usage(
                    expr=lhs_expr,
                    variable=target_var,
                    scope=scope,
                    event=edit_event
                )
            except AttributeError:
                # record_debug_variable_usage 메서드가 없으면 무시
                pass

    def _extract_array_length_from_value(self, value) -> int | None:
        """
        value에서 배열 길이를 추출
        - [N, N] → N
        - N (int) → N
        - UnsignedIntegerInterval([N, N]) → N (min == max일 때)
        """
        # Interval 객체인 경우
        if hasattr(value, 'min_value') and hasattr(value, 'max_value'):
            if value.min_value == value.max_value and isinstance(value.min_value, int):
                return value.min_value
        # 리스트 형태
        elif isinstance(value, list) and len(value) == 2:
            # [N, N] 형태
            if value[0] == value[1] and isinstance(value[0], int):
                return value[0]
        # 정수 형태
        elif isinstance(value, int):
            return value
        return None

    def _initialize_dynamic_array_to_length(self, arr: ArrayVariable, target_length: int):
        """
        동적 배열을 target_length 크기로 초기화
        - 현재 크기보다 크면 지정되지 않은 원소(typed ⊤)로 채움
        - 현재 크기보다 작으면 뒤쪽 요소 제거
        """
        # (2026-10-23, 희소 배열) 지정되지 않은 원소 = D(구체화하지 않음, typed ⊤). 길이 밖의 구체 셀만 버린다.
        arr.elements = {k: v for k, v in arr.elements.items() if k < target_length}
        arr.length = UnsignedIntegerInterval(target_length, target_length, 256)   # (2026-10-23) 추상 길이

    def _create_unspecified_array_element(self, arr: ArrayVariable, index: int, base_type: SolType) -> Variables:
        """
        디버그 입력이 지정하지 않은 배열 원소 생성 — 값을 모름 → typed ⊤.
        ★ (Phase 2, D2) 예전엔 ⊥ 로 채워, 그 원소를 읽으면 env 전체가 도달 불가로 판정될 수 있었다.
        """
        elem_id = f"{arr.identifier}[{index}]"

        # elementary type인 경우
        if base_type.typeCategory == "elementary":
            et = base_type.elementaryTypeName
            bits = base_type.intTypeLength or 256

            if et.startswith("uint"):
                from Domain.Interval import UnsignedIntegerInterval
                unspecified_val = UnsignedIntegerInterval.top(bits)
            elif et.startswith("int"):
                from Domain.Interval import IntegerInterval
                unspecified_val = IntegerInterval.top(bits)
            elif et == "bool":
                from Domain.Interval import BoolInterval
                unspecified_val = BoolInterval.top()
            elif et == "address":
                from Domain.AddressSet import AddressSet
                unspecified_val = AddressSet.top()
            else:
                # bytes, string 등
                unspecified_val = f"symbolic_{elem_id}"

            return Variables(
                identifier=elem_id,
                value=unspecified_val,
                scope=arr.scope,
                typeInfo=base_type
            )

        # struct type인 경우
        elif base_type.typeCategory == "struct":
            sv = StructVariable(
                identifier=elem_id,
                struct_type=base_type.structTypeName,
                scope=arr.scope
            )
            if not arr.struct_defs or not arr.enum_defs:
                all_structs, all_enums = self.an.get_full_struct_enum_defs()
                arr.struct_defs = {**all_structs, **arr.struct_defs}
                arr.enum_defs = {**all_enums, **arr.enum_defs}
            if base_type.structTypeName in arr.struct_defs:
                sv.initialize_struct(arr.struct_defs[base_type.structTypeName],
                                     struct_defs=arr.struct_defs, enum_defs=arr.enum_defs)
            return sv

        # nested array인 경우
        elif base_type.typeCategory == "array":
            return ArrayVariable(
                identifier=elem_id,
                base_type=base_type.arrayBaseType,
                array_length=base_type.arrayLength,
                is_dynamic=base_type.isDynamicArray,
                scope=arr.scope
            )

        # mapping인 경우 (배열의 요소로는 불가능하지만 방어적 처리)
        else:
            return Variables(
                identifier=elem_id,
                value=f"symbolic_{elem_id}",
                scope=arr.scope,
                typeInfo=base_type
            )
