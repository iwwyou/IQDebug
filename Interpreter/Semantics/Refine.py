from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                         # 타입 검사 전용
     from Analyzer.ContractAnalyzer import ContractAnalyzer

from Analyzer.EnhancedSolidityVisitor import READONLY_MEMBERS, READONLY_GLOBAL_BASES
from Domain.Interval import *
from Domain.Variable import Variables
from Domain.IR import Expression
from Domain.AddressSet import AddressSet
from Utils.Helper import VariableEnv


class Refine:

    def __init__(self, an: "ContractAnalyzer"):
        self.an = an

    @property
    def ev(self):
        return self.an.evaluator        # Evaluation 싱글톤

    @property
    def up(self):
        return self.an.updater          # Update 싱글톤

    def update_variables_with_condition(self, variables, condition_expr, is_true_branch):
        """
            condition_expr: Expression
              - 연산자(operator)가 비교연산(==,!=,<,>,<=,>=)일 수도 있고,
              - 논리연산(&&, ||, !)일 수도 있고,
              - 단일 변수(IdentifierExpContext)나 bool literal, etc. 일 수도 있음
            is_true_branch:
              - True => 조건이 만족되는 브랜치 (if, while 등의 true 분기)
              - False => 조건이 불만족인 브랜치 (else, while not, etc)
            variables: { var_name: Variables }  (CFGNode 상의 변수 상태)
            """

        if self._is_read_only_expr(condition_expr, variables): return

        # 1) condition_expr.operator 파악
        op = condition_expr.operator

        # 2) 만약 operator가 None인데, context가 IdentifierExpContext(단일 변수) 등 "단순 bool 변환"이라면
        if op is None:
            # 예: if (myBoolVar) => true branch라면 myBoolVar = [1,1], false branch라면 myBoolVar=[0,0]
            return self._update_single_condition(variables, condition_expr, is_true_branch)

        # 3) 논리 연산 처리
        elif op in ['&&', '||', '!']:
            return self._update_logical_condition(variables, condition_expr, is_true_branch)

        # 4) 비교 연산 처리 (==, !=, <, >, <=, >=)
        elif op in ['==', '!=', '<', '>', '<=', '>=']:
            return self._update_comparison_condition(variables, condition_expr, is_true_branch)

        else:
            # 함수 호출, 타입 변환 등 refine 불가능한 연산은 스킵 (sound overapproximation)
            return

    def _update_single_condition(self, vars_, cond_expr, is_true_branch):
        # bool literal인 경우는 영향 없음
        if cond_expr.context == "LiteralExpContext":
            return

        val = self.an.evaluator.evaluate_expression(cond_expr, vars_, None, None)
        # ▸ bool interval로 강제 변환
        if not isinstance(val, BoolInterval):
            if VariableEnv.is_interval(val):  # 숫자/주소
                val = VariableEnv.convert_int_to_bool_interval(val)
            else:
                return  # symbol 등 – 포기

        tgt = BoolInterval(1, 1) if is_true_branch else BoolInterval(0, 0)
        refined = val.meet(tgt)

        name = (cond_expr.identifier
                if cond_expr.context == "IdentifierExpContext" else None)
        if name and name in vars_:
            vars_[name].value = refined

    def _update_logical_condition(
            self,
            variables: dict[str, Variables],
            cond_expr: Expression,
            is_true_branch: bool) -> None:

        op = cond_expr.operator  # '&&', '||', '!'
        if op == '!':
            # !X : true-branch → X=false, false-branch → X=true
            return self._update_single_condition(
                variables,
                cond_expr.expression,
                not is_true_branch)

        condA = cond_expr.left
        condB = cond_expr.right

        # 먼저 현재 env에서 A, B를 불리언으로 평가(정제 전에 힌트 확보)
        A_b = self._as_bool_interval(condA, variables)
        B_b = self._as_bool_interval(condB, variables)

        # ───────── AND ─────────
        if op == '&&':
            if is_true_branch:
                # A && B 가 참 → 둘 다 참이어야 함 (교집합)
                self.update_variables_with_condition(variables, condA, True)
                self.update_variables_with_condition(variables, condB, True)
                return
            else:
                # ¬(A && B) ≡ (¬A) ∨ (¬B)
                # 단락 최적화: 한쪽이 확실히 false면 그쪽만 적용
                if self._is_false(A_b):
                    self.update_variables_with_condition(variables, condA, False)
                    return
                if self._is_false(B_b):
                    self.update_variables_with_condition(variables, condB, False)
                    return
                # 둘 다 불명 → 어느 쪽이 거짓인지 알 수 없으므로 정제 skip (sound)
                return

        # ───────── OR ─────────
        if op == '||':
            if is_true_branch:
                # A || B 가 참
                # 단락 최적화: 한쪽이 확실히 true면 그쪽만 적용
                if self._is_true(A_b):
                    self.update_variables_with_condition(variables, condA, True)
                    return
                if self._is_true(B_b):
                    self.update_variables_with_condition(variables, condB, True)
                    return
                # 둘 다 불명 → 어느 쪽이 참인지 알 수 없으므로 정제 skip (sound)
                return
            else:
                # ¬(A || B) ≡ ¬A ∧ ¬B → 둘 다 false로 정제
                self.update_variables_with_condition(variables, condA, False)
                self.update_variables_with_condition(variables, condB, False)
                return

        raise ValueError(f"Unexpected logical operator '{op}'")

    def _update_comparison_condition(
            self,
            variables: dict[str, Variables],
            cond_expr: Expression,
            is_true_branch: bool) -> None:

        _NON_LVALUE_CONTEXTS = frozenset({
            'FunctionCallContext', 'InterfaceFunctionCallContext',
            'LibraryFunctionCallContext', 'SuperFunctionCallContext',
            'ThisFunctionCallContext',
            'BinaryExpContext', 'UnaryExpContext',
            'LiteralExpContext', 'TupleExpContext',
            'TypeConversionContext',
        })

        def _has_non_lvalue_in_chain(e, variables):
            """expression의 base chain에 refine 불가능한 context가 있는지 확인"""
            while e is not None:
                ctx = getattr(e, 'context', '')
                if ctx in _NON_LVALUE_CONTEXTS:
                    return True
                # operator 기반 체크 (binary/unary)
                op = getattr(e, 'operator', None)
                if op in ('+', '-', '*', '/', '%', '**', '&&', '||', '!',
                          '<<', '>>', '&', '|', '^', '~'):
                    return True
                # `EnumType.Member`(예: `Status.Invalid`) 또는 qualified library
                # 상수(예: `TickMath.MIN_TICK`) — base가 실제 변수가 아니라
                # enum 타입 이름이나 라이브러리 이름 자체를 가리키는
                # MemberAccessContext는 상수 리터럴이지 narrow 가능한 lvalue가
                # 아님(enum 쪽은 web3bugs_42_H_01, `details[_id].status !=
                # Status.Invalid`에서 발견; 라이브러리 쪽은 web3bugs_35_H_08/
                # 35_H_12 재검증 중 `Ticks.sol`의 `lower == TickMath.MIN_TICK`
                # 에서 발견 — 둘 다 base를 변수로 착각해 `update_left_var`가
                # 죽던 같은 계열 문제, 라이브러리 쪽은 base_obj가 `{"isLibrary":
                # True, ...}` dict라서 에러 메시지 조립조차 실패하며 크래시함).
                # base 식별자가 `variables`에 없고 알려진 enum 타입 이름 또는
                # 라이브러리 이름과 일치하면 상수로 판정.
                if ctx == 'MemberAccessContext':
                    base = getattr(e, 'base', None)
                    if (base is not None
                            and getattr(base, 'context', '') == 'IdentifierExpContext'
                            and getattr(base, 'identifier', None)
                            and base.identifier not in variables
                            and (self.ev._find_enum_in_chain(base.identifier)
                                 or base.identifier in getattr(self.an.sa, 'file_level_enums', {})
                                 or base.identifier in self.an.library_cfgs)):
                        return True
                e = getattr(e, 'base', None)
            return False

        def _maybe_update(expr, variables, new_iv):
            if self._is_read_only_expr(expr, variables):
                return  # read-only → 변수 상태 수정 X
            if _has_non_lvalue_in_chain(expr, variables):
                return  # non-l-value → narrowing 대상 아님

            # ★ Update.py의 전략 적용: 매핑/배열 접근에서 심볼릭 인덱스 처리
            # IndexAccessContext일 때 인덱스가 interval인지 확인
            if getattr(expr, "context", "") == "IndexAccessContext":
                idx_expr = expr.index
                if getattr(idx_expr, "context", "") == "IdentifierExpContext":
                    idx_name = idx_expr.identifier
                    if idx_name in variables:
                        idx_val = variables[idx_name].value
                        # interval이 Top이거나 범위가 너무 크면 업데이트 skip
                        if VariableEnv.is_interval(idx_val):
                            # Top interval 체크 (0부터 최댓값까지)
                            if idx_val.min_value == 0 and idx_val.max_value >= 2**255:
                                return  # Top interval은 업데이트 불가
                            # 범위가 너무 큰 경우도 skip
                            MAX_REFINE_RANGE = 20
                            if idx_val.max_value - idx_val.min_value + 1 > MAX_REFINE_RANGE:
                                return

            # MemberAccessContext에서 base가 IndexAccess이고 심볼릭 인덱스인 경우
            if getattr(expr, "context", "") == "MemberAccessContext":
                base_expr = expr.base
                if getattr(base_expr, "context", "") == "IndexAccessContext":
                    idx_expr = base_expr.index
                    if getattr(idx_expr, "context", "") == "IdentifierExpContext":
                        idx_name = idx_expr.identifier
                        if idx_name in variables:
                            idx_val = variables[idx_name].value
                            if VariableEnv.is_interval(idx_val):
                                # Top interval이거나 범위가 큰 경우 업데이트 skip
                                if idx_val.min_value == 0 and idx_val.max_value >= 2**255:
                                    return
                                MAX_REFINE_RANGE = 20
                                if idx_val.max_value - idx_val.min_value + 1 > MAX_REFINE_RANGE:
                                    return

            try:
                # (2026-10-23) refine 은 저장이 아니라 좁히기 — alias 전파·선택자 무효화를 하지 않는다
                self.up.update_left_var(expr, new_iv, '=', variables, None, None, False, assume=True)
            except ValueError as e:
                # 매핑 키를 resolve할 수 없는 경우는 조용히 무시
                if "Cannot resolve mapping key" in str(e):
                    return
                raise

        # ───── 준비 ─────────────────────────────────────────────
        op = cond_expr.operator
        actual_op = op if is_true_branch else self.negate_operator(op)
        left_expr = cond_expr.left
        right_expr = cond_expr.right

        left_val = self.ev.evaluate_expression(left_expr, variables, None, None)
        right_val = self.ev.evaluate_expression(right_expr, variables, None, None)

        # ───────── CASE 1 : 둘 다 Interval ─────────────────────
        if VariableEnv.is_interval(left_val) and VariableEnv.is_interval(right_val):
            # 비트 길이 통일: Solidity 비교 시 큰 쪽으로 promotion 후 refine, 결과는 원래 비트로 복원
            orig_l_bits = left_val.type_length
            orig_r_bits = right_val.type_length
            if orig_l_bits != orig_r_bits:
                promo_bits = max(orig_l_bits, orig_r_bits)
                left_val = type(left_val)(left_val.min_value, left_val.max_value, promo_bits)
                right_val = type(right_val)(right_val.min_value, right_val.max_value, promo_bits)
            new_l, new_r = self.refine_intervals_for_comparison(left_val, right_val, actual_op)
            # 원래 비트 길이 복원
            if orig_l_bits != orig_r_bits:
                if not new_l.is_bottom():
                    new_l = type(new_l)(new_l.min_value, new_l.max_value, orig_l_bits)
                else:
                    new_l = new_l.bottom(orig_l_bits)
                if not new_r.is_bottom():
                    new_r = type(new_r)(new_r.min_value, new_r.max_value, orig_r_bits)
                else:
                    new_r = new_r.bottom(orig_r_bits)
            _maybe_update(left_expr, variables, new_l)
            _maybe_update(right_expr, variables, new_r)
            return

        # ───────── CASE 2-A : Interval vs literal ──────────────
        if VariableEnv.is_interval(left_val) and not VariableEnv.is_interval(right_val):
            coerced_r = self._coerce_literal_to_interval(right_val, left_val.type_length)
            if coerced_r is None:          # 상대를 구간으로 표현 못 함 → 정제 없음(건전)
                return
            new_l, _dummy = self.refine_intervals_for_comparison(left_val, coerced_r, actual_op)
            _maybe_update(left_expr, variables, new_l)  # ★ 변경
            return

        # ───────── CASE 2-B : literal vs Interval ──────────────
        if VariableEnv.is_interval(right_val) and not VariableEnv.is_interval(left_val):
            coerced_l = self._coerce_literal_to_interval(left_val, right_val.type_length)
            if coerced_l is None:          # 상대를 구간으로 표현 못 함 → 정제 없음(건전)
                return
            _dummy, new_r = self.refine_intervals_for_comparison(coerced_l, right_val, actual_op)
            _maybe_update(right_expr, variables, new_r)  # ★ 변경
            return

        # ───────── CASE 3 : BoolInterval 비교 ──────────────────
        if isinstance(left_val, BoolInterval) or isinstance(right_val, BoolInterval):
            self._update_bool_comparison(
                variables,
                left_expr, right_expr,
                left_val, right_val,
                actual_op)
            return

        # ───────── CASE 4 : address Interval 비교 ───────────────
        if (VariableEnv.is_interval(left_val) and left_val.type_length == 160) or \
                (VariableEnv.is_interval(right_val) and right_val.type_length == 160):

            if not VariableEnv.is_interval(left_val):
                left_val = self._coerce_literal_to_interval(left_val, 160)
            if not VariableEnv.is_interval(right_val):
                right_val = self._coerce_literal_to_interval(right_val, 160)
            if left_val is None or right_val is None:   # 구간으로 표현 못 함 → 정제 없음(건전)
                return

            new_l, new_r = self.refine_intervals_for_comparison(left_val, right_val, actual_op)
            _maybe_update(left_expr, variables, new_l)  # ★ 변경
            _maybe_update(right_expr, variables, new_r)  # ★ 변경
            return

        # ───────── CASE 5 : AddressSet 비교 (==/!=) ─────────────
        # VariableEnv.is_interval()이 AddressSet을 명시적으로 제외해서 CASE 1-4
        # 어디에도 안 걸림 — address 타입끼리의 ==/!= 비교는 이 narrowing이
        # 추가되기 전까지 코드베이스 전체에서 단 한 번도 분기 narrowing이
        # 안 됐음(web3bugs_29_H_11에서 발견).
        if isinstance(left_val, AddressSet) or isinstance(right_val, AddressSet):
            if not isinstance(left_val, AddressSet):
                left_val = self._coerce_literal_to_addressset(left_val)
            if not isinstance(right_val, AddressSet):
                right_val = self._coerce_literal_to_addressset(right_val)
            if left_val is None or right_val is None:
                return  # 리터럴을 address로 못 바꾸면 narrowing 포기(안전)

            if actual_op == '==':
                new_l = new_r = left_val.meet(right_val)
                _maybe_update(left_expr, variables, new_l)
                _maybe_update(right_expr, variables, new_r)
            elif actual_op == '!=':
                new_l, new_r = left_val, right_val
                if left_val.is_singleton() and right_val.is_singleton() and \
                        left_val.get_singleton_id() == right_val.get_singleton_id():
                    # 동일 singleton이면 모순 → bottom
                    new_l = new_r = AddressSet.bot()
                else:
                    if right_val.is_singleton() and not left_val.is_top:
                        remaining = left_val.ids - right_val.ids
                        new_l = AddressSet(remaining)
                    if left_val.is_singleton() and not right_val.is_top:
                        remaining = right_val.ids - left_val.ids
                        new_r = AddressSet(remaining)
                _maybe_update(left_expr, variables, new_l)
                _maybe_update(right_expr, variables, new_r)
            return

    def _coerce_literal_to_addressset(self, lit) -> "AddressSet | None":
        """address 비교의 반대편이 AddressSet이 아닐 때(정수 0/hex 문자열 등)
        가능하면 AddressSet으로 변환. 변환 불가면 None(narrowing 포기 신호)."""
        if isinstance(lit, AddressSet):
            return lit
        if isinstance(lit, (int, float)):
            return AddressSet(ids={int(lit)})
        if isinstance(lit, str):
            txt = lit
            if txt.lower().startswith("0x") and all(c in "0123456789abcdef" for c in txt[2:].lower()):
                return AddressSet(ids={int(txt, 16)})
            try:
                return AddressSet(ids={int(txt, 0)})
            except ValueError:
                return None
        if VariableEnv.is_interval(lit) and lit.min_value == lit.max_value:
            return AddressSet(ids={lit.min_value})
        return None

    def refine_intervals_for_comparison(
            self,
            a_iv,  # IntegerInterval | UnsignedIntegerInterval
            b_iv,
            op: str):

        # ── bottom short-cut ─────────────────────────
        if a_iv.is_bottom() or b_iv.is_bottom():
            return (a_iv.bottom(a_iv.type_length),
                    b_iv.bottom(b_iv.type_length))

        A, B = a_iv.copy(), b_iv.copy()

        # 내부 헬퍼 -----------------------------------
        def clamp_max(iv, new_max):
            if iv.is_bottom():
                return iv
            if new_max < iv.min_value:
                return iv.bottom(iv.type_length)
            iv.max_value = min(iv.max_value, new_max)
            return iv

        def clamp_min(iv, new_min):
            if iv.is_bottom():
                return iv
            if new_min > iv.max_value:
                return iv.bottom(iv.type_length)
            iv.min_value = max(iv.min_value, new_min)
            return iv

        def both_bottom_if_either(A, B):
            """한쪽이 ⊥ 면 조건 자체가 성립 불가 → 양쪽 모두 ⊥.

            `_branch_feasible` 은 env 에 ⊥ 가 하나라도 있으면 infeasible 로 보므로
            feasibility 판정은 어느 쪽이든 같다. 다만 화면에 ⊥ 로 보이는 변수 집합이
            달라지는데, 도달 불가 env 라면 "둘 다 불가" 가 정직하다 (`_set_bottom_env`
            가 env 전체를 ⊥ 로 만드는 것과 같은 관점).
            """
            if A.is_bottom() or B.is_bottom():
                return A.bottom(A.type_length), B.bottom(B.type_length)
            return A, B

        # --------------------------------------------

        # <  ------------------------------------------------
        # ★ a < b 가 성립 **가능한** 값만 남긴다:
        #     a ≤ B.max − 1   (b 가 될 수 있는 최댓값보다 작아야 한다)
        #     b ≥ A.min + 1   (a 가 될 수 있는 최솟값보다 커야 한다)
        #   ⚠ 양쪽 모두 **원본**(`a_iv`/`b_iv`) 상대편으로 제약한다. 한쪽을 정제한
        #     결과를 다시 반대쪽 제약에 쓰면 중복 적용돼 과도하게 좁아진다.
        #   ⚠ 예전 코드는 `B.min − 1` / `A.max + 1` 이라 **반대쪽 경계**를 썼다.
        #     우변이 리터럴이면 `B.min == B.max` 라 우연히 맞았지만, 비-싱글톤이면
        #     feasible 상태를 버린다 — `[0,0] < [0,10]` 이 ⊥ 가 되어 `while (i < n)`
        #     의 본문이 영원히 진입되지 않았다(F43).
        if op == '<':
            if b_iv.max_value != INFINITY:
                A = clamp_max(A, b_iv.max_value - 1)
            if a_iv.min_value != NEG_INFINITY:
                B = clamp_min(B, a_iv.min_value + 1)
            return both_bottom_if_either(A, B)

        # >  ------------------------------------------------
        #   a ≥ B.min + 1,  b ≤ A.max − 1   (위와 대칭)
        if op == '>':
            if b_iv.min_value != NEG_INFINITY:
                A = clamp_min(A, b_iv.min_value + 1)
            if a_iv.max_value != INFINITY:
                B = clamp_max(B, a_iv.max_value - 1)
            return both_bottom_if_either(A, B)

        # <=  -----------------------------------------------
        if op == '<=':
            lt_a, lt_b = self.refine_intervals_for_comparison(a_iv, b_iv, '<')
            eq_a, eq_b = self.refine_intervals_for_comparison(a_iv, b_iv, '==')
            return lt_a.join(eq_a), lt_b.join(eq_b)

        # >=  -----------------------------------------------
        if op == '>=':
            gt_a, gt_b = self.refine_intervals_for_comparison(a_iv, b_iv, '>')
            eq_a, eq_b = self.refine_intervals_for_comparison(a_iv, b_iv, '==')
            return gt_a.join(eq_a), gt_b.join(eq_b)

        # ==  -----------------------------------------------
        if op == '==':
            meet = A.meet(B)
            return meet, meet

        # !=  -----------------------------------------------
        if op == '!=':
            if (A.min_value == A.max_value ==
                    B.min_value == B.max_value):
                # 동일 싱글톤이면 모순
                return (A.bottom(A.type_length),
                        B.bottom(B.type_length))

            # 상대방이 싱글톤일 때 경계값 제외 (sound refinement)
            if B.min_value == B.max_value:
                b_val = B.min_value
                if A.min_value == b_val:
                    A.min_value = b_val + 1
                elif A.max_value == b_val:
                    A.max_value = b_val - 1
            if A.min_value == A.max_value:
                a_val = A.min_value
                if B.min_value == a_val:
                    B.min_value = a_val + 1
                elif B.max_value == a_val:
                    B.max_value = a_val - 1

            return A, B

        # 알 수 없는 op → 변경 없음
        return A, B

    def _coerce_literal_to_interval(self, lit, default_bits=256):
        def _hex_addr_to_addressset(hex_txt: str):
            """'0x…' 문자열을 AddressSet({val}) 로 변환"""
            from Domain.AddressSet import AddressSet
            val = int(hex_txt, 16)
            return AddressSet(ids={val})

        def _is_address_literal(txt: str) -> bool:
            return txt.lower().startswith("0x") and all(c in "0123456789abcdef" for c in txt[2:])

        if isinstance(lit, (int, float)):
            v = int(lit)
            return IntegerInterval(v, v, default_bits) if v < 0 \
                else UnsignedIntegerInterval(v, v, default_bits)
        if isinstance(lit, str):
            if _is_address_literal(lit):
                return _hex_addr_to_addressset(lit)  # ◀︎ AddressSet
            try:
                v = int(lit, 0)
                return IntegerInterval(v, v, default_bits) if v < 0 \
                    else UnsignedIntegerInterval(v, v, default_bits)
            except ValueError:
                return None
        # ★ (Phase 2, D2) 구간으로 바꿀 수 없는 비교 상대(심볼릭 값 등) → None = "정제하지 않음".
        #   예전엔 ⊥ 를 돌려 비교 결과를 '불가능' 으로 정제해 분기 전체를 도달 불가로 만들었다(불건전).
        return None

    def _update_bool_comparison(
            self,
            variables: dict[str, Variables],
            left_expr: Expression,
            right_expr: Expression,
            left_val,  # evaluate_expression 결과
            right_val,  # 〃
            op: str  # '==', '!=' ...
    ):
        """
        bool - bool 비교식을 통해 피연산자의 BoolInterval 을 좁힌다.
          * op == '==' : 두 피연산자가 동일 값이어야 함 → 교집합(meet)
          * op == '!=' : 두 피연산자가 상이해야 함
                         ─ 한쪽이 단정(True/False) ⇒ 다른 쪽은 반대값으로
                         ─ 양쪽 모두 Top([0,1]) ⇒ 정보 부족 → 건너뜀
        """

        def extract_identifier_if_possible(expr: Expression) -> str | None:
            """
            Expression 이 단순 '경로(path)' 형태인지 판별해
              -  foo                      → "foo"
              -  foo.bar                 → "foo.bar"
              -  foo[3]                  → "foo[3]"
              -  foo.bar[2].baz          → "foo.bar[2].baz"
            처럼 **오직 식별자 / 멤버 / 정수-리터럴 인덱스**만으로 이루어져 있을 때
            그 전체 경로 문자열을 돌려준다.

            산술, 함수 호출, 심볼릭 인덱스 등이 섞이면 None 반환.
            """

            # ───── 1. 멤버/인덱스가 전혀 없는 루트 ─────
            if expr.base is None:
                # 순수 식별자인지 확인
                if expr.context == "IdentifierExpContext":
                    return expr.identifier
                return None  # literal, 연산 등 → 식별자 아님

            # ───── 2. 먼저 base-경로를 재귀적으로 확보 ──────
            base_path = extract_identifier_if_possible(expr.base)
            if base_path is None:
                return None  # base 가 이미 복합 → 포기

            # ───── 3.A  멤버 접근 foo.bar ──────────────
            if expr.member is not None:
                return f"{base_path}.{expr.member}"

            # ───── 3.B  인덱스 접근 foo[3] ─────────────
            if expr.index is not None:
                # 인덱스가 "정수 리터럴"인지(실행 시 결정되면 안 됨)
                if expr.index.context == "LiteralExpContext" and str(expr.index.literal).lstrip("-").isdigit():
                    return f"{base_path}[{int(expr.index.literal, 0)}]"
                return None  # 심볼릭 인덱스면 변수 하나로 볼 수 없음

            # 그 밖의 케이스(예: 슬라이스, 함수 호출 등)
            return None


        # ───────────────── 0. BoolInterval 변환 ─────────────────
        def _as_bool_iv(val):
            # 이미 BoolInterval
            if isinstance(val, BoolInterval):
                return val
            # 정수 Interval [0,0]/[1,1] => BoolInterval
            if VariableEnv.is_interval(val):
                return VariableEnv.convert_int_to_bool_interval(val)
            return None  # 그밖엔 Bool 로 간주하지 않음

        l_iv = _as_bool_iv(left_val)
        r_iv = _as_bool_iv(right_val)
        if l_iv is None or r_iv is None:
            # 둘 다 Bool 로 환원 안 되면 관여하지 않는다
            return

        # ※ left_expr / right_expr 가 identifier 인지 → 이름 얻기
        l_name = extract_identifier_if_possible(left_expr)
        r_name = extract_identifier_if_possible(right_expr)

        # helper ― 변수 env 에 실제 적용
        def _replace(name, new_iv: BoolInterval):
            if name in variables and isinstance(variables[name].value, BoolInterval):
                variables[name].value = variables[name].value.meet(new_iv)

        # ───────────────── 1. op == '==' ───────────────────────
        if op == "==":
            meet = l_iv.meet(r_iv)  # 교집합
            _replace(l_name, meet)
            _replace(r_name, meet)
            return

        # ───────────────── 2. op == '!=' ───────────────────────
        if op == "!=":
            # 한쪽이 [1,1]/[0,0] 처럼 단정이라면 → 다른 쪽을 반대 값으로 강제
            def _is_const(iv: BoolInterval) -> bool:
                return iv.min_value == iv.max_value

            if _is_const(l_iv) and _is_const(r_iv):
                # 둘 다 단정인데 현재 env 가 모순이면 meet 하면 bottom,
                # 분석기에서는 "실행 불가" 분기로 처리하거나 그대로 둠
                if l_iv.equals(r_iv):
                    # a != a 는 거짓 ⇒ 해당 분기는 불가능 → 아무 것도 하지 않고 탈출
                    return
                # a(0) != b(1) 처럼 이미 참 ⇒ 정보 없음
                return

            if _is_const(l_iv):
                opposite = BoolInterval(0, 0) if l_iv.min_value == 1 else BoolInterval(1, 1)
                _replace(r_name, opposite)
                return

            if _is_const(r_iv):
                opposite = BoolInterval(0, 0) if r_iv.min_value == 1 else BoolInterval(1, 1)
                _replace(l_name, opposite)
                return

            # 양쪽 다 [0,1] → 정보 없음
            return

        # ───────────────── 3. <,>,<=,>= (불리언엔 의미 X) ──────
        #   원하는 정책에 따라 symbolic 처리하거나 경고만 남김
        #   여기선 그냥 통과
        return

    def _is_read_only_expr(self, e: Expression, variables: dict) -> bool:
        ctx = getattr(e, "context", "")

        # ── (a) 숫자‧문자‧bool 리터럴
        if ctx == "LiteralExpContext":
            return True

        # ── (b) 배열·주소·함수의 read-only 멤버
        if ctx == "MemberAccessContext":
            if e.member in READONLY_MEMBERS:
                return True
            # type(uint256).max 등
            if getattr(e.base, "context", "") == "MetaTypeContext":
                return True

        # ── (c) 전역(block/msg/tx) 멤버 → 쓰기 불가
        if ctx == "MemberAccessContext" and isinstance(e.base, Expression):
            if getattr(e.base, "identifier", "") in READONLY_GLOBAL_BASES:
                return True

        # ── (d) constant / immutable 변수
        if (ctx == "IdentifierExpContext"
                and e.identifier in variables
                and getattr(variables[e.identifier], "isConstant", False)):
            return True

        return False

    def negate_operator(self, op: str) -> str:
        neg_map = {
            '==': '!=',
            '!=': '==',
            '<': '>=',
            '>': '<=',
            '<=': '>',
            '>=': '<'
        }
        return neg_map.get(op, op)

    def _clone_env(self, env: dict[str, Variables]) -> dict[str, Variables]:
        return VariableEnv.copy_variables(env)

    def _join_two_envs(self, e1: dict[str, Variables], e2: dict[str, Variables]) -> dict[str, Variables]:
        out = self._clone_env(e1)
        for k, v2 in e2.items():
            if k in out:
                # Variables.value 에 lattice join 필요
                # None 값 체크 추가
                if out[k].value is None or v2.value is None:
                    # 한쪽이 None이면 다른 쪽 값 사용, 둘 다 None이면 None 유지
                    if out[k].value is None and v2.value is not None:
                        out[k].value = v2.value
                    # out[k].value가 None이 아니고 v2.value가 None이면 현재 값 유지
                else:
                    out[k].value = out[k].value.join(v2.value)
            else:
                out[k] = VariableEnv.copy_single_variable(v2)
        return out

    def _apply_env_inplace(self, dst: dict[str, Variables], src: dict[str, Variables]) -> None:
        # dst를 src로 대체(메타데이터를 유지하려면 필요시 필드 단위 복사)
        # 여기서는 value만 바꾸는 것으로 충분할 때가 많지만, 안전하게 통째로 교체
        dst.clear()
        for k, v in src.items():
            dst[k] = VariableEnv.copy_single_variable(v)

    def _as_bool_interval(self, expr: Expression, variables: dict[str, Variables]) -> BoolInterval | None:
        val = self.ev.evaluate_expression(expr, variables, None, None)
        if isinstance(val, BoolInterval):
            return val
        if VariableEnv.is_interval(val):
            return VariableEnv.convert_int_to_bool_interval(val)
        return None  # bool로 해석 불가

    def _is_true(self, bi: BoolInterval | None) -> bool:
        return isinstance(bi, BoolInterval) and bi.min_value == bi.max_value == 1

    def _is_false(self, bi: BoolInterval | None) -> bool:
        return isinstance(bi, BoolInterval) and bi.min_value == bi.max_value == 0
