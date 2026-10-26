# engine.py (SolDebug 통합 버전)

from __future__ import annotations
from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.Variable import Variables, MappingVariable, ArrayVariable, StructVariable, EnumVariable
from Domain.IR import Expression
from Domain.Interval import UnsignedIntegerInterval, IntegerInterval, BoolInterval
from Domain.AddressSet import AddressSet
from Domain.BytesSet import BytesSet
from Domain.FunctionRef import FunctionRef
from Domain.Annotation import CompoundDuringAnnotation, CompoundPostAnnotation
from Utils.CFG import CFGNode, FunctionCFG
from Utils.Helper import VariableEnv
from Interpreter.Semantics.Evaluation import coerce_to_declared
from collections import deque
from typing import cast

class Engine:
    def __init__(self, an: "ContractAnalyzer"):
        self.an = an
        # ── 기록 제어 플래그(기존 Runtime 것이 여기로 이동) ───────────────
        self._record_enabled: bool = True
        self._suppress_stmt_records: bool = False  # 고정점 중 문장 기록 억제
        self._in_widening_mode: bool = False  # widening 중에는 side-effect 억제
        self._unchecked: bool = False  # (Phase 2) 지금 해석 중인 문장이 `unchecked { }` 안인가 → unsigned 뺄셈 wrap

    # ── lazy properties (그대로 유지) ──────────────────────────────────
    @property
    def ref(self):
        return self.an.refiner
    @property
    def rec(self):
        return self.an.recorder
    @property
    def eval(self):
        return self.an.evaluator
    @property
    def up(self):
        return self.an.updater


    # =================================================================
    #  (통합) 문장 해석기 – 기존 Runtime.* 가 여기로 이동
    # =================================================================
    def update_statement_with_variables(self, stmt, current_variables, ret_acc=None, node=None):
        # ★ N4/S1. "지금 해석 중인 노드" = callee 로 하강할 때의 **호출 지점**.
        #   여기에 두는 이유: `_run_worklist` 는 평문 노드에 `transfer_function` 을 쓰지 않고
        #   이 함수를 직접 부른다. 두 경로(`transfer_function` / `_run_worklist`)가 모두
        #   `node=` 를 넘기므로 이 한 곳이 유일한 공통 지점이다.
        #   callee 해석 중에는 callee 자신의 노드로 덮이지만, `interpret_function_cfg` 가
        #   **진입 시점에 로컬로 받아두므로** 중첩 호출에서도 각 프레임이 자기 호출 지점을 쥔다.
        if node is not None:
            self._call_site_node = node
        # (Phase 2) unchecked 문맥은 문장 단위로 켜고, callee 해석이 끝나면 되돌린다.
        _old_unchecked = self._unchecked
        self._unchecked = bool(getattr(node, "unchecked_block", False)) if node is not None else _old_unchecked
        try:
            return self._dispatch_statement(stmt, current_variables, ret_acc, node)
        finally:
            self._unchecked = _old_unchecked

    def _dispatch_statement(self, stmt, current_variables, ret_acc=None, node=None):
        typ = stmt.statement_type
        if   typ == 'variableDeclaration':
            return self._interpret_var_decl(stmt, current_variables)
        elif typ == 'assignment':
            return self._interpret_assignment(stmt, current_variables, node=node)
        elif typ == 'unary':
            return self._interpret_unary(stmt, current_variables)
        elif typ == 'functionCall':
            return self._interpret_func_call(stmt, current_variables, node=node)
        elif typ == 'return':
            return self._interpret_return(stmt, current_variables, ret_acc)
        elif typ == 'revert':
            return self._interpret_revert(stmt, current_variables)
        elif typ == 'break':
            return self._interpret_break(stmt, current_variables)
        elif typ == 'continue':
            return self._interpret_continue(stmt, current_variables)
        elif typ == 'assembly':
            return self._interpret_assembly(stmt, current_variables)
        elif typ == 'emit':
            return self._interpret_emit(stmt, current_variables)
        elif typ == 'expression':
            if not self._is_bottom_env(current_variables):
                self.eval.evaluate_expression(stmt.expression, current_variables, None, None)
            return current_variables
        else:
            raise ValueError(f"Statement '{typ}' is not implemented.")

    def _interpret_emit(self, stmt, variables):
        """emit 문(Phase 2): 인자 식만 평가한다(호출 부수효과). 이벤트 로그 자체는 추상 상태가 아니다."""
        if self._is_bottom_env(variables):
            return variables
        for arg in stmt.arguments:
            self.eval.evaluate_expression(arg, variables, None, None)
        return variables

    def _interpret_var_decl(self, stmt, variables):
        var_type = stmt.type_obj
        var_name = stmt.var_name
        init_expr = stmt.init_expr

        # (Phase 3b, D8) 바깥 이름을 가리는 선언 — 바깥 binding 을 보관(블록 종료 시 `_restrict_scope` 가 복원)
        if getattr(stmt, "shadow_unsupported", False):
            raise ValueError(f"line {stmt.src_line}: declaration of '{var_name}' shadows an outer name inside a "
                             f"block without a scope-exit node (bare/unchecked/do/try block) — not supported")
        if getattr(stmt, "shadows", False) and var_name in variables:
            variables[f"{var_name}{self.SHADOW_SEP}shadow@{id(stmt)}"] = variables[var_name]
        # (2026-10-23) 새 바인딩 — 이 이름을 읽던 선택자 셀(`m[i]`)은 이제 다른 위치를 가리킨다
        self.an.storage.on_assign(variables, var_name)

        # ──────────────────────────────────────────────────────────────
        # 1. 선언 = 매 실행마다 새 변수
        #   ★ (Phase 3a, D2) 예전엔 "이미 있으면 건너뜀" 이라, 루프 안 선언이 다시 실행될 때 이전 반복 값이
        #     남았다(back-edge 로 들어온 같은 이름). 선언은 실행될 때마다 새 바인딩을 만든다.
        # ──────────────────────────────────────────────────────────────
        if var_type.typeCategory in ("struct", "array", "mapping"):
            # ★ F29. `ContractAnalyzer._create_variable_object` 와 **같은 것**을 만들어야 한다.
            #   여기엔 그 함수의 **인라인 사본**이 있었는데 struct/enum 정의
            #   (`struct_defs`/`enum_defs`)를 붙이지 않아 **멤버가 빈** 객체가 나왔다.
            #
            #   왜 이 경로가 타지는가: `process_variable_declaration` 은 선언 노드를 만든
            #   직후 `reinterpret_from(fcfg, stmt_blk)` 를 부른다. 재해석은 in-env 를
            #   predecessor 에서 다시 계산하므로(선언 전이니 그 변수가 없는 게 정상)
            #   선언문이 **재실행**되고, 그때 이 코드가 ContractAnalyzer 가 제대로 만든
            #   객체를 덮어쓴다. 실측 객체 수명(`AddressProviderMeta.fromUInt`):
            #     [1] CA 가 members=['freezable','frozen'] 로 생성(id=84016)
            #     [2] 블록 env 에 심음 + 선언 statement 도 CFG 에 등록
            #     [3] reinterpret_from 진입 — pred 에 meta 없음(정상)
            #     [4] 여기서 members=[] 로 재생성(id=75280)  ← 덮어씀
            #     [5] `meta.freezable = ...` → Member 'freezable' not in struct 'meta'
            #   ★ `meta` 가 사라지는 게 아니라 `meta.members` 가 빈다.
            #
            #   "CFG 에 등록된 expression 을 보면 되지 않나" = **이미 그렇게 하고 있다**
            #   ([4]가 `stmt.type_obj` 를 읽는다). 문제는 "보지 않음" 이 아니라
            #   **"보고 만드는 방법이 틀림"** 이었다. 그래서 사본을 지우고 팩토리를 부른다.
            #   배열·매핑도 `struct_defs`/`enum_defs` 를 안 넘겨 같은 결함이 있었다
            #   (구조체 배열 · 값이 구조체인 매핑).
            #
            #   ⚠ 대안(선언 statement 에 프로토타입을 저장해 clone)은 멱등성은 지키지만
            #     **증분 편집에 불리**하다 — struct 정의가 바뀌면 프로토타입이 낡는다.
            #     `type_obj` 재구성은 **현재 정의**를 집어온다.
            #   ⚠ `_create_variable_object` 의 3번째 인자 `ccf` 는 현재 미사용이고
            #     내부적으로 `get_full_struct_enum_defs()` → `current_target_contract` 를
            #     본다(백로그 ⑪). 기존 호출부와 동일한 한계이므로 회귀는 아니다.
            variables[var_name] = self.an._create_variable_object(var_type, var_name, None)
        else:
            # Elementary / interface 타입 처리
            vobj = Variables(identifier=var_name, scope="local")
            vobj.typeInfo = var_type
            if var_type.typeCategory == "interface":
                ifc_name = getattr(var_type, 'interfaceName', None)
                vobj.value = AddressSet.top()
                if ifc_name:
                    vobj.value._cast_interface = ifc_name
            elif var_type.typeCategory == "function":     # (2026-10-23) 함수 포인터 = ⊤ (Domain/FunctionRef.py)
                vobj.value = FunctionRef.top()
            else:
                et = var_type.elementaryTypeName
                if et and et.startswith("uint"):
                    bits = var_type.intTypeLength or 256
                    vobj.value = UnsignedIntegerInterval.top(bits)
                elif et and et.startswith("int"):
                    bits = var_type.intTypeLength or 256
                    vobj.value = IntegerInterval.top(bits)
                elif et == "bool":
                    vobj.value = BoolInterval.top()
                elif et == "address":
                    vobj.value = AddressSet.top()
                else:
                    vobj.value = f"symbol_{var_name}"
            variables[var_name] = vobj

        # 1-b. 초기화식이 없으면 Solidity 기본값(0 / false / address(0) / 빈 동적 배열 / 0 으로 채운 struct·정적 배열).
        #   ★ (Phase 3a, D2) 예전엔 ⊤ 로 두었다(건전하지만 부정확). 파라미터·상태변수(⊤)와 달리 지역변수는 값이 정해져 있다.
        if init_expr is None:
            var = variables[var_name]
            if isinstance(var, (ArrayVariable, StructVariable)):
                self._delete_composite(var)
            elif isinstance(var, EnumVariable):
                var.valueIndex = 0
                if isinstance(getattr(var, "members", None), dict) and var.members:
                    var.value = next(iter(var.members))
            elif not isinstance(var, MappingVariable) and not isinstance(getattr(var, "value", None), (str, FunctionRef)):
                zero = self._zero_of(var)
                if isinstance(zero, AddressSet) and isinstance(getattr(var, "value", None), AddressSet):
                    zero._cast_interface = getattr(var.value, "_cast_interface", None)
                var.value = zero

        # ──────────────────────────────────────────────────────────────
        # 2. 초기화식이 있으면 항상 평가 (변수가 이미 있어도)
        # ──────────────────────────────────────────────────────────────
        if init_expr is not None:
            if var_type.typeCategory in ("struct", "array", "mapping"):
                # 구조체/배열/매핑: 초기화식 평가 결과로 변수 대체
                eval_result = self.eval.evaluate_expression(init_expr, variables, None, None)
                if isinstance(eval_result, (StructVariable, ArrayVariable, MappingVariable)):
                    eval_result.identifier = var_name
                    variables[var_name] = eval_result
                    vobj = eval_result
                else:
                    vobj = variables[var_name]
            else:
                # Elementary 타입: 값 평가 후 업데이트
                vobj = variables[var_name]
                eval_result = self.eval.evaluate_expression(init_expr, variables, None, None)

                # 평가 결과가 Variables 객체면 그 value를 추출
                if isinstance(eval_result, Variables) and not isinstance(eval_result, (ArrayVariable, StructVariable, MappingVariable)):
                    eval_result = getattr(eval_result, 'value', eval_result)
                # (Phase 4b) 선언 타입으로 암묵 변환 — `coerce_to_declared` 주석 참고
                vobj.value = coerce_to_declared(eval_result, var_type, f"initializer of '{var_name}'")

                if self._record_enabled and not self._suppress_stmt_records:
                    self.rec.record_variable_declaration(
                        line_no=stmt.src_line,
                        var_name=var_name,
                        var_obj=vobj
                    )
                    # ★ assign_env에 선언 시점 값 기록 (During assignCurrent용)
                    fcfg = self.an.current_target_function_cfg
                    if fcfg is not None:
                        fcfg.assign_env[var_name] = VariableEnv.copy_single_variable(vobj)

        return variables

    def _interpret_assignment(self, stmt, variables, node=None):
        lexp, rexpr, op = stmt.left, stmt.right, stmt.operator
        r_val = (self.eval.evaluate_expression(rexpr, variables, None, None)
                 if isinstance(rexpr, Expression) else rexpr)

        should_record = self._record_enabled and not self._suppress_stmt_records
        line_num = getattr(stmt, 'src_line', None)

        # ★ before_envs: 대입 직전 스냅샷 (동적 재해석 시 During beforeAfter용)
        if should_record and line_num is not None and node is not None:
            node.before_envs[line_num] = VariableEnv.copy_variables(variables)

        # stmt.src_line을 line_no로 전달
        self.up.update_left_var(lexp, r_val, op, variables, None, None, should_record, line_num)
        return variables

    def _interpret_unary(self, stmt, variables):
        op = stmt.operator
        operand = stmt.operand

        if op == '++':
            self.up.update_left_var(operand, 1, '+=', variables, None, None, False)
            return variables
        if op == '--':
            self.up.update_left_var(operand, 1, '-=', variables, None, None, False)
            return variables
        if op == 'delete':
            if self._is_bottom_env(variables):
                return variables
            target = self.up.resolve_lhs_expr(operand, variables)
            if isinstance(target, (ArrayVariable, StructVariable, MappingVariable)):
                # ★ (Phase 3a) 예전엔 구성 시점(`ContractAnalyzer.handle_delete._wipe`)에만 타입별 0 초기화가 있었고,
                #   해석기는 피연산자에 정수 0 을 대입했다(복합 타입이면 틀린 결과). 의미를 해석기로 옮긴다.
                self._delete_composite(target)
                self.up.flush_resolved_write()      # (2026-10-23) `delete m[a]` 도 쓰기 — alias 전파
            else:
                self.up.update_left_var(operand, self._zero_of(target), '=', variables, None, None,
                                        (self._record_enabled and not self._suppress_stmt_records))
            return variables
        if op in ('unary_prefix', 'unary_suffix'):
            # Phase 2 이전에 빌드된 pkl 의 IR — ++/-- 구분이 없어 의미를 복원할 수 없다.
            raise ValueError(f"stale IR: unary operator token '{op}' (pkl built before 2026-10-21 Phase 2) "
                             f"— rebuild the dependency pkl")
        # ★ (Phase 2) 모르는 단항 연산자를 조용히 무시하지 않는다(D4).
        raise ValueError(f"unary statement operator '{op}' is not supported")

    @staticmethod
    def _zero_of(var):
        """`delete x` 의 결과 = 타입의 0 값 (uint/int/bool/address/enum/bytesN)."""
        from Domain.BytesSet import BytesSet
        if isinstance(var, EnumVariable):
            return 0
        val = getattr(var, "value", None)
        ti = getattr(var, "typeInfo", None)
        et = getattr(ti, "elementaryTypeName", "") or ""
        bits = getattr(ti, "intTypeLength", None) or 256
        if isinstance(val, UnsignedIntegerInterval) or et.startswith("uint"):
            return UnsignedIntegerInterval(0, 0, bits)
        if isinstance(val, IntegerInterval) or et.startswith("int"):
            return IntegerInterval(0, 0, bits)
        if isinstance(val, BoolInterval) or et == "bool":
            return BoolInterval(0, 0)
        if isinstance(val, AddressSet) or et == "address":
            return AddressSet(ids={0})
        if isinstance(val, BytesSet):
            return BytesSet(values={0}, byte_size=val.byte_size)
        raise ValueError(f"delete: zero value of type '{et or type(val).__name__}' is not modeled")

    def _delete_composite(self, obj):
        """`delete` on struct/array (Solidity): 동적 배열 → 길이 0, 정적 배열·struct → 원소/멤버 재귀 0, mapping 은 영향 없음."""
        if isinstance(obj, MappingVariable):
            return
        if isinstance(obj, ArrayVariable):
            # (2026-10-23) 선택자 셀은 바인딩만 버린다(원소가 다시 정해졌으므로 원소들에서 다시 만들어진다)
            obj.sym_cells.clear()
            obj.sym.clear()
            if obj.typeInfo.isDynamicArray:
                obj.elements.clear()
                obj.length = UnsignedIntegerInterval(0, 0, 256)
                obj.summary = None
            else:
                # (2026-10-23, 희소 배열) 모든 원소 = 0 → 구체 셀을 버리고 D(summary) = 0 셀. 초기화식 없는 지역 정적 배열도
                #   이 경로로 0 이 된다(예전엔 원소 list 가 비어 있어 아무것도 안 했고, 이후 접근이 범위 오류였다).
                z = obj.fresh_default()
                self._delete_composite(z) if isinstance(z, (ArrayVariable, StructVariable, MappingVariable)) \
                    else setattr(z, "value", self._zero_of(z))
                obj.elements = {}
                obj.summary = z
            return
        if isinstance(obj, StructVariable):
            for m in obj.members.values():
                if isinstance(m, (ArrayVariable, StructVariable, MappingVariable)):
                    self._delete_composite(m)
                elif isinstance(m, EnumVariable):
                    m.valueIndex = 0
                    m.value = next(iter(m.members), None) if isinstance(getattr(m, "members", None), dict) else 0
                else:
                    m.value = self._zero_of(m)

    def _interpret_func_call(self, stmt, variables, node=None):
        # ★ infeasible branch(BOTTOM env)에서는 함수 호출 스킵
        #   → caller_env 반영이 BOTTOM 값을 TOP으로 오염시키는 것을 방지
        if self._is_bottom_env(variables):
            return variables
        # (2026-10-23, M1) 호출 직전 상태 — 인자 intent(`@During transfer.arg[0] …`)는 인자가 평가되는 이 상태로 검증한다
        #   (예전엔 호출 **후** 상태로 인자식을 다시 평가했다). 대입문의 before_envs 와 같은 장치.
        line_num = getattr(stmt, 'src_line', None)
        if self._record_enabled and not self._suppress_stmt_records and line_num is not None and node is not None:
            node.before_envs[line_num] = VariableEnv.copy_variables(variables)
        fe = stmt.function_expr
        if str(getattr(fe, "context", "")).startswith("Yul"):   # (2026-10-23) Yul 문장(builtin 호출·미지원 구문)
            self.eval.evaluate_expression(fe, variables, None, None)
            return variables
        self.eval.evaluate_function_call_context(fe, variables, None, None)
        return variables

    def _interpret_return(self, stmt, variables, ret_acc=None):
        rexpr = stmt.return_expr
        if rexpr is None:
            # bare return; (값 없는 리턴)
            r_val = None
        else:
            r_val = self.eval.evaluate_expression(rexpr, variables, None, None)
            r_val = self._coerce_return(r_val)
        if self._record_enabled and not self._suppress_stmt_records:
            self.rec.record_return(
                line_no=stmt.src_line,
                return_expr=rexpr,
                return_val=r_val,
                fn_cfg=self.an.current_target_function_cfg
            )
        exit_node = self.an.current_target_function_cfg.get_exit_node()
        exit_node.return_vals[stmt.src_line] = r_val
        if ret_acc is not None:
            ret_acc.append(r_val); ret_acc.append("__STOP__")
        return variables

    def _coerce_return(self, r_val):
        """(Phase 4b) 반환값 → 선언 반환 타입 암묵 변환. 다중 반환은 리스트(슬롯 위치 보존)."""
        fc = self.an.current_target_function_cfg
        rtypes = list(getattr(fc, "return_types", None) or [])
        fname = getattr(fc, "function_name", "?")
        if isinstance(r_val, list):
            if len(r_val) != len(rtypes):
                raise ValueError(f"return: {len(r_val)} values for {len(rtypes)} return types")
            return [coerce_to_declared(v, t, f"return value #{i} of '{fname}'") for i, (v, t) in enumerate(zip(r_val, rtypes))]
        if isinstance(r_val, Variables) and not isinstance(r_val, (ArrayVariable, StructVariable, MappingVariable)):
            r_val = r_val.value
        return coerce_to_declared(r_val, rtypes[0], f"return value of '{fname}'") if len(rtypes) == 1 else r_val

    def _interpret_revert(self, stmt, variables):
        return variables
    def _interpret_break(self, stmt, variables):
        return variables
    def _interpret_continue(self, stmt, variables):
        return variables

    def _interpret_assembly(self, stmt, variables):
        """
        Assembly 블록 내 변수 할당 처리:
        - 해당 변수를 Top으로 설정 (assembly에서 정확한 값 추적 불가)
        """
        var_name = getattr(stmt, 'variable_name', None)
        if var_name and var_name in variables:
            var_obj = variables[var_name]
            # 변수 타입에 따라 적절한 Top 값 설정
            if hasattr(var_obj, 'typeInfo') and var_obj.typeInfo:
                et = getattr(var_obj.typeInfo, 'elementaryTypeName', None)
                if et:
                    if et.startswith("uint"):
                        bits = var_obj.typeInfo.intTypeLength or 256
                        var_obj.value = UnsignedIntegerInterval.top(bits)
                    elif et.startswith("int"):
                        bits = var_obj.typeInfo.intTypeLength or 256
                        var_obj.value = IntegerInterval.top(bits)
                    elif et == "bool":
                        var_obj.value = BoolInterval.top()
                    elif et == "address":
                        var_obj.value = AddressSet.top()
                    elif et.startswith("bytes") and len(et) > 5:
                        byte_size = int(et[5:])
                        var_obj.value = BytesSet.top(byte_size)
                    else:
                        var_obj.value = f"assembly_top_{var_name}"
                else:
                    var_obj.value = f"assembly_top_{var_name}"
            else:
                # 타입 정보 없으면 기본적으로 uint256 Top
                var_obj.value = UnsignedIntegerInterval.top()
        return variables

    # =================================================================
    #  Transfer (엔진 내부에서 바로 호출하도록 수정)
    # =================================================================
    def transfer_function(self, node: CFGNode, in_vars: Optional[dict[str, Variables]]) -> dict[str, Variables]:
        env_in = in_vars or {}
        if getattr(node, "fixpoint_evaluation_node", False):
            node.variables = VariableEnv.copy_variables(env_in)
            return env_in
        env_out = VariableEnv.copy_variables(env_in)
        if getattr(node, "statements", None):
            for st in node.statements:
                env_out = self.update_statement_with_variables(st, env_out, node=node)
        node.variables = VariableEnv.copy_variables(env_out)
        return env_out

    # =================================================================
    #  Loop Condition Convergence Check & Iteration Estimation
    # =================================================================
    def _check_loop_condition_converged(self, cond_expr: Expression,
                                       prev_env: dict[str, Variables],
                                       curr_env: dict[str, Variables]) -> bool:
        """
        루프 조건식을 평가하여 조건이 수렴했는지 체크

        조건식의 좌변과 우변을 각각 평가하여 둘 다 singleton으로 수렴했는지 확인

        Args:
            cond_expr: 루프 조건식
            prev_env: 이전 iteration의 변수 환경
            curr_env: 현재 iteration의 변수 환경

        Returns:
            True if 조건식의 좌변/우변이 모두 singleton으로 수렴
        """
        if cond_expr is None or not prev_env or not curr_env:
            return False

        # Binary expression이 아니면 판단 불가
        if not (hasattr(cond_expr, 'left') and hasattr(cond_expr, 'right')):
            return False

        try:
            # 좌변 평가
            prev_left = self.eval.evaluate_expression(cond_expr.left, prev_env, None, None)
            curr_left = self.eval.evaluate_expression(cond_expr.left, curr_env, None, None)

            # 우변 평가
            prev_right = self.eval.evaluate_expression(cond_expr.right, prev_env, None, None)
            curr_right = self.eval.evaluate_expression(cond_expr.right, curr_env, None, None)

            # 좌변 수렴 체크
            left_converged = False
            if VariableEnv.is_interval(prev_left) and VariableEnv.is_interval(curr_left):
                prev_left_singleton = (not prev_left.is_bottom() and
                                      prev_left.min_value == prev_left.max_value)
                curr_left_singleton = (not curr_left.is_bottom() and
                                      curr_left.min_value == curr_left.max_value)
                if prev_left_singleton and curr_left_singleton:
                    left_converged = (prev_left.min_value == curr_left.min_value)

            # 우변 수렴 체크
            right_converged = False
            if VariableEnv.is_interval(prev_right) and VariableEnv.is_interval(curr_right):
                prev_right_singleton = (not prev_right.is_bottom() and
                                       prev_right.min_value == prev_right.max_value)
                curr_right_singleton = (not curr_right.is_bottom() and
                                       curr_right.min_value == curr_right.max_value)
                if prev_right_singleton and curr_right_singleton:
                    right_converged = (prev_right.min_value == curr_right.min_value)

            # 좌변과 우변이 모두 수렴하면 true
            return left_converged and right_converged

        except Exception:
            return False

    # (2026-10-23, 사용자 결정) widening 지연(delayed widening):
    #   d(L) = 조건에서 반복 규모 N̂ 를 얻으면 min(N̂, LOOP_DELAY_MAX), 얻지 못하면 LOOP_DELAY_UNKNOWN.
    #   N̂ 는 최소 실행 횟수가 아니라 "widening 을 허용하기 전 최대 지연" 이다 — 그 전에 수렴하면 바로 끝난다.
    #   예전엔 [1,20] 으로 잘라, 길이가 ⊤ 인 `i < tokens.length - 1` 이 20(지연 최대)이 되고 `i < 1000` 은 20 으로 줄었다.
    LOOP_DELAY_MAX = 300
    LOOP_DELAY_UNKNOWN = 2

    def _estimate_loop_iterations(self, head: CFGNode, start_env: dict) -> int:
        """루프 widening 지연 d(L). 조건 `a < b`(<=, >, >=) 를 진입 환경에서 평가해 N̂ = 상한 − 하한 (+1).
        평가할 수 없거나(이항 비교 아님·구간 아님·⊥·예외), 경계가 정보가 없는 구간(폭이 타입 범위의 절반 이상 — ⊤ 에서
        유도된 `tokens.length - 1` 등)이거나 `!=` 이면 "모름" → LOOP_DELAY_UNKNOWN."""
        unknown = self.LOOP_DELAY_UNKNOWN
        if not getattr(head, 'condition_node', False):
            return unknown
        cond_expr = getattr(head, 'condition_expr', None)
        if cond_expr is None or getattr(cond_expr, 'operator', None) not in ('<', '<=', '>', '>='):
            return unknown
        try:
            left_val = self.eval.evaluate_expression(cond_expr.left, start_env, None, None)
            right_val = self.eval.evaluate_expression(cond_expr.right, start_env, None, None)
        except Exception:
            return unknown
        if not (VariableEnv.is_interval(left_val) and VariableEnv.is_interval(right_val)):
            return unknown
        if left_val.is_bottom() or right_val.is_bottom():
            return unknown

        def uninformative(iv):
            bits = getattr(iv, "type_length", 256) or 256
            return iv.max_value - iv.min_value >= (1 << (bits - 1))

        if uninformative(left_val) or uninformative(right_val):
            return unknown
        if cond_expr.operator in ('<', '<='):
            n = right_val.max_value - left_val.min_value + (1 if cond_expr.operator == '<=' else 0)
        else:
            n = left_val.max_value - right_val.min_value + (1 if cond_expr.operator == '>=' else 0)
        return max(1, min(int(n), self.LOOP_DELAY_MAX))

    # =================================================================
    #  Fixpoint (루프 중 문장기록 억제)
    # =================================================================
    def fixpoint(self, head: CFGNode) -> CFGNode:
        # ★ (Phase 1a) 루프 해석 중 예외가 나도 기록 억제·widening 플래그를 복원한다.
        old_sup, old_widening = self._suppress_stmt_records, self._in_widening_mode
        try:
            return self._fixpoint_impl(head)
        finally:
            self._suppress_stmt_records = old_sup
            self._in_widening_mode = old_widening

    def _fixpoint_impl(self, head: CFGNode) -> CFGNode:
        G = self.an.current_target_function_cfg.graph
        ref = self.ref; rec = self.rec

        def _edge_flow_from_node_out(node, succ, node_out_env):
            base = VariableEnv.copy_variables(node_out_env or {})
            # (Phase 3b, D8) back-edge(본문 → 루프 join): 본문에서 선언된 이름은 다음 반복으로 넘어가지 않는다
            if succ is join_node_ref and node in loop_nodes and base:
                base = self._restrict_scope(base, set(join_node_ref.join_baseline_env or {}))
            if getattr(node, "condition_node", False):
                ed = G.get_edge_data(node, succ, default=None)
                cond_expr = getattr(node, "condition_expr", None)
                if ed and "condition" in ed and cond_expr is not None:
                    want_true = bool(ed["condition"])
                    self._call_site_node = node   # (2026-10-22) 조건식 안의 호출도 호출 지점 = 이 조건 노드 (예전엔 직전 문장 노드가 남아 엉뚱한 노드에 캡처됐다)
                    ref.update_variables_with_condition(base, cond_expr, want_true)
                    if not self._branch_feasible(base, cond_expr, want_true):
                        return None
            return base

        def _pred_src(p):
            """predecessor p의 out-flow로 쓸 env를 계산.
            p가 loop_nodes 밖에 있고 아직 한 번도 (outer worklist에서) 방문돼
            `.variables`가 채워진 적 없으면 None을 반환해서 호출자가 이
            predecessor를 join에서 완전히 제외하게 한다 — 그러지 않고 빈
            `{}`를 "이 predecessor를 지나면 모든 변수가 사라진다"는 뜻으로
            잘못 취급하면, 그 predecessor가 condition_node일 때
            (`require(sumNative...)`처럼) 빈 env로 조건을 refine하려다가
            크래시하거나(변수를 못 찾음) 값이 조용히 비어버림 — 루프 안팎의
            require/revert가 하나의 공유 ERROR sink로 수렴하는 구조에서
            루프 밖 predecessor(함수 뒷부분, 아직 outer worklist가 도달
            안 함)가 이 경로를 타면서 발생(web3bugs_52_H_04/52_H_34에서 발견).
            """
            if p in loop_nodes:
                return out_vars.get(p) or {}
            p_vars = getattr(p, "variables", None)
            if not p_vars:
                return None
            return p_vars

        loop_nodes: set[CFGNode] = self.traverse_loop_nodes(head)
        from collections import defaultdict
        visit_cnt: defaultdict[CFGNode, int] = defaultdict(int)
        in_vars: dict[CFGNode, dict[str, Variables] | None] = {n: None for n in loop_nodes}
        out_vars: dict[CFGNode, dict[str, Variables] | None] = {n: None for n in loop_nodes}

        # ★ fixpoint 시작 전에 join 노드의 cached fixpoint vars 초기화
        for n in loop_nodes:
            if getattr(n, "fixpoint_evaluation_node", False):
                if hasattr(n, "fixpoint_evaluation_node_vars"):
                    delattr(n, "fixpoint_evaluation_node_vars")

        # ★ start_env 계산: join 노드의 경우 back edge를 제외하고 초기 진입 edge만 사용
        start_env = None
        join_node_ref = None
        for p in G.predecessors(head):
            # join 노드인 경우: 그 predecessor 중 loop 내부가 아닌 것만 사용
            if getattr(p, "fixpoint_evaluation_node", False):
                join_node_ref = p
                for pp in G.predecessors(p):
                    # back edge (증감식)는 제외: loop_nodes에 속하는 predecessor는 skip
                    if pp in loop_nodes:
                        continue
                    pp_vars = getattr(pp, "variables", {}) or {}
                    start_env = VariableEnv.join_variables_simple(start_env, pp_vars)
            else:
                # 일반 predecessor
                p_vars = getattr(p, "variables", {}) or {}
                start_env = VariableEnv.join_variables_simple(start_env, p_vars)
        in_vars[head] = start_env or {}

        # ★ join 노드를 loop_nodes에 추가하고 초기값 설정
        # 이렇게 하면 첫 iteration에서 i=1부터 시작됨
        if join_node_ref is not None:
            loop_nodes.add(join_node_ref)
            in_vars[join_node_ref] = VariableEnv.copy_variables(start_env or {})
            out_vars[join_node_ref] = None
            join_node_ref.join_baseline_env = VariableEnv.copy_variables(start_env or {})

        # ── 루프 내부 문장 기록 억제 on
        old_sup = self._suppress_stmt_records
        self._suppress_stmt_records = True

        # Loop 반복 횟수 추정 (조건식 기반)
        widening_threshold = (self._estimate_loop_iterations(head, in_vars[head]))

        # ★ widening - 초기 WL은 head만 (join 노드는 predecessor가 준비되면 자동으로 추가됨)
        # (2026-10-23) 자원 상한 W_MAX 에 닿아도 수렴 전 상태를 결과로 내지 않는다(예전엔 작업 목록이 남은 채 빠져나와 narrowing 으로
        #   넘어갔다 — 고정점이 아닌 환경 = unsound). 상한에 닿으면 모든 루프 join 에서 강제 widening 으로 바꿔 수렴할 때까지 계속하고,
        #   그래도 W_FORCED_MAX 를 넘기면 명시적 오류.
        W_MAX = 300
        W_FORCED_MAX = 2 * W_MAX
        forced = False
        WL = deque([head])
        iteration = 0

        while WL:
            top_visits = max(visit_cnt.values(), default=0)
            if top_visits >= W_FORCED_MAX:
                raise ValueError(f"loop fixpoint at '{head.name}' did not converge within {W_FORCED_MAX} visits "
                                 f"even with forced widening")
            if not forced and top_visits >= W_MAX:
                forced = True
            node = WL.popleft()
            visit_cnt[node] += 1
            iteration += 1

            # ★ in_vars[node]가 None인 경우 처리
            if in_vars[node] is None:
                # None이면 predecessor들의 out을 join해서 계산
                in_new = None
                for p in G.predecessors(node):
                    if p in loop_nodes and out_vars[p] is not None:
                        flow = _edge_flow_from_node_out(p, node, out_vars[p])
                        if flow is not None:
                            in_new = VariableEnv.join_variables_simple(in_new, flow)
                if in_new is not None:
                    in_vars[node] = in_new

            out_old = out_vars[node]

            # ★ widening 모드 설정 (후속 노드들도 widening threshold 넘으면 추상화)
            old_widening = self._in_widening_mode
            if forced or visit_cnt[node] > widening_threshold:
                self._in_widening_mode = True

            out_raw = self.transfer_function(node, in_vars[node])

            # ★ widening 모드 복원
            self._in_widening_mode = old_widening

            # ★ out_vars에서는 widening 적용하지 않음
            #    표준 abstract interpretation에서 widening은 loop header의 in_vars에서만 적용
            #    out_vars에서 widening하면 loop body로 전파되어 불필요한 over-approximation 발생
            out_joined = VariableEnv.join_variables_simple(out_old, out_raw)

            if getattr(node, "fixpoint_evaluation_node", False):
                node.fixpoint_evaluation_node_vars = VariableEnv.copy_variables(out_joined)

            equal = VariableEnv.variables_equal(out_old, out_joined)

            if equal:
                out_vars[node] = out_joined; continue
            out_vars[node] = out_joined

            for succ in G.successors(node):
                if succ not in loop_nodes:  # 루프 밖은 고정점 후 reinterpret에서 처리
                    continue
                flow = _edge_flow_from_node_out(node, succ, out_joined)
                if flow is None:
                    continue
                if getattr(succ, "fixpoint_evaluation_node", False):
                    succ_widen = forced or (visit_cnt[succ] > widening_threshold)
                else:
                    succ_widen = False

                # ★ fixpoint_evaluation_node (join 노드)의 경우:
                #    모든 predecessor의 최신 flow를 먼저 join한 후 widening 적용
                if getattr(succ, "fixpoint_evaluation_node", False):
                    # 모든 predecessor의 flow를 join
                    all_pred_flows = None
                    for pred in G.predecessors(succ):
                        if pred in loop_nodes:
                            pred_out = out_vars.get(pred) or getattr(pred, "variables", {}) or {}
                        else:
                            # 루프 밖 predecessor (initial entry) - 현재 variables 사용
                            pred_out = getattr(pred, "variables", {}) or {}
                        pred_flow = _edge_flow_from_node_out(pred, succ, pred_out)
                        if pred_flow is not None:
                            all_pred_flows = VariableEnv.join_variables_simple(all_pred_flows, pred_flow)

                    # 이제 old in_vars와 새로 join된 값을 비교하여 widening
                    if all_pred_flows is not None:
                        in_new = (VariableEnv.join_variables_with_widening(in_vars[succ], all_pred_flows)
                                  if succ_widen
                                  else VariableEnv.join_variables_simple(in_vars[succ], all_pred_flows))
                    else:
                        in_new = in_vars[succ]
                else:
                    in_new = (VariableEnv.join_variables_with_widening(in_vars[succ], flow)
                              if succ_widen
                              else VariableEnv.join_variables_simple(in_vars[succ], flow))

                changed_succ = not VariableEnv.variables_equal(in_vars[succ], in_new)

                if changed_succ:
                    in_vars[succ] = VariableEnv.copy_variables(in_new)
                    WL.append(succ)

        # narrowing — widening에서 도달한(reachable) node만 대상
        reachable = [n for n in loop_nodes if out_vars.get(n) is not None]
        WL = deque(reachable); N_MAX = 30
        while WL and N_MAX:
            N_MAX -= 1
            node = WL.popleft()

            new_in = None
            for p in G.predecessors(node):
                src = _pred_src(p)
                if src is None:
                    continue  # 아직 계산 안 된 loop 밖 predecessor — join 에서 제외
                flow = _edge_flow_from_node_out(p, node, src)
                if flow is None:
                    continue
                new_in = VariableEnv.join_variables_simple(new_in, flow)

            if not VariableEnv.variables_equal(in_vars[node], new_in):
                in_vars[node] = VariableEnv.copy_variables(new_in or {})

            # ★ unreachable 노드 처리: in_vars가 빈 딕셔너리면 skip
            if not in_vars[node]:
                # 이 노드는 unreachable (모든 predecessor의 flow가 None)
                continue

            tmp_out = self.transfer_function(node, in_vars[node])
            narrowed = (VariableEnv.narrow_variables(out_vars[node], tmp_out)
                        if getattr(node, "fixpoint_evaluation_node", False)
                        else tmp_out)

            if VariableEnv.variables_equal(out_vars[node], narrowed):
                continue
            out_vars[node] = narrowed

            for succ in G.successors(node):
                if succ in loop_nodes:
                    WL.append(succ)


        # 루프 내부 문장 기록 억제 off
        self._suppress_stmt_records = old_sup

        # (Phase 3c, D2) 수렴 후에도 값이 한 번도 흘러 들어오지 않은 루프 노드.
        #   들어오는 흐름이 전부 infeasible 엣지이거나 ⊥ 노드에서 오는 경우에만 명시적 ⊥ (바깥 worklist 와 같은 의미).
        #   도달한 선행에서 feasible 한 흐름이 있는데도 해석되지 않았다면 해석기 결함 → 예외(⊥ 로 덮지 않는다).
        pending = [n for n in loop_nodes if out_vars.get(n) is None and not n.variables]
        bot_nodes: set = set()
        changed = True
        while pending and changed:
            changed = False
            for n in list(pending):
                ok = True
                for p in G.predecessors(n):
                    if p in bot_nodes:
                        continue
                    if p in loop_nodes:
                        if out_vars.get(p) is None:
                            if p in pending:
                                ok = False          # 아직 판정 안 된 선행 — 다음 회차
                                break
                            continue
                        if _edge_flow_from_node_out(p, n, out_vars[p]) is not None:
                            raise ValueError(f"interpreter invariant: loop node '{n.name}' has a feasible flow "
                                             f"from '{p.name}' but was not interpreted")
                if ok:
                    bot = VariableEnv.copy_variables(start_env or {})
                    self._set_bottom_env(bot)
                    n.variables = bot
                    bot_nodes.add(n)
                    pending.remove(n)
                    changed = True
        if pending:
            raise ValueError(f"interpreter invariant: loop nodes not interpreted: {[n.name for n in pending]}")

        # exit env
        exit_node = self.find_loop_exit_node(head)
        if not exit_node:
            raise ValueError("loop without exit-node")

        exit_env = None
        for p in G.predecessors(exit_node):
            src = _pred_src(p)
            if src is None:
                continue  # 아직 계산 안 된 loop 밖 predecessor — join 에서 제외
            flow = _edge_flow_from_node_out(p, exit_node, src)
            if flow is None:
                continue
            exit_env = VariableEnv.join_variables_simple(exit_env, flow)
        exit_node.variables = VariableEnv.copy_variables(exit_env or {})

        # loopDelta: 헤더(조건) 라인에 기록
        # ★ fixpoint evaluation node(join)의 고정점 상태를 사용 (false edge 정제 전)
        join_node = None
        for p in G.predecessors(head):
            if getattr(p, "fixpoint_evaluation_node", False):
                join_node = p
                break

        if join_node is not None:
            base_env = getattr(join_node, "join_baseline_env", None)
            # ★ join 노드의 **최종**(narrowing 이후) 상태를 쓴다 = `join_node.variables`.
            #   narrowing 패스의 `transfer_function` 이 매 방문마다 써 주며, join 노드의
            #   transfer 는 identity 라 in == out 이다.
            #   ⚠ `fixpoint_evaluation_node_vars` 는 **widening 루프 안에서만** 갱신되므로
            #     narrowing 전 스냅샷이고, 그 안에는 widening 이 넣은 `INFINITY` 가 그대로
            #     남아 `[0,inf]` 로 직렬화된다. 같은 변수를 노드 state 로 보면
            #     `[0,2^256-1]` 이라 **한 화면에 두 표기가 섞였다**(N3 에서 발견).
            #     narrowing 이 join 노드에 도달하지 못한 경우 `.variables` 는 마지막
            #     widening 값이라 예전 동작과 같아진다 — 별도 폴백이 필요 없다.
            fixpoint_env = getattr(join_node, "variables", None) or getattr(
                join_node, "fixpoint_evaluation_node_vars", None)

            if base_env is not None and fixpoint_env is not None:
                changed_flat = VariableEnv.diff_changed(base_env, fixpoint_env)

                if changed_flat:
                    ln_head = getattr(head, "src_line", None)
                    if ln_head is not None:
                        # loopDelta 기록: diff_changed는 이미 직렬화된 딕셔너리를 반환하므로
                        # _append_or_replace를 직접 호출
                        rec._append_or_replace(
                            ln_head,
                            {"kind": "loopDelta", "vars": changed_flat},
                            replace_rule=lambda old, new: old.get("kind") == new["kind"],
                        )

        return exit_node

    # =================================================================
    #  interpret_function_cfg (Case B: 내부 함수 호출용)
    #  - evaluate_function_call_context 등에서 호출
    #  - 기록 비활성화, caller env의 state/global을 callee에 공유
    # =================================================================
    @staticmethod
    def _is_shared_storage(name: str, var) -> bool:
        """caller ↔ callee 가 **같은 것을 가리키는** 변수인가 (F36).

        Solidity 에서 caller 와 callee 가 공유하는 것은
          · **state 변수/상수** — 같은 storage
          · **block/msg/tx 전역** — 같은 트랜잭션 컨텍스트
        뿐이다. 지역변수·파라미터·named return 은 각 프레임의 것이므로 공유하면 안 된다.

        판정: `Variables.scope == 'state'` 또는 이름에 `.` 이 있으면(전역) 공유 대상.
        (실측 분포 — `related_variables`: scope='state' = state 변수·상수,
         scope=None + `block.*`/`msg.*`/`tx.*` = 전역, scope='local' = 지역·파라미터·named return)
        """
        if "." in name:                      # block.timestamp, msg.sender, tx.origin …
            return True
        return getattr(var, "scope", None) == "state"

    # ── (2026-10-23) 재귀 호출 요약 ─────────────────────────────────────────
    #   내부 호출은 callee 본문을 매번 펼쳐 해석하므로, 재귀는 깊이에 끝이 없다(n=⊤ 이면 Python RecursionError).
    #   이미 실행 중인 함수를 다시 부르면 본문을 펼치지 않고 그 호출의 **효과를 요약**한다:
    #     · 그 함수(와 그 함수가 부를 수 있는 함수들, 전이적으로)가 대입하는 상태변수 → ⊤ (쓰지 않는 상태변수는 유지)
    #       매핑·배열 원소에 쓰면 그 컨테이너 전체 ⊤ — 재귀 프레임마다 인덱스가 달라 "같은 셀" 이라는 근거가 없다.
    #     · 그 함수가 대입하는 복합 타입 파라미터에 넘긴 인자(참조 의미) → ⊤
    #     · 반환값 = 선언 반환 타입의 ⊤
    #   어느 경로가 실행될지 모르므로 쓰기 집합은 모든 경로의 합집합이다(sound 에 필요한 만큼).
    _ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>="})

    def _call_stack(self) -> list:
        st = getattr(self, "_call_frames", None)
        if st is None:
            st = self._call_frames = []
        return st

    @staticmethod
    def _func_key(fcfg) -> tuple:
        f = getattr(fcfg, "_live_origin", fcfg)
        return (getattr(f, "owner_contract", None), f.function_name,
                tuple(getattr(f, "parameter_types", None) or ()))

    @staticmethod
    def _lhs_roots(e) -> set:
        """대입 좌변 식의 루트 변수 이름들 (`a[i].b` → a, `(x, y)` → x, y)."""
        if e is None:
            return set()
        elems = getattr(e, "elements", None)
        if isinstance(elems, (list, tuple)):
            out = set()
            for x in elems:
                out |= Engine._lhs_roots(x)
            return out
        while e is not None:
            if getattr(e, "context", None) == "IdentifierExpContext":
                return {e.identifier}
            e = getattr(e, "base", None) if getattr(e, "base", None) is not None else getattr(e, "expression", None)
        return set()

    def _scan_writes(self, fcfg) -> tuple[set, set, set]:
        """함수 하나의 (대입 루트 이름, 호출하는 함수 이름, 한정 라이브러리 호출 (lib, 이름))."""
        writes, calls, lib_calls = set(), set(), set()
        seen = set()

        def walk(e):
            if isinstance(e, (list, tuple)):
                for x in e:
                    walk(x)
                return
            if isinstance(e, dict):
                for x in e.values():
                    walk(x)
                return
            if not isinstance(e, Expression) or id(e) in seen:
                return
            seen.add(id(e))
            op = getattr(e, "operator", None)
            if op in self._ASSIGN_OPS and getattr(e, "left", None) is not None:
                writes.update(self._lhs_roots(e.left))
            if op in ("++", "--", "delete"):
                writes.update(self._lhs_roots(getattr(e, "expression", None)))
            fn = getattr(e, "function", None)
            if isinstance(fn, Expression):
                if fn.context == "IdentifierExpContext":
                    calls.add(fn.identifier)
                elif fn.context == "MemberAccessContext":
                    calls.add(fn.member)
                    if fn.member in ("push", "pop"):
                        writes.update(self._lhs_roots(fn.base))
                    b = getattr(fn, "base", None)
                    if getattr(b, "context", None) == "IdentifierExpContext":
                        lib_calls.add((b.identifier, fn.member))
            for v in vars(e).values():
                if isinstance(v, (Expression, list, tuple, dict)):
                    walk(v)

        for n in fcfg.graph.nodes:
            for st in getattr(n, "statements", None) or []:
                if st.statement_type == "assignment":
                    writes.update(self._lhs_roots(st.left))
                elif st.statement_type == "unary" and st.operator in ("++", "--", "delete"):
                    writes.update(self._lhs_roots(st.operand))
                for v in vars(st).values():
                    if isinstance(v, (Expression, list, tuple, dict)):
                        walk(v)
            if getattr(n, "condition_expr", None) is not None:
                walk(n.condition_expr)
        for name, _args in getattr(fcfg, "modifier_invocations", None) or []:
            calls.add(name)
        return writes, calls, lib_calls

    def _callee_pool(self, fcfg) -> list:
        """`fcfg` 안의 이름 호출이 갈 수 있는 함수들이 있는 곳: 소유 컨트랙트·타깃 컨트랙트의 상속 체인 + 그들의 using 라이브러리."""
        an = self.an
        cfgs = {**(an.library_cfgs or {}), **(an.contract_cfgs or {})}
        pool, seen = [], set()

        def add(c):
            if c is None or id(c) in seen:
                return
            seen.add(id(c))
            pool.append(c)
            for p in (getattr(c, "parent_cfgs", None) or {}).values():
                add(p)
            for libs in (getattr(c, "using_libraries", None) or {}).values():
                for lib in libs:
                    add(lib)
            for lib in getattr(c, "using_all_libraries", None) or []:
                add(lib)

        add(cfgs.get(getattr(getattr(fcfg, "_live_origin", fcfg), "owner_contract", None)))
        add(cfgs.get(an.current_target_contract))
        return pool

    def _write_set(self, fcfg) -> tuple[set, set]:
        """(전이적 대입 루트 이름 전체, `fcfg` 자신이 대입하는 루트 이름)."""
        an = self.an
        cfgs = {**(an.library_cfgs or {}), **(an.contract_cfgs or {})}
        own, _, _ = self._scan_writes(fcfg)
        allw, todo, done = set(), [fcfg], set()
        while todo:
            f = todo.pop()
            if id(f) in done:
                continue
            done.add(id(f))
            w, calls, lib_calls = self._scan_writes(f)
            allw |= w
            for c in self._callee_pool(f):
                for name in calls:
                    subs = (getattr(c, "functions", None) or {}).get(name)
                    if subs:
                        todo.extend(subs.values() if isinstance(subs, dict) else [subs])
            for lib, name in lib_calls:
                subs = (getattr(cfgs.get(lib), "functions", None) or {}).get(name) if lib in cfgs else None
                if subs:
                    todo.extend(subs.values() if isinstance(subs, dict) else [subs])
        return allw, own

    def _owner_cfg(self, fcfg):
        owner = getattr(getattr(fcfg, "_live_origin", fcfg), "owner_contract", None)
        return (self.an.contract_cfgs or {}).get(owner) or (self.an.library_cfgs or {}).get(owner)

    def _havoc(self, v, owner_cfg) -> None:
        """변수 v 를 제자리에서 ⊤ 로 (복합 타입은 모든 위치)."""
        if isinstance(v, MappingVariable):
            v.mapping.clear()
            v.sym.clear()
            v.summary = None                      # None = 선언 기본값(⊤, D5)
            return
        if isinstance(v, ArrayVariable):
            v.elements = {}                       # 모든 원소 ⊤ = 구체 셀 없음 + D 기본값(⊤)
            v.sym_cells.clear()
            v.sym.clear()
            v.summary = None
            if v.typeInfo.isDynamicArray:
                v.length = None                   # 미지 길이(⊤)
            return
        if isinstance(v, StructVariable):
            for m in v.members.values():
                self._havoc(m, owner_cfg)
            return
        if isinstance(v, EnumVariable):
            raise ValueError(f"recursive-call summary: enum variable '{v.identifier}' cannot be set to ⊤ "
                             f"(enum 값 도메인에 ⊤ 표현이 없다)")
        v.value = self.eval._top_of_type(v.typeInfo, owner_cfg, v.identifier)

    def _recursive_call_summary(self, fcfg, arg_values, caller_env, kind):
        owner_cfg = self._owner_cfg(fcfg)
        allw, own = self._write_set(fcfg)
        if caller_env is not None:
            for name in sorted(allw):
                v = caller_env.get(name)
                if v is not None and "." not in name and getattr(v, "scope", None) == "state":
                    self._havoc(v, owner_cfg)
        if kind != "this":                        # this.f() 는 외부 호출 — 인자는 ABI 복사라 호출자 객체가 바뀌지 않는다
            params = list(getattr(fcfg, "parameters", []) or [])
            for pname, val in zip(params, arg_values):
                if pname in own and isinstance(val, (StructVariable, ArrayVariable, MappingVariable)):
                    self._havoc(val, owner_cfg)
        rts = self.eval.declared_return_types(fcfg)
        tops = [self.eval._top_of_type(t, owner_cfg, f"{fcfg.function_name}_ret{i}") for i, t in enumerate(rts)]
        if not tops:
            return None
        return tops[0] if len(tops) == 1 else tops

    def invoke_callee(self, fcfg: FunctionCFG, arg_values: list, caller_env: dict | None, *, kind: str,
                      call_expr=None):
        """(Phase 4, D3) 모든 내부 호출(일반·library·super·this)의 공통 진입점.

        - 인자는 callee 의 `related_variables` 를 고치지 않고 **바인딩**으로 넘긴다(예전엔 4 개 binder 가 callee CFG 의
          파라미터 객체를 제자리에서 덮어썼다 → 실행 간 누수, super/this 는 caller env 전체를 영구 병합).
        - 값 타입 인자 = 사본. 복합 타입(memory struct/array, storage 참조) 인자 = 내부 호출(`kind != "this"`)에서는
          **참조 의미** — callee 종료 시 파라미터의 최종 내용을 caller 의 원래 객체에 되쓴다.
          `this.f()` 는 외부 호출이므로 ABI 복사(되쓰기 없음).
        """
        params = list(getattr(fcfg, "parameters", []) or [])
        if len(arg_values) != len(params):
            raise ValueError(f"call to '{fcfg.function_name}': expected {len(params)} arguments, got {len(arg_values)}")
        # (2026-10-23) 재귀 — 이미 실행 중인 함수를 다시 부르면 본문을 펼치지 않고 요약한다(예전엔 Python RecursionError)
        if self._func_key(fcfg) in self._call_stack():
            return self._recursive_call_summary(fcfg, arg_values, caller_env, kind)
        bindings, sources = {}, {}
        for pname, val in zip(params, arg_values):
            proto = (fcfg.related_variables or {}).get(pname)
            if proto is None:
                raise ValueError(f"call to '{fcfg.function_name}': parameter '{pname}' has no declaration")
            if isinstance(val, (StructVariable, ArrayVariable, MappingVariable)):
                b = VariableEnv.copy_single_variable(val)
                b.identifier = pname
                if kind != "this":
                    sources[pname] = val
            elif isinstance(proto, (StructVariable, ArrayVariable, MappingVariable)):
                raise ValueError(f"call to '{fcfg.function_name}': argument for composite parameter '{pname}' "
                                 f"is not a composite value ({type(val).__name__})")
            else:
                b = VariableEnv.copy_single_variable(proto)
                if isinstance(val, Variables):
                    val = val.value
                # (Phase 4b) 인자 → 파라미터 타입 암묵 변환
                b.value = coerce_to_declared(VariableEnv._copy_value(val), getattr(proto, "typeInfo", None),
                                             f"argument for '{pname}' of '{fcfg.function_name}'")
            bindings[pname] = b
        # (2026-10-22) 이 호출의 식 — callee 상태 캡처 키(호출 지점 노드 안의 몇 번째 호출인가)에 쓴다.
        #   같은 노드에서 같은 함수를 두 번 부르면(`half(a) + half(a + 1)`) 예전 키 (노드, 함수) 가 겹쳐 앞 호출이 사라졌다.
        self._pending_call_expr = call_expr
        # (2026-10-23) 호출자 선택자 ↔ 파라미터 대응 — 호출자 프레임에서 계산 (Storage.callee_aliases)
        self._pending_aliases = self.an.storage.callee_aliases(fcfg, call_expr, kind)
        return self.interpret_function_cfg(fcfg, caller_env, param_bindings=bindings, param_sources=sources)

    def interpret_function_cfg(self, fcfg: FunctionCFG, caller_env: dict[str, Variables] | None = None,
                               param_bindings: dict | None = None, param_sources: dict | None = None):
        fcfg = self._composed_callee(fcfg)   # (Phase 4, D6) modifier 합성본
        an = self.an
        _old_func = an.current_target_function
        _old_fcfg = an.current_target_function_cfg
        _old_record_enabled = self._record_enabled
        # ★ N4/S1. 호출 지점을 **진입 시점에** 로컬로 받아둔다 — callee 해석이
        #   `self._call_site_node` 를 자기 노드로 덮으므로, 중첩 호출에서도 각 프레임이
        #   자기 호출 지점을 유지한다.
        _call_site_node = getattr(self, "_call_site_node", None)
        _call_expr = getattr(self, "_pending_call_expr", None)
        self._pending_call_expr = None
        _aliases = getattr(self, "_pending_aliases", None) or {}
        self._pending_aliases = None

        an.current_target_function = fcfg.function_name
        an.current_target_function_cfg = fcfg
        self._record_enabled = False
        an._seen_stmt_ids.clear()

        # caller의 ireturn_registry를 callee에 전파 (sub-function에서도 IReturn 적용)
        _saved_ireturn = dict(fcfg.ireturn_registry)
        if _old_fcfg and _old_fcfg.ireturn_registry:
            for key, val in _old_fcfg.ireturn_registry.items():
                fcfg.ireturn_registry.setdefault(key, val)

        _saved_node_vars = self._reset_node_vars(fcfg)
        an.storage.push_frame(fcfg.function_name, _aliases)
        self._call_stack().append(self._func_key(fcfg))
        # ★ (Phase 1a) 해석 중 예외가 나도 callee 노드·컨텍스트·플래그를 반드시 복원한다.
        #   예전엔 복원이 정상 경로에만 있어서, 예외 한 번이면 `_record_enabled=False` 와
        #   callee 컨텍스트가 남아 이후 기록·During 검증이 조용히 꺼졌다.
        try:
            return self._interpret_callee_body(fcfg, caller_env, _call_site_node, param_bindings, param_sources,
                                               _call_expr)
        finally:
            self._call_stack().pop()
            an.storage.pop_frame()
            for blk, saved_vars in _saved_node_vars.items():
                blk.variables = saved_vars
                blk.in_variables = None       # live callee 는 구성 결과로 되돌린다(IN 은 캡처본에만)
            an.current_target_function = _old_func
            an.current_target_function_cfg = _old_fcfg
            fcfg.ireturn_registry = _saved_ireturn
            self._record_enabled = _old_record_enabled
            # (2026-10-22) 호출 지점 복원 — callee 해석이 자기 노드로 덮어 둔 채 돌아오면, 같은 caller 문장의
            #   **다음** 호출(`half(a) + half(a + 1)` 의 두 번째)이 callee 의 마지막 노드를 호출 지점으로 잡았다.
            self._call_site_node = _call_site_node

    def _interpret_callee_body(self, fcfg: FunctionCFG, caller_env, _call_site_node,
                               param_bindings=None, param_sources=None, _call_expr=None):
        """interpret_function_cfg 의 본체(진입 env 구성 → worklist → exit 처리 → caller 되쓰기).
        노드/컨텍스트 복원은 호출자(interpret_function_cfg)의 finally 가 맡는다."""
        an = self.an
        # (2026-10-23) 진입 환경은 ENTRY 에 둔다. 예전엔 ENTRY 의 후속 노드에 두어 ENTRY 가 빈 채였고, 빈 본문 함수
        #   (ENTRY → EXIT)는 `_force_join_before_exit` 가 유일한 정상 종료 선행인 ENTRY 를 "도달 불가"로 보고 EXIT 를 ⊥ 로 만들었다
        #   → 호출자의 상태변수가 전부 ⊥ (47_H_02·78_H_02).
        start_block = fcfg.get_entry_node()

        # callee env 초기화: callee 자체 정의(파라미터, 로컬) 기반
        start_block.variables = VariableEnv.copy_variables(fcfg.related_variables)

        # caller env의 state variable / global을 callee에 공유 (같은 storage)
        # - callee에 이미 있는 key도 caller 값으로 덮어씀 (단, 파라미터는 제외)
        #
        # ★ F36. 공유 대상을 **state 변수 + global 로 한정**한다.
        #   예전 코드는 `if k in param_names: continue` 하나만 걸러 **caller 의 지역변수까지
        #   전부** callee 에 밀어넣었다. 주석은 원래부터 "state variable / global" 이라고
        #   써 있었으니(Guardian 46c497e, 2026-04-02) **의도는 처음부터 이것이었고 필터만
        #   구현되지 않았다.**
        #
        #   왜 "덮어쓰기" 자체는 필요한가: callee 의 `related_variables` 에는 이미 state
        #   변수가 들어 있지만 그것은 **선언 시점 스냅샷**이다. caller 가 `balance -= amount`
        #   를 한 뒤 callee 를 부르면 callee 는 낡은 `balance` 를 본다 — Solidity 에서
        #   state 변수는 caller/callee 가 같은 storage 이므로 오류다. 그래서 46c497e 가
        #   "없는 것만 채우기" → "덮어쓰기" 로 바꿨다.
        #
        #   그런데 지역변수까지 넘기면 **이름이 겹칠 때 callee 의 값을 덮어쓰고**, 복귀 시
        #   write-back 이 그 값을 caller 에 되써서 caller 가 오염된다. 실측:
        #     `PRBMathSD59x18.powu` 의 `uint256 absX = uint256(abs(x));` 한 줄이
        #     **파라미터 `y` 를 ⊥ 로 만들었다**(`y` 는 그 줄에 등장조차 하지 않는다).
        #     함수 ENTRY 노드까지 오염되고, 이어서 루프 조건의 양쪽 분기가 도달불가로
        #     판정되어 `for_exit` env 가 `{}` 가 되고 뒤 문장이 "미선언" 으로 터졌다.
        #
        #   판정 기준은 `Variables.scope` 가 이미 제공한다(실측 분포):
        #     scope='state' → state 변수·상수  · scope=None + 이름에 '.' → block/msg/tx 전역
        #     scope='local' → 지역변수·파라미터·named return  ← 공유 금지
        if caller_env is not None:
            param_names = set(getattr(fcfg, 'parameters', []))
            for k, v in caller_env.items():
                if k in param_names:
                    continue
                if not self._is_shared_storage(k, v):
                    continue
                start_block.variables[k] = VariableEnv.copy_single_variable(v)

        # (Phase 4) 인자 바인딩 — invoke_callee 가 만든 새 파라미터 객체
        for pname, b in (param_bindings or {}).items():
            start_block.variables[pname] = b
        # (2026-10-23) 이 함수 프레임의 선택자 셀은 이 호출에서 새로 묶인다 — 이전 호출(같은 함수)이 남긴 셀을 쓰지 않는다
        an.storage.purge_scope(start_block.variables, fcfg.function_name)

        return_values = self._run_worklist(fcfg, start_block)

        self._force_join_before_exit(fcfg)
        self._restore_all_shadows(fcfg.get_exit_node().variables)   # (Phase 3b, D8)
        self._sync_named_return_vars(fcfg)

        # exit env를 node 복원 전에 캡처
        _exit_env = VariableEnv.copy_variables(fcfg.get_exit_node().variables) if caller_env is not None else None
        if _exit_env is not None:
            # (2026-10-23) callee 프레임의 선택자(파라미터·지역변수)는 복귀하면 사라진다 — 바인딩만 버린다(쓰기 효과는 남는다)
            an.storage.purge_scope(_exit_env, fcfg.function_name)

        # (Phase 4) 복합 타입 인자의 참조 의미 — callee 가 바꾼 내용을 caller 의 원래 객체에 되쓴다.
        #   exit 가 ⊥(항상 revert)면 이후 caller 경로는 도달 불가이므로 되쓰지 않는다.
        _exit_vars = fcfg.get_exit_node().variables or {}
        if param_sources and _exit_vars and not self._is_bottom_env(_exit_vars):
            for pname, src in param_sources.items():
                final = _exit_vars.get(pname)
                if final is None:
                    continue
                if isinstance(src, StructVariable) and isinstance(final, StructVariable):
                    src.members = VariableEnv.copy_variables(final.members)
                elif isinstance(src, (ArrayVariable, MappingVariable)) and type(src) is type(final):
                    VariableEnv.adopt_storage(src, final)

        # ★ N4/S1. restore 로 baseline 이 복원되기 **전에** callee 노드 상태를 떠낸다.
        #   아래 restore 루프가 `_reset_node_vars` 의 백업(= 선언 시점 스냅샷)으로 되돌려
        #   놓으므로, 이 지점을 놓치면 이 호출의 계산 결과는 **어디에도 남지 않는다**.
        #   (실측: 같은 callee 를 4회 호출해도 복귀 후 상태가 4회 모두 동일 = 전부 baseline)
        if getattr(an, "_capture_debug_fcfg", False):
            self._capture_callee_states(fcfg, _call_site_node, _call_expr)

        # callee의 exit env를 caller에 반영 (state variable 변경 전파)
        # callee 파라미터가 caller의 동명 변수를 오염시키지 않도록 제외
        # ★ F36. 진입 쪽과 **대칭으로** state 변수 + global 만 되쓴다.
        #   되쓰기의 목적은 "callee 가 바꾼 storage 를 caller 에 반영" 이므로 지역변수는
        #   애초에 대상이 아니다. 필터가 없으면 callee 안에서 ⊥ 이 된 동명 지역변수가
        #   caller 를 오염시킨다(위 진입 쪽 주석의 `powu`/`abs` 사례).
        if caller_env is not None and _exit_env is not None:
            _callee_params = set(getattr(fcfg, 'parameters', []))
            for k, v in _exit_env.items():
                if k in _callee_params:
                    continue
                if not self._is_shared_storage(k, v):
                    continue
                if k in caller_env:
                    cv = caller_env[k]
                    if isinstance(v, (MappingVariable, ArrayVariable)) and type(cv) is type(v):
                        VariableEnv.adopt_storage(cv, v)
                    elif isinstance(v, StructVariable) and isinstance(cv, StructVariable):
                        cv.members = v.members
                    elif isinstance(v, EnumVariable) and isinstance(cv, EnumVariable):
                        cv.value = v.value
                        cv.valueIndex = v.valueIndex
                    elif hasattr(cv, "value"):
                        cv.value = v.value
                    else:
                        caller_env[k] = v
                elif isinstance(v, (MappingVariable, ArrayVariable)):
                    caller_env[k] = v

        # log_implicit=False → 기록 플래그·컨텍스트와 무관(return_vars / RETURN.return_vals 만 읽음)
        return self._extract_return_value(fcfg, return_values)

    # =================================================================
    #  interpret_function_cfg_for_debug (Case A: debug batch flush용)
    #  - ContractAnalyzer.flush에서 호출
    #  - 기록 활성화, assign_env/entry_env 갱신, annotation 처리
    # =================================================================
    def interpret_function_cfg_for_debug(self, fcfg: FunctionCFG, caller_env: dict[str, Variables] | None = None):
        """디버깅 입력 1회 해석.

        ★ (리팩터링 Phase 1a, REFACTOR_PLAN.md) 실행 격리: live fcfg 를 직접 해석하지 않고
          **실행용 사본(run)** 을 만들어 그 위에서 해석한다. live CFG 는 구성 결과로서 불변으로 남고,
          계산된 states 는 사본에만 남는다 → 노드 reset/restore 가 필요 없고, 이전 회차의
          before_envs / return_vals / ireturn_registry 가 다음 회차에 새지 않는다.
          사본은 그대로 freeze 결과가 된다(예전의 해석 후 pickle 과 같은 비용).
        """
        import pickle as _pickle
        an = self.an; rec = self.rec
        _old_func = an.current_target_function
        _old_fcfg = an.current_target_function_cfg
        _old_record_enabled = self._record_enabled

        # 실행용 사본 — debug 지시자가 패치한 related_variables·ireturn_registry 를 그대로 싣는다.
        run = _pickle.loads(_pickle.dumps(fcfg))
        # 줄 좌표(sa.line_info 의 cfg_nodes)는 live 노드 identity 로 기록돼 있다 → 원본 참조를 단다.
        run._live_origin = fcfg
        self._composed_cache = {}            # (Phase 4) callee modifier 합성본 — 실행마다 새로
        self._compose_modifiers(run)         # (Phase 4, D6) modifier 를 실행 사본 위에서 합성
        # @IReturn 지시자는 이번 회차 입력이다 → 사본으로 넘긴 뒤 live 에서 비운다(다음 회차 누수 방지).
        fcfg.ireturn_registry = {}
        # 이전 해석 흔적 제거: 노드 env·before_envs·return_vals 는 이번 실행이 새로 만든다.
        for blk in run.graph.nodes:
            blk.variables = {}
            blk.in_variables = None
            blk.before_envs = {}
            blk.return_vals = {}
            for st in blk.statements:
                ln = getattr(st, "src_line", None)
                if ln is not None and ln in an.analysis_per_line:
                    an.analysis_per_line[ln].clear()

        try:
            an.current_target_function = run.function_name
            an.current_target_function_cfg = run
            self._record_enabled = True
            an._seen_stmt_ids.clear()
            # ★ N4/S1. 이번 해석에서 떠낼 callee 상태를 새로 모은다(이전 회차 잔류 방지).
            #   역색인도 무효화 — 의존성이 추가됐을 수 있다.
            an._frozen_callee_states = {}
            an._fcfg_owner_index = None

            # debug-patched related_variables로 assign_env / entry_env 갱신
            run.assign_env = VariableEnv.copy_variables(run.related_variables)
            run.entry_env = VariableEnv.copy_variables(run.related_variables)

            start_block = run.get_entry_node()     # (2026-10-23) 진입 환경 = ENTRY (_interpret_callee_body 주석 참조)

            start_block.variables = VariableEnv.copy_variables(run.related_variables)

            if caller_env is not None:
                for k, v in caller_env.items():
                    if k not in start_block.variables:
                        start_block.variables[k] = VariableEnv.copy_single_variable(v)

            self._call_frames = [self._func_key(run)]     # (2026-10-23) 재귀 감지 — 타깃 함수가 바닥 프레임
            try:
                return_values = self._run_worklist(run, start_block)
            finally:
                self._call_frames = []

            self._force_join_before_exit(run)
            self._restore_all_shadows(run.get_exit_node().variables)   # (Phase 3b, D8)
            self._sync_named_return_vars(run)
            self._classify_unvisited(run)                               # (Phase 3c, D2)

            # ═══ Post Annotation 처리 ═══
            self._process_post_annotations(run)

            # engine_api(IQDebug): 계산된 states 를 가진 사본을 freeze 결과로 넘긴다.
            if getattr(an, "_capture_debug_fcfg", False):
                an._frozen_debug_fcfg = run
        finally:
            an.current_target_function = _old_func
            an.current_target_function_cfg = _old_fcfg
            self._record_enabled = _old_record_enabled

        # 반환값 (기록 포함)
        self._record_enabled = True
        try:
            return self._extract_return_value(run, return_values, log_implicit=True)
        finally:
            self._record_enabled = _old_record_enabled

    # =================================================================
    #  공통 헬퍼
    # =================================================================
    # ── N4/S1. callee 상태 보존 ─────────────────────────────────────────
    def _fcfg_owner(self, fcfg) -> tuple:
        """`fcfg` 가 속한 (컨트랙트명, 함수명).

        (2026-10-23) 등록 시점에 `ContractCFG/LibraryCFG.add_function_cfg` 가 단 `owner_contract` 를 읽는다.
          예전엔 `contract_cfgs` 를 훑어 **객체 identity 역색인**을 만들었는데, 의존성 pkl 은 컨트랙트마다 따로 저장돼
          부모(`parent_cfgs`)·using 라이브러리 CFG 가 **사본으로 중복** 로드되므로, 그 사본으로 실행되는 callee 는
          색인에 없어 조용히 (None, 함수명) 이 됐다(callee Step Into 누락, library 안 using-for 조회 실패 — 31_H_01).
        """
        fcfg = getattr(fcfg, "_live_origin", fcfg)
        owner = getattr(fcfg, "owner_contract", None)
        if owner is None:
            raise ValueError(f"function '{getattr(fcfg, 'function_name', '?')}' has no owner_contract "
                             f"(의존성 pkl 이 2026-10-23 이전 빌드 — build_project_deps 로 다시 빌드)")
        return owner, fcfg.function_name

    @staticmethod
    def _call_ordinal(site, call_expr) -> int:
        """호출 지점 노드 안에서 `call_expr` 가 몇 번째 호출 식인가(전위 순회, 0부터). pickle 사본 사이에서도 같은 값이라
        실행 회차가 달라도 안정적이다. 노드에서 찾지 못하면 오류 — 캡처 키가 엉뚱한 호출에 붙는 것을 막는다."""
        calls: list = []
        seen: set = set()

        def walk(e):
            if isinstance(e, (list, tuple)):
                for x in e:
                    walk(x)
                return
            if isinstance(e, dict):
                for x in e.values():
                    walk(x)
                return
            if not isinstance(e, Expression) or id(e) in seen:
                return
            seen.add(id(e))
            if getattr(e, "function", None) is not None:
                calls.append(e)
            for v in vars(e).values():
                if isinstance(v, (Expression, list, tuple, dict)):
                    walk(v)

        for st in getattr(site, "statements", []) or []:
            for v in vars(st).values():
                if isinstance(v, (Expression, list, tuple, dict)):
                    walk(v)
        if getattr(site, "condition_expr", None) is not None:
            walk(site.condition_expr)
        for i, c in enumerate(calls):
            if c is call_expr:
                return i
        raise ValueError(f"callee capture: call expression not found in call-site node '{getattr(site, 'name', '?')}'")

    def _capture_callee_states(self, fcfg, call_site_node, call_expr=None) -> None:
        """callee 노드 상태를 `{노드명: {변수: 직렬화값}}` 으로 떠내 analyzer 에 적재한다.

        ★ fcfg 를 pickle 하지 않는다 — 프론트가 필요한 건 이 dict 뿐이고, 구조(노드·엣지)는
          호출 횟수와 무관하게 한 번만 직렬화하면 된다(`engine_api` 가 live fcfg 에서 읽는다).
          전수 pickle 은 측정상 evaluate 의 2%·1.5MB 였지만 호출 수에 선형으로 늘어난다.

        ★ 키 = **호출 지점 + callee**. 함수 이름만으로 키를 잡으면 같은 callee 를 두 지점에서
          부를 때 마지막 호출이 앞을 덮어 `half(i)` 에 Step Into 했는데 `half(acc)` 의 상태가
          보인다. 같은 호출 지점의 반복 해석(fixpoint)은 **마지막 = 수렴값**이 남는 게 맞다.
        """
        an = self.an
        store = getattr(an, "_frozen_callee_states", None)
        if store is None:
            store = an._frozen_callee_states = {}
        cname, fname = self._fcfg_owner(fcfg)
        # (2026-10-22) 노드 식별 = uid (이름은 줄 이동 시 중복될 수 있다) + 노드 안 호출 순번
        site = getattr(call_site_node, "uid", None) or "?"
        ordinal = self._call_ordinal(call_site_node, call_expr) if (call_site_node is not None
                                                                     and call_expr is not None) else 0

        def _flat(env) -> dict[str, str]:
            flat: dict[str, str] = {}
            for k, var in (env or {}).items():
                if self.SHADOW_SEP in k:      # (Phase 3b) 보관된 바깥 binding — 내부용
                    continue
                self.rec._flatten_var(var, k, flat)
            return {kk: ("⊥" if vv == "[None,None]" else vv) for kk, vv in flat.items()}

        states: dict[str, dict[str, str]] = {}
        in_states: dict[str, dict[str, str]] = {}
        for n in fcfg.graph.nodes():
            out = _flat(getattr(n, "variables", {}))
            if out:
                states[n.uid] = out
            inn = getattr(n, "in_variables", None)
            if inn is not None:
                in_states[n.uid] = _flat(inn)
        store[(site, ordinal, cname, fname)] = {"state": states, "in_state": in_states}

    def _reset_node_vars(self, fcfg: FunctionCFG) -> dict:
        """node variables 백업 및 초기화. 백업 dict 반환."""
        an = self.an
        saved = {blk: dict(blk.variables) for blk in fcfg.graph.nodes}
        for blk in fcfg.graph.nodes:
            blk.variables = {}
            blk.in_variables = None
            for st in blk.statements:
                ln = getattr(st, "src_line", None)
                if ln is not None and ln in an.analysis_per_line:
                    an.analysis_per_line[ln].clear()
        return saved

    # ── (Phase 4, D6) modifier 합성 ─────────────────────────────────────────
    #   구성 시점엔 `fcfg.modifier_invocations = [(이름, [인자 식])]` 만 있다. 디버깅 해석 때 실행 사본 위에서
    #   f() M1 M2 { B }  →  M1( M2( B ) ) 로 그래프를 합성한다:
    #     ENTRY → [Mi 인자 바인딩] → Mi 본문 … `_` 자리에 안쪽(B 또는 안쪽 modifier) … Mi 나머지 → EXIT
    #   안쪽의 정상 종료(EXIT/RETURN 으로 가던 엣지)는 `_` 다음으로, Mi 의 require 실패는 함수 ERROR 로.
    #   modifier 파라미터·지역변수는 `이름$변수` 로 바꿔 함수 쪽 이름과 섞이지 않게 한다.
    def _compose_modifiers(self, fcfg: FunctionCFG) -> None:
        import pickle as _pickle
        invs = list(getattr(fcfg, "modifier_invocations", None) or [])
        if not invs:
            return
        an = self.an
        ccf = an.contract_cfgs.get(an.current_target_contract)
        G = fcfg.graph
        fn_entry, fn_exit = fcfg.get_entry_node(), fcfg.get_exit_node()
        fn_ret, fn_err = fcfg.get_return_exit_node(), fcfg.get_error_exit_node()

        for k_app, (name, args) in enumerate(reversed(invs)):
            if name in an.contract_cfgs:            # 생성자의 기반 생성자 호출 — modifier 아님 (Phase 7)
                continue
            mod = ccf.get_function_cfg(name) if ccf is not None else None
            if mod is None and ccf is not None:
                mod = an._find_in_ancestors(ccf, name)
            if mod is None or getattr(mod, "function_type", None) != "modifier":
                raise ValueError(f"modifier '{name}' is not defined")
            params = list(getattr(mod, "parameters", []) or [])
            if len(args) != len(params):
                raise ValueError(f"modifier '{name}': expected {len(params)} arguments, got {len(args)}")

            clone = _pickle.loads(_pickle.dumps(mod))
            CG = clone.graph
            m_entry, m_exit = clone.get_entry_node(), clone.get_exit_node()
            m_ret, m_err = clone.get_return_exit_node(), clone.get_error_exit_node()
            placeholders = [n for n in CG.nodes if n.name.startswith("MOD_PLACEHOLDER")]
            if len(placeholders) != 1:
                raise ValueError(f"modifier '{name}': {len(placeholders)} placeholders '_' — only exactly one is supported")
            ph = placeholders[0]

            # 이름 바꾸기: 파라미터 + modifier 안에서 선언된 지역변수
            local_names = set(params)
            for n in CG.nodes:
                for st in n.statements:
                    if st.statement_type == "variableDeclaration":
                        local_names.add(st.var_name)
            mapping = {v: f"{name}${v}" for v in local_names}
            for n in CG.nodes:
                n.name = f"{name}::{n.name}"
                n.uid = f"{name}#{k_app}:{n.uid}"     # (2026-10-22) 적용 회차별로 구분되는 결정적 ID
                for st in n.statements:
                    self._rename_in_statement(st, mapping)
                if getattr(n, "condition_expr", None) is not None:
                    self._rename_in_expr(n.condition_expr, mapping)

            # 인자 바인딩 노드: `uint v$gt = <인자 식>` (함수 진입 env 에서 평가된다)
            # 인자는 함수 헤더(modifier 호출 위치)에서 평가된다 → 그 줄로 기록
            hdr_line = self._function_start_line(fcfg)
            bind = CFGNode(f"{name}::ARGS", src_line=hdr_line)
            bind.uid = f"{name}#{k_app}:ARGS"        # 실행마다 새로 만들어지는 노드 — ID 는 결정적으로
            for pname, a in zip(params, args):
                ptype = mod.related_variables[pname].typeInfo
                bind.add_variable_declaration_statement(ptype, mapping[pname], a, hdr_line)

            # 안쪽(현재 함수 몸체) 경계
            (inner_start,) = list(G.successors(fn_entry))
            inner_exits = [(u, v) for v in (fn_exit, fn_ret) for u in list(G.predecessors(v))]
            (ph_succ,) = list(CG.successors(ph)) if CG.out_degree(ph) == 1 else (None,)
            if ph_succ is None:
                raise ValueError(f"modifier '{name}': placeholder must have exactly one successor")

            # modifier 노드 편입
            for n in CG.nodes:
                if n in (m_entry, m_exit, m_ret, m_err, ph):
                    continue
                G.add_node(n)
            G.add_node(bind)
            G.remove_edge(fn_entry, inner_start)
            G.add_edge(fn_entry, bind)

            def _map(n):
                if n is m_exit or n is m_ret:
                    return fn_exit
                if n is m_err:
                    return fn_err
                return n
            for u, v, data in list(CG.edges(data=True)):
                if v is ph:
                    G.add_edge(_map(u) if u is not m_entry else bind, inner_start)
                    continue
                if u is ph:
                    continue
                src = bind if u is m_entry else _map(u)
                G.add_edge(src, _map(v), **data)
            # 안쪽의 정상 종료 → `_` 다음 (`_` 가 modifier 의 마지막이면 곧 함수 종료)
            after = _map(ph_succ)
            for u, v in inner_exits:
                if u is bind:
                    continue
                G.remove_edge(u, v)
                G.add_edge(u, after)

    def _rename_in_statement(self, st, mapping: dict) -> None:
        if getattr(st, "statement_type", None) == "variableDeclaration" and st.var_name in mapping:
            st.var_name = mapping[st.var_name]
        for attr in ("left", "right", "init_expr", "return_expr", "function_expr", "operand"):
            e = getattr(st, attr, None)
            if e is not None:
                self._rename_in_expr(e, mapping)
        for e in (getattr(st, "arguments", None) or []):
            self._rename_in_expr(e, mapping)

    def _rename_in_expr(self, e, mapping: dict, _seen=None) -> None:
        if _seen is None:
            _seen = set()
        if isinstance(e, (list, tuple)):
            for x in e:
                self._rename_in_expr(x, mapping, _seen)
            return
        if isinstance(e, dict):
            for x in e.values():
                self._rename_in_expr(x, mapping, _seen)
            return
        if not isinstance(e, Expression) or id(e) in _seen:
            return
        _seen.add(id(e))
        if getattr(e, "context", None) == "IdentifierExpContext" and getattr(e, "identifier", None) in mapping:
            e.identifier = mapping[e.identifier]
        for v in vars(e).values():
            if isinstance(v, (Expression, list, tuple, dict)):
                self._rename_in_expr(v, mapping, _seen)

    def _composed_callee(self, fcfg: FunctionCFG) -> FunctionCFG:
        """modifier 가 붙은 callee 의 합성본(실행 1 회 동안 캐시). 원본은 건드리지 않는다."""
        if not getattr(fcfg, "modifier_invocations", None):
            return fcfg
        import pickle as _pickle
        cache = getattr(self, "_composed_cache", None)
        if cache is None:
            cache = self._composed_cache = {}
        hit = cache.get(id(fcfg))
        if hit is None:
            hit = _pickle.loads(_pickle.dumps(fcfg))
            hit._live_origin = fcfg
            self._compose_modifiers(hit)
            cache[id(fcfg)] = hit
        return hit

    def _classify_unvisited(self, fcfg: FunctionCFG) -> None:
        """(Phase 3c, D2) 해석 종료 후 "값이 없는" 노드 정리 — 사용자에게 '평가 안 됨' 이 ⊥ 처럼 보이지 않게.

        - ENTRY 에서 그래프상 경로가 없는 노드(`return`/`revert` 뒤 dead code 등) → 명시적 ⊥ (도달 불가).
        - 경로가 있는데 해석되지 않은 노드 → 해석기 버그이므로 예외(조용히 빈 상태로 내보내지 않는다).
        sink(EXIT/RETURN/ERROR)·ENTRY 는 상태를 따로 다루므로 제외한다.
        """
        G = fcfg.graph
        entry = fcfg.get_entry_node()

        def _sink(n):
            return (getattr(n, "function_exit_node", False) or getattr(n, "return_exit_node", False)
                    or getattr(n, "error_exit_node", False))

        # 도달성은 sink 에서 멈춘다 — sink 는 해석의 종착점이다(그 뒤에 붙은 노드는 해석 대상이 아니다).
        reach, stack = set(), [entry]
        while stack:
            u = stack.pop()
            for v in G.successors(u):
                if v not in reach:
                    reach.add(v)
                    if not _sink(v):
                        stack.append(v)
        for n in G.nodes:
            if n is entry or n.variables or _sink(n):
                continue
            if n in reach:
                raise ValueError(f"interpreter invariant: node '{n.name}' is reachable from ENTRY "
                                 f"but was not interpreted")
            bottom = VariableEnv.copy_variables(fcfg.related_variables)
            self._set_bottom_env(bottom)
            n.variables = bottom

    # ── (Phase 3b, D8) 블록 스코프 ─────────────────────────────────────────
    #   env 는 이름 → Variables 이므로, 블록이 끝나는 노드(분기 join, 루프 exit, 루프 back-edge)에서
    #   "블록에 들어가기 전에 있던 이름" 만 남긴다. 블록 안에서 바깥 이름을 다시 선언(shadowing)한 경우엔
    #   선언 시점에 바깥 binding 을 `name␟shadow@<id>` 키로 보관해 두었다가 여기서 되돌린다.
    SHADOW_SEP = "\x1f"

    def _scope_keys(self, G, anchor) -> set:
        """블록 진입 시점의 이름 집합. anchor = if/else-if cond(그 env = 분기 직전) 또는 루프 join(진입 baseline)."""
        if anchor not in G:
            raise ValueError(f"scope anchor '{anchor.name}' is not in the CFG (stale structure after edit)")
        if getattr(anchor, "fixpoint_evaluation_node", False):
            base = getattr(anchor, "join_baseline_env", None)
            if base is None:
                raise ValueError(f"loop scope anchor '{anchor.name}' has no entry baseline (fixpoint not run)")
            keys = set(base)
            # for-init 에서 선언된 변수(`for (uint i …)`)는 루프 블록 소속
            for pp in G.predecessors(anchor):
                if getattr(pp, "name", "").startswith("for_init_"):
                    keys -= {st.var_name for st in pp.statements if st.statement_type == "variableDeclaration"}
            return keys
        return set(anchor.variables or {})

    def _restrict_scope(self, env: dict, keep: set) -> dict:
        """블록 종료: 보관된 바깥 binding 복원 → keep 밖의 이름(블록 지역변수·이 블록의 보관 키) 제거."""
        if not env:
            return env
        sep = self.SHADOW_SEP
        for k in [k for k in env if sep in k and k not in keep]:
            env[k.split(sep, 1)[0]] = env.pop(k)
        for k in [k for k in env if k not in keep]:
            del env[k]
        return env

    def _restore_all_shadows(self, env: dict) -> None:
        """함수 종료: 함수 최상위 블록에서 상태변수 등을 가린 지역변수가 있으면 바깥 binding 복원."""
        if not env:
            return
        sep = self.SHADOW_SEP
        for k in sorted([k for k in env if sep in k], reverse=True):
            env[k.split(sep, 1)[0]] = env.pop(k)

    def _run_worklist(self, fcfg: FunctionCFG, start_block) -> list:
        """CFG worklist 순회. return_values 리스트 반환."""
        G = fcfg.graph

        def _is_sink(n: CFGNode) -> bool:
            if getattr(n, "function_exit_node", False): return True
            if getattr(n, "error_exit_node", False):    return True
            if getattr(n, "return_exit_node", False):   return True
            nm = getattr(n, "name", "")
            return nm in {"EXIT", "ERROR", "RETURN"}

        def _edge_env_from_pred(pred: CFGNode, succ: CFGNode):
            base = VariableEnv.copy_variables(getattr(pred, "variables", {}) or {})
            # BOTTOM env는 refine 불필요 (BOTTOM ⊓ cond = BOTTOM)
            if self._is_bottom_env(base):
                return base
            if getattr(pred, "condition_node", False):
                cond_expr = getattr(pred, "condition_expr", None)
                ed = G.get_edge_data(pred, succ, default=None)
                if cond_expr is not None and ed and "condition" in ed:
                    want_true = bool(ed["condition"])
                    self._call_site_node = pred   # (2026-10-22) 조건식 안의 호출도 호출 지점 = 이 조건 노드 (예전엔 직전 문장 노드가 남아 엉뚱한 노드에 캡처됐다)
                    self.ref.update_variables_with_condition(base, cond_expr, want_true)
                    if not self._branch_feasible(base, cond_expr, want_true):
                        return None
            return base

        work = deque([start_block])
        visited: set[CFGNode] = set()
        return_values = []

        while work:
            node = work.popleft()
            if node in visited: continue

            preds = list(G.predecessors(node))
            if getattr(node, 'join_point_node', False) and preds:
                unvisited_preds = [p for p in preds if p not in visited]
                if unvisited_preds and len(work) > 0:
                    work.append(node)
                    continue

            visited.add(node)

            if preds:
                joined = None
                for p in preds:
                    if p not in visited:
                        continue
                    flow = _edge_env_from_pred(p, node)
                    if flow is None: continue
                    joined = (VariableEnv.copy_variables(flow) if joined is None
                              else VariableEnv.join_variables_simple(joined, flow))
                if joined is not None:
                    node.variables = joined

            # (Phase 3b, D8) 분기 블록이 끝나는 join — 블록 안에서 선언된 이름 제거.
            #   루프 exit 는 fixpoint 직후에 정리한다(여기로 오는 루프 exit 는 루프가 ⊥ 로 건너뛰어진 경우뿐).
            anchor = getattr(node, "scope_anchor", None)
            if anchor is not None and node.variables \
                    and not getattr(anchor, "fixpoint_evaluation_node", False):
                node.variables = self._restrict_scope(node.variables,
                                                      self._scope_keys(G, node.scope_anchor))

            # (2026-10-22) 실행 전 상태(IN) — 프론트 커서의 "이 줄 실행 직전" 표시용.
            #   문장 노드는 아래에서 node.variables 를 제자리에서 갱신(→ OUT)하므로 사본을 둔다.
            #   루프 헤더·φ 는 fixpoint 가 node.variables 를 수렴값으로 바꾸므로 역시 사본 — 이 시점의
            #   값은 back-edge 선행이 아직 미방문이라 **루프 밖에서 들어오는 상태**다(사용자 결정 F2).
            #   문장 없는 join/분기 노드는 IN == OUT 이라 None.
            node.in_variables = (VariableEnv.copy_variables(node.variables)
                                 if (node.statements
                                     or getattr(node, "fixpoint_evaluation_node", False)
                                     or (node.condition_node
                                         and node.condition_node_type in ("while", "for", "do_while")))
                                 else None)

            cur_vars = node.variables
            node.evaluated = True

            if self._is_bottom_env(cur_vars):
                for nxt in list(G.successors(node)):
                    if _is_sink(nxt): continue
                    nxt.variables = VariableEnv.copy_variables(cur_vars)
                    work.append(nxt)
                continue

            if node.condition_node:
                condition_expr = node.condition_expr

                if node.condition_node_type in ["if", "else if"]:
                    true_succs  = [s for s in G.successors(node) if G.edges[node, s].get('condition') is True]
                    false_succs = [s for s in G.successors(node) if G.edges[node, s].get('condition') is False]
                    if len(true_succs) != 1 or len(false_succs) != 1:
                        raise ValueError("if/else-if node must have exactly one true and one false successor.")

                    true_variables  = VariableEnv.copy_variables(cur_vars)
                    false_variables = VariableEnv.copy_variables(cur_vars)
                    self._call_site_node = node   # (2026-10-22) 조건식 안의 호출도 호출 지점 = 이 조건 노드 (예전엔 직전 문장 노드가 남아 엉뚱한 노드에 캡처됐다)
                    self.ref.update_variables_with_condition(true_variables,  condition_expr, True)
                    self.ref.update_variables_with_condition(false_variables, condition_expr, False)

                    can_true  = self._branch_feasible(true_variables,  condition_expr, True)
                    can_false = self._branch_feasible(false_variables, condition_expr, False)

                    if not can_true and not can_false:
                        continue

                    t = true_succs[0]; f = false_succs[0]
                    if can_true:
                        t.variables = true_variables;  work.append(t)
                    else:
                        t.variables = VariableEnv.copy_variables(cur_vars)
                        self._set_bottom_env(t.variables); work.append(t)
                    if can_false:
                        f.variables = false_variables; work.append(f)
                    else:
                        f.variables = VariableEnv.copy_variables(cur_vars)
                        self._set_bottom_env(f.variables); work.append(f)
                    continue

                elif node.condition_node_type in ["require", "assert"]:
                    (t,) = [s for s in G.successors(node) if G.edges[node, s].get('condition') is True]
                    true_variables = VariableEnv.copy_variables(cur_vars)
                    self._call_site_node = node   # (2026-10-22) 조건식 안의 호출도 호출 지점 = 이 조건 노드 (예전엔 직전 문장 노드가 남아 엉뚱한 노드에 캡처됐다)
                    self.ref.update_variables_with_condition(true_variables, condition_expr, True)
                    can_true = self._branch_feasible(true_variables, condition_expr, True)

                    if can_true:
                        t.variables = true_variables; work.append(t)
                    else:
                        # ★ 가드가 통과 불가 = 이후 경로 전체가 도달 불가.
                        #   위 if/else-if 분기와 동일한 3단 패턴으로 맞춘다:
                        #     ① cur_vars 로 seed  ② BOTTOM 으로  ③ worklist 전파.
                        #   ①이 없으면 t.variables 가 아직 {} 라 _set_bottom_env 가
                        #   no-op(빈 dict 를 순회) 이고, ③이 없으면 BOTTOM 이 downstream
                        #   으로 전혀 퍼지지 않아 이후 노드·EXIT 가 전부 빈 {} 로 남는다.
                        #   빈 {} 는 _is_bottom_env 가 False 를 주므로 도달불가를 놓치고,
                        #   @Post 는 "internal error: ... may not be declared" 로 둔갑했다.
                        t.variables = VariableEnv.copy_variables(cur_vars)
                        self._set_bottom_env(t.variables)
                        work.append(t)

                    self._process_node_intents(node, cur_vars, getattr(node, 'src_line', None))
                    continue

                elif node.condition_node_type in ["while", "for", "do_while"]:
                    exit_node = self.fixpoint(node)
                    # (Phase 3b, D8) 루프 블록(본문·for-init) 변수는 루프 밖에서 사라진다
                    if getattr(exit_node, "scope_anchor", None) is not None and exit_node.variables:
                        exit_node.variables = self._restrict_scope(
                            exit_node.variables, self._scope_keys(G, exit_node.scope_anchor))
                    # ★ loop-exit 도 **처리 완료**로 표시한다. `fixpoint` 가 그 env 를 이미
                    #   확정했으므로(`exit_node.variables`) 뒤의 join 이 그대로 쓰면 된다.
                    #
                    # ⚠ 이게 없으면 루프 **뒤에 join 노드가 오는 모양**에서 무한 루프가 된다.
                    #   위 join 지연 로직(`unvisited_preds and len(work) > 0` → 재큐잉)이
                    #   loop-exit 를 영원히 "미방문 선행" 으로 보기 때문이다. work 에 같은
                    #   join 이 두 개 쌓이면 서로 `len(work) > 0` 을 만족시켜 빠져나오지 못한다.
                    #   ⑭ 이전에는 `for` 가 블록 안에서 열릴 때 loop-exit 가 join 의 선행이
                    #   **아니었기 때문에**(배선이 끊겨 있었다) 이 결함이 가려져 있었다.
                    #   `for`-in-`if` 로 재현됨 — decision #49.
                    visited.add(exit_node)
                    _succs = list(G.successors(exit_node))
                    for nxt in _succs:
                        if _is_sink(nxt): continue
                        nxt.variables = VariableEnv.copy_variables(exit_node.variables)
                        work.append(nxt)
                    continue

                elif node.condition_node_type == "try":
                    t_succs = [s for s in G.successors(node) if G.edges[node, s].get('condition') is True]
                    f_succs = [s for s in G.successors(node) if G.edges[node, s].get('condition') is False]
                    for s in (t_succs + f_succs):
                        if _is_sink(s): continue
                        s.variables = VariableEnv.copy_variables(cur_vars)
                        work.append(s)
                    continue

                else:
                    raise ValueError(f"Unknown condition node type: {node.condition_node_type}")

            elif getattr(node, "is_for_increment", False):
                for stmt in node.statements:
                    cur_vars = self.update_statement_with_variables(stmt, cur_vars, return_values, node=node)
                for succ in G.successors(node):
                    if _is_sink(succ): continue
                    succ.variables = VariableEnv.copy_variables(node.variables)
                    work.append(succ)
                continue

            else:
                if "__STOP__" in return_values:
                    return_values.remove("__STOP__")

                for stmt in node.statements:
                    cur_vars = self.update_statement_with_variables(stmt, cur_vars, return_values, node=node)

                    # ═══ During Annotation 처리 ═══
                    self._process_during_annotations(stmt, node, cur_vars)

                    if "__STOP__" in return_values:
                        break

                for nxt in list(G.successors(node)):
                    if _is_sink(nxt): continue
                    nxt.variables = VariableEnv.copy_variables(cur_vars)
                    work.append(nxt)

        return return_values

    def _extract_return_value(self, fcfg: FunctionCFG, return_values: list, log_implicit: bool = False):
        """return_values 리스트에서 최종 반환값을 추출."""
        rec = self.rec

        def _log_implicit_return(var_objs: list[Variables]):
            if not log_implicit or not self._record_enabled:
                return
            ln = self._last_executable_line(fcfg)
            if ln is None: return
            if len(var_objs) == 1:
                rec.record_return(line_no=ln, return_expr=None, return_val=var_objs[0].value, fn_cfg=fcfg)
            else:
                env_dict = {v.identifier: v for v in var_objs}
                self.rec.add_env_record(ln, "implicitReturn", env_dict)

        if len(return_values) == 0:
            if fcfg.return_vars:
                _log_implicit_return(fcfg.return_vars)
                # ★ composite(Array/Struct/Mapping) named return 은 `.value` 가 아니라
                # 객체 자체를 리턴해야 함 — 실제 데이터는 `.elements`/`.members`/`.mapping`
                # 에 있고 `.value` 는 기본값 None 그대로다. 예전엔 무조건 `.value` 라서
                # explicit return 없이 named return var 만 채우고 끝나는 함수가 항상
                # None 을 리턴했다.
                def _rv_value(rv):
                    if isinstance(rv, (StructVariable, ArrayVariable, MappingVariable)):
                        return rv
                    return rv.value
                if len(fcfg.return_vars) == 1:
                    return _rv_value(fcfg.return_vars[0])
                return [_rv_value(rv) for rv in fcfg.return_vars]
            else:
                exit_retvals = list(fcfg.get_return_exit_node().return_vals.values())
                if exit_retvals:
                    joined = exit_retvals[0]
                    for v in exit_retvals[1:]:
                        joined = self._join_return_value(joined, v, fcfg)
                    return joined
                return None
        elif len(return_values) == 1:
            return return_values[0]
        else:
            filtered_values = [rv for rv in return_values if not (isinstance(rv, str) and rv == "__STOP__")]
            if not filtered_values:
                return None
            joined_ret = filtered_values[0]
            for rv in filtered_values[1:]:
                joined_ret = self._join_return_value(joined_ret, rv, fcfg)
            return joined_ret

    def _join_return_value(self, a, b, fcfg: FunctionCFG):
        """(2026-10-23) 두 `return` 경로의 반환값 join.
        예전엔 `.join` 이 없는 값(tuple = 파이썬 list, 복합 변수)을 만나면 **첫 경로 값을 그대로** 돌려줬다 —
        `return (1, a)` / `return (2, b)` 인 함수가 늘 첫 tuple 만 반환했다."""
        if a is None and b is None:
            return None
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                raise ValueError(f"return join in '{fcfg.function_name}': tuple arity {len(a)} vs {len(b)}")
            return [self._join_return_value(x, y, fcfg) for x, y in zip(a, b)]
        if isinstance(a, (StructVariable, ArrayVariable, MappingVariable)) and type(a) is type(b):
            return VariableEnv._merge_values(a, b, "join")
        if isinstance(a, Variables) and isinstance(b, Variables):
            a, b = a.value, b.value
        if type(a) is type(b) and hasattr(a, "join"):
            return a.join(b)
        raise ValueError(f"return join in '{fcfg.function_name}': cannot join "
                         f"{type(a).__name__} with {type(b).__name__}")

    # =================================================================
    #  Helpers (branch feasible, bottom, EXIT sync, line utils)
    # =================================================================
    def _branch_feasible(self, env: dict, cond: Expression, assume_true: bool) -> bool:
        r = self.eval.evaluate_expression(cond, env, None, None)
        if isinstance(r, BoolInterval):
            result = (r.max_value == 1) if assume_true else (r.min_value == 0)
            return result
        if VariableEnv.is_interval(r):
            as_bool = VariableEnv.convert_int_to_bool_interval(r)
            result = (as_bool.max_value == 1) if assume_true else (as_bool.min_value == 0)
            return result
        return True

    def _is_bottom_env(self, env: dict) -> bool:
        """env가 infeasible branch를 나타내는지 검사 (하나라도 BOTTOM이면 infeasible)."""
        for v in env.values():
            if self._is_bottom_var(v):
                return True
        return False

    def _is_bottom_var(self, v) -> bool:
        """Variable 하나(또는 하위 원소)가 BOTTOM인지 재귀 검사."""
        if isinstance(v, ArrayVariable):
            return any(self._is_bottom_var(e) for e in v.elements.values()) or \
                (v.summary is not None and self._is_bottom_var(v.summary))
        if isinstance(v, StructVariable):
            return any(self._is_bottom_var(m) for m in v.members.values())
        if isinstance(v, MappingVariable):
            return any(self._is_bottom_var(mv) for mv in v.mapping.values())
        val = getattr(v, "value", None)
        if val is not None and hasattr(val, "is_bottom"):
            return val.is_bottom()
        return False

    def _set_bottom_env(self, env: dict[str, Variables]) -> None:
        for v in env.values():
            self._make_bottom(v)

    def _make_bottom(self, v: Variables) -> None:
        if isinstance(v, ArrayVariable):
            for elem in v.elements.values():
                self._make_bottom(elem)
            z = v.summary if v.summary is not None else v.fresh_default()
            self._make_bottom(z)
            v.summary = z
            return
        if isinstance(v, StructVariable):
            for m in v.members.values():
                self._make_bottom(m)
            return
        if isinstance(v, MappingVariable):
            for mv in v.mapping.values():
                self._make_bottom(mv)
            return
        val = getattr(v, "value", None)
        if isinstance(val, UnsignedIntegerInterval):
            v.value = UnsignedIntegerInterval.bottom(val.type_length); return
        if isinstance(val, IntegerInterval):
            v.value = IntegerInterval.bottom(val.type_length); return
        if isinstance(val, BoolInterval):
            v.value = BoolInterval.bottom(); return
        from Domain.AddressSet import AddressSet
        if isinstance(val, AddressSet):
            bot = AddressSet.bot()
            bot._cast_interface = getattr(val, '_cast_interface', None)
            v.value = bot; return
        from Domain.BytesSet import BytesSet
        if isinstance(val, BytesSet):              # (Phase 2) bytesN 도 고유 ⊥ 를 가진다
            v.value = BytesSet.bot(val.byte_size); return
        v.value = None

    @staticmethod
    def normal_completion_preds(fcfg: FunctionCFG) -> list:
        """함수의 **정상 종료** 경로의 마지막 노드들 = EXIT 의 선행 ∪ RETURN 의 선행 (2026-10-21).

        Solidity 에서 `return` 문 실행과 본문 끝 도달은 똑같이 정상 종료다(호출자는 둘을 구분할 수 없다).
        빌더는 `return` 을 RETURN sink 로 보내고 EXIT 로 잇지 않으므로, EXIT 만 보면 `return` 경로가 빠진다.
        한쪽 종료 방식이 없는 함수(예: return 문이 없는 함수)는 그쪽 선행이 0개라 자연히 빠진다.
        (revert/require 실패는 ERROR sink 로 가므로 여기에 들어오지 않는다.)"""
        g = fcfg.graph
        preds = []
        for sink in (fcfg.get_exit_node(), fcfg.get_return_exit_node()):
            if sink is not None and sink in g:
                preds.extend(p for p in g.predecessors(sink) if p not in preds)
        return preds

    def _force_join_before_exit(self, fcfg: FunctionCFG) -> None:
        """EXIT 노드의 환경 = 정상 종료 상태 (2026-10-21, 사용자 승인).

        - 정상 종료 선행(EXIT ∪ RETURN, `normal_completion_preds`) 중 **도달 가능한(⊥ 아닌)** 것만 join 한다.
          예전엔 EXIT 선행만, 그것도 ⊥ 환경까지 join 해서 (1) `return` 으로 끝나는 callee 의 상태 변경이 호출자에
          전달되지 않았고, (2) 조기 return 만 실제로 가능한 callee(PRBMath.mulDivFixedPoint)는 EXIT 가 ⊥ 가 되어
          호출자 경로를 죽였다(16_H_04·16_H_06).
        - 도달 가능한 정상 종료가 하나도 없으면 **명시적으로 ⊥ 환경**을 둔다. 빈 `{}` 를 두면 호출자 되쓰기가
          아무것도 하지 않아, 절대 돌아오지 않는 호출 뒤를 호출 전 상태로 계속 해석하게 된다.
        - RETURN/EXIT 노드 자체는 그래프에서 분리된 채 둔다(디버거 표시·출처 추적용). 합치기는 계산으로만 한다."""
        exit_node = fcfg.get_exit_node()
        preds = self.normal_completion_preds(fcfg)
        live = [p for p in preds
                if (getattr(p, "variables", None) or {}) and not self._is_bottom_env(p.variables)]
        if live:
            joined_env = None
            for p in live:
                joined_env = VariableEnv.join_variables_simple(joined_env, p.variables)
            exit_node.variables = joined_env
            return
        # 정상 종료 불가 → ⊥ 환경 (선행의 변수 구성이 있으면 그걸, 없으면 함수의 관련 변수를 바탕으로)
        base = next((p.variables for p in preds if getattr(p, "variables", None)), None) \
            or getattr(fcfg, "related_variables", {}) or {}
        bottom = VariableEnv.copy_variables(base)
        self._set_bottom_env(bottom)
        exit_node.variables = bottom

    def _sync_named_return_vars(self, fcfg: FunctionCFG) -> None:
        exit_env = fcfg.get_exit_node().variables
        for i, rv in enumerate(fcfg.return_vars or []):
            if rv.identifier in exit_env:
                src = exit_env[rv.identifier]
                if hasattr(rv, "value"): rv.value = getattr(src, "value", src)
                else: fcfg.return_vars[i] = src

    def _last_executable_line(self, fcfg: FunctionCFG) -> int | None:
        rng = self._function_body_range(fcfg)
        if rng is None: return None
        body_start, body_end = rng
        for ln in range(body_end, body_start - 1, -1):
            code = self.an.sa.full_code_lines.get(ln, "").strip()
            if not code or code == "}" or code.startswith("//"): continue
            return ln
        return None

    def _function_start_line(self, fcfg: FunctionCFG) -> int | None:
        # 실행용 사본(interpret_function_cfg_for_debug)이면 live 원본의 ENTRY 로 찾는다.
        entry = getattr(fcfg, "_live_origin", fcfg).get_entry_node()
        for ln, info in (self.an.sa.line_info or {}).items():
            nodes = []
            if isinstance(info.get("cfg_nodes"), list): nodes.extend(info["cfg_nodes"])
            if entry in nodes: return ln
        return None

    def _function_body_range(self, fcfg: FunctionCFG) -> tuple[int, int] | None:
        li = self.an.sa.line_info or {}
        fn_start_ln = self._function_start_line(fcfg)
        if fn_start_ln is None: return None
        def _oc(ln: int) -> tuple[int, int]:
            info = li.get(ln, {}); return int(info.get("open", 0)), int(info.get("close", 0))
        start_balance = 0
        for ln in range(1, fn_start_ln):
            o, c = _oc(ln); start_balance += (o - c)
        body_start = None; balance = start_balance
        max_ln = max(li.keys()) if li else fn_start_ln
        for ln in range(fn_start_ln, max_ln + 1):
            o, c = _oc(ln); balance += (o - c)
            if balance == start_balance + 1 and body_start is None:
                body_start = ln + 1
            if balance == start_balance and body_start is not None:
                return (body_start, ln - 1)
        return None

    # Engine 클래스 내부에 추가
    def traverse_loop_nodes(self, loop_head: CFGNode) -> set[CFGNode]:
        """
        loop_head(while/for/do-while 의 조건 노드)에서 전진하며 루프 본문 노드를 수집한다.
        절단점은 **이 루프 자신의 exit** = 자기 헤더의 False 엣지가 가리키는 노드뿐이다.

        ⚠ 예전에는 `loop_exit_node` **플래그만** 보고 절단했다. 그러면 중첩 루프에서
          **내부 루프의 exit** 이 바깥 루프 본문 한가운데 놓이므로 거기서 탐색이 멈췄고,
          그 뒤에 있는 `for_incr_*`(증감)과 back edge 가 바깥 루프의 `loop_nodes` 에서
          빠졌다. `fixpoint` 의 전파 조건이 `if succ not in loop_nodes: continue` 이라
          **바깥 fixpoint 가 1회만 돌았다**(⑮ / decision #49).

        ★ 이 방식은 Bourdoncle 분류의 **flat(iterative) chaotic iteration** 이다.
          바깥 worklist 가 중첩 루프를 평평하게 함께 반복한다. 건전하고 빠르지만,
          안쪽 루프의 back edge 가 **이미 widen 된 바깥 변수**를 안쪽 join 으로 되돌려
          넣기 때문에 바깥 루프 카운터가 ⊤ 로 남을 수 있다(알려진 정밀도 비용).
          대안인 hierarchical(recursive) 방식은 2중 루프에서 14배 느리고(47s) 증분 빌드
          중간 상태에서 터져서 채택하지 않았다 — 상세는 decision #49.
        """
        G = self.an.current_target_function_cfg.graph
        own_exit = {s for s in G.successors(loop_head)
                    if G[loop_head][s].get("condition") is False}
        visited: set[CFGNode] = set()
        stack = [loop_head]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for succ in G.successors(cur):
                if succ in own_exit:   # 이 루프의 탈출구 — 바깥이다
                    continue
                stack.append(cast(CFGNode, succ))
        return visited

    def is_node_in_loop(self, node: CFGNode, loop_head: CFGNode) -> bool:
        """
        node 가 loop_head 로 정의된 루프 본문에 속하는지 확인.

        ⚠ `loop_exit_node` 선행 검사를 제거했다 — 자기 exit 는 `traverse_loop_nodes` 가
          이미 제외하고, **내부 루프의 exit 는 바깥 루프 본문에 속한다**(⑮).
        (현재 호출자 없음.)
        """
        return node in self.traverse_loop_nodes(loop_head)

    def find_loop_exit_node(self, loop_head: CFGNode) -> CFGNode:
        """
        (가능하면) 헤더의 False-edge 로 표시된 loop-exit 를 먼저 사용하고,
        없다면 traverse 기반으로 '루프 내부 노드들의 후속 중 루프 밖에 있는 유일한 노드'를 찾는다.
        여러 후보면 loop_exit_node=True 를 우선, 그래도 애매하면 오류.
        """
        G = self.an.current_target_function_cfg.graph

        # 1) 가장 신뢰도 높은 경로: 헤더의 False-edge + loop_exit_node=True
        for succ in G.successors(loop_head):
            if G[loop_head][succ].get("condition") is False and getattr(succ, "loop_exit_node", False):
                return cast(CFGNode, succ)

        # 2) traverse 로 루프 내부 집합을 만든 뒤 바깥으로 나가는 successor 를 후보로 수집
        loop_nodes = self.traverse_loop_nodes(loop_head)
        exit_candidates: set[CFGNode] = set()
        for n in loop_nodes:
            for succ in G.successors(n):
                succ = cast(CFGNode, succ)
                if succ not in loop_nodes:
                    exit_candidates.add(succ)

        if not exit_candidates:
            raise ValueError("loop exit node not found")

        # 3) loop_exit_node=True 가 달린 후보가 하나면 그걸 사용
        flagged = [c for c in exit_candidates if getattr(c, "loop_exit_node", False)]
        if len(flagged) == 1:
            return flagged[0]
        if len(flagged) > 1:
            # 헤더의 False-edge 쪽과 맞닿은 것을 우선적으로 선택 (있으면)
            for c in flagged:
                if loop_head in G.predecessors(c) and G[loop_head][c].get("condition") is False:
                    return c
            raise ValueError(f"ambiguous loop exits: {len(flagged)} candidates with loop_exit_node=True")

        # 4) 플래그가 하나도 없으면,
        #    (a) 헤더 False-edge에서 바로 이어지는 후보가 있으면 그걸 사용
        for c in exit_candidates:
            if loop_head in G.predecessors(c) and G[loop_head][c].get("condition") is False:
                return c
        #    (b) 바깥으로 나간 뒤 다시 루프 안으로 돌아오지 않는 후보를 선호
        for c in exit_candidates:
            if all(succ2 not in loop_nodes for succ2 in G.successors(c)):
                return c
        #    (c) 최후수단: 첫 후보 반환
        return next(iter(exit_candidates))

    # =================================================================
    #  During / Post Annotation 처리
    # =================================================================

    def _process_during_annotations(self, stmt, node, cur_vars):
        """
        Statement 실행 후 해당 라인의 During annotations 검증

        Args:
            stmt: 실행된 Statement 객체
            node: 현재 CFGNode
            cur_vars: 현재 변수 환경
        """
        if not self._record_enabled:
            return

        line_no = getattr(stmt, "src_line", None)
        if line_no is None:
            return

        # ═══ CFG 노드에 저장된 intent 검증 (새로운 메커니즘) ═══
        self._process_node_intents(node, cur_vars, line_no)

        # line_info에서 during_annotations 가져오기 (기존 메커니즘)
        line_info = self.an.sa.line_info.get(line_no, {})
        during_annotations = line_info.get('during_annotations', [])

        if not during_annotations:
            return

        # 각 during annotation 검증
        for annot in during_annotations:
            # (2026-10-23, M1) intent 검증은 실행이 아니다 — 평가 중 callee 를 부르더라도(인자식 `amount.div(12)`) UI 용 callee
            #   상태 캡처를 하지 않는다. 예전엔 캡처가 호출 지점 노드에서 그 호출식을 못 찾아 internal error 가 됐다(BoostToken).
            try:
                with self._no_callee_capture():
                    result = self._verify_during_annotation(annot, node, cur_vars)

                # 결과를 analysis_per_line에 기록
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []

                self.an.analysis_per_line[line_no].append({
                    'type': 'during',
                    'annotation_type': annot.annotation_type,
                    'result': result
                })

            except Exception as e:
                # 에러 발생 시 기록
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []

                self.an.analysis_per_line[line_no].append({
                    'type': 'during',
                    'annotation_type': annot.annotation_type,
                    'result': {
                        'status': 'error',
                        'message': f'During annotation processing error: {str(e)}'
                    }
                })

    @__import__("contextlib").contextmanager
    def _no_callee_capture(self):
        """intent 평가 동안 callee 상태 캡처(UI Step Into 용)를 끈다."""
        old = getattr(self.an, "_capture_debug_fcfg", False)
        self.an._capture_debug_fcfg = False
        try:
            yield
        finally:
            self.an._capture_debug_fcfg = old

    def _process_node_intents(self, node, cur_vars, line_no):
        """
        CFG 노드에 저장된 intent 검증 (새로운 메커니즘)

        Args:
            node: CFGNode (intents 리스트 포함)
            cur_vars: 현재 변수 환경
            line_no: 현재 라인 번호 (코드 라인)
        """
        if not hasattr(node, 'intents') or not node.intents:
            return

        # A2 (PROGRESS decision #40): 이 program point 가 도달불가면 clause 검사 없이 Unreachable.
        # env 의 변수 중 하나라도 BOTTOM = 곱집합 공집합 = 도달하는 구체 실행 없음(건전).
        # _is_bottom_env 는 엔진 자신의 infeasibility 기준(:1148)과 동일 → 분기 pruning 판단과 일관.
        # 한계: 구간 도메인이 bottom 으로 판정한 것만 잡음. 파생 변수 상관 소실(x=a+b 후 x·a 제약)은
        # bottom 이 아니라 놓침(기존 한계, A2 가 악화시키지 않음). during 만 처리(post 는 별도 경로).
        if not cur_vars or self._is_bottom_env(cur_vars):
            for intent in node.intents:
                if intent.get("type") != "during":
                    continue
                report_line = intent.get("line_no", line_no)
                print(f"[INTENT UNREACHABLE] Line {report_line}: 경로 조건상 도달 불가")
                self.an.analysis_per_line.setdefault(report_line, []).append({
                    'type': 'during_intent',
                    'clauses': intent.get("clauses", []),
                    'result': {'status': 'unreachable',
                               'message': 'intent 위치 도달 불가 (경로 조건상 실행되지 않음)'}
                })
            return

        guardian = self.an.guardian_verifier

        for intent in node.intents:
            if intent.get("type") != "during":
                continue

            # standalone annotation은 자체 라인 번호 사용, inline은 코드 라인 번호 사용
            report_line = intent.get("line_no", line_no)

            clauses = intent.get("clauses", [])
            logic_ops = intent.get("logic_ops", [])

            try:
                # 각 clause 검증
                results = []
                with self._no_callee_capture():      # (2026-10-23, M1) intent 검증은 실행이 아니다 — 위 주석 참고
                    for clause in clauses:
                        result = self._verify_during_clause_dynamic(clause, node, cur_vars, guardian, line_no)
                        results.append(result)

                # 논리 연산자로 조합
                combined_result = self._combine_logic_results(results, logic_ops)

                # 결과 출력 (annotation 라인 번호로 표시)
                status = combined_result.get("status", "unknown")
                risk = combined_result.get("risk_score", "")
                risk_str = f" [risk={risk}]" if risk != "" else ""
                if status == "violated":
                    print(f"[INTENT VIOLATED] Line {report_line}: {combined_result.get('message', '')}{risk_str}")
                elif status == "satisfied":
                    print(f"[INTENT SATISFIED] Line {report_line}: {combined_result.get('message', '')}{risk_str}")
                else:
                    print(f"[INTENT {status.upper()}] Line {report_line}: {combined_result.get('message', '')}{risk_str}")

                detail_str = self._format_violation_detail(results, clauses)
                if detail_str:
                    print(detail_str)

                # analysis_per_line에 기록 (annotation 라인 번호 기준)
                if report_line not in self.an.analysis_per_line:
                    self.an.analysis_per_line[report_line] = []

                self.an.analysis_per_line[report_line].append({
                    'type': 'during_intent',
                    'clauses': clauses,
                    'result': combined_result
                })

            except Exception as e:
                print(f"[INTENT ERROR] Line {report_line}: {str(e)}")
                if report_line not in self.an.analysis_per_line:
                    self.an.analysis_per_line[report_line] = []
                self.an.analysis_per_line[report_line].append({
                    'type': 'during_intent',
                    'result': {'status': 'error', 'message': str(e)}
                })

    def _verify_during_annotation(self, annot, node, cur_vars):
        """
        During annotation 검증 (CompoundDuringAnnotation 지원)

        Args:
            annot: DuringAnnotation 또는 CompoundDuringAnnotation 객체
            node: CFGNode
            cur_vars: 변수 환경

        Returns:
            검증 결과 딕셔너리
        """
        # CompoundDuringAnnotation 처리
        if isinstance(annot, CompoundDuringAnnotation):
            return self._verify_compound_during(annot, node, cur_vars)

        # 기존 DuringAnnotation 처리 (하위 호환성)
        guardian = self.an.guardian_verifier
        # varRef(Before) 의 sigma_before 조회(cfg_node.before_envs[line_no])를 위해
        # ambient 설정 — evaluate_guardian_expression 이 표현식 트리 어디서든 참조 가능하게.
        guardian._before_cfg_node = node
        guardian._before_line_no = annot.line_no
        atype = annot.annotation_type

        if atype == "beforeAfter":
            return guardian.verify_during_before_after(
                var_ref=annot.var_ref,
                comp_op=annot.comp_op,
                line_no=annot.line_no,
                cfg_node=node
            )

        elif atype == "assignCurrent":
            return guardian.verify_during_assign_current(
                var_ref=annot.var_ref,
                comp_op=annot.comp_op,
                line_no=annot.line_no,
                cfg_node=node
            )

        elif atype == "returnExpression":
            return guardian.verify_during_return_expression(
                comp_op=annot.comp_op,
                value_expr=annot.value_expr,
                line_no=annot.line_no,
                cfg_node=node
            )

        elif atype == "returnVariable":
            return guardian.verify_during_return_variable(
                var_ref=annot.var_ref,
                comp_op=annot.comp_op,
                value_expr=annot.value_expr,
                line_no=annot.line_no,
                cfg_node=node
            )

        elif atype == "directComparison":
            return guardian.verify_during_direct_comparison(
                lhs_expr=annot.lhs_expr,
                comp_op=annot.comp_op,
                rhs_expr=annot.rhs_expr,
                line_no=annot.line_no,
                cfg_node=node
            )

        else:
            return {
                'status': 'error',
                'message': f'Unknown during annotation type: {atype}'
            }

    def _verify_compound_during(self, compound: CompoundDuringAnnotation, node, cur_vars):
        """
        동적 분석: CompoundDuringAnnotation 검증
        debug annotation이 주어진 경우 더 정확한 검증
        """
        guardian = self.an.guardian_verifier
        results = []

        # 각 clause 검증
        for clause in compound.clauses:
            result = self._verify_during_clause_dynamic(clause, node, cur_vars, guardian)
            results.append(result)

        # 논리 연산자로 조합
        return self._combine_logic_results(results, compound.logic_ops)

    def _verify_during_clause_dynamic(self, clause: dict, node, cur_vars, guardian, line_no=None):
        """개별 during clause 동적 검증"""
        kind = clause["kind"]
        if line_no is None:
            # fallback: 노드의 첫 번째 statement에서 라인 번호 추출
            line_no = getattr(node.statements[0], "src_line", 0) if node.statements else 0

        # varRef(Before) 의 sigma_before 조회(cfg_node.before_envs[line_no])를 위해
        # ambient 설정 — evaluate_guardian_expression 이 표현식 트리 어디서든 참조 가능하게.
        guardian._before_cfg_node = node
        guardian._before_line_no = line_no

        if kind == "beforeAfter":
            return guardian.verify_during_before_after(
                var_ref=clause["var"],
                comp_op=clause["op"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "assignCurrent":
            return guardian.verify_during_assign_current(
                var_ref=clause["var"],
                comp_op=clause["op"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "retExpr":
            return guardian.verify_during_return_expression(
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "retIndex":
            return guardian.verify_during_return_index(
                index=clause["index"],
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "retVar":
            return guardian.verify_during_return_variable(
                var_ref=clause["lhs"],
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "direct":
            return guardian.verify_during_direct_comparison(
                lhs_expr=clause["lhs"],
                comp_op=clause["op"],
                rhs_expr=clause["rhs"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "implication":
            return guardian.verify_during_implication(
                antecedent=clause["antecedent"],
                consequent=clause["consequent"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "functionArg":
            # @During transfer.arg[0] > 27 형식
            return guardian.verify_during_function_arg(
                func_name=clause["func_name"],
                arg_index=clause["arg_index"],
                comp_op=clause["op"],
                rhs_expr=clause["rhs"],
                line_no=line_no,
                cfg_node=node
            )
        elif kind == "feasible":
            # @During require feasible / assert feasible
            return guardian.verify_during_feasible(
                target=clause["target"],
                line_no=line_no,
                cfg_node=node,
                cur_vars=cur_vars
            )
        elif kind == "changed":
            # @During changed(x, true/false)
            return guardian.verify_during_changed(
                var_ref=clause["var"],
                expect_changed=clause["expect_changed"],
                line_no=line_no,
                cfg_node=node,
                cur_vars=cur_vars
            )
        else:
            return {"status": "error", "message": f"Unknown clause kind: {kind}"}

    def _process_post_annotations(self, fcfg):
        """
        함수 종료 시 Post annotations 검증

        Args:
            fcfg: FunctionCFG 객체
        """
        if not self._record_enabled:
            return

        # ═══ Exit 노드에 저장된 post intent 검증 (새로운 메커니즘) ═══
        self._process_exit_node_intents(fcfg)

        # 함수 내 모든 post annotations 수집 (기존 메커니즘)
        post_annotations = []
        for line_no, line_info in self.an.sa.line_info.items():
            annots = line_info.get('post_annotations', [])
            post_annotations.extend(annots)

        if not post_annotations:
            return

        # 각 post annotation 검증
        for annot in post_annotations:
            try:
                result = self._verify_post_annotation(annot, fcfg)

                # 결과를 analysis_per_line에 기록
                line_no = annot.line_no
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []

                self.an.analysis_per_line[line_no].append({
                    'type': 'post',
                    'annotation_type': annot.annotation_type,
                    'result': result
                })

            except Exception as e:
                # 에러 발생 시 기록
                line_no = annot.line_no
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []

                self.an.analysis_per_line[line_no].append({
                    'type': 'post',
                    'annotation_type': annot.annotation_type,
                    'result': {
                        'status': 'error',
                        'message': f'Post annotation processing error: {str(e)}'
                    }
                })

    def _process_exit_node_intents(self, fcfg):
        """
        Exit 노드에 저장된 post intent 검증 (새로운 메커니즘)

        Args:
            fcfg: FunctionCFG 객체
        """
        if fcfg is None:
            return

        exit_node = fcfg.get_exit_node() if hasattr(fcfg, 'get_exit_node') else None
        if exit_node is None or not hasattr(exit_node, 'intents'):
            return

        if not exit_node.intents:
            return

        guardian = self.an.guardian_verifier

        # ═══ A2-post: @Post 도달불가 판정 (decision #40 ④ 의 @Post 판) ═══
        # @During(_process_node_intents) 와 같은 기준을, @Post 가 실제로 읽는 env 에 적용.
        #  · predecessor 개수가 아니라 env 를 본다 — debug 입력이 가드와 모순이어도
        #    그래프는 안 끊긴다(preds 그대로). 끊기는 건 env 다.
        #  · _preds 를 먼저 보는 이유 두 가지: (a) 정상 경로로 exit 에 닿는 길이 아예
        #    없으면 그 자체로 도달불가, (b) _exit_env 는 preds 가 없으면 raise 하므로
        #    (fail-loud 전환) 여기서 먼저 걸러야 한다.
        #  · _exit_env 는 _verify_post_clause_dynamic 이 쓰는 바로 그 env →
        #    "엔진은 죽었다는데 verdict 는 살았다" 가 생기지 않는다.
        _unreachable = False
        if not guardian._preds(fcfg, normal_only=True):
            _unreachable = True
        else:
            _exit_env = guardian._exit_env(fcfg, normal_only=True)
            _unreachable = (not _exit_env) or self._is_bottom_env(_exit_env)

        if _unreachable:
            for intent in exit_node.intents:
                if intent.get("type") != "post":
                    continue
                report_line = intent.get("line_no", 0)
                print(f"[POST INTENT UNREACHABLE] Line {report_line}: 경로 조건상 함수 정상 종료 불가")
                self.an.analysis_per_line.setdefault(report_line, []).append({
                    'type': 'post_intent',
                    'clauses': intent.get("clauses", []),
                    'result': {'status': 'unreachable',
                               'message': 'intent 위치 도달 불가 (정상 경로로 함수가 종료되지 않음)'}
                })
            return

        for intent in exit_node.intents:
            if intent.get("type") != "post":
                continue

            clauses = intent.get("clauses", [])
            logic_ops = intent.get("logic_ops", [])
            line_no = intent.get("line_no", 0)

            try:
                # 각 clause 검증
                results = []
                for clause in clauses:
                    result = self._verify_post_clause_dynamic(clause, line_no, fcfg, guardian)
                    results.append(result)

                # 논리 연산자로 조합
                combined_result = self._combine_logic_results(results, logic_ops)

                # 결과 출력
                status = combined_result.get("status", "unknown")
                risk = combined_result.get("risk_score", "")
                risk_str = f" [risk={risk}]" if risk != "" else ""
                if status == "violated":
                    print(f"[POST INTENT VIOLATED] Line {line_no}: {combined_result.get('message', '')}{risk_str}")
                elif status == "satisfied":
                    print(f"[POST INTENT SATISFIED] Line {line_no}: {combined_result.get('message', '')}{risk_str}")
                else:
                    print(f"[POST INTENT {status.upper()}] Line {line_no}: {combined_result.get('message', '')}{risk_str}")

                detail_str = self._format_violation_detail(results, clauses)
                if detail_str:
                    print(detail_str)

                # analysis_per_line에 기록
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []

                self.an.analysis_per_line[line_no].append({
                    'type': 'post_intent',
                    'clauses': clauses,
                    'result': combined_result
                })

            except Exception as e:
                print(f"[POST INTENT ERROR] Line {line_no}: {str(e)}")
                if line_no not in self.an.analysis_per_line:
                    self.an.analysis_per_line[line_no] = []
                self.an.analysis_per_line[line_no].append({
                    'type': 'post_intent',
                    'result': {'status': 'error', 'message': str(e)}
                })

    def _verify_post_annotation(self, annot, fcfg):
        """
        Post annotation 검증 (CompoundPostAnnotation 지원)

        Args:
            annot: PostAnnotation 또는 CompoundPostAnnotation 객체
            fcfg: FunctionCFG

        Returns:
            검증 결과 딕셔너리
        """
        # CompoundPostAnnotation 처리
        if isinstance(annot, CompoundPostAnnotation):
            return self._verify_compound_post(annot, fcfg)

        # 기존 PostAnnotation 처리 (하위 호환성)
        guardian = self.an.guardian_verifier
        # varRef(Entry)/varRef(Assign) 의 sigma_entry/sigma_assign 조회를 위해 ambient 설정
        # (GuardianVerificationEngine._evaluate_snapshot_var_ref 참고).
        guardian._current_fn_cfg = fcfg
        atype = annot.annotation_type

        if atype == "entryExit":
            return guardian.verify_post_entry_exit(
                var_ref=annot.var_ref,
                comp_op=annot.comp_op,
                line_no=annot.line_no,
                fn_cfg=fcfg
            )

        elif atype == "changed":
            return guardian.verify_post_changed(
                var_ref=annot.var_ref,
                expect_changed=getattr(annot, "expect_changed", False),
                line_no=annot.line_no,
                fn_cfg=fcfg
            )

        elif atype == "returnExpression":
            return guardian.verify_post_return_expression(
                comp_op=annot.comp_op,
                value_expr=annot.value_expr,
                line_no=annot.line_no,
                fn_cfg=fcfg
            )

        elif atype == "returnVariable":
            return guardian.verify_post_return_variable(
                var_ref=annot.var_ref,
                comp_op=annot.comp_op,
                value_expr=annot.value_expr,
                line_no=annot.line_no,
                fn_cfg=fcfg
            )

        elif atype == "directComparison":
            return guardian.verify_post_direct_comparison(
                lhs_expr=annot.lhs_expr,
                comp_op=annot.comp_op,
                rhs_expr=annot.rhs_expr,
                line_no=annot.line_no,
                fn_cfg=fcfg
            )

        else:
            return {
                'status': 'error',
                'message': f'Unknown post annotation type: {atype}'
            }

    def _verify_compound_post(self, compound: CompoundPostAnnotation, fcfg):
        """
        동적 분석: CompoundPostAnnotation 검증
        debug annotation이 주어진 경우 더 정확한 검증
        """
        guardian = self.an.guardian_verifier
        results = []

        # 각 clause 검증
        for clause in compound.clauses:
            result = self._verify_post_clause_dynamic(clause, compound.line_no, fcfg, guardian)
            results.append(result)

        # 논리 연산자로 조합
        return self._combine_logic_results(results, compound.logic_ops)

    def _verify_post_clause_dynamic(self, clause: dict, line_no: int, fcfg, guardian):
        """개별 post clause 동적 검증"""
        kind = clause["kind"]

        # varRef(Entry)/varRef(Assign) 의 sigma_entry/sigma_assign 조회를 위해 ambient 설정
        # (GuardianVerificationEngine._evaluate_snapshot_var_ref 참고).
        guardian._current_fn_cfg = fcfg

        if kind == "entryExit":
            return guardian.verify_post_entry_exit(
                var_ref=clause["var"],
                comp_op=clause["op"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "changed":
            return guardian.verify_post_changed(
                var_ref=clause["var"],
                expect_changed=clause["expect_changed"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "retExpr":
            return guardian.verify_post_return_expression(
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "retIndex":
            return guardian.verify_post_return_index(
                index=clause["index"],
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "retVar":
            return guardian.verify_post_return_variable(
                var_ref=clause["lhs"],
                comp_op=clause["op"],
                value_expr=clause["rhs"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "direct":
            return guardian.verify_post_direct_comparison(
                lhs_expr=clause["lhs"],
                comp_op=clause["op"],
                rhs_expr=clause["rhs"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        elif kind == "implication":
            return guardian.verify_post_implication(
                antecedent=clause["antecedent"],
                consequent=clause["consequent"],
                line_no=line_no,
                fn_cfg=fcfg
            )
        else:
            return {"status": "error", "message": f"Unknown clause kind: {kind}"}

    def _combine_logic_results(self, results: list[dict], logic_ops: list[str]) -> dict:
        """
        여러 clause 결과를 논리 연산자로 조합

        Args:
            results: 각 clause의 검증 결과 리스트
            logic_ops: 논리 연산자 리스트 ('&&' 또는 '||')

        Returns:
            조합된 결과 딕셔너리
        """
        if not results:
            return {"status": "error", "message": "No results to combine"}

        if len(results) == 1:
            r = results[0]
            # details 내부의 risk_score를 top-level로 전파
            if r.get("risk_score") is None and r.get("details"):
                rs = r["details"].get("risk_score")
                if rs is not None:
                    r["risk_score"] = rs
            return r

        # 초기 결과
        combined = results[0]
        combined_status = combined.get("status", "unknown")

        for i, result in enumerate(results[1:]):
            op = logic_ops[i] if i < len(logic_ops) else "&&"
            status = result.get("status", "unknown")

            if op == "&&":
                # AND: violated > warning > unknown > error > satisfied
                #   (warning = 증명 불가. interval 도메인에서 흔함 → 누락 시 satisfied 로 오판)
                if combined_status == "violated" or status == "violated":
                    combined_status = "violated"
                elif combined_status == "warning" or status == "warning":
                    combined_status = "warning"
                elif combined_status == "unknown" or status == "unknown":
                    combined_status = "unknown"
                elif combined_status == "error" or status == "error":
                    combined_status = "error"
                else:
                    combined_status = "satisfied"
            elif op == "||":
                # OR: satisfied > warning > error > unknown > violated
                if combined_status == "satisfied" or status == "satisfied":
                    combined_status = "satisfied"
                elif combined_status == "warning" or status == "warning":
                    combined_status = "warning"
                elif combined_status == "error" or status == "error":
                    combined_status = "error"
                elif combined_status == "unknown" or status == "unknown":
                    combined_status = "unknown"
                else:
                    combined_status = "violated"

        # 메시지 조합
        messages = [r.get("message", "") for r in results if r.get("message")]
        combined_msg = " ; ".join(messages) if messages else ""

        # risk_score: 가장 높은 위험도를 전파 (top-level 또는 details 내부에서 추출)
        risk_scores = []
        for r in results:
            rs = r.get("risk_score")
            if rs is None:
                rs = r.get("details", {}).get("risk_score")
            if rs is not None:
                risk_scores.append(rs)
        max_risk = max(risk_scores) if risk_scores else None

        out = {"status": combined_status, "message": combined_msg}
        if max_risk is not None:
            out["risk_score"] = max_risk
        return out

    # ── 상세 출력 헬퍼 ──────────────────────────────────────────────
    @staticmethod
    def _risk_type_label(risk_type: int) -> str:
        return {1: "safe", 2: "one-side", 3: "both-side"}.get(risk_type, "unknown")

    @staticmethod
    def _risk_type_from_score(score) -> int:
        """risk_score(0-10)에서 risk type 역산"""
        if score is None:
            return 0
        score = float(score)
        if score <= 3.3:
            return 1
        elif score <= 6.6:
            return 2
        else:
            return 3

    @staticmethod
    def _is_literal_expr(expr) -> bool:
        """Expression 객체가 순수 리터럴(상수)인지 판별"""
        if expr is None:
            return False
        return (getattr(expr, "literal", None) is not None
                and getattr(expr, "operator", None) is None
                and getattr(expr, "identifier", None) is None)

    @staticmethod
    def _fmt_val(v: str) -> str:
        """'UnsignedIntegerInterval([0, 100])' → '[0, 100]' 로 간결화"""
        import re
        m = re.search(r'\[([^\]]*)\]', v)
        return f"[{m.group(1)}]" if m else v

    def _format_violation_detail(self, results: list[dict], clauses: list[dict]) -> str:
        """각 clause 결과의 상세 정보를 포맷팅하여 반환"""
        lines = []
        for i, result in enumerate(results):
            details = result.get("details", {})
            kind = result.get("kind", "")
            clause = clauses[i] if i < len(clauses) else {}
            risk_score = details.get("risk_score")
            risk_type = self._risk_type_from_score(risk_score) if risk_score is not None else None
            risk_label = self._risk_type_label(risk_type) if risk_type else None

            prefix = "  "
            if len(results) > 1:
                prefix = f"  [{i+1}] "

            fv = self._fmt_val

            if kind == "duringAssignCurrent":
                lines.append(f"{prefix}Assign  = {fv(details.get('assign_value', '?'))}")
                lines.append(f"{prefix}Current = {fv(details.get('current_value', '?'))}")
            elif kind == "duringBeforeAfter":
                lines.append(f"{prefix}Before = {fv(details.get('before', '?'))}")
                lines.append(f"{prefix}After  = {fv(details.get('after', '?'))}")
            elif kind in ("postEntryExit", "postChanged"):
                lines.append(f"{prefix}Entry = {fv(details.get('entry_value', '?'))}")
                lines.append(f"{prefix}Exit  = {fv(details.get('exit_value', '?'))}")
            elif kind in ("duringDirectCmp", "postDirectCmp"):
                lhs_val = fv(details.get("lhs", details.get("lhs_value", "?")))
                rhs_val = fv(details.get("rhs", details.get("rhs_value", "?")))
                # RHS가 리터럴이면 Analysis/Intent, 아니면 LHS/RHS
                rhs_expr = clause.get("rhs")
                if rhs_expr is not None and self._is_literal_expr(rhs_expr):
                    lines.append(f"{prefix}Analysis = {lhs_val}")
                    lines.append(f"{prefix}Intent   = {rhs_val}")
                else:
                    lines.append(f"{prefix}LHS = {lhs_val}")
                    lines.append(f"{prefix}RHS = {rhs_val}")
            elif kind in ("duringRetExpr", "postRetExpr",
                          "duringRetIndex", "postRetIndex",
                          "duringRetVar", "postRetVar"):
                ret_val = fv(details.get("return_value", details.get("return_join",
                          details.get("indexed_join", details.get("actual", "?")))))
                rhs_val = fv(details.get("expected", details.get("rhs_value", "?")))
                lines.append(f"{prefix}Analysis = {ret_val}")
                lines.append(f"{prefix}Intent   = {rhs_val}")
            elif kind in ("duringFunctionArg",):
                lines.append(f"{prefix}Analysis = {fv(details.get('arg_value', '?'))}")
                lines.append(f"{prefix}Intent   = {fv(details.get('rhs_value', '?'))}")
            elif kind in ("duringImplication", "postImplication"):
                ant = details.get("antecedent", {})
                con = details.get("consequent", {})
                ant_status = ant.get("status", "?") if isinstance(ant, dict) else str(ant)
                con_status = con.get("status", "?") if isinstance(con, dict) else str(con)
                logic = details.get("logic")
                if logic == "vacuous-true":
                    lines.append(f"{prefix}Antecedent = false (vacuous truth)")
                else:
                    lines.append(f"{prefix}Antecedent = {ant_status}")
                    lines.append(f"{prefix}Consequent = {con_status}")
                risk_label = None  # implication은 risk type 별도 미표시

            if risk_label:
                lines.append(f"{prefix}Risk: Type {risk_type} ({risk_label})")

        return "\n".join(lines) if lines else ""
