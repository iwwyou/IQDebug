"""
GuardianVerificationEngine.py

Handles verification of @During and @Post intent annotations.
Provides temporal state checking capabilities for SolQDebug.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from Analyzer.ContractAnalyzer import ContractAnalyzer

from Domain.IR import Expression
from Domain.Variable import Variables, StructVariable, ArrayVariable, MappingVariable
from Domain.Interval import IntegerInterval, UnsignedIntegerInterval, BoolInterval
from Utils.Helper import VariableEnv
from Utils.CFG import CFGNode

class GuardianVerificationEngine:
    # varRef(Entry)/(Exit)/(Before)/(After)/(Assign) — 각각 다른 env(sigma)를 참조하는
    # 5개의 snapshot-qualified 컨텍스트. _materialize_snapshot_refs 가 이 컨텍스트를
    # 가진 노드를 찾아 미리 계산된 값으로 치환한다.
    _SNAPSHOT_CONTEXTS = frozenset({
        "VarRefAtEntry", "VarRefAtExit", "VarRefAtBefore", "VarRefAtAfter", "VarRefAtAssign",
    })

    # Expression 이 자식 Expression 을 담을 수 있는 필드들 — Domain/IR.py 의 생성자
    # 시그니처와 정확히 일치해야 함(구조적 트리 순회에 사용, 의미는 몰라도 됨).
    _CHILD_SINGLE_FIELDS = (
        "left", "right", "function", "base", "index",
        "start_index", "end_index", "expression",
        "condition", "true_expr", "false_expr",
    )
    _CHILD_LIST_FIELDS = ("arguments", "elements")

    _EXPR_CTOR_FIELDS = (
        "left", "operator", "right", "identifier", "literal", "var_type",
        "function", "arguments", "named_arguments", "base", "access",
        "index", "start_index", "end_index", "member", "options",
        "typeName", "expression", "condition", "true_expr", "false_expr",
        "is_postfix", "elements", "expr_type", "type_length", "context",
    )

    def __init__(self, analyzer: "ContractAnalyzer"):
        self.analyzer = analyzer
        # varRef(Before) 해석용 ambient (cfg_node, line_no). sigma_before 는 라인별
        # (cfg_node.before_envs[line_no])이고 evaluate_guardian_expression 은 표현식
        # 트리 임의 깊이에서 호출되므로, clause 단위로 Engine.py 가 미리 설정한다
        # (_verify_during_annotation / _verify_during_clause_dynamic).
        self._before_cfg_node = None
        self._before_line_no = None
        # varRef(Entry)/varRef(Assign) 해석용 ambient fn_cfg. Post 계열 verify_* 는
        # fn_cfg 를 인자로 받으므로 거기서 설정(staleness 회피), During 계열은 기존대로
        # analyzer.current_target_function_cfg 를 쓴다.
        self._current_fn_cfg = None

    # ═══════════════════════════════════════════════════════════════════
    #  Guardian DSL Expression Evaluation
    #  (VarRef*, InlineInterval, PercentOf, Ceil, Floor, Address literals)
    # ═══════════════════════════════════════════════════════════════════

    def evaluate_guardian_expression(self, expr: Expression, variables: dict,
                                     callerObject=None, callerContext=None):
        """
        Guardian DSL expression 평가 (주석 전용 구문)

        Guardian-specific contexts:
        - VarRefBase, VarRefMemberAccess, VarRefIndexAccess: 변수 참조
        - ReturnElemRef: return tuple 원소 접근
        - InlineInterval: [lo, hi] 구간 리터럴
        - PercentOfFuncContext: PercentOf(x, n)
        - CeilFuncContext: ceil(x, n)
        - FloorFuncContext: floor(x, n)
        - AddrLiteralExprContext, SymAddrLiteralExprContext: address 리터럴

        일반 Solidity expression은 evaluator에 위임
        """
        ctx = expr.context

        # ─── Guardian-specific contexts ───────────────────────────
        if ctx == "ReturnElemRef":
            return self._evaluate_return_elem_ref(expr, variables, callerObject, callerContext)

        elif ctx == "InlineInterval":
            return self._evaluate_inline_interval(expr)

        elif ctx == "PercentOfFuncContext":
            return self._evaluate_percent_of(expr, variables, callerObject, callerContext)

        elif ctx == "CeilFuncContext":
            return self._evaluate_ceil(expr, variables, callerObject, callerContext)

        elif ctx == "FloorFuncContext":
            return self._evaluate_floor(expr, variables, callerObject, callerContext)

        elif ctx in {"AddrLiteralExprContext", "SymAddrLiteralExprContext"}:
            return self._evaluate_address_literal(expr)

        # ─── varRef(Entry)/(Exit)/(Before)/(After)/(Assign) ───────
        # Solidity.g4 arithFactor 의 5개 snapshot 대안. 안쪽 varRef 는 expr.elements[0]
        # 에 있고, 평가할 *환경*만 일반 참조와 다르다.
        elif ctx in self._SNAPSHOT_CONTEXTS:
            return self._evaluate_snapshot_var_ref(expr, ctx, variables, callerObject, callerContext)

        # ─── VarRef contexts → evaluator에 위임 ───────────────────
        # NormalVarRef, IntentMemberAccess, IntentIndexAccess는
        # 일반 IdentifierExpContext, MemberAccessContext, IndexAccessExpContext와
        # 동일하게 처리되므로 evaluator에 위임
        elif ctx in {"NormalVarRef", "IntentMemberAccess", "IntentIndexAccess"}:
            return self.analyzer.evaluator.evaluate_expression(
                expr, variables, callerObject, callerContext
            )

        # ─── 일반 Solidity expression → evaluator 위임 ───────────
        # 단, 위임 *전에* 트리 안 어디에 중첩돼 있든 snapshot 참조를 먼저 계산해
        # 합성 변수로 치환해야 한다 — evaluator(Evaluation.py)는 이 5개 컨텍스트를
        # 전혀 모르고 단일 env 만 다루므로, 안 그러면 중첩된 snapshot 참조가 조용히
        # None 을 리턴하다 크래시한다.
        else:
            materialized_expr, aug_vars = self._materialize_snapshot_refs(
                expr, variables, callerObject, callerContext
            )
            return self.analyzer.evaluator.evaluate_expression(
                materialized_expr, aug_vars, callerObject, callerContext
            )

    # ─── Guardian DSL 평가 헬퍼들 ─────────────────────────────────────

    def _evaluate_snapshot_var_ref(self, expr: Expression, ctx: str, variables: dict,
                                   callerObject=None, callerContext=None):
        """
        varRef(Entry)/varRef(Exit)/varRef(Before)/varRef(After)/varRef(Assign) 평가.

        expr.elements[0] = 안쪽 varRef Expression (visitVarRefAt* 참고, EnhancedSolidityVisitor.py)

        - Exit (Post) / After (During): Post의 evaluate_guardian_expression 호출은 이미
          exit_env로(_eval_on_exit_value), During의 direct/return 계열 호출은 이미
          cfg_node.variables로 진입하므로, 별도 snapshot 없이 지금 넘어온 `variables`를
          그대로 재사용하면 이미 sigma_exit / sigma_pt다.
        - Entry: fn_cfg.related_variables (verify_post_changed과 동일한 fallback 패턴).
        - Assign: fn_cfg.assign_env — 변수의 함수 내 최초 대입 시점 상태.
        - Before: cfg_node.before_envs[line_no] — self._before_cfg_node/_before_line_no로
          ambient하게 전달됨 (Engine.py의 _verify_during_annotation /
          _verify_during_clause_dynamic에서 clause 단위로 설정).
        """
        inner = expr.elements[0]

        if ctx in ("VarRefAtExit", "VarRefAtAfter"):
            target_env = variables

        elif ctx == "VarRefAtEntry":
            fn_cfg = self._current_fn_cfg or self.analyzer.current_target_function_cfg
            if fn_cfg is None:
                raise ValueError("varRef(Entry): no current function CFG available")
            target_env = getattr(fn_cfg, "entry_env", None) or fn_cfg.related_variables

        elif ctx == "VarRefAtAssign":
            fn_cfg = self._current_fn_cfg or self.analyzer.current_target_function_cfg
            if fn_cfg is None or getattr(fn_cfg, "assign_env", None) is None:
                raise ValueError(f"varRef(Assign): no first-assignment state captured for "
                                 f"'{self._pretty_expr(inner)}'")
            target_env = fn_cfg.assign_env

        elif ctx == "VarRefAtBefore":
            if self._before_cfg_node is None or self._before_line_no is None:
                raise ValueError("varRef(Before): no (cfg_node, line_no) set for this @During clause")
            target_env = self._before_cfg_node.before_envs.get(self._before_line_no)
            if target_env is None:
                raise ValueError(f"varRef(Before): no before-env captured for line {self._before_line_no}")

        else:
            raise ValueError(f"Unknown snapshot var-ref context: {ctx}")

        return self.evaluate_guardian_expression(inner, target_env, callerObject, callerContext)

    def _clone_expr_with(self, expr: Expression, **overrides) -> Expression:
        """expr과 동일한 필드값을 갖되 overrides로 지정한 필드만 교체한 새
        Expression을 만든다. 원본은 건드리지 않음(파스 트리는 여러 CFG fixpoint
        반복/여러 line에 걸쳐 재사용되는 공유 객체라 in-place mutation은 위험)."""
        kwargs = {f: getattr(expr, f) for f in self._EXPR_CTOR_FIELDS}
        kwargs.update(overrides)
        return Expression(**kwargs)

    def _materialize_snapshot_refs(self, expr: Expression, variables: dict,
                                   callerObject=None, callerContext=None,
                                   aug_vars: dict | None = None) -> tuple[Expression, dict]:
        """
        expr 트리 안 어디에 있든 varRef(Entry/Exit/Before/After/Assign) 노드를
        찾아서 지금 바로 계산한 값으로 치환한다 — 값은 합성 identifier로 감싸서
        env(사본)에 넣고, 트리의 해당 위치는 그 identifier를 가리키는 평범한
        leaf 노드로 바꿔치기한다.

        일반 evaluator(Interpreter/Semantics/Evaluation.py)는 이 5개 snapshot
        컨텍스트를 전혀 모르고 항상 env 하나만 다루기 때문에, `a == a(Before) +
        (b - a(Before))`처럼 snapshot 참조가 binary/ternary/함수호출 등 복합
        expression 안에 중첩되면 evaluator가 그 노드를 만나는 순간 조용히 None을
        리턴해서 크래시로 이어짐 (2026-09-01, web3bugs_29_H_11/35_H_11에서 발견).
        이 함수로 "여러 env가 섞인 문제"를 "치환 한 번으로 env 하나만 남는 문제"로
        미리 풀어버리면, 남은 트리는 기존 evaluator에 통째로 한 번에 넘겨도 안전함
        — evaluator의 binary/ternary/함수호출 dispatch를 중복 구현할 필요가 없고,
        앞으로 새 expression 문법이 추가돼도(구조만 Expression의 알려진 자식
        필드를 쓰는 한) 이 함수를 안 고쳐도 자동으로 커버됨.

        원본 expr 트리는 절대 in-place로 안 바꿈(CFG fixpoint 반복/여러 line에서
        같은 파스 트리가 재사용되므로) — 바뀐 경로만 얕은 복사로 새 Expression을
        만들어서 반환. variables도 원본을 안 바꾸고 첫 호출 시 한 번만 얕은
        복사해서(aug_vars) 그 사본에 합성 변수를 추가해나감.

        Returns: (new_expr, aug_vars) — new_expr을 aug_vars와 함께 evaluator에
        넘기면 됨.
        """
        if aug_vars is None:
            aug_vars = dict(variables)

        if expr is None:
            return expr, aug_vars

        ctx = expr.context
        if ctx in self._SNAPSHOT_CONTEXTS:
            value = self._evaluate_snapshot_var_ref(expr, ctx, variables, callerObject, callerContext)
            synth_name = f"__snap_{ctx}_L{self._before_line_no}_{id(expr)}" if ctx == "VarRefAtBefore" \
                else f"__snap_{ctx}_{id(expr)}"
            if isinstance(value, (StructVariable, ArrayVariable, MappingVariable)):
                aug_vars[synth_name] = value
            else:
                wrapper = Variables(identifier=synth_name)
                wrapper.value = value
                aug_vars[synth_name] = wrapper
            new_leaf = Expression(identifier=synth_name, context="VarRefBase")
            return new_leaf, aug_vars

        # 순수 구조적 재귀 — Expression이 자식을 담을 수 있는 필드들만 순회.
        # 부모 노드의 의미(삼항이든 함수호출이든 튜플이든)는 몰라도 됨.
        changed = False
        overrides = {}

        for f in self._CHILD_SINGLE_FIELDS:
            child = getattr(expr, f)
            if child is not None:
                new_child, aug_vars = self._materialize_snapshot_refs(
                    child, variables, callerObject, callerContext, aug_vars)
                if new_child is not child:
                    overrides[f] = new_child
                    changed = True

        for f in self._CHILD_LIST_FIELDS:
            lst = getattr(expr, f)
            if lst:
                new_lst = []
                local_changed = False
                for item in lst:
                    new_item, aug_vars = self._materialize_snapshot_refs(
                        item, variables, callerObject, callerContext, aug_vars)
                    new_lst.append(new_item)
                    if new_item is not item:
                        local_changed = True
                if local_changed:
                    overrides[f] = new_lst
                    changed = True

        if not changed:
            return expr, aug_vars

        return self._clone_expr_with(expr, **overrides), aug_vars


    def _evaluate_return_elem_ref(self, expr: Expression, variables: dict,
                                  callerObject=None, callerContext=None):
        """
        ReturnElemRef: return[N]

        expr.base  = Expression(identifier='return', ...)
        expr.index = Expression(literal='N', ...)
        """
        # (1) 'return' 변수 가져오기 (Recorder/CFG가 variables에 넣어둠)
        ret_var = variables.get("return")
        if ret_var is None:
            raise ValueError("return tuple not available in current context")

        # (2) 인덱스 계산
        idx_val = self.evaluate_guardian_expression(
            expr.index, variables, callerObject, callerContext
        )
        idx = int(idx_val.min_value) if hasattr(idx_val, "min_value") else int(idx_val)

        # (3) 튜플 원소 반환
        return ret_var.value[idx]

    def _evaluate_inline_interval(self, expr: Expression):
        """
        InlineInterval: [lo, hi]

        expr.elements[0] = Expression(literal='lo', ...)
        expr.elements[1] = Expression(literal='hi', ...)
        """
        lo = int(expr.elements[0].literal, 0)
        hi = int(expr.elements[1].literal, 0)
        cls = IntegerInterval if lo < 0 else UnsignedIntegerInterval
        bits = 256  # 필요하면 가변
        return cls(lo, hi, bits)

    def _evaluate_percent_of(self, expr: Expression, variables: dict,
                            callerObject=None, callerContext=None):
        """
        PercentOf(x, n): x의 n%

        expr.arguments[0] = valueExpr (기준 값)
        expr.arguments[1] = numberLiteral (퍼센트)
        """
        base_iv = self.evaluate_guardian_expression(
            expr.arguments[0], variables, callerObject, callerContext
        )
        pct = int(expr.arguments[1].literal, 0)
        return base_iv.percent_of(pct)

    def _evaluate_ceil(self, expr: Expression, variables: dict,
                      callerObject=None, callerContext=None):
        """
        ceil(x, n): x를 n 단위로 올림

        expr.arguments[0] = arithExpr (기준 값)
        expr.arguments[1] = numberLiteral (단위)
        """
        base_iv = self.evaluate_guardian_expression(
            expr.arguments[0], variables, callerObject, callerContext
        )
        unit = int(expr.arguments[1].literal, 0)
        return base_iv.ceil_to_unit(unit)

    def _evaluate_floor(self, expr: Expression, variables: dict,
                       callerObject=None, callerContext=None):
        """
        floor(x, n): x를 n 단위로 내림

        expr.arguments[0] = arithExpr (기준 값)
        expr.arguments[1] = numberLiteral (단위)
        """
        base_iv = self.evaluate_guardian_expression(
            expr.arguments[0], variables, callerObject, callerContext
        )
        unit = int(expr.arguments[1].literal, 0)
        return base_iv.floor_to_unit(unit)

    def _evaluate_address_literal(self, expr: Expression):
        """
        address N / symbolicAddress N

        expr.literal = 숫자 문자열
        """
        val = int(expr.literal, 0)
        return UnsignedIntegerInterval(val, val, 160)

    # ═══════════════════════════════════════════════════════════════════
    #  DURING / POST Verification Methods
    # ═══════════════════════════════════════════════════════════════════

    def verify_during_before_after(
        self, *, var_ref: Expression, comp_op: str,
        line_no: int, cfg_node: CFGNode
    ) -> dict[str, Any]:

        try:
            # 1. before / after variable environments -----------------
            before_env = cfg_node.before_envs.get(line_no)
            if before_env is None:
                return self._err(
                    "duringBeforeAfter",
                    f"no before-env captured for line {line_no}", line_no
                )

            after_env  = cfg_node.variables

            # 2. evaluate var_ref in the two envs ---------------------
            before_val = self.evaluate_guardian_expression(var_ref, before_env, None, None)
            after_val  = self.evaluate_guardian_expression(var_ref, after_env,  None, None)

            # 3. compare ---------------------------------------------
            cmp = self._compare_values(before_val, comp_op, after_val)
            status = self._status_from_cmp(cmp)
            return {
                "status": status,
                "kind": "duringBeforeAfter",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "before": str(before_val),
                    "after": str(after_val),
                    "operator": comp_op,
                    **cmp,
                    "prob_true": self._prob_true_from_cmp(cmp),
                    "prob_false": round(1.0 - self._prob_true_from_cmp(cmp), 3),
                },
                "message": f'{self._pretty_expr(var_ref)}(Before {comp_op} After) → {cmp["message"]}',
            }

        except Exception as e:
            return self._err("duringBeforeAfter", self._exc_msg(e), line_no)

    def verify_during_assign_current(self, *, var_ref, comp_op,
                                     line_no, cfg_node):

        fcfg = self.analyzer.current_target_function_cfg

        # 1)  Assign 값  ― assign_env 를 통째로 변수-환경으로 사용
        assign_val = self.evaluate_guardian_expression(
            var_ref, fcfg.assign_env, None, None)

        if assign_val is None:
            return self._err("duringAssignCurrent",
                             "no initial assignment for variable", line_no)

        # 2)  Current 값
        current_val = self.evaluate_guardian_expression(
            var_ref, cfg_node.variables, None, None)

        # 3)  비교
        cmp = self._compare_values(assign_val, comp_op, current_val)
        status = self._status_from_cmp(cmp)

        return {
            "status": status,
            "kind": "duringAssignCurrent",
            "line": line_no,
            "details": {
                "variable": self._expr_to_str(var_ref),
                "assign_value": str(assign_val),
                "current_value": str(current_val),
                "operator": comp_op,
                **cmp
            },
            "message": f'{self._expr_to_str(var_ref)}(Assign {comp_op} Current) '
                       f'→ {cmp["message"]}',
        }

    def verify_during_return_expression(
        self, *, comp_op: str, value_expr: Expression,
        line_no: int, cfg_node: CFGNode
    ) -> dict[str, Any]:

        try:
            # ── 1) "현재 함수" CFG -------------------------------
            fcfg = self.analyzer.current_target_function_cfg
            if fcfg is None:
                return self._err("duringRetExpr",
                                  "no active FunctionCFG", line_no)

            # ── 2) return 값 확보 -------------------------------
            # builder 가 EXIT.return_vals[ln] 에 저장해 둔다.
            ret_vals = fcfg.get_exit_node().return_vals
            if line_no not in ret_vals:
                return self._err("duringRetExpr",
                                  f"no return at line {line_no}", line_no)
            ret_val = ret_vals[line_no]

            # ── 3) valueExpr 평가 -------------------------------
            rhs_val = self.evaluate_guardian_expression(
                value_expr, cfg_node.variables, None, None)

            # ── 4) 비교 ----------------------------------------
            cmp = self._compare_values(ret_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)

            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            return {
                "status":  status,
                "kind":    "duringRetExpr",
                "line":    line_no,
                "details": {
                    "return_value": str(ret_val),
                    "expected":     str(rhs_val),
                    "operator":     comp_op,
                    **cmp
                },
                "message": (f"returnExpression {comp_op} {self._pretty_expr(value_expr)} "
                            f"→ {cmp['message']}{msg_tail}")
            }

        except Exception as e:
            return self._err("duringRetExpr", self._exc_msg(e), line_no)

    # ────────────────────────────────────────────────────────────────
    #  DURING : return[idx] 비교
    # ----------------------------------------------------------------
    def verify_during_return_index(
        self, *, index: int, comp_op: str, value_expr: Expression,
        line_no: int, cfg_node: CFGNode
    ) -> dict[str, Any]:
        """
        @During return[idx] op value 검증
        tuple 반환값의 특정 인덱스 원소를 value와 비교
        """
        try:
            # ── 1) "현재 함수" CFG -------------------------------
            fcfg = self.analyzer.current_target_function_cfg
            if fcfg is None:
                return self._err("duringRetIndex",
                                  "no active FunctionCFG", line_no)

            # ── 2) return 값 확보 -------------------------------
            ret_vals = fcfg.get_exit_node().return_vals
            if line_no not in ret_vals:
                return self._err("duringRetIndex",
                                  f"no return at line {line_no}", line_no)
            ret_val = ret_vals[line_no]

            # ── 3) tuple 인덱스 접근 ----------------------------
            if isinstance(ret_val, tuple):
                if index < 0 or index >= len(ret_val):
                    return self._err("duringRetIndex",
                                      f"index {index} out of range for tuple of length {len(ret_val)}", line_no)
                indexed_val = ret_val[index]
            else:
                # 단일 반환값인 경우 인덱스 0만 허용
                if index != 0:
                    return self._err("duringRetIndex",
                                      f"return value is not a tuple, index {index} invalid", line_no)
                indexed_val = ret_val

            # ── 4) valueExpr 평가 -------------------------------
            rhs_val = self.evaluate_guardian_expression(
                value_expr, cfg_node.variables, None, None)

            # ── 5) 비교 ----------------------------------------
            cmp = self._compare_values(indexed_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)

            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            return {
                "status":  status,
                "kind":    "duringRetIndex",
                "line":    line_no,
                "details": {
                    "return_index": index,
                    "indexed_value": str(indexed_val),
                    "expected":     str(rhs_val),
                    "operator":     comp_op,
                    **cmp
                },
                "message": (f"return[{index}] {comp_op} {self._pretty_expr(value_expr)} "
                            f"→ {cmp['message']}{msg_tail}")
            }

        except Exception as e:
            return self._err("duringRetIndex", self._exc_msg(e), line_no)

    # ────────────────────────────────────────────────────────────────
    #  DURING : return var  비교
    # ----------------------------------------------------------------
    def verify_during_return_variable(
            self, *, var_ref, comp_op, value_expr, line_no, cfg_node
    ) -> dict[str, Any]:

        try:
            fcfg = self.analyzer.current_target_function_cfg
            exit_n = fcfg.get_exit_node()

            # ① "해당‧또는 가장 가까운 이전" return-값 가져오기 -------------
            if line_no in exit_n.return_vals:
                ret_val = exit_n.return_vals[line_no]
            else:  # 앞쪽에서 찾기
                prevs = [ln for ln in exit_n.return_vals if ln < line_no]
                if not prevs:
                    return self._err("duringRetVar",
                                     "no return value available", line_no)
                ln_sel = max(prevs)  # 가장 가까운 것
                ret_val = exit_n.return_vals[ln_sel]

            # ② LHS 값 추출  -------------------------------------------------
            if var_ref.context == "ReturnElemRef":
                idx = int(var_ref.index.literal, 0)
                if not isinstance(ret_val, (list, tuple)):
                    return self._err("duringRetVar",
                                     "function does not return a tuple", line_no)
                if idx >= len(ret_val):
                    return self._err("duringRetVar",
                                     f"tuple index {idx} out of range", line_no)
                lhs_val = ret_val[idx]

            else:  # 보통의 varRef (이름 있는 return 변수 등)
                lhs_val = self.evaluate_guardian_expression(
                    var_ref, cfg_node.variables, None, None
                )

            # ③ RHS 값 계산  -------------------------------------------------
            rhs_val = self.evaluate_guardian_expression(
                value_expr, cfg_node.variables, None, None
            )

            # ④ 비교  --------------------------------------------------------
            cmp = self._compare_values(lhs_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)

            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            return {
                "status": status,
                "kind": "duringRetVar",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "actual": str(lhs_val),
                    "expected": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'return {self._pretty_expr(var_ref)} {comp_op} '
                           f'{rhs_val}  →  {cmp["message"]} {msg_tail}'
            }

        except Exception as e:
            return self._err("duringRetVar", self._exc_msg(e), line_no)

    # ────────────────────────────────────────────────────────────────
    #  DURING :  valueExpr  op  valueExpr
    # ----------------------------------------------------------------
    def verify_during_direct_comparison(
            self, *, lhs_expr, comp_op, rhs_expr, line_no, cfg_node
    ) -> dict[str, Any]:

        try:
            vars_env = cfg_node.variables  # 현재 변수 Env

            # ① 두 피연산식 계산 -------------------------------------------------
            lhs_val = self.evaluate_guardian_expression(lhs_expr, vars_env, None, None)
            rhs_val = self.evaluate_guardian_expression(rhs_expr, vars_env, None, None)

            # ② 비교 ------------------------------------------------------------
            cmp = self._compare_values(lhs_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)

            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            # ③ 결과 dict -------------------------------------------------------
            return {
                "status": status,
                "kind": "duringDirectCmp",
                "line": line_no,
                "details": {
                    "lhs": str(lhs_val),
                    "rhs": str(rhs_val),
                    "operator": comp_op,
                    **cmp  # satisfied / violated / warning / prob_true
                },
                "message": f'{self._pretty_expr(lhs_expr)} {comp_op} '
                           f'{self._pretty_expr(rhs_expr)}  →  {cmp["message"]} {msg_tail}'
            }

        except Exception as e:
            return self._err("duringDirectCmp",
                             self._exc_msg(e), line_no)

    def verify_during_implication(self, *, antecedent: dict, consequent: dict, line_no: int, cfg_node: CFGNode) -> dict:
        try:
            a_res = self._eval_during_predicate(antecedent, line_no=line_no, cfg_node=cfg_node)
            a_state = self._tri_state(a_res)

            if a_state == "violated":  # A=false  → vacuously true
                status = "success"
                return {
                    "status": status,
                    "kind": "duringImplication",
                    "line": line_no,
                    "details": {"antecedent": a_res, "consequent": None, "logic": "vacuous-true"},
                    "message": "A ⇒ B holds vacuously (A is false)."
                }

            if a_state == "satisfied":  # A=true   → B로 결정
                b_res = self._eval_during_predicate(consequent, line_no=line_no, cfg_node=cfg_node)
                b_state = self._tri_state(b_res)
                status = self._status_from_state(b_state)
                return {
                    "status": status,
                    "kind": "duringImplication",
                    "line": line_no,
                    "details": {"antecedent": a_res, "consequent": b_res},
                    "message": f"A ⇒ B with A=true → B is {b_state}."
                }

            # A warning
            return {
                "status": "warning",
                "kind": "duringImplication",
                "line": line_no,
                "details": {"antecedent": a_res, "consequent": None},
                "message": "A ⇒ B warning (A is warning)."
            }

        except Exception as e:
            return self._err("duringImplication", self._exc_msg(e), line_no)

    # ────────────────────────────────────────────────────────────────
    #  DURING : funcName.arg[index] op value
    # ----------------------------------------------------------------
    def verify_during_function_arg(
            self, *, func_name: str, arg_index: int, comp_op: str,
            rhs_expr, line_no: int, cfg_node
    ) -> dict[str, Any]:
        """
        @During transfer.arg[0] > 0 검증
        해당 라인의 함수 호출에서 arg_index번째 인자의 값을 rhs와 비교
        """
        try:
            # ① statements에서 함수 호출 찾기
            target_call_expr = None
            for stmt in cfg_node.statements:
                if stmt.statement_type == 'functionCall':
                    found = self._find_function_call_in_expr(stmt.function_expr, func_name)
                    if found:
                        target_call_expr = found
                        break

            if target_call_expr is None:
                return self._err("duringFunctionArg",
                    f"함수 '{func_name}' 호출을 찾을 수 없음", line_no)

            # ② 인자 추출
            arguments = target_call_expr.arguments or []
            if arg_index >= len(arguments):
                return self._err("duringFunctionArg",
                    f"arg[{arg_index}] 범위 초과 (인자 개수: {len(arguments)})", line_no)

            arg_expr = arguments[arg_index]

            # ③ 인자 값 evaluate — (2026-10-23, M1) 호출 **직전** 상태(인자가 평가되는 상태)에서.
            #   예전엔 노드 해석 후 상태(cfg_node.variables)로 다시 평가해, 호출의 효과가 인자 값에 섞일 수 있었다.
            vars_env = (getattr(cfg_node, "before_envs", None) or {}).get(line_no)
            if vars_env is None:
                raise ValueError(f"line {line_no}: 호출 직전 상태가 기록되지 않았다 (호출문이 실행되지 않음)")
            arg_val = self.analyzer.evaluator.evaluate_expression(arg_expr, vars_env, None, None)

            # ④ RHS evaluate 및 비교
            rhs_val = self.evaluate_guardian_expression(rhs_expr, vars_env, None, None)
            cmp = self._compare_values(arg_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)

            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            return {
                "status": status,
                "kind": "duringFunctionArg",
                "line": line_no,
                "details": {
                    "func_name": func_name,
                    "arg_index": arg_index,
                    "arg_value": str(arg_val),
                    "rhs_value": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f"{func_name}.arg[{arg_index}] {comp_op} {self._pretty_expr(rhs_expr)} → {cmp['message']}{msg_tail}"
            }

        except Exception as e:
            return self._err("duringFunctionArg", self._exc_msg(e), line_no)

    def _find_function_call_in_expr(self, expr, target_name: str):
        """
        Expression 트리에서 target_name 함수 호출을 재귀적으로 찾음
        반환: 해당 함수 호출 Expression (arguments 포함) 또는 None
        """
        if expr is None:
            return None

        # Case 1: FunctionCallContext - 함수 호출
        if getattr(expr, 'context', None) == 'FunctionCallContext':
            func = expr.function
            if func:
                # MemberAccess: base.transfer()
                if getattr(func, 'context', None) == 'MemberAccessContext' and func.member == target_name:
                    return expr
                # Direct call: transfer()
                if getattr(func, 'context', None) == 'IdentifierExpContext' and func.identifier == target_name:
                    return expr

            # 인자들에서 재귀 검색 (체인 호출: a.mul(x).div(y)에서 mul 찾기)
            if expr.arguments:
                for arg in expr.arguments:
                    found = self._find_function_call_in_expr(arg, target_name)
                    if found:
                        return found

            # function 표현식에서도 재귀 (base에서 검색)
            if func:
                found = self._find_function_call_in_expr(func, target_name)
                if found:
                    return found

        # Case 2: MemberAccessContext - base에서 검색
        if getattr(expr, 'context', None) == 'MemberAccessContext' and expr.base:
            return self._find_function_call_in_expr(expr.base, target_name)

        # Case 3: 이항 연산자
        if getattr(expr, 'left', None):
            found = self._find_function_call_in_expr(expr.left, target_name)
            if found:
                return found
        if getattr(expr, 'right', None):
            found = self._find_function_call_in_expr(expr.right, target_name)
            if found:
                return found

        return None


    # === POST =======================================================

    # GuardianVerificationEngine.py
    # ────────────────────────────────────────────────────────────────
    #  POST :  varRef( Entry <op> Exit )
    # ----------------------------------------------------------------
    def verify_post_entry_exit(self, *, var_ref, comp_op: str, line_no: int, fn_cfg) -> dict[str, Any]:
        try:
            entry_env = fn_cfg.related_variables  # debug annotation 패치가 반영된 값 사용

            entry_val = self._materialize(self.evaluate_guardian_expression(var_ref, entry_env, None, None))
            exit_val = self._eval_on_exit_value(var_ref, fn_cfg, normal_only=True)

            cmp = self._compare_values(entry_val, comp_op, exit_val)
            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""

            return {
                "status": status,
                "kind": "postEntryExit",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "entry_value": str(entry_val),
                    "exit_value": str(exit_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'{self._pretty_expr(var_ref)}(Entry {comp_op} Exit) → {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postEntryExit", self._exc_msg(e), line_no)

    def verify_post_return_expression(self, *, comp_op: str, value_expr, line_no: int, fn_cfg):
        try:
            exit_node = self._get_post_exit_node(fn_cfg)
            ret_vals = list(exit_node.return_vals.values())

            if not ret_vals:
                return_val = None  # void
            else:
                # 값 조인은 list/tuple 가능성 있으니 얇은 어댑터 사용
                acc = ret_vals[0]
                for v in ret_vals[1:]:
                    acc = self._join_values(acc, v)
                return_val = self._materialize(acc)

            rhs_val = self._eval_on_exit_value(value_expr, fn_cfg, normal_only=True)

            cmp = self._compare_values(return_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""
            return {
                "status": status,
                "kind": "postRetExpr",
                "line": line_no,
                "details": {
                    "return_join": str(return_val),
                    "rhs_value": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'returnExpression {comp_op} {self._pretty_expr(value_expr)} → {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postRetExpr", self._exc_msg(e), line_no)

    def verify_post_return_index(self, *, index: int, comp_op: str, value_expr, line_no: int, fn_cfg):
        """
        @Post return[idx] op value 검증
        함수 종료 시 tuple 반환값의 특정 인덱스 원소를 value와 비교
        """
        try:
            exit_node = self._get_post_exit_node(fn_cfg)
            ret_vals = list(exit_node.return_vals.values())

            if not ret_vals:
                return self._err("postRetIndex", "no return values", line_no)

            # 모든 return 값에서 인덱스 접근 후 조인
            indexed_vals = []
            for rv in ret_vals:
                if isinstance(rv, tuple):
                    if index < 0 or index >= len(rv):
                        return self._err("postRetIndex",
                                          f"index {index} out of range for tuple of length {len(rv)}", line_no)
                    indexed_vals.append(rv[index])
                else:
                    # 단일 반환값인 경우 인덱스 0만 허용
                    if index != 0:
                        return self._err("postRetIndex",
                                          f"return value is not a tuple, index {index} invalid", line_no)
                    indexed_vals.append(rv)

            # 값 조인
            acc = indexed_vals[0]
            for v in indexed_vals[1:]:
                acc = self._join_values(acc, v)
            indexed_val = self._materialize(acc)

            rhs_val = self._eval_on_exit_value(value_expr, fn_cfg, normal_only=True)

            cmp = self._compare_values(indexed_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""
            return {
                "status": status,
                "kind": "postRetIndex",
                "line": line_no,
                "details": {
                    "return_index": index,
                    "indexed_join": str(indexed_val),
                    "rhs_value": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'return[{index}] {comp_op} {self._pretty_expr(value_expr)} → {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postRetIndex", self._exc_msg(e), line_no)

    def verify_post_return_variable(self, *, var_ref, comp_op: str, value_expr, line_no: int, fn_cfg):
        try:
            exit_node = self._get_post_exit_node(fn_cfg)
            comp_vals = []
            for rv in exit_node.return_vals.values():
                comp_vals.append(self._pick_from_return(rv, var_ref))

            if not comp_vals:
                ret_comp = None
            else:
                acc = comp_vals[0]
                for v in comp_vals[1:]:
                    acc = self._join_values(acc, v)
                ret_comp = self._materialize(acc)

            rhs_val = self._eval_on_exit_value(value_expr, fn_cfg, normal_only=True)

            cmp = self._compare_values(ret_comp, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""
            return {
                "status": status,
                "kind": "postRetVar",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "return_value": str(ret_comp),
                    "rhs_value": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'return {self._pretty_expr(var_ref)} {comp_op} {self._pretty_expr(value_expr)} → {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postRetVar", self._exc_msg(e), line_no)

    def verify_post_direct_comparison(self, *, lhs_expr, comp_op: str, rhs_expr, line_no: int, fn_cfg):
        try:
            lhs_val = self._eval_on_exit_value(lhs_expr, fn_cfg, normal_only=True)
            rhs_val = self._eval_on_exit_value(rhs_expr, fn_cfg, normal_only=True)

            cmp = self._compare_values(lhs_val, comp_op, rhs_val)
            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""
            return {
                "status": status,
                "kind": "postDirectCmp",
                "line": line_no,
                "details": {
                    "lhs_value": str(lhs_val),
                    "rhs_value": str(rhs_val),
                    "operator": comp_op,
                    **cmp
                },
                "message": f'{self._pretty_expr(lhs_expr)} {comp_op} {self._pretty_expr(rhs_expr)} → {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postDirectCmp", self._exc_msg(e), line_no)

    def verify_post_changed(self, *, var_ref, expect_changed: bool, line_no: int, fn_cfg):
        """
        @Post changed(x, true/false) 검증.
        Entry 값과 Exit 값을 비교하여 변경 여부 판정.
        expect_changed=True: 변경되었어야 함 (Entry != Exit → satisfied)
        expect_changed=False: 변경 안 되었어야 함 (Entry == Exit → satisfied)
        """
        try:
            entry_env = getattr(fn_cfg, "entry_env", fn_cfg.related_variables)
            entry_val = self._materialize(self.evaluate_guardian_expression(var_ref, entry_env, None, None))
            exit_val = self._eval_on_exit_value(var_ref, fn_cfg, normal_only=True)

            # Entry != Exit 판정
            cmp_neq = self._compare_values(entry_val, '!=', exit_val)
            cmp_eq = self._compare_values(entry_val, '==', exit_val)

            if expect_changed:
                # changed(x, true): Entry != Exit → satisfied
                cmp = cmp_neq
                label = "changed"
            else:
                # changed(x, false): Entry == Exit → satisfied
                cmp = cmp_eq
                label = "unchanged"

            status = self._status_from_cmp(cmp)
            if status == "warning" and "false_regions" in cmp:
                fr = cmp["false_regions"]
                msg_tail = f" | violation lower={fr.get('lower_violation')} upper={fr.get('upper_violation')} overlap={fr.get('overlap_zone')}"
            else:
                msg_tail = ""
            return {
                "status": status,
                "kind": "postChanged",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "entry_value": str(entry_val),
                    "exit_value": str(exit_val),
                    "expect_changed": expect_changed,
                    **cmp
                },
                "message": f'changed({self._pretty_expr(var_ref)}, {str(expect_changed).lower()}) → {label}: {cmp["message"]}{msg_tail}',
            }
        except Exception as e:
            return self._err("postChanged", self._exc_msg(e), line_no)

    def verify_during_changed(self, *, var_ref, expect_changed: bool, line_no: int, cfg_node, cur_vars=None):
        """
        @During changed(x, true/false) 검증.
        Entry 값(함수 진입 시)과 현재 값(이 program point)을 비교.
        expect_changed=True: 변경되었어야 함 (Entry != Current → satisfied)
        expect_changed=False: 변경 안 되었어야 함 (Entry == Current → satisfied)
        """
        try:
            fcfg = self.analyzer.current_target_function_cfg
            if fcfg is None:
                return self._err("duringChanged", "No active FunctionCFG", line_no)

            # Entry 값 (함수 진입 시 환경)
            entry_env = getattr(fcfg, "entry_env", fcfg.related_variables)
            entry_val = self._materialize(self.evaluate_guardian_expression(var_ref, entry_env, None, None))

            # Current 값 (현재 program point의 환경, cur_vars 우선)
            cur_env = cur_vars if cur_vars is not None else cfg_node.variables
            cur_val = self._materialize(self.evaluate_guardian_expression(var_ref, cur_env, None, None))

            if expect_changed:
                cmp = self._compare_values(entry_val, '!=', cur_val)
                label = "changed"
            else:
                cmp = self._compare_values(entry_val, '==', cur_val)
                label = "unchanged"

            status = self._status_from_cmp(cmp)
            return {
                "status": status,
                "kind": "duringChanged",
                "line": line_no,
                "details": {
                    "variable": self._pretty_expr(var_ref),
                    "entry_value": str(entry_val),
                    "current_value": str(cur_val),
                    "expect_changed": expect_changed,
                    **cmp
                },
                "message": f'changed({self._pretty_expr(var_ref)}, {str(expect_changed).lower()}) → {label}: {cmp["message"]}',
            }
        except Exception as e:
            return self._err("duringChanged", self._exc_msg(e), line_no)

    def verify_post_implication(self, *, antecedent: dict, consequent: dict, line_no: int, fn_cfg) -> dict:
        try:
            a_res = self._eval_post_predicate(antecedent, line_no=line_no, fn_cfg=fn_cfg)
            a_state = self._tri_state(a_res)

            if a_state == "violated":
                return {
                    "status": "success",
                    "kind": "postImplication",
                    "line": line_no,
                    "details": {"antecedent": a_res, "consequent": None, "logic": "vacuous-true"},
                    "message": "A ⇒ B holds vacuously (A is false)."
                }

            if a_state == "satisfied":
                b_res = self._eval_post_predicate(consequent, line_no=line_no, fn_cfg=fn_cfg)
                b_state = self._tri_state(b_res)
                status = self._status_from_state(b_state)
                return {
                    "status": status,
                    "kind": "postImplication",
                    "line": line_no,
                    "details": {"antecedent": a_res, "consequent": b_res},
                    "message": f"A ⇒ B with A=true → B is {b_state}."
                }

            return {
                "status": "warning",
                "kind": "postImplication",
                "line": line_no,
                "details": {"antecedent": a_res, "consequent": None},
                "message": "A ⇒ B warning (A is warning)."
            }

        except Exception as e:
            return self._err("postImplication", self._exc_msg(e), line_no)

    # ----------------------------------------------------------------
    # helper: uniform ok / error payloads
    # ----------------------------------------------------------------
    # ───────── require/assert feasible ─────────────────────────
    def verify_during_feasible(
            self, *, target: str, line_no: int, cfg_node, cur_vars
    ) -> dict[str, Any]:
        """
        @During require feasible / assert feasible 검증.
        다음 라인의 require/assert 조건식을 evaluate하여
        항상 false([0,0])이면 violated (절대 통과 불가).
        """
        try:
            # ① 현재 노드 또는 successor에서 require/assert condition 노드 찾기
            condition_expr = None
            fcfg = self.analyzer.current_target_function_cfg

            if fcfg is None:
                return self._err("duringFeasible",
                    "No active FunctionCFG", line_no)

            # CFG에서 require/assert condition 노드 탐색
            for node in fcfg.graph.nodes:
                if (getattr(node, "condition_node", False) and
                    getattr(node, "condition_node_type", "") == target and
                    getattr(node, "src_line", None) is not None and
                    node.src_line >= line_no):
                    condition_expr = getattr(node, "condition_expr", None)
                    break

            if condition_expr is None:
                return self._err("duringFeasible",
                    f"'{target}' statement를 찾을 수 없음 (line {line_no} 이후)", line_no)

            # ② 조건식 evaluate → BoolInterval
            cond_val = self.analyzer.evaluator.evaluate_expression(
                condition_expr, cur_vars, None, None)

            # BoolInterval이 아닌 경우 변환 시도
            if not isinstance(cond_val, BoolInterval):
                if VariableEnv.is_interval(cond_val):
                    cond_val = VariableEnv.convert_int_to_bool_interval(cond_val)
                else:
                    return self._err("duringFeasible",
                        f"조건식 평가 결과가 boolean이 아님: {cond_val}", line_no)

            # ③ feasibility 판정
            if cond_val.max_value == 0:
                # [0,0] → 항상 false → 절대 통과 불가 → violated
                status = "violated"
                message = f"{target} condition is always false — never passable"
            elif cond_val.min_value == 1:
                # [1,1] → 항상 true → 항상 통과 → satisfied
                status = "satisfied"
                message = f"{target} condition is always true"
            else:
                # [0,1] → 통과 가능 → satisfied
                status = "satisfied"
                message = f"{target} condition is feasible (can be true or false)"

            return {
                "status": status,
                "kind": "duringFeasible",
                "line": line_no,
                "details": {
                    "target": target,
                    "condition_value": str(cond_val),
                },
                "message": message,
            }

        except Exception as e:
            return self._err("duringFeasible", self._exc_msg(e), line_no)

    @staticmethod
    def _exc_msg(e: Exception) -> str:
        """(2026-10-23) intent 평가 실패 메시지. 그 지점에서 선언되지 않은 이름을 참조한 경우는 **사용자 입력 문제**라
        엔진 내부 오류로 표시하지 않는다(예: 31_H_01 — 선언보다 앞에 붙은 @During 이 그 변수를 참조)."""
        from Utils.Errors import UnresolvedName
        if isinstance(e, UnresolvedName):
            return f"intent error: '{e.name}' is not declared at this point of the function"
        return f"internal error: {e}"

    def _err(self, kind: str, msg: str, ln: int) -> dict[str, Any]:
        return {"status": "error", "kind": kind, "line": ln, "message": msg}

    def _pretty_expr(self, expr: Expression) -> str:
        """very small utility – turn Expression into a readable string"""
        return getattr(expr, "identifier", "") or str(expr)

    # ----------------------------------------------------------------
    # Risk type / Risk score 계산
    # ----------------------------------------------------------------
    def _compute_risk_type(self, state: str, false_regions: dict, op: str) -> int:
        """
        위험도 타입 판별:
          1 = satisfied (위반 경로 없음, spec 느슨할 수 있음)
          2 = 한쪽 방향만 위반
          3 = 양쪽 방향 위반 (in operator 등)
        violated 상태는 violation_ratio=1.0 과 결합하여 최대 점수가 됨.
        """
        if state == "satisfied":
            return 1
        if state == "violated":
            # violated는 타입 자체는 false_regions로 판별
            # violated이면서 양방향이면 3, 아니면 2
            pass

        # warning 또는 violated: false_regions 기반 판별
        lower = false_regions.get("lower_violation") if false_regions else None
        upper = false_regions.get("upper_violation") if false_regions else None

        has_lower = lower is not None
        has_upper = upper is not None

        # == / != 는 interval 기반 분석과 본질적으로 궁합이 안 맞으므로
        # warning이면 타입 3 (높은 위험도) 부여
        if op in {"==", "!="}:
            return 3 if state != "satisfied" else 1

        if has_lower and has_upper:
            return 3
        elif has_lower or has_upper:
            return 2
        else:
            # false_regions 계산 실패 등 → overlap_zone만 있으면 타입 2
            overlap = false_regions.get("overlap_zone") if false_regions else None
            return 2 if overlap is not None else 1

    def _compute_risk_score(self, state: str, prob_true: float,
                            false_regions: dict, op: str) -> float:
        """
        0-10 스케일 위험도 점수 계산.
          satisfied → 0.0 (고정)
          violated  → 10.0 (고정)
          warning:
            타입 1 (한쪽 경미)  → 0.1 ~ 3.3
            타입 2 (한쪽 위반)  → 3.4 ~ 6.6
            타입 3 (양쪽 위반)  → 6.7 ~ 9.9
        """
        if state == "satisfied":
            return 0.0
        if state == "violated":
            return 10.0

        # warning 전용: risk_type으로 구간 결정
        risk_type = self._compute_risk_type(state, false_regions, op)
        violation_ratio = 1.0 - prob_true  # 위반 비율

        # 타입별 구간
        if risk_type == 1:
            base, span = 0.1, 3.2   # 0.1 ~ 3.3
        elif risk_type == 2:
            base, span = 3.4, 3.2   # 3.4 ~ 6.6
        else:
            base, span = 6.7, 3.2   # 6.7 ~ 9.9

        score = base + violation_ratio * span
        return round(min(9.9, max(0.1, score)), 1)

    # ----------------------------------------------------------------
    # Interval-aware comparison with probability
    # ----------------------------------------------------------------
    def _compare_intervals_prob(self, left_iv, right_iv, op: str) -> dict:
        """
        두 Interval 사이의 관계를
          - 'satisfied' : 반드시 성립
          - 'violated'  : 절대 성립 불가
          - 'warning'   : 일부 구간만 성립
        로 판정하고, warning 인 경우에는
          prob_true ∈ (0,1)  ≒  '성립할 확률' 값을 계산한다.
        """

        def _enrich(info):
            """모든 반환 경로에 risk_type, risk_score를 추가"""
            fr = info.get("false_regions", {})
            info["risk_type"] = self._compute_risk_type(info["state"], fr, op)
            info["risk_score"] = self._compute_risk_score(
                info["state"], info["prob_true"], fr, op)
            return info

        # ① min/max 가 None → 정보 부족 → 완전 불확정
        if (left_iv.min_value is None or left_iv.max_value is None or
                right_iv.min_value is None or right_iv.max_value is None):
            return _enrich({"state": "warning", "prob_true": 0.5})

        # ② Interval 폭
        lw, rw = left_iv.max_value - left_iv.min_value, right_iv.max_value - right_iv.min_value

        # ── 정수 구간 카운팅 (모든 연산자 공통) ──
        int_total = lw + 1  # L 구간의 정수 개수

        # ─── 포함(in) / 비포함(not in) ────────────────────────────
        if op in {"in", "not in"}:
            # left ⊆ right ?   (구간 포함 여부)
            left_inside = (left_iv.min_value >= right_iv.min_value and
                           left_iv.max_value <= right_iv.max_value)
            if left_inside:
                return _enrich({"state": "satisfied" if op == "in" else "violated",
                                "prob_true": 1.0 if op == "in" else 0.0})
            # 완전히 분리 → 'in' 은 false 확정,  'not in' 은 true 확정
            separated = (left_iv.max_value < right_iv.min_value or
                         left_iv.min_value > right_iv.max_value)
            if separated:
                return _enrich({"state": "violated" if op == "in" else "satisfied",
                                "prob_true": 0.0 if op == "in" else 1.0})
            # 부분-겹침 → 정수 카운팅으로 겹치는 비율 계산
            overlap_count = max(0, min(left_iv.max_value, right_iv.max_value)
                                - max(left_iv.min_value, right_iv.min_value) + 1)
            conf = (1 - overlap_count / int_total) if op == "not in" else (overlap_count / int_total)
            info = {"state": "warning", "prob_true": round(conf, 3)}
            try:
                info["false_regions"] = self._false_regions_for_op(left_iv, right_iv, op)
            except Exception:
                info["false_regions"] = {"lower_violation": None, "upper_violation": None,
                                         "overlap_zone": None, "notes": "failed to compute"}
            return _enrich(info)

        if op == '>':
            # L > R: L.min > R.max → satisfied, L.max <= R.min → violated
            if left_iv.min_value > right_iv.max_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            if left_iv.max_value <= right_iv.min_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            # 확실히 true: L > R.max  →  L ∈ {R.max+1 .. L.max}
            true_len = max(0, left_iv.max_value - right_iv.max_value)
            # 확실히 false: L ≤ R.min  →  L ∈ {L.min .. R.min}
            false_len = max(0, right_iv.min_value - left_iv.min_value + 1)
            total = int_total
        elif op == '<':
            # L < R: L.max < R.min → satisfied, L.min >= R.max → violated
            if left_iv.max_value < right_iv.min_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            if left_iv.min_value >= right_iv.max_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            # 확실히 true: L < R.min  →  L ∈ {L.min .. R.min-1}
            true_len = max(0, right_iv.min_value - left_iv.min_value)
            # 확실히 false: L ≥ R.max  →  L ∈ {R.max .. L.max}
            false_len = max(0, left_iv.max_value - right_iv.max_value + 1)
            total = int_total
        elif op == '>=':
            # L >= R: L.min >= R.max → satisfied, L.max < R.min → violated
            if left_iv.min_value >= right_iv.max_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            if left_iv.max_value < right_iv.min_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            true_len = max(0, left_iv.max_value - right_iv.max_value + 1)
            false_len = max(0, right_iv.min_value - left_iv.min_value)
            total = int_total
        elif op == '<=':
            # L <= R: L.max <= R.min → satisfied, L.min > R.max → violated
            if left_iv.max_value <= right_iv.min_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            if left_iv.min_value > right_iv.max_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            true_len = max(0, right_iv.min_value - left_iv.min_value + 1)
            false_len = max(0, left_iv.max_value - right_iv.max_value)
            total = int_total
        elif op == '==':
            # disjoint → violated, both singletons & equal → satisfied
            if left_iv.max_value < right_iv.min_value or left_iv.min_value > right_iv.max_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            if lw == 0 and rw == 0 and left_iv.min_value == right_iv.min_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            # 겹치는 정수 개수를 "true", 나머지를 "false"
            overlap_count = max(0, min(left_iv.max_value, right_iv.max_value)
                                - max(left_iv.min_value, right_iv.min_value) + 1)
            true_len = overlap_count
            false_len = int_total - true_len
            total = int_total
        elif op == '!=':
            # disjoint → satisfied, both singletons & equal → violated
            if left_iv.max_value < right_iv.min_value or left_iv.min_value > right_iv.max_value:
                return _enrich({"state": "satisfied", "prob_true": 1.0})
            if lw == 0 and rw == 0 and left_iv.min_value == right_iv.min_value:
                return _enrich({"state": "violated", "prob_true": 0.0})
            overlap_count = max(0, min(left_iv.max_value, right_iv.max_value)
                                - max(left_iv.min_value, right_iv.min_value) + 1)
            true_len = int_total - overlap_count
            false_len = overlap_count
            total = int_total
        else:
            raise ValueError(f"unsupported op {op}")

        # ④ 결과 state / prob_true ----------------------------------
        uncertain = int_total - true_len - false_len
        if false_len == 0 and uncertain == 0 and not (op in ('==', '!=') and rw > 0):
            return _enrich({"state": "satisfied", "prob_true": 1.0})
        if true_len == 0 and uncertain == 0 and not (op in ('==', '!=') and rw > 0):
            return _enrich({"state": "violated", "prob_true": 0.0})

        # 불확정 구간의 기대 true 개수 (R이 범위일 때, 균등분포 가정)
        unc_true = 0
        if uncertain > 0 and rw > 0 and op in {'>', '<', '>=', '<='}:
            r_count = rw + 1  # R 정수 개수
            if op in ('>', '<='):
                unc_lo = max(left_iv.min_value, right_iv.min_value + 1)
                unc_hi = min(left_iv.max_value, right_iv.max_value)
            else:  # >= 또는 <
                unc_lo = max(left_iv.min_value, right_iv.min_value)
                unc_hi = min(left_iv.max_value, right_iv.max_value - 1)
            n = max(0, unc_hi - unc_lo + 1)
            if n > 0:
                if op == '>':
                    a = unc_lo - right_iv.min_value
                    b = unc_hi - right_iv.min_value
                elif op == '>=':
                    a = unc_lo - right_iv.min_value + 1
                    b = unc_hi - right_iv.min_value + 1
                elif op == '<':
                    a = right_iv.max_value - unc_hi
                    b = right_iv.max_value - unc_lo
                else:  # <=
                    a = right_iv.max_value - unc_hi + 1
                    b = right_iv.max_value - unc_lo + 1
                unc_true = (a + b) * n / 2 / r_count

        if op == '==':
            # overlap 값은 "확실히 true"가 아니라 1/(rw+1) 확률로 true
            conf = true_len / (total * (rw + 1))
        elif op == '!=':
            # overlap 값은 "확실히 false"가 아니라 1/(rw+1) 확률로 false
            conf = 1 - false_len / (total * (rw + 1))
        else:
            conf = (true_len + unc_true) / total
        info = {"state": "warning", "prob_true": round(conf, 3)}

        try:
            info["false_regions"] = self._false_regions_for_op(left_iv, right_iv, op)
        except Exception:
            info["false_regions"] = {"lower_violation": None, "upper_violation": None,
                                     "overlap_zone": None, "notes": "failed to compute"}

        return _enrich(info)

    def _compare_values(self, left, op: str, right) -> dict:
        # ───────── Interval ↔ Interval (기존) ─────────
        if hasattr(left, "min_value") and hasattr(right, "min_value"):
            info = self._compare_intervals_prob(left, right, op)
            out = {
                "satisfied": info["state"] == "satisfied",
                "violated": info["state"] == "violated",
                "warning": info["state"] == "warning",
                "prob_true": info["prob_true"],
                "risk_score": info.get("risk_score", 0.0),
                "risk_type": info.get("risk_type", 1),
                "message": f"{info['state']} (risk={info.get('risk_score', 0.0)})"
            }
            if "false_regions" in info:
                out["false_regions"] = info["false_regions"]
            return out

        # ───────── 스칼라 ↔ Interval (주로 in / not in) ─────────
        if not hasattr(left, "min_value") and hasattr(right, "min_value"):
            if op in {"in", "not in"}:
                if right.min_value is None or right.max_value is None:
                    # 경계가 진짜 미상(⊤) — satisfied 도 violated 도 증명 불가이므로
                    # Cmp* 의 형식 정의상 j = W(Warning)이지 Violation 이 아니다.
                    # (_compare_intervals_prob 의 min/max-None 분기와 같은 관례.)
                    risk_type = self._compute_risk_type("warning", {}, op)
                    risk_score = self._compute_risk_score("warning", 0.5, {}, op)
                    return {"satisfied": False, "violated": False,
                            "warning": True, "prob_true": 0.5,
                            "risk_score": risk_score, "risk_type": risk_type,
                            "message": "interval unknown"}
                inside = right.min_value <= left <= right.max_value
                satisfied = inside if op == "in" else not inside
                return {
                    "satisfied": satisfied,
                    "violated": not satisfied,
                    "warning": False,
                    "prob_true": 1.0,
                    "risk_score": 0.0 if satisfied else 10.0,
                    "risk_type": 1 if satisfied else 2,
                    "message": f"{left} {op} [{right.min_value},{right.max_value}] = {satisfied}"
                }

        # ───────── 스칼라 ↔ 스칼라 (기존 + in/not in 방어) ─────────
        try:
            tbl = {
                '<': lambda a, b: a < b,
                '>': lambda a, b: a > b,
                '<=': lambda a, b: a <= b,
                '>=': lambda a, b: a >= b,
                '==': lambda a, b: a == b,
                '!=': lambda a, b: a != b,
            }
            if op in tbl:
                satisfied = tbl[op](left, right)
            elif op == "in":
                satisfied = left == right  # 스칼라끼리 in 은 동일성으로 취급
            elif op == "not in":
                satisfied = left != right
            else:
                raise ValueError(f"unsupported op {op}")

            return {
                "satisfied": satisfied,
                "violated": not satisfied,
                "warning": False,
                "prob_true": 1.0,
                "risk_score": 0.0 if satisfied else 10.0,
                "risk_type": 1 if satisfied else 2,
                "message": f"{left} {op} {right} = {satisfied}"
            }
        except Exception as e:
            # 비교를 아예 평가할 수 없었음 — satisfied 도 violated 도 증명 불가이므로
            # Cmp* 의 형식 정의상 j = W(Warning)이지 Violation 이 아니다.
            risk_type = self._compute_risk_type("warning", {}, op)
            risk_score = self._compute_risk_score("warning", 0.5, {}, op)
            return {"satisfied": False, "violated": False,
                    "warning": True, "prob_true": 0.5,
                    "risk_score": risk_score, "risk_type": risk_type,
                    "message": f"comparison error: {e}"}

    def _expr_to_str(self, e):  # 간단 문자열 직렬화 helper
        if getattr(e, "identifier", None):
            return e.identifier
        if getattr(e, "member", None):
            return f"{self._expr_to_str(e.base)}.{e.member}"
        if getattr(e, "index", None):
            idx = getattr(e.index, "literal", "?")
            return f"{self._expr_to_str(e.base)}[{idx}]"
        return str(e)

    def _pick_from_return(self, ret_v, var_ref):
        """
        ret_v :  한 줄의 return 값 (Interval, list, tuple …)
        var_ref.context:
            • "ReturnTupleBase"        → 전체 반환
            • "ReturnElemRef"          → return[i]
        """
        # 전체 tuple 그대로
        if var_ref.context == "ReturnTupleBase":
            return ret_v

        # return[ idx ]  ── element 추출
        if var_ref.context == "ReturnElemRef":
            idx = int(var_ref.index.literal, 0)
            if isinstance(ret_v, (list, tuple)) and idx < len(ret_v):
                return ret_v[idx]
            return None
        # 기타 경우는 evaluator 로 해결하도록 위임
        return ret_v

    def _join_values(self, a, b):
        """
        Helper의 _merge_values를 최대한 재사용.
        단, list/tuple/dict 은 Helper가 모르므로 여기서만 얇게 감싸서 원소별 join.
        """
        if a is None: return b
        if b is None: return a

        # list/tuple → 원소별 재귀
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                return "⊤"
            return type(a)(self._join_values(x, y) for x, y in zip(a, b))

        # dict → key union 후 값별 재귀
        if isinstance(a, dict) and isinstance(b, dict):
            keys = set(a) | set(b)
            return {k: self._join_values(a.get(k), b.get(k)) for k in keys}

        # 나머지는 Helper에게 위임(Interval/Variables/Struct/Array/Mapping 등)
        try:
            return VariableEnv._merge_values(a, b, "join")
        except Exception:
            # 서로 다른 Interval형 조합 등에서 join이 예외를 던질 수 있음 → 보수적 ⊤
            return "⊤"

    def _materialize(self, v):
        """
        Variables/Struct/Array/Mapping → 값 뷰로 평탄화
        Interval/스칼라 → 그대로
        """
        if v is None:
            return None
        from Domain.Variable import ArrayVariable
        if isinstance(v, ArrayVariable):
            # (2026-10-23, 희소 배열) 값 목록 = 길이만큼의 Read(k). 길이를 모르거나 매우 크면 목록으로 만들 수 없다.
            n = v.static_length()
            if n is None and v.length is not None and v.length.min_value == v.length.max_value:
                n = v.length.min_value
            if n is None or n > 4096:
                raise ValueError(f"intent: 배열 `{v.identifier}` 의 길이가 정해지지 않았거나 너무 커 값 목록으로 비교할 수 없다")
            return [self._materialize(getattr(e, "value", e)) for e in (v.read_element(k) for k in range(n))]
        if hasattr(v, "members") and isinstance(getattr(v, "members"), dict):  # StructVariable
            return {k: self._materialize(getattr(m, "value", m)) for k, m in v.members.items()}
        if hasattr(v, "mapping") and isinstance(getattr(v, "mapping"), dict):  # MappingVariable
            return {k: self._materialize(getattr(m, "value", m)) for k, m in v.mapping.items()}
        if hasattr(v, "value") and not (hasattr(v, "elements") or hasattr(v, "members") or hasattr(v, "mapping")):
            return self._materialize(v.value)
        if isinstance(v, (list, tuple)):
            return type(v)(self._materialize(x) for x in v)
        return v

    # GuardianVerificationEngine.py  ─ GuardianVerificationEngine 클래스 내부

    def _get_post_exit_node(self, fn_cfg):
        """
        Post 검증용 exit 노드 선택:
        - return이 있는 함수 → return_exit_node
        - return이 없는 함수 → exit_node

        ★ 라이브 인터프리테이션에서 명시적 `return`문은 실제로
        `Interpreter/Engine.py._interpret_return`이 `fn_cfg.get_exit_node().
        return_vals[line]`에 씀(return_exit이 아니라 일반 exit_node) —
        `return_exit.return_vals`는 이 프로젝트 다른 static 경로(예:
        interface 함수 pkl 빌드) 전용이라 라이브 케이스에선 늘 비어있고,
        사실상 매번 exit_node로 떨어짐. 그래서 named-return 함수가 명시적
        return 없이 끝나는 경우(web3bugs_5_H_12의 `getAddedAmount` 등),
        exit_node.return_vals도 아무도 안 채워서 `@Post returnExpression`
        검증이 "void 함수"로 오판해 None을 리턴하던 버그가 있었음 — 여기서
        딱 그 빈 슬롯 하나만 채움(named return 변수의 exit 시점 실제 값으로).
        """
        return_exit = fn_cfg.get_return_exit_node()
        if return_exit.return_vals:
            return return_exit

        exit_node = fn_cfg.get_exit_node()
        if fn_cfg.return_vars and not exit_node.return_vals:
            # ★ node=exit_node를 명시해서 _exit_env→_preds가 다시
            # _get_post_exit_node를 호출하는 무한 재귀를 피함.
            exit_env = self._exit_env(fn_cfg, normal_only=True, node=exit_node)

            def _rv_value(rv):
                live = exit_env.get(rv.identifier, rv)
                if isinstance(live, (StructVariable, ArrayVariable, MappingVariable)):
                    return live
                return getattr(live, "value", None)

            if len(fn_cfg.return_vars) == 1:
                exit_node.return_vals[-1] = _rv_value(fn_cfg.return_vars[0])
            else:
                exit_node.return_vals[-1] = [_rv_value(rv) for rv in fn_cfg.return_vars]

        return exit_node

    def _preds(self, fn_cfg, *, normal_only: bool = True, node=None):
        """
        EXIT의 predecessor 중 정상 경로만 뽑아옴.
        (빌더에서 revert/require/assert(false) 엣지에 edge['abnormal']=True 를 붙였다고 가정)

        `return` statement는 일반 exit 노드가 아니라 별도의 RETURN 전용 exit
        노드(`get_return_exit_node()`)에 엣지를 연결한다
        (`DynamicCFGBuilder.build_return_statement`). 함수에 실제 return이
        있는데 일반 `get_exit_node()`만 조회하면 predecessor가 0개로 나와서
        (아무도 거기 연결한 적이 없으므로) exit 환경이 통째로 비어버린다 —
        `_get_post_exit_node`가 이미 이 구분을 올바르게 처리하므로 그걸 재사용.

        `node`: 명시하면 `_get_post_exit_node` 재호출 없이 그 노드의
        predecessor를 바로 조회(`_get_post_exit_node` 자신이 내부에서
        exit_node 기준 env를 계산할 때 순환 호출을 피하기 위해 씀).
        """
        G = fn_cfg.graph
        # ★ (2026-10-21) node 를 지정하지 않으면 **정상 종료 전체** = EXIT 선행 ∪ RETURN 선행.
        #   예전엔 `_get_post_exit_node` 가 RETURN 이나 EXIT 중 **하나**만 골라서, 중간 `return` 과 본문 끝 도달이
        #   섞인 함수에서 한쪽 경로를 @Post 판정에서 빠뜨렸다. Solidity 에서 둘은 똑같이 정상 종료다.
        #   엔진 쪽(`Engine.normal_completion_preds`, `_force_join_before_exit`)과 같은 집합을 쓴다.
        sinks = [node] if node is not None else [fn_cfg.get_exit_node(), fn_cfg.get_return_exit_node()]
        out = []
        for exit_n in sinks:
            if exit_n is None or exit_n not in G:
                continue
            for p in G.predecessors(exit_n):
                ed = G.get_edge_data(p, exit_n, default={})
                if normal_only and ed.get("abnormal"):
                    continue
                if p not in out:
                    out.append(p)
        return out

    def _exit_env(self, fn_cfg, *, normal_only: bool = True, node=None) -> dict:
        """
        EXIT 직전의 '변수 환경'을 Helper의 join으로 하나로 만든다.
        ⇒ 이후 expr은 이 env로 한 번만 평가하면 됨.
        """
        preds = self._preds(fn_cfg, normal_only=normal_only, node=node)
        if not preds:
            # ★ 과거엔 여기서 related_variables(= *선언된* 변수 전체)로 폴백했다.
            #   그 폴백은 _get_post_exit_node 가 없던 시절 "explicit return 함수의
            #   EXIT 는 predecessor 가 0개" 를 덮던 응급처치(Initial commit 유래)이고,
            #   근본 원인은 _get_post_exit_node 가 RETURN 을 고르면서 이미 해결됐다.
            #   지금 남겨두면 *도달 불가한* exit 를 "선언 초기값 env" 로 덮어써서
            #   그럴싸한 vacuous verdict 를 만든다 — 조용한 오분석이라 최악이다.
            #   호출부(_process_exit_node_intents)가 preds 를 먼저 확인해 도달불가로
            #   처리하므로, 여기 닿으면 그건 진짜 불변식 위반이다. fail-loud.
            raise RuntimeError(
                f"_exit_env: no normal predecessor for exit of "
                f"{getattr(fn_cfg, 'function_name', '?')} "
                f"(node={getattr(node, 'name', None)}). "
                f"호출부에서 도달불가를 먼저 걸러야 한다."
            )

        # ★ (2026-10-21) 도달 불가(⊥) 선행은 join 에서 뺀다 — 넣으면 그 경로에 남은 다른 변수의 묵은 값이
        #   정상 경로 값과 섞여 구간이 넓어진다. 전부 ⊥ 면 예전처럼 ⊥ 들의 join 을 돌려줘서 호출부가 도달불가로 판정한다.
        is_bot = self.analyzer.engine._is_bottom_env
        live = [p for p in preds if not is_bot(p.variables or {})]
        use = live or preds
        env = VariableEnv.copy_variables(use[0].variables)
        for p in use[1:]:
            env = VariableEnv.join_variables_simple(env, p.variables)
        return env
    def _eval_on_exit_value(self, expr, fn_cfg, *, normal_only: bool = True):
        """
        pred별로 평가해서 값을 join 하지 말고,
        pred들의 env를 Helper join으로 합친 다음 expr을 '한 번'만 평가.
        """
        exit_env = self._exit_env(fn_cfg, normal_only=normal_only)
        val = self.evaluate_guardian_expression(expr, exit_env, None, None)
        return self._materialize(val)

    # DURING predicate evaluator
    def _eval_during_predicate(self, pred: dict, *, line_no: int, cfg_node: CFGNode) -> dict:
        k = pred["kind"]
        if k == "beforeAfter":
            return self.verify_during_before_after(var_ref=pred["var"], comp_op=pred["op"], line_no=line_no,
                                                   cfg_node=cfg_node)
        if k == "assignCurrent":
            return self.verify_during_assign_current(var_ref=pred["var"], comp_op=pred["op"], line_no=line_no,
                                                     cfg_node=cfg_node)
        if k == "retExpr":
            return self.verify_during_return_expression(comp_op=pred["op"], value_expr=pred["rhs"], line_no=line_no,
                                                        cfg_node=cfg_node)
        if k == "retVar":
            return self.verify_during_return_variable(var_ref=pred["lhs"], comp_op=pred["op"], value_expr=pred["rhs"],
                                                      line_no=line_no, cfg_node=cfg_node)
        if k == "direct":
            return self.verify_during_direct_comparison(lhs_expr=pred["lhs"], comp_op=pred["op"], rhs_expr=pred["rhs"],
                                                        line_no=line_no, cfg_node=cfg_node)
        if k == "nonzero":
            return self._eval_nonzero(pred["expr"], cfg_node.variables, line_no)
        return self._err("duringPredicate", f"unknown DURING predicate kind: {k}", line_no)

    # POST predicate evaluator
    def _eval_post_predicate(self, pred: dict, *, line_no: int, fn_cfg) -> dict:
        k = pred["kind"]
        if k == "entryExit":
            return self.verify_post_entry_exit(var_ref=pred["var"], comp_op=pred["op"], line_no=line_no, fn_cfg=fn_cfg)
        if k == "changed":
            return self.verify_post_changed(var_ref=pred["var"], expect_changed=pred["expect_changed"], line_no=line_no, fn_cfg=fn_cfg)
        if k == "retExpr":
            return self.verify_post_return_expression(comp_op=pred["op"], value_expr=pred["rhs"], line_no=line_no,
                                                      fn_cfg=fn_cfg)
        if k == "retVar":
            return self.verify_post_return_variable(var_ref=pred["lhs"], comp_op=pred["op"], value_expr=pred["rhs"],
                                                    line_no=line_no, fn_cfg=fn_cfg)
        if k == "direct":
            return self.verify_post_direct_comparison(lhs_expr=pred["lhs"], comp_op=pred["op"], rhs_expr=pred["rhs"],
                                                      line_no=line_no, fn_cfg=fn_cfg)
        if k == "nonzero":
            exit_env = self._exit_env(fn_cfg)
            return self._eval_nonzero(pred["expr"], exit_env, line_no)
        return self._err("postPredicate", f"unknown POST predicate kind: {k}", line_no)

    def _eval_nonzero(self, expr, vars_env, line_no: int) -> dict:
        """intentValue를 평가하여 nonzero 여부 판정."""
        val = self.evaluate_guardian_expression(expr, vars_env, None, None)
        val = self._materialize(val)

        if hasattr(val, "min_value") and hasattr(val, "max_value"):
            lo, hi = val.min_value, val.max_value
            if lo > 0 or hi < 0:
                satisfied, violated = True, False
            elif lo == 0 and hi == 0:
                satisfied, violated = False, True
            else:
                satisfied, violated = False, False
        elif isinstance(val, bool):
            satisfied, violated = val, not val
        else:
            satisfied, violated = False, False

        if satisfied:
            status = "success"
        elif violated:
            status = "failure"
        else:
            status = "warning"

        return {
            "status": status,
            "kind": "nonzero",
            "line": line_no,
            "details": {"satisfied": satisfied, "violated": violated, "value": str(val)},
            "message": f"{self._pretty_expr(expr)} is {'nonzero' if satisfied else 'zero' if violated else 'possibly zero'} ({val})"
        }

    def _tri_state(self, res: dict) -> str:
        """
        result → 'satisfied' / 'violated' / 'warning'
        (기존 verify_*들의 details에 들어있는 cmp 필드 기반)
        """
        d = res.get("details", {})
        if d.get("satisfied"):
            return "satisfied"
        if d.get("violated"):
            return "violated"
        if d.get("warning"):
            return "warning"
        # fallback: status
        st = res.get("status")
        if st == "success":    return "satisfied"
        if st == "violation":  return "violated"
        return "warning"

    def _status_from_state(self, st: str) -> str:
        return "success" if st == "satisfied" else ("violation" if st == "violated" else "warning")

    # GuardianVerificationEngine 내부에 추가

    def _status_from_cmp(self, cmp: dict) -> str:
        if cmp.get("satisfied"): return "success"
        if cmp.get("violated"):  return "violation"
        if cmp.get("warning"):   return "warning"
        return "warning"

    def _prob_true_from_cmp(self, cmp: dict) -> float:
        # prob_true = 조건이 참일 확률 (0~1)
        if cmp.get("satisfied"): return 1.0
        if cmp.get("violated"):  return 0.0
        return float(cmp.get("prob_true", 0.5))

    def _prob_true_from_result(self, res: dict) -> float:
        # verify_* 결과에서 details에 펼쳐 넣은 cmp를 그대로 사용
        d = res.get("details", {})
        if d.get("satisfied"): return 1.0
        if d.get("violated"):  return 0.0
        if "prob_true" in d:  return float(d["prob_true"])
        # 비교 에러/정보부족 등
        return 0.5

    # GuardianVerificationEngine 내부에 추가

    def _mk_interval(self, lo, hi):
        """정수 구간 [lo,hi] (inclusive). lo>hi면 None."""
        if lo is None or hi is None:
            return None
        lo = int(lo)
        hi = int(hi)
        if lo > hi:
            return None
        return [lo, hi]

    def _intersect(self, a, b):
        """a,b = [lo,hi]. 교집합 반환 또는 None."""
        if a is None or b is None:
            return None
        return self._mk_interval(max(a[0], b[0]), min(a[1], b[1]))

    def _minus(self, a, b):
        """정수 구간 차집합: a\b → 구간 리스트"""
        if a is None:
            return []
        inter = self._intersect(a, b)
        if inter is None:
            return [a]
        out = []
        if a[0] < inter[0]:
            out.append([a[0], inter[0] - 1])
        if inter[1] < a[1]:
            out.append([inter[1] + 1, a[1]])
        return out

    def _false_regions_for_op(self, L, R, op: str) -> dict:
        """
        L,R: Interval-like (min_value, max_value).
        warning일 때 '거짓이 될 수 있는' 후보 구간을 방향별로 분리해서 리턴.
        반환 예:
          { "lower_violation": [lo, hi] or None,   # intent 하한 아래로 벗어난 구간
            "upper_violation": [lo, hi] or None,   # intent 상한 위로 벗어난 구간
            "overlap_zone":    [lo, hi] or None,   # 겹치는 불확정 구간
            "notes": "inclusive integer intervals" }
        """
        empty = {"lower_violation": None, "upper_violation": None,
                 "overlap_zone": None, "notes": "unknown bounds"}

        if (getattr(L, "min_value", None) is None or getattr(L, "max_value", None) is None or
                getattr(R, "min_value", None) is None or getattr(R, "max_value", None) is None):
            return empty

        a = [int(L.min_value), int(L.max_value)]
        b = [int(R.min_value), int(R.max_value)]
        lower = None   # actual이 intent 하한 아래로 벗어남
        upper = None   # actual이 intent 상한 위로 벗어남
        overlap = None # 겹치는 불확정 구간

        if op == '>':
            # L > R 가 거짓인 구간: L이 R 이하인 부분
            # lower: L의 하단이 R.min 이하 → 확실히 거짓
            # overlap: L과 R이 겹치는 구간 → 불확정
            if a[0] <= b[0]:
                lower = self._mk_interval(a[0], min(a[1], b[0]))
            overlap = self._mk_interval(max(a[0], b[0] + 1), min(a[1], b[1]))

        elif op == '<':
            # L < R 가 거짓인 구간: L이 R 이상인 부분
            # upper: L의 상단이 R.max 이상 → 확실히 거짓
            # overlap: L과 R이 겹치는 구간 → 불확정
            if a[1] >= b[1]:
                upper = self._mk_interval(max(a[0], b[1]), a[1])
            overlap = self._mk_interval(max(a[0], b[0]), min(a[1], b[1] - 1))

        elif op == '>=':
            # L >= R 가 거짓인 구간: L < R 인 부분
            if a[0] < b[0]:
                lower = self._mk_interval(a[0], min(a[1], b[0] - 1))
            overlap = self._mk_interval(max(a[0], b[0]), min(a[1], b[1]))

        elif op == '<=':
            # L <= R 가 거짓인 구간: L > R 인 부분
            if a[1] > b[1]:
                upper = self._mk_interval(max(a[0], b[1] + 1), a[1])
            overlap = self._mk_interval(max(a[0], b[0]), min(a[1], b[1]))

        elif op == '==':
            # 거짓은 '겹침 밖' 영역
            inter = self._intersect(a, b)
            if inter:
                if a[0] < inter[0]:
                    lower = self._mk_interval(a[0], inter[0] - 1)
                if a[1] > inter[1]:
                    upper = self._mk_interval(inter[1] + 1, a[1])
                overlap = inter  # 겹치는 구간 (같을 수 있는 유일한 구간)
            else:
                # 완전 분리 → 전체가 violation
                if a[1] < b[0]:
                    lower = a
                else:
                    upper = a

        elif op == '!=':
            # 거짓은 '같을 수 있는' 영역 = 겹치는 부분
            inter = self._intersect(a, b)
            overlap = inter  # 겹치는 구간이 false-support

        elif op == 'in':
            # L ⊆ R 가 거짓인 구간: L이 R 밖으로 벗어나는 부분
            if a[0] < b[0]:
                lower = self._mk_interval(a[0], min(a[1], b[0] - 1))
            if a[1] > b[1]:
                upper = self._mk_interval(max(a[0], b[1] + 1), a[1])

        elif op == 'not in':
            # L ⊄ R 가 거짓인 구간: L이 R 안에 들어가는 부분
            inter = self._intersect(a, b)
            overlap = inter  # R 안에 들어가는 부분이 위험

        return {"lower_violation": lower, "upper_violation": upper,
                "overlap_zone": overlap, "notes": "inclusive integer intervals"}


