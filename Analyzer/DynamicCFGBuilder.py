from __future__ import annotations

# Analyzer/CFGBuilder.py
from Utils.CFG import CFGNode, FunctionCFG
from Utils.Helper import VariableEnv
from Domain.IR import Expression
from Domain.Variable import Variables

from typing import TYPE_CHECKING

if TYPE_CHECKING:                                         # 타입 검사 전용
     from Analyzer.ContractAnalyzer import ContractAnalyzer

class DynamicCFGBuilder:
    def __init__(self, an: "ContractAnalyzer"):
        self.an = an                    # Engine 을 새로 만들지 않습니다.

    @property
    def eng(self):
        return self.an.engine

    def build_variable_declaration(
            self,
            *,
            cur_block: CFGNode,
            var_name: str,
            type_obj,
            init_expr,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict
    ) -> CFGNode:
        """
        Create a new statement block for variable declaration.
        New strategy: Always create a new block between cur_block and its successors.
        (Phase 3a) 구조·IR 만 — 변수 객체·값은 디버깅 해석이 만든다.
        """
        # 1) Create new statement block
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="VarDecl"
        )

        # 2) Add declaration statement to the new block
        new_block.add_variable_declaration_statement(type_obj, var_name, init_expr, line_no)

        # interface 타입 지역변수는 @IReturn 조회용으로 별도 등록
        if hasattr(type_obj, 'typeCategory') and type_obj.typeCategory == "interface":
            ifc_name = getattr(type_obj, 'interfaceName', None)
            if ifc_name:
                fcfg.interface_var_types[var_name] = ifc_name

        return new_block

    def build_variable_declaration_tuple(
            self,
            *,
            cur_block: CFGNode,
            decls: list,        # [(type_obj, var_name), ...] — 빈 슬롯 제외
            init_expr,
            tuple_lhs=None,     # (Phase 2) 슬롯 위치 보존 TupleExpression — 있으면 `(a, , b) = init_expr` 대입문 추가
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict
    ) -> CFGNode:
        """
        Create a new statement block for tuple variable declaration.
        Example: (bool success, bytes memory data) = addr.call(...)

        This handles all variables in the tuple as a single statement.
        """
        # 1) Create new statement block
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="VarDeclTuple"
        )

        # 2) 변수별 선언문 (초기화는 아래 튜플 대입문이 한다)
        for type_obj, var_name in decls:
            new_block.add_variable_declaration_statement(type_obj, var_name, None, line_no)

            # interface 타입 지역변수는 @IReturn 조회용으로 별도 등록
            if hasattr(type_obj, 'typeCategory') and type_obj.typeCategory == "interface":
                ifc_name = getattr(type_obj, 'interfaceName', None)
                if ifc_name:
                    fcfg.interface_var_types[var_name] = ifc_name

        # 3) (Phase 2) 초기화 = 튜플 대입문. 선언문 뒤에 두어 해석기가 init_expr 를 한 번 평가하고
        #    슬롯별로 나눠 넣는다(Update.update_left_var 의 TupleExpressionContext 경로).
        if tuple_lhs is not None and init_expr is not None:
            new_block.add_assign_statement(tuple_lhs, '=', init_expr, line_no)

        return new_block

    def build_assignment_statement(
            self,
            *,
            cur_block: CFGNode,
            expr: Expression,  # a = b, a[i] += 1 …
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> CFGNode:
        """
        Create a new statement block for assignment.
        New strategy: Always create a new block between cur_block and its successors.
        """
        # 1) Create new statement block
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="Assign"
        )

        # 2) Add statement to the new block
        new_block.add_assign_statement(expr.left, expr.operator, expr.right, line_no)
        
        return new_block

    def build_unary_statement(
            self,
            *,
            cur_block: CFGNode,
            expr: Expression,  # ++x  /  delete y 등
            op_token: str,  # '++' / '--' / 'delete'
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> CFGNode:
        """
        Create a new statement block for unary operations.
        New strategy: Always create a new block between cur_block and its successors.
        """
        # 1) Create new statement block
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="Unary"
        )

        # 2) Add statement to the new block
        new_block.add_unary_statement(expr, op_token, line_no)
        
        return new_block

    def build_function_call_statement(
        self,
        *,
        cur_block: CFGNode,
        expr: Expression,          # foo(a,b)   전체 Expression
        line_no: int,
        fcfg: FunctionCFG,
        line_info: dict,
    ) -> CFGNode:
        """
        Create a new statement block for function call.
        New strategy: Always create a new block between cur_block and its successors.
        """
        # 1) Create new statement block
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="FuncCall"
        )

        # 2) Add function call statement to the new block
        new_block.add_function_call_statement(expr, line_no)

        # 3) Update FCG
        fcfg.update_block(new_block)

        return new_block

    def build_expression_statement(self, *, cur_block: CFGNode, expr, line_no: int,
                                   fcfg: FunctionCFG, line_info: dict) -> CFGNode:
        """값만 있는 식 문장 — 함수 호출문과 같은 배선(새 문장 블록)."""
        new_block = self.insert_new_statement_block(
            pred_block=cur_block, fcfg=fcfg, line_no=line_no, line_info=line_info, tag="Expr")
        new_block.add_expression_statement(expr, line_no)
        fcfg.update_block(new_block)
        return new_block

    def build_emit_statement(self, *, cur_block: CFGNode, event_name, arguments, line_no: int,
                             fcfg: FunctionCFG, line_info: dict) -> CFGNode:
        """emit 문 블록(Phase 2) — 함수 호출문과 같은 삽입 전략."""
        new_block = self.insert_new_statement_block(pred_block=cur_block, fcfg=fcfg, line_no=line_no,
                                                    line_info=line_info, tag="Emit")
        new_block.add_emit_statement(event_name, arguments, line_no)
        fcfg.update_block(new_block)
        return new_block

    def build_if_statement(
            self,
            *,
            cur_block: CFGNode,
            condition_expr: Expression,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
            end_line: int | None = None,  # 있으면 거기도 매핑해도 됨 (선택)
    ) -> CFGNode:
        G = fcfg.graph
        old_succs = list(G.successors(cur_block))
        for s in old_succs:
            G.remove_edge(cur_block, s)

        cond = CFGNode(f"if_condition_{line_no}", condition_node=True,
                       condition_node_type="if", src_line=line_no, is_loop_body=cur_block.is_loop_body)
        cond.condition_expr = condition_expr

        t_blk = CFGNode(f"if_true_{line_no}", branch_node=True, is_true_branch=True, src_line=line_no,
                        is_loop_body=cur_block.is_loop_body)

        f_blk = CFGNode(f"if_false_{line_no}", branch_node=True, is_true_branch=False, src_line=line_no,
                        is_loop_body=cur_block.is_loop_body)

        # ★ join의 src_line은 end_line으로 설정 (line_info[end_line]에 등록되므로 _shift_meta와 일치시킴)
        join_src_line = end_line if end_line is not None else line_no
        join = CFGNode(f"if_join_{line_no}", join_point_node=True, src_line=join_src_line,
                       is_loop_body=cur_block.is_loop_body)
        # (Phase 3b, D8) 분기 블록이 끝나는 곳 — 해석기는 여기서 cond 시점에 없던(블록 안에서 선언된) 변수를 버린다.
        join.scope_anchor = cond

        for n in (cond, t_blk, f_blk, join):
            G.add_node(n)

        G.add_edge(cur_block, cond)
        G.add_edge(cond, t_blk, condition=True)
        G.add_edge(cond, f_blk, condition=False)
        G.add_edge(t_blk, join)
        G.add_edge(f_blk, join)
        for s in old_succs:
            G.add_edge(join, s)

        # line_info: start line에는 cond만, end line에는 join만
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        # end_line에 join 추가
        if end_line is not None:
            bc_end = line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})
            bc_end["cfg_nodes"].append(join)

        return join

    def build_else_if_statement(
            self,
            *,
            prev_cond: CFGNode,
            outer_join: CFGNode | None,  # ★ 추가: get_current_block에서 전달받음
            condition_expr: Expression,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
            end_line: int | None = None,
    ) -> CFGNode:
        G = fcfg.graph

        # ① 이전 False edge 제거
        for s in list(G.successors(prev_cond)):
            if G.edges[prev_cond, s].get("condition") is False:
                G.remove_edge(prev_cond, s)
                if len(list(G.predecessors(s))) == 0:
                    for nxt in list(G.successors(s)):
                        G.remove_edge(s, nxt)
                    G.remove_node(s)
                break

        # ② 새 cond / t / f / local-join
        cond = CFGNode(f"else_if_condition_{line_no}", condition_node=True,
                       condition_node_type="else if", src_line=line_no,
                       is_loop_body=prev_cond.is_loop_body)
        cond.condition_expr = condition_expr

        t_blk = CFGNode(f"else_if_true_{line_no}", branch_node=True, is_true_branch=True, src_line=line_no,
                        is_loop_body=prev_cond.is_loop_body)

        f_blk = CFGNode(f"else_if_false_{line_no}", branch_node=True, is_true_branch=False, src_line=line_no,
                        is_loop_body=prev_cond.is_loop_body)

        local_join = CFGNode(f"else_if_join_{line_no}", join_point_node=True, src_line=line_no,
                             is_loop_body=prev_cond.is_loop_body)
        local_join.scope_anchor = cond   # (Phase 3b, D8)

        for n in (cond, t_blk, f_blk, local_join):
            G.add_node(n)

        G.add_edge(prev_cond, cond, condition=False)
        G.add_edge(cond, t_blk, condition=True)
        G.add_edge(cond, f_blk, condition=False)
        G.add_edge(t_blk, local_join)
        G.add_edge(f_blk, local_join)

        # ③ outer-join: 이미 get_current_block에서 찾아서 전달받음
        if outer_join is None:
            raise ValueError("else-if: outer join not found (passed from get_current_block)")

        G.add_edge(local_join, outer_join)


        # ⑤ line_info: start line에는 cond만, end line에는 local_join만
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        if end_line is not None:
            bc_end = line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})
            bc_end["cfg_nodes"].append(local_join)

        return local_join

    def build_else_statement(
            self,
            *,
            cond_node: CFGNode,
            outer_join: CFGNode | None,  # ★ 추가: get_current_block에서 전달받음
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
            end_line: int | None = None,
    ) -> CFGNode:
        G = fcfg.graph

        # ① 기존 False successor 제거(+고아 정리)
        old_false = None
        for s in list(G.successors(cond_node)):
            if G.edges[cond_node, s].get("condition") is False:
                old_false = s
                G.remove_edge(cond_node, s)
                break
        if old_false is not None and len(list(G.predecessors(old_false))) == 0:
            for nxt in list(G.successors(old_false)):
                G.remove_edge(old_false, nxt)
            G.remove_node(old_false)

        # ② target-join: get_current_block에서 전달받은 것을 우선 사용
        target_join = outer_join
        if target_join is None:
            # fallback: 그래프 기반 검색
            target_join = self._outer_join_from_graph(cond_node, fcfg)
        if target_join is None:
            raise ValueError("else: target join not found (passed from get_current_block)")

        # ③ else 블록 생성 및 연결
        else_blk = CFGNode(f"else_block_{line_no}", branch_node=True, is_true_branch=False, src_line=line_no
                           ,is_loop_body=cond_node.is_loop_body)

        G.add_node(else_blk)
        G.add_edge(cond_node, else_blk, condition=False)
        G.add_edge(else_blk, target_join)


        # ⑤ line_info
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(else_blk)
        if end_line is not None:
            bc_end = line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})
            bc_end["cfg_nodes"].append(target_join)

        return target_join

    # DynamicCFGBuilder.py (시그니처/본문/리턴 변경)
    def build_while_statement(
            self,
            *,
            cur_block: CFGNode,
            condition_expr: Expression,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
            end_line: int | None = None,  # ★ 추가: 루프 끝 라인
    ) -> CFGNode:  # ★ 변경: exit 노드 반환
        """
        cur_block ─▶ join ─▶ cond ─▶ true(body) ───┐
                              │                    │
                              └──▶ false(exit) ────┘
        """

        G = fcfg.graph

        # ── ① join-노드 --------------------------------------------------
        join = CFGNode(f"while_join_{line_no}", fixpoint_evaluation_node=True)
        G.add_node(join)

        # cur_block → join
        old_succs = list(G.successors(cur_block))
        for s in old_succs:
            G.remove_edge(cur_block, s)
        G.add_edge(cur_block, join)

        # ── ② condition-노드 --------------------------------------------
        cond = CFGNode(
            f"while_cond_{line_no}",
            condition_node=True,
            condition_node_type="while",
            src_line=line_no
        )
        cond.condition_expr = condition_expr
        G.add_node(cond)
        G.add_edge(join, cond)

        # ── ③ body / exit 블록 ------------------------------------------
        body = CFGNode(f"while_body_{line_no}", branch_node=True, is_true_branch=True)
        body.is_loop_body = True

        exit_ = CFGNode(
            f"while_exit_{line_no}",
            branch_node=True,
            is_true_branch=False,
            loop_exit_node=True,
            src_line=(end_line if end_line is not None else line_no)  # ★ end_line 반영
        )
        exit_.scope_anchor = join   # (Phase 3b, D8) 루프 진입 시점 변수만 루프 밖으로

        G.add_nodes_from([body, exit_])
        G.add_edge(cond, body, condition=True)
        G.add_edge(cond, exit_, condition=False)

        # body → join (back-edge)
        G.add_edge(body, join)

        # exit_ → 이전 cur_block successors
        for s in old_succs:
            G.add_edge(exit_, s)

        # ── ④ line_info ------------------------------------------------
        # 시작 라인: 조건 노드만 매핑
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        # 끝 라인: loop-exit 노드만 매핑
        if end_line is not None:
            bc_end = line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})
            bc_end["cfg_nodes"].append(exit_)

        return exit_  # ★ seed 용으로 exit 반환

    # DynamicCFGBuilder.py (시그니처/본문/리턴 변경)
    def build_for_statement(
            self,
            *,
            cur_block: CFGNode,
            init_node: CFGNode | None,
            cond_expr: Expression | None,
            incr_node: CFGNode | None,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
            end_line: int | None = None,  # ★ 추가: 루프 끝 라인
    ) -> CFGNode:  # ★ 변경: exit 노드 반환
        """
        pre ─▶ [init] ─▶ join ─▶ cond ─▶ body ─▶ incr
                               │         ▲        │
                               └─────────┘        │
                                         └────────┘
        """
        from typing import cast, Optional

        G = fcfg.graph
        pre = cur_block

        # ★ pre 의 **기존 후속 전부**를 떼어내 루프 뒤(exit_)로 옮긴다 — 증분 수술의 splice.
        #   `build_while_statement` 와 동일한 방식이다(그쪽은 처음부터 이렇게 했다).
        #
        # ⚠ 예전에는 **`EXIT` 이라는 이름의 노드 하나만** 찾아서 옮겼다. 최상위 `for` 은
        #   슬라이서가 닫는 중괄호를 미리 만들어 두는 덕에 `pre` 의 후속이 **항상 `EXIT`** 이라
        #   우연히 맞았다. 그런데 `for` 이 **다른 블록 안에서** 열리면(`if` 의 then, 다른 루프의
        #   본문 …) `pre` 의 후속이 그 블록의 continuation(`if_join_*`, `for_incr_*` …)이므로
        #   두 가지가 동시에 깨졌다:
        #     ① 떼지 않은 헌 엣지가 남아 **루프를 우회하는 경로**가 됐다
        #     ② `exit_` 에 붙일 곳을 못 찾아 **나가는 엣지가 없는 sink** 가 됐다
        #   → 루프가 올바르게 계산한 값이 어디로도 흐르지 못하고, 뒷 문장은 우회 경로의
        #     옛 값만 받았다(`for`-in-`if` 에서 `acc=[7,7]`). ⑭ / decision #49.
        old_succs = list(G.successors(pre))
        for s in old_succs:
            G.remove_edge(pre, s)

        # ── ① join ------------------------------------------------------
        join = CFGNode(f"for_join_{line_no}", fixpoint_evaluation_node=True)

        # ── ② condition -------------------------------------------------
        cond = CFGNode(
            f"for_cond_{line_no}",
            condition_node=True,
            condition_node_type="for",
            src_line=line_no
        )
        cond.condition_expr = cond_expr

        # ── ③ body & exit ----------------------------------------------
        body = CFGNode(f"for_body_{line_no}", branch_node=True, is_true_branch=True)
        body.is_loop_body = True

        exit_ = CFGNode(
            f"for_exit_{line_no}",
            branch_node=True,
            is_true_branch=False,
            loop_exit_node=True,
            src_line=(end_line if end_line is not None else line_no)  # ★ end_line 반영
        )
        exit_.scope_anchor = join   # (Phase 3b, D8) for-init 변수도 루프 밖에선 사라진다

        # ── ④ 그래프 ----------------------------------------------------
        for n in (join, cond, body, exit_):
            G.add_node(n)
        if init_node:
            G.add_node(init_node)
        if incr_node:
            G.add_node(incr_node)

        # pre → init? join?
        if init_node:
            G.add_edge(pre, init_node)
            G.add_edge(init_node, join)
        else:
            G.add_edge(pre, join)

        # join → cond
        G.add_edge(join, cond)

        # cond True/False
        G.add_edge(cond, body, condition=True)
        G.add_edge(cond, exit_, condition=False)

        # body → incr → join (증감식이 없으면 body → join)
        if incr_node:
            G.add_edge(body, incr_node)
            G.add_edge(incr_node, join)
        else:
            G.add_edge(body, join)

        # exit_ → pre 의 원래 후속들 (함수 EXIT 도 그중 하나일 뿐이다)
        for s in old_succs:
            G.add_edge(exit_, s)

        # ── ⑤ line_info ------------------------------------------------
        # 시작 라인: 조건 노드만 매핑
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        # 끝 라인: loop-exit 노드만 매핑
        if end_line is not None:
            bc_end = line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})
            bc_end["cfg_nodes"].append(exit_)

        return exit_  # ★ seed 용으로 exit 반환

    def build_continue_statement(
            self,
            *,
            cur_block: CFGNode,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> CFGNode:  # 반환: 해당 루프의 loop-exit 노드 (seed 용)

        G = fcfg.graph

        # 1) pred(cur_block)과 그 후속 사이에 새 블록 삽입
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="Continue"
        )
        # 2) 새 블록에 statement 추가
        new_block.add_continue_statement(line_no)

        # 3) 감싸는 루프 → new_block 의 모든 후속을 제거하고 루프의 다음 반복 지점으로 재배선
        _cond, join, exit_node = self._enclosing_loop(line_no, fcfg, "continue")

        # (Phase 4b) for 루프의 continue 는 증감식을 거쳐 join 으로 간다. 예전에는 join 으로
        #   직행해 continue 경로에서 i++ 가 빠졌다(unsound: 실제로 도달하는 i 값이 누락).
        target = next((p for p in G.predecessors(join)
                       if getattr(p, "is_for_increment", False)), join)

        old_succs = list(G.successors(new_block))  # insert가 붙여 놓은 원래 후속들
        for s in old_succs:
            G.remove_edge(new_block, s)
        G.add_edge(new_block, target)

        # line_info 는 insert_new_statement_block 에서 이미 추가됨
        return exit_node  # seed

    def _enclosing_loop(self, line_no: int, fcfg: FunctionCFG, kw: str) -> tuple[CFGNode, CFGNode, CFGNode]:
        """`line_no` 의 `continue`/`break` 가 가리키는 루프 = **문법적으로 감싸는 가장 안쪽 루프** (Solidity 정의).
        반환 (조건 노드, join(fixpoint) 노드, loop-exit 노드).

        (Phase 4b) 예전엔 new_block 에서 **역방향 DFS** 로 처음 만나는 fixpoint join 을 골랐다. 안쪽 루프가 **앞에서 이미
        닫힌** 경우(`for i { for j {…} if (c) { continue; } }`) 역방향 경로가 안쪽 루프의 exit → 헤더 → join 으로
        들어가 continue/break 가 **안쪽 루프**에 연결됐다. 소스의 중괄호 체인을 따라 올라가 처음 나오는 루프 헤더를 쓴다.
        """
        ca = self.an
        lines = ca.sa.full_code_lines
        ln = ca._enclosing_open_brace_line(line_no, inclusive=False)
        while ln is not None:
            ctx = ca.determine_top_level_context(lines.get(ln, ""))
            if ctx in ("for", "while"):
                cond = next((n for n in (ca.sa.line_info.get(ln, {}).get("cfg_nodes") or [])
                             if getattr(n, "condition_node", False)
                             and getattr(n, "condition_node_type", None) == ctx and n in fcfg.graph), None)
                if cond is None:
                    raise ValueError(f"line {line_no}: {kw} — line {ln} 의 {ctx} 조건 노드가 CFG 에 없음 (내부 오류)")
                G = fcfg.graph
                join = next((p for p in G.predecessors(cond) if getattr(p, "fixpoint_evaluation_node", False)), None)
                exit_ = next((s for s in G.successors(cond)
                              if G[cond][s].get("condition") is False and getattr(s, "loop_exit_node", False)), None)
                if join is None or exit_ is None:
                    raise ValueError(f"line {line_no}: {kw} — line {ln} 루프의 join/exit 노드 없음 (내부 오류)")
                return cond, join, exit_
            if ctx == "do_while":
                # do-while 의 조건 노드는 `} while (...)` 줄이 들어올 때 생긴다 — Phase 6(D6) 에서 연결.
                raise ValueError(f"line {line_no}: {kw} inside do-while is not supported yet (Phase 6)")
            if ctx in ca._ASM_BOUNDARY:
                break
            ln = ca._enclosing_open_brace_line(ln, inclusive=False)
        raise ValueError(f"line {line_no}: '{kw}' outside of a loop (compile error)")

    def build_return_statement(
            self,
            *,
            cur_block: CFGNode,
            return_expr: Expression | None,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> list[CFGNode]:  # 반환: 재배선 전 ‘원래 후속’(seed 용)

        G = fcfg.graph

        # 1) 새 블록 삽입
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="Return"
        )
        # 2) statement 추가
        new_block.add_return_statement(return_expr, line_no)

        # 3) seed 용으로 현재(new_block) 후속 = “원래 cur_block 의 후속” 확보
        old_succs = list(G.successors(new_block))

        # 4) RETURN_EXIT 로 재배선
        return_exit_n = fcfg.get_return_exit_node()
        for s in old_succs:
            G.remove_edge(new_block, s)
        G.add_edge(new_block, return_exit_n)


        # line_info 는 insert 헬퍼가 이미 등록
        return old_succs  # seed

    def build_break_statement(
            self,
            *,
            cur_block: CFGNode,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> CFGNode:  # 반환: loop-exit (seed 용)

        G = fcfg.graph

        # 1) 새 블록 삽입
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="Break"
        )
        # 2) statement 추가
        new_block.add_break_statement(line_no)

        # 3) 감싸는 루프의 loop-exit (Phase 4b: 문법적 최내곽 루프 — `_enclosing_loop` 주석 참고)
        _cond, _join, exit_node = self._enclosing_loop(line_no, fcfg, "break")

        # 4) new_block 의 기본 후속 제거 → loop-exit 으로 재배선
        old_succs = list(G.successors(new_block))
        for s in old_succs:
            G.remove_edge(new_block, s)
        G.add_edge(new_block, exit_node)

        return exit_node  # seed

    def build_revert_statement(
            self,
            *,
            cur_block: CFGNode,
            revert_id: str | None,
            string_literal: str | None,
            call_args: list[Expression] | None,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> list[CFGNode]:  # ★ 변경: 이전 succ 들을 반환

        G = fcfg.graph

        # 1) 새 블록 삽입
        new_block = self.insert_new_statement_block(
            pred_block=cur_block,
            fcfg=fcfg,
            line_no=line_no,
            line_info=line_info,
            tag="revert"
        )
        # 2) statement 추가
        new_block.add_revert_statement(revert_id, string_literal, call_args, line_no)

        # ★ seed 용으로 '재배선 전' succ 보관 — insert_new_statement_block 이 이미
        # cur_block -> new_block -> old_succs 로 재배선해둔 상태라, 진짜 "재배선 전"
        # succ 는 cur_block 이 아니라 new_block 의 후속으로 남아있다.
        g = fcfg.graph
        old_succs = list(g.successors(new_block))

        # ② edge --> ERROR_EXIT
        # revert 문 자체(new_block)에서 ERROR 로 가야 함 — cur_block 에서 직접 ERROR 로
        # 보내면 new_block 이 그래프에서 고아가 돼(아무도 안 가리킴), new_block 을
        # predecessor 로 기다리는 join 노드가 영원히 unvisited 로 남아 _run_worklist 가
        # 무한 재큐잉에 빠진다(livelock 근본 원인, Guardian 2026-09-02 확인).
        error_exit_n = fcfg.get_error_exit_node()
        for s in old_succs:
            g.remove_edge(new_block, s)
        g.add_edge(new_block, error_exit_n)

        # ③ 데이터-플로우
        fcfg.update_block(new_block)

        # ④ line_info — 이 줄을 실제로 대표하는 노드는 new_block(revert 문 자체)임
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(new_block)

        return old_succs  # ★ seed

    def build_require_statement(
            self,
            *,
            cur_block: CFGNode,
            condition_expr: Expression,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> list[CFGNode]:  # ★ 변경: true-분기 succ 들 반환

        G = fcfg.graph

        # ── 조건 노드
        cond = CFGNode(
            name=f"require_condition_{line_no}",
            condition_node=True,
            condition_node_type="require",
            src_line=line_no,
            is_loop_body=cur_block.is_loop_body
        )
        cond.condition_expr = condition_expr

        # ── True 블록
        t_blk = CFGNode(
            name=f"require_true_{line_no}",
            branch_node=True,
            is_true_branch=True,
            src_line=line_no,
            is_loop_body=cur_block.is_loop_body
        )

        # ── 재배선 (old_succ 보관)
        old_succ = list(G.successors(cur_block))
        for s in old_succ:
            G.remove_edge(cur_block, s)

        G.add_node(cond)
        G.add_edge(cur_block, cond)

        # False → ERROR
        error_exit_n = fcfg.get_error_exit_node()
        G.add_edge(cond, error_exit_n, condition=False)

        # True → t_blk
        G.add_node(t_blk)
        G.add_edge(cond, t_blk, condition=True)

        # t_blk → 원래 succ (없으면 EXIT)
        true_succs = old_succ if old_succ else [fcfg.get_exit_node()]
        for s in true_succs:
            G.add_edge(t_blk, s)

        # 데이터-플로우
        fcfg.update_block(cur_block)

        # line_info
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        # ★ seed: True 분기가 향하는 후속 노드들
        #  (EXIT는 sink라 seed에 넣어도 자동 필터링됨)
        return true_succs

    def build_assert_statement(
            self,
            *,
            cur_block: CFGNode,
            condition_expr: Expression,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ) -> list[CFGNode]:  # ★ 변경: true-분기 succ 들 반환

        G = fcfg.graph

        # ── 조건 노드
        cond = CFGNode(
            name=f"assert_condition_{line_no}",
            condition_node=True,
            condition_node_type="assert",
            src_line=line_no,
            is_loop_body=cur_block.is_loop_body
        )
        cond.condition_expr = condition_expr

        # ── True 블록
        t_blk = CFGNode(
            name=f"assert_true_{line_no}",
            branch_node=True,
            is_true_branch=True,
            src_line=line_no,
            is_loop_body=cur_block.is_loop_body
        )

        # ── 재배선 (old_succ 보관)
        old_succ = list(G.successors(cur_block))
        for s in old_succ:
            G.remove_edge(cur_block, s)

        G.add_node(cond)
        G.add_edge(cur_block, cond)

        # False → ERROR
        exit_n = fcfg.get_error_exit_node()
        G.add_edge(cond, exit_n, condition=False)

        # True → t_blk
        G.add_node(t_blk)
        G.add_edge(cond, t_blk, condition=True)

        # t_blk → 원래 succ (없으면 EXIT)
        true_succs = old_succ if old_succ else [fcfg.get_exit_node()]
        for s in true_succs:
            G.add_edge(t_blk, s)

        # 데이터-플로우, line_info
        fcfg.update_block(cur_block)
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        return true_succs  # ★ seed

    def build_modifier_placeholder(
            self,
            *,
            cur_block: CFGNode,
            fcfg: FunctionCFG,
            line_no: int,
            line_info: dict,
    ) -> None:
        """
        • 현재 modifier-CFG에서 식별자 ‘_’(place-holder)를 만나면
          =⇒ 새로운 CFGNode("MOD_PLACEHOLDER_n") 를 cur_block 뒤에 삽입.

        cur_block ─▶ placeholder ─▶ (원래 succ …)
        """
        # ① 새 노드 (이전 block의 variables 상속)
        idx = len(getattr(fcfg, "placeholders", []))
        ph = CFGNode(f"MOD_PLACEHOLDER_{idx}")

        # ② 그래프 재배선
        G = fcfg.graph
        succs = list(G.successors(cur_block))

        G.add_node(ph)
        G.add_edge(cur_block, ph)
        for s in succs:
            G.remove_edge(cur_block, s)
            G.add_edge(ph, s)

        # ③ bookkeeping
        fcfg.placeholders = getattr(fcfg, "placeholders", []) + [ph]
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(ph)

    def build_unchecked_block(
            self,
            *,
            cur_block: CFGNode,
            line_no: int,
            fcfg: FunctionCFG,
            line_info: dict,
    ):
        """
        unchecked 키워드를 만나면

            cur_block ─▶ unchecked ─▶ (기존 succcl …)

        로 그래프를 재배선한다. 생성된 노드를 반환.
        """
        unchecked = CFGNode(f"unchecked_{line_no}", unchecked_block=True, is_loop_body=cur_block.is_loop_body)

        G = fcfg.graph
        succs = list(G.successors(cur_block))

        # ① 그래프 재배선
        G.add_node(unchecked)
        G.add_edge(cur_block, unchecked)
        for s in succs:
            G.remove_edge(cur_block, s)
            G.add_edge(unchecked, s)

        # ② line_info
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(unchecked)

    # Analyzer/DynamicCFGBuilder.py  내부 메소드 교체/추가

    def build_do_statement(
            self, *, cur_block: CFGNode, line_no: int, fcfg: FunctionCFG, line_info: dict
    ) -> None:
        G = fcfg.graph
        do_entry = CFGNode(f"do_body_{line_no}", src_line=line_no, is_loop_body=True)
        do_entry.is_do_entry = True
        do_end = CFGNode(f"do_end_{line_no}", src_line=line_no, is_loop_body=False)
        do_end.is_do_end = True

        # env

        G.add_node(do_entry)
        G.add_node(do_end)

        # prev → do_entry → do_end
        old_succs = list(G.successors(cur_block))
        for s in old_succs:
            G.remove_edge(cur_block, s)
        G.add_edge(cur_block, do_entry)
        G.add_edge(do_entry, do_end)

        # do_end → (원래 succ들)
        for s in old_succs:
            G.add_edge(do_end, s)

        # line_info: do 시작 라인에는 do_entry만 추가
        # do_end는 while 조건 라인에서 찾을 수 있도록 그래프 구조로만 연결
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(do_entry)

    def build_do_while_statement(
            self, *, do_entry: CFGNode, while_line: int, fcfg: FunctionCFG,
            condition_expr, line_info: dict
    ) -> CFGNode:
        G = fcfg.graph

        # do_end = successor of do_entry (직결)
        succs = list(G.successors(do_entry))
        if not succs:
            raise ValueError("do_while: do_entry has no successor.")
        do_end = succs[0]

        # do_end 의 후속 간선을 잠시 떼어낸다
        post_succs = list(G.successors(do_end))
        for s in post_succs:
            G.remove_edge(do_end, s)

        # φ / cond / exit
        phi = CFGNode(f"do_while_phi_{while_line}", fixpoint_evaluation_node=True, src_line=while_line)

        cond = CFGNode(f"do_while_cond_{while_line}", condition_node=True,
                       condition_node_type="do_while", src_line=while_line)
        cond.condition_expr = condition_expr

        exit_ = CFGNode(f"do_while_exit_{while_line}", loop_exit_node=True, src_line=while_line)

        G.add_nodes_from([phi, cond, exit_])

        # 배선: do_end→φ→cond,  cond True→do_entry(Back-edge),  False→exit_,  exit_→post_succs
        G.add_edge(do_end, phi)
        G.add_edge(phi, cond)
        G.add_edge(cond, do_entry, condition=True)
        G.add_edge(cond, exit_, condition=False)
        for s in post_succs:
            G.add_edge(exit_, s)

        # line_info: while 라인에는 cond만 추가
        bc = line_info.setdefault(while_line, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)

        # exit은 별도 end_line이 있다면 거기에 추가되어야 하지만,
        # do-while은 보통 } while (cond); 형태로 한 줄이므로 여기서는 추가 안함

        return exit_

    def build_try_skeleton(
            self, *, cur_block: CFGNode, function_expr, line_no: int,
            fcfg: FunctionCFG, line_info: dict, end_line: int | None = None
    ):
        """
        cur_block → try_cond ──True──▶ try_true ─▶ join
                            └─False──▶ (아직 없다. 첫 catch 가 여기 붙는다)

        (2026-10-29 재설계) catch 절들을 `if`/`else if` 와 **동형인 순서 있는 조건 체인**으로
        만든다(`build_catch_clause`). Solidity 의 catch 절은 에러 타입으로 **위에서부터 매칭되어
        처음 맞는 하나만** 실행되므로, 예전의 "try_cond 의 병렬 False 간선 여러 개" 모델은
        순서와 배타성을 잃고 있었다.

        세 가지가 예전과 다르다.

        ① `condition_expr` 를 **두지 않는다.**
           try/catch 의 분기는 호출의 *반환값*이 아니라 *revert 여부*로 갈리고, 외부 호출의
           revert 는 모델링하지 않는다 → 두 분기가 **항상** feasible 해야 한다. 예전엔 호출식을
           `condition_expr` 에 넣어 둬서 간선 flow 계산(`Engine._edge_env_from_pred` /
           `_edge_flow_from_node_out`)이 그걸 **술어로 refine** 하고 `_branch_feasible` 로 분기를
           ⊥ 로 만들었다. 호출 결과가 정확히 0/1 이거나 BoolInterval 이면 한쪽이 죽었고,
           루프 **안**에서는 그게 그대로 반영돼 **catch 가 도달 불가로 판정됐다**(불건전).
           `condition_expr is None` 이면 두 함수 모두 조건 처리를 통째로 건너뛴다.
           기록용 호출식은 `try_call_expr` 로 따로 둔다.

        ② 호출은 `try_cond` 의 **statement** 다(`process_try_statement` 가 붙인다).
           그래서 `returns (…)` 가 **평범한 대입 경로**로 바인딩되고(예전엔 ⊤ 로 남았다),
           callee 가 **착지 가능한** 노드에 등록되어 Step Into 가 되고, 호출 평가가 1회로 끝난다
           (예전엔 간선마다 평가해 catch 3개면 4회였다).

        ③ **false 간선을 만들지 않는다** — try 에도, 마지막 catch 에도.
           `if` 는 else 가 없어도 조건이 거짓이면 *if 다음으로 계속 진행*하므로 join 으로 가는
           false 간선이 필요하다. 반면 맞는 catch 절이 없으면 *예외가 함수 밖으로 전파*되어
           이어지는 경로가 없다. 체인 유비가 깨지는 유일한 지점이고, 말단 간선을 join 으로
           보내면 **호출 이전 환경이 join 에 섞여** 모든 try/catch 의 정밀도가 떨어진다.
        """
        G = fcfg.graph

        # 1) 선행 간선 분리
        old_succs = list(G.successors(cur_block))
        for s in old_succs:
            G.remove_edge(cur_block, s)

        # 2) 노드 — if/else-if 와 같게 `is_loop_body` 를 전파한다. 예전엔 누락돼서 루프 본문 안의
        #    try/catch 하위 노드가 전부 플래그를 잃고, 탐색에서 "착지 가능한데 NG 에서 고립된"
        #    노드가 됐다(= 걸 수는 있지만 Continue 가 영원히 도달하지 못하는 breakpoint).
        cond = CFGNode(f"try_cond_{line_no}",
                       condition_node=True, condition_node_type="try",
                       src_line=line_no, is_loop_body=cur_block.is_loop_body)
        cond.condition_expr = None          # ★ ① 술어가 아니다
        cond.try_call_expr = function_expr  # 기록용(UI·진단). refine 경로는 보지 않는다

        t_blk = CFGNode(f"try_true_{line_no}", branch_node=True, is_true_branch=True, src_line=line_no,
                        is_loop_body=cur_block.is_loop_body)
        # (2026-10-23) if 와 같게: join 은 닫는 줄(end_line)에 둔다 — 본문 문장이 "다음 줄 노드 = join" 을 보고
        #   try_true 안에 들어간다. 예전엔 등록이 없어 본문과 catch 본문이 모두 join **뒤에** 차례로 붙었다.
        join = CFGNode(f"try_join_{line_no}", join_point_node=True,
                       src_line=end_line if end_line is not None else line_no,
                       is_loop_body=cur_block.is_loop_body)
        # (Phase 3b, D8) 분기 블록이 끝나는 곳. `returns (…)` 로 선언된 이름은 try 성공 블록
        #   소속이므로 여기서 사라진다 — `Engine._scope_keys` 가 anchor 가 try cond 일 때
        #   자기 선언문 이름을 빼 준다(for-init 과 같은 처리).
        join.scope_anchor = cond

        # 3) 배선 — ③ false 간선 없음
        for n in (cond, t_blk, join):
            G.add_node(n)
        G.add_edge(cur_block, cond)
        G.add_edge(cond, t_blk, condition=True)
        G.add_edge(t_blk, join)
        # join → 원래 succ 복원
        for s in old_succs:
            G.add_edge(join, s)

        # 4) 표시 — catch 가 붙을 합류점. (2026-10-23) 예전엔 `cond.__catch_attached` 였는데 클래스 안이라 이름이
        #   `_DynamicCFGBuilder__catch_attached` 로 바뀌어(name mangling) `getattr(n, "__catch_attached")` 검사가 늘 False 였다.
        cond.try_join = join

        # 5) line_info: start line 에는 cond, end line 에는 join (build_if_statement 와 같다)
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)
        if end_line is not None:
            line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})["cfg_nodes"].append(join)

        return cond, t_blk, join

    def build_catch_clause(
            self, *, prev_cond: CFGNode, outer_join: CFGNode | None,
            line_no: int, fcfg: FunctionCFG, line_info: dict, end_line: int | None = None
    ):
        """catch 절 하나 = 체인의 다음 링크. `build_else_if_statement` 와 **동형**이다.

            prev_cond ──False──▶ catch_cond ──True──▶ catch_true ─▶ [본문] ─▶ catch_join ─▶ outer_join
                                            (False 간선은 만들지 않는다)

        `prev_cond` 는 체인의 바로 앞 조건 — 첫 catch 면 `try_cond`, 둘째부터는 앞 `catch_cond` 다
        (`get_current_block(context="catch")` 가 join 에서 predecessor BFS 로 찾아 준다).

        `build_else_if_statement` 와 다른 점이 둘뿐이다.
          · **false 분기 더미를 만들지 않는다.** 맞는 절이 없으면 예외가 함수 밖으로 전파되므로
            이어지는 경로가 없다(`build_try_skeleton` 의 주석 ③ 참조).
          · 그래서 **앞 조건의 false 간선을 떼어낼 일도 없다** — 애초에 없다.
            (else-if 는 `if_false` 더미를 만들어 뒀다가 떼고 교체한다.)

        `condition_expr` 는 두지 않는다 — "revert 데이터가 `Error(string)` 과 맞는가" 는 interval
        도메인으로 표현할 수 없다. None 이면 간선 flow 계산이 조건 처리를 건너뛰므로 두 분기가
        항상 feasible 하다(`build_try_skeleton` 의 주석 ① 참조).
        """
        G = fcfg.graph

        cond = CFGNode(f"catch_cond_{line_no}", condition_node=True,
                       condition_node_type="catch", src_line=line_no,
                       is_loop_body=prev_cond.is_loop_body)
        cond.condition_expr = None

        t_blk = CFGNode(f"catch_true_{line_no}", branch_node=True, is_true_branch=True,
                        src_line=line_no, is_loop_body=prev_cond.is_loop_body)

        local_join = CFGNode(f"catch_join_{line_no}", join_point_node=True, src_line=line_no,
                             is_loop_body=prev_cond.is_loop_body)
        local_join.scope_anchor = cond   # (Phase 3b, D8) catch 파라미터는 이 블록 소속

        for n in (cond, t_blk, local_join):
            G.add_node(n)

        G.add_edge(prev_cond, cond, condition=False)
        G.add_edge(cond, t_blk, condition=True)
        G.add_edge(t_blk, local_join)

        if outer_join is None:
            raise ValueError("catch: outer join not found (passed from get_current_block)")
        G.add_edge(local_join, outer_join)

        # line_info: start line 에는 cond, end line 에는 local_join (build_else_if_statement 와 같다)
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(cond)
        if end_line is not None:
            line_info.setdefault(end_line, {"open": 0, "close": 0, "cfg_nodes": []})["cfg_nodes"].append(local_join)

        return cond, t_blk, local_join


    # Analyzer/DynamicCFGBuilder.py  (클래스 내부에 교체/추가)

    def insert_new_statement_block(
            self,
            *,
            pred_block: CFGNode,
            fcfg: FunctionCFG,
            line_no: int,
            line_info: dict,
            tag: str = "Block"
    ) -> CFGNode:
        """
        Create a new statement block and insert it between pred_block and its successors.
        
        Args:
            pred_block: The predecessor block
            fcfg: The function CFG
            line_no: Source line number
            line_info: Line info mapping
            tag: Name prefix for the new block
            
        Returns:
            The newly created block
        """
        from Utils.Helper import VariableEnv
        
        G = fcfg.graph
        
        # 1. Get current successors of pred_block
        old_succs = list(G.successors(pred_block))
        
        # 2. Create new block with pred's environment
        new_block = CFGNode(f"{tag}_{line_no}", is_loop_body=pred_block.is_loop_body)
        new_block.src_line = line_no
        # (Phase 2) `unchecked { }` 안의 문장 블록 표시 — 해석기가 이 노드의 문장에서만 wrap 산술을 쓴다.
        #   표시는 노드에 실려 pkl 로도 보존된다(의존성 라이브러리의 unchecked 포함).
        if self.an.is_inside_unchecked(line_no):
            new_block.unchecked_block = True
        
        # 3. Add new block to graph
        G.add_node(new_block)
        
        # 4. Rewire edges: pred -> new_block -> old_succs
        for succ in old_succs:
            G.remove_edge(pred_block, succ)
            G.add_edge(new_block, succ)
        G.add_edge(pred_block, new_block)
        
        # 5. Update line_info mapping
        bc = line_info.setdefault(line_no, {"open": 0, "close": 0, "cfg_nodes": []})
        bc["cfg_nodes"].append(new_block)
        
        # 6. Update FCG
        fcfg.update_block(new_block)
        
        return new_block

    def get_current_block(self, context: str = "statement") :
        """
        Return the predecessor anchor where a new statement-block will be inserted,
        or the condition node for special contexts (else/else-if/catch).

        Algorithm:
        - For regular statements: Look at L+1 node
          * If L+1 is loop-exit or join: check L-1
            - If L-1 is cond: return cond's TRUE branch
            - Else: return L-1 node
          * Else: return L+1's predecessor

        - For special statements (else-if/else/catch): Look at L node
          * Traverse CFG predecessors from L to find specific node:
            - else-if/else: first cond node (type=if/else-if)
            - catch: first cond node (type=try)

        Note: Do-while is NOT handled by this implementation yet.

        Args:
            context: "statement" (default), "else", "else_if", or "catch"
        """
        an = self.an
        fcfg = an.current_target_function_cfg
        if fcfg is None:
            raise ValueError("Active FunctionCFG not found.")

        G = fcfg.graph
        # ★ L-1을 볼 때는 start_line 기준, L+1을 볼 때는 end_line 기준
        L_start = an.current_start_line
        L_end = (an.current_end_line if getattr(an, "current_end_line", None) is not None
                 else an.current_start_line)
        if L_start is None:
            raise ValueError("current_start_line is not set.")

        # ---------- Helper functions ----------
        def _line_nodes(line: int, search_backward: bool = False) -> list[CFGNode]:
            """Get all CFG nodes at given line.

            Args:
                line: Line number to search
                search_backward: If True and line has no nodes, search backward to previous lines
            """
            info = an.sa.line_info.get(line, None)
            cfg_nodes = info.get("cfg_nodes", []) if info else []

            # cfg_nodes가 비어있고 search_backward가 True이면 이전 라인 검색
            if not cfg_nodes and search_backward:
                search_line = line - 1
                while search_line >= 1:
                    info = an.sa.line_info.get(search_line, None)
                    if info:
                        cfg_nodes = info.get("cfg_nodes", [])
                        if cfg_nodes:
                            break
                    search_line -= 1

            if isinstance(cfg_nodes, list):
                return [n for n in cfg_nodes if n in G.nodes]
            return []

        def _line_first(line: int) -> CFGNode | None:
            """Get first CFG node at given line."""
            ns = _line_nodes(line)
            return ns[0] if ns else None

        def _is_loop_exit(n: CFGNode) -> bool:
            return getattr(n, "loop_exit_node", False)

        def _is_join(n: CFGNode) -> bool:
            return getattr(n, "join_point_node", False)

        def _is_cond(n: CFGNode) -> bool:
            return getattr(n, "condition_node", False)

        def _get_cond_type(n: CFGNode) -> str | None:
            """Get condition node type (if/else-if/try/etc)."""
            return getattr(n, "condition_node_type", None) if _is_cond(n) else None

        def _get_true_branch(cond: CFGNode) -> CFGNode | None:
            """Get TRUE branch of condition node."""
            for s in G.successors(cond):
                if G[cond][s].get("condition") is True:
                    return s
            return None

        def _find_cond_in_nodes(nodes: list[CFGNode]) -> CFGNode | None:
            """Find condition node in list (search from end)."""
            for n in reversed(nodes):
                if _is_cond(n):
                    return n
            return None

        # ========== Special contexts (else-if/else/catch) ==========
        if context in ["else_if", "else", "catch"]:
            # Get L line nodes (else 자신의 라인부터 시작, 없으면 이전 라인 검색)
            L_nodes = _line_nodes(L_start, search_backward=True)
            if not L_nodes:
                raise ValueError(f"No CFG nodes found at or before line {L_start} for context '{context}'")

            # ★ L_nodes에서 outer join 찾기 (else_if/else의 경우)
            found_outer_join = None
            if context in ["else_if", "else"]:
                for n in L_nodes:
                    if _is_join(n):
                        found_outer_join = n
                        break

            # (2026-10-29) catch 전용 선행 경로 — if/else 경로는 건드리지 않는다.
            #   `catch` 는 아래 predecessor BFS 에 기댈 수 없는 경우가 있다: **try 본문이
            #   `return`/`revert` 로 끝나면** 본문이 RETURN/ERROR sink 로 가고 try 는 false 간선을
            #   만들지 않으므로 **join 에 들어오는 간선이 하나도 없다**(그래프상 고아) → BFS 가
            #   아무것도 못 찾는다. 예전 구조에서는 `try_false_stub → join` 간선이 우연히 그 경로를
            #   받쳐 주고 있었다. `scope_anchor` 는 `build_try_skeleton`/`build_catch_clause` 가
            #   그 join 을 만든 조건 노드로 설정하므로 정확히 "앞 조건" 이다.
            if context == "catch":
                for n in L_nodes:
                    if not _is_join(n):
                        continue
                    anchor = getattr(n, "scope_anchor", None)
                    if (anchor is not None and _is_cond(anchor)
                            and _get_cond_type(anchor) in ("try", "catch") and anchor in G):
                        return (anchor, n)
                    break

            # BFS through predecessors to find target node
            from collections import deque
            visited = set()
            queue = deque(L_nodes)
            found_cond = None

            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)

                # Check if this is the target node
                if _is_cond(node):
                    node_type = _get_cond_type(node)

                    if context in ["else_if", "else"]:
                        # Looking for if/else-if condition
                        if node_type in ["if", "else if"]:
                            found_cond = node
                            # ★ outer join과 함께 반환
                            if found_outer_join is None:
                                # fallback: 그래프에서 찾기
                                found_outer_join = self._outer_join_from_graph(found_cond, fcfg)
                            return (found_cond, found_outer_join)
                    elif context == "catch":
                        # (2026-10-29) catch 절은 **순서 있는 조건 체인**의 다음 링크다 — "앞 조건" 은
                        #   첫 catch 면 `try`, 둘째부터는 앞 `catch` 다. 예전엔 늘 try cond 를 찾아
                        #   모든 catch 가 try 의 병렬 False 분기가 됐다.
                        if node_type in ("try", "catch"):
                            catch_outer_join = None
                            for n in L_nodes:
                                if _is_join(n):
                                    catch_outer_join = n
                                    break
                            if catch_outer_join is not None:
                                return (node, catch_outer_join)
                            return node

                # Continue searching predecessors
                for pred in G.predecessors(node):
                    if pred not in visited:
                        queue.append(pred)

            if found_cond is None:
                raise ValueError(f"No matching condition node found for context '{context}' at line {L_start}")

        # ========== Regular statement context ==========
        # Look at L+1 node (L_end + 1) and search forward if empty
        search_line = L_end + 1
        L_plus_1_node = None

        # Search forward until we find a line with CFG nodes or reach end
        max_line = max(an.sa.line_info.keys()) if an.sa.line_info else L_end + 100
        while search_line <= max_line:
            L_plus_1_node = _line_first(search_line)
            if L_plus_1_node is not None:
                break
            search_line += 1

        if L_plus_1_node is None:
            raise ValueError(f"No CFG node found after line {L_end} (searched up to line {max_line})")

        # Check if L+1 is loop-exit or join
        #is_exit = _is_loop_exit(L_plus_1_node)
        #is_join = _is_join(L_plus_1_node)

        if _is_loop_exit(L_plus_1_node) or _is_join(L_plus_1_node):
            # Look at L-1 nodes (L_start - 1) and search upward if empty
            search_line = L_start - 1
            L_minus_1_nodes = []

            # Search upward until we find a line with CFG nodes
            while search_line >= 1:
                L_minus_1_nodes = _line_nodes(search_line)
                if L_minus_1_nodes:
                    break
                search_line -= 1

            if not L_minus_1_nodes:
                # Still empty after searching, return first predecessor of L+1
                preds = list(G.predecessors(L_plus_1_node))
                return preds[0] if preds else fcfg.get_entry_node()

            # Check if any L-1 node is a condition
            cond_node = _find_cond_in_nodes(L_minus_1_nodes)

            if cond_node:
                # L-1 is cond: return TRUE branch
                true_branch = _get_true_branch(cond_node)
                if true_branch:
                    return true_branch
                else:
                    # Fallback: return cond itself (shouldn't happen)
                    return cond_node
            else:
                # L-1 is not cond: return last L-1 node
                return L_minus_1_nodes[-1]
        else:
            # L+1 is not loop-exit/join: return L+1's predecessor
            preds = list(G.predecessors(L_plus_1_node))

            if len(preds) == 1:
                return preds[0]
            elif len(preds) > 1:
                # Multiple predecessors (shouldn't happen for regular statement)
                # Return the one closest to current line
                def distance(n: CFGNode):
                    ln = getattr(n, "src_line", None)
                    return abs((ln or 0) - L_start) if ln is not None else 999999
                return min(preds, key=distance)
            else:
                # No predecessors, return ENTRY
                return fcfg.get_entry_node()


    def find_loop_join(self, start: CFGNode, fcfg: FunctionCFG) -> CFGNode | None:
        """
        역-DFS 로 가장 가까운 `fixpoint_evaluation_node`(while/for join) 반환.
        """
        G = fcfg.graph
        stk, seen = [start], set()
        while stk:
            n = stk.pop()
            if n in seen:
                continue
            seen.add(n)
            if n.fixpoint_evaluation_node:
                return n
            stk.extend(G.predecessors(n))
        return None

    # ─────────────────────────────────────────────────────────────────
    #  Code Deletion (논문 Alg. Code Deletion / IQDebug §5.6 T-stmt 2단계)
    #  start_line 의 statement/블록을 SESE region 으로 제거 후 succ 재해석.
    #  region = descendants(entry) ∩ ancestors(exit) ∪ {entry,exit}
    #    → skeleton(t_blk/f_blk/while_join/body)·중첩·back-edge 자동 포함(M 의존 없음).
    #  소스 라인/ledger 시프트는 호출자(update_code delete)가 담당. 여기는 그래프 수술 + 재해석만.
    # ─────────────────────────────────────────────────────────────────
    def _nearest_join(self, cond: CFGNode, fcfg: FunctionCFG) -> CFGNode | None:
        """if/else-if cond 의 머지 join (body 유무 무관): 양 분기에서 도달하는 가장 가까운 join_point."""
        import networkx as nx
        G = fcfg.graph
        t = next((s for s in G.successors(cond) if G[cond][s].get("condition") is True), None)
        f = next((s for s in G.successors(cond) if G[cond][s].get("condition") is False), None)
        if t is None or f is None:
            return None
        reach_t = nx.descendants(G, t) | {t}
        reach_f = nx.descendants(G, f) | {f}
        common = [n for n in (reach_t & reach_f) if getattr(n, "join_point_node", False)]
        if not common:
            return None
        def _dist(n):
            try:
                return nx.shortest_path_length(G, cond, n)
            except nx.NetworkXNoPath:
                return 1 << 30
        return min(common, key=_dist)

    def _delete_endpoints(self, node0: CFGNode, fcfg: FunctionCFG) -> tuple[CFGNode, CFGNode]:
        """삭제 영역의 (entry, exit) 식별. 단순/require/assert/basic 은 entry==exit==node0."""
        G = fcfg.graph
        is_cond = getattr(node0, "condition_node", False)
        ctype = getattr(node0, "condition_node_type", None) if is_cond else None
        if ctype in {"while", "for", "do_while", "doWhile"}:
            entry = self.find_loop_join(node0, fcfg) or node0
            exit_ = next((s for s in G.successors(node0)
                          if G[node0][s].get("condition") is False
                          and getattr(s, "loop_exit_node", False)), None) or node0
            return entry, exit_
        if ctype in {"if", "else if", "else_if"}:
            return node0, (self._nearest_join(node0, fcfg) or node0)
        # simple statement / require / assert / basic → 단일 노드
        return node0, node0

    def _delete_else_clause(self, fcfg: FunctionCFG, start_line: int) -> list[CFGNode]:
        """else 절 삭제 = false-분기 body 만 제거하고 빈 false-분기(else_block)→join 유지.
        (cond/join 은 보존; if 는 plain if 가 됨.)"""
        import networkx as nx
        G = fcfg.graph
        info = self.an.sa.line_info.get(start_line)
        # Ls 의 false-분기 진입 노드(else_block) = branch_node & is_true_branch=False
        else_block = next((n for n in (info.get("cfg_nodes", []) if info else [])
                           if G.has_node(n) and getattr(n, "branch_node", False)
                           and not getattr(n, "is_true_branch", True)), None)
        if else_block is None:
            return []  # 이미 제거됨
        cond = next((p for p in G.predecessors(else_block)
                     if G[p][else_block].get("condition") is False), None)
        if cond is None:
            return []
        join = self._nearest_join(cond, fcfg)
        if join is None:
            return []
        # else body = else_block 와 join 사이 (else_block·join 제외)
        body = (nx.descendants(G, else_block) & nx.ancestors(G, join))
        body.discard(join)
        for n in list(body):
            if G.has_node(n):
                G.remove_node(n)
        if not G.has_edge(else_block, join):  # 빈 false-분기 → join
            G.add_edge(else_block, join)
        seeds = [join]
        return seeds

    def _delete_else_if_clause(self, fcfg: FunctionCFG, start_line: int) -> list[CFGNode]:
        """else-if 절 삭제: else-if cond+true-body+local_join 제거 → false-child(다음 else/else-if)를
        위 cond 의 false 로 reparent, else 꼬리를 outer join 으로. (체인도 동일 일반화.)"""
        import networkx as nx
        G = fcfg.graph
        info = self.an.sa.line_info.get(start_line)
        eic = next((n for n in (info.get("cfg_nodes", []) if info else [])
                    if G.has_node(n) and getattr(n, "condition_node", False)
                    and getattr(n, "condition_node_type", None) in ("else if", "else_if")), None)
        if eic is None:
            return []
        parent = next((p for p in G.predecessors(eic)
                       if G[p][eic].get("condition") is False), None)
        false_child = next((s for s in G.successors(eic)
                            if G[eic][s].get("condition") is False), None)
        true_succ = next((s for s in G.successors(eic)
                          if G[eic][s].get("condition") is True), None)
        local_join = self._nearest_join(eic, fcfg)
        if parent is None or false_child is None or true_succ is None or local_join is None:
            return []
        join_succ = next((s for s in G.successors(local_join)), None)
        true_region = (nx.descendants(G, true_succ) & nx.ancestors(G, local_join)) | {true_succ}
        true_region.discard(local_join)
        survivors = [p for p in G.predecessors(local_join)
                     if p not in true_region and p is not eic]
        for n in list(true_region) + [eic, local_join]:
            if G.has_node(n):
                G.remove_node(n)
        G.add_edge(parent, false_child, condition=False)        # 위 cond[false] → else
        if join_succ is not None:
            for p in survivors:                                  # else 꼬리 → outer join
                if G.has_node(p) and not G.has_edge(p, join_succ):
                    G.add_edge(p, join_succ)
        seeds = [join_succ] if join_succ is not None else []
        return seeds

    def _walk_up_to_cond(self, node: CFGNode, fcfg: FunctionCFG) -> CFGNode | None:
        """node 에서 선행을 거슬러 가장 가까운 condition_node 를 찾는다(분기 선두까지)."""
        G = fcfg.graph
        cur, seen = node, set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if getattr(cur, "condition_node", False):
                return cur
            ps = list(G.predecessors(cur))
            cur = ps[0] if ps else None
        return None

    def _continuation_via_other_branch(self, cond: CFGNode, exit_node: CFGNode,
                                       fcfg: FunctionCFG) -> CFGNode | None:
        """early-exit 이 속하지 '않은' 분기를 타고 내려가 첫 join/loop_exit(=fall-through 대상)을 찾는다."""
        import networkx as nx
        G = fcfg.graph
        t = next((s for s in G.successors(cond) if G[cond][s].get("condition") is True), None)
        f = next((s for s in G.successors(cond) if G[cond][s].get("condition") is False), None)
        in_true = t is not None and (exit_node is t or exit_node in nx.descendants(G, t))
        other = f if in_true else t
        if other is None:
            return None
        cur, seen = other, set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            if getattr(cur, "join_point_node", False) or getattr(cur, "loop_exit_node", False):
                return cur
            succs = list(G.successors(cur))
            cur = succs[0] if succs else None
        return None

    def _delete_early_exit(self, fcfg: FunctionCFG, start_line: int) -> list[CFGNode]:
        """분기 안 lone return/revert 삭제: 빌드 때 버려진 fall-through 를 복원.
        true 쪽은 sink 로 가 join 미도달 → '다른 분기'를 타고 내려가 join 을 찾아 pred→join 연결."""
        G = fcfg.graph
        info = self.an.sa.line_info.get(start_line)

        def _has_sink_succ(n):
            return any(getattr(s, "function_exit_node", False) or getattr(s, "return_exit_node", False)
                       or getattr(s, "error_exit_node", False) or getattr(s, "loop_exit_node", False)
                       for s in G.successors(n))

        node = next((n for n in (info.get("cfg_nodes", []) if info else [])
                     if G.has_node(n) and _has_sink_succ(n)), None)
        if node is None:
            return []
        preds = list(G.predecessors(node))
        if len(preds) != 1:
            return []
        pred = preds[0]
        cond = self._walk_up_to_cond(pred, fcfg)
        if cond is None:
            return []
        cont = self._continuation_via_other_branch(cond, node, fcfg)
        if cont is None:
            return []
        G.remove_node(node)                  # node→sink 엣지 드롭
        if not G.has_edge(pred, cont):
            G.add_edge(pred, cont)           # 빈 분기 → fall-through join
        seeds = [cont]
        return seeds

    def modify_condition_expr(self, fcfg: FunctionCFG, line: int, new_expr: Expression) -> bool:
        """조건 헤더 in-place modify: line 의 cond 노드 condition_expr 만 교체(body·구조 보존) 후
        그 노드부터 재해석. 논문 Code Modification(단일 statement 인플레이스). 성공 시 True."""
        G = fcfg.graph
        info = self.an.sa.line_info.get(line)
        cond = next((n for n in (info.get("cfg_nodes", []) if info else [])
                     if G.has_node(n) and getattr(n, "condition_node", False)), None)
        if cond is None:
            return False
        cond.condition_expr = new_expr
        return True

    def delete_cfg_range(self, *, fcfg: FunctionCFG, start_line: int, kind: str = "auto",
                         end_line: int | None = None) -> list[CFGNode]:
        """start_line 의 statement/블록을 CFG 에서 제거하고 pred→succ 재연결 후 succ 재해석.

        kind: "auto"(simple/if/while/for = generic SESE) | "else"/"else if"(의존 절 = false-분기 collapse).
              호출자(B1 apply)가 삭제 레코드 code 로 판별해 넘긴다.
        반환: 재해석 seed. node0 가 이미 사라졌으면(상위 블록 삭제로) no-op([])."""
        import networkx as nx
        if kind == "else":
            return self._delete_else_clause(fcfg, start_line)
        if kind in ("else if", "else_if"):
            return self._delete_else_if_clause(fcfg, start_line)
        if kind in ("return", "revert"):
            return self._delete_early_exit(fcfg, start_line)
        G = fcfg.graph
        info = self.an.sa.line_info.get(start_line)
        node0 = next((n for n in info.get("cfg_nodes", [])), None) if info else None
        node0 = node0 if (node0 is not None and G.has_node(node0)) else None
        if node0 is None:
            return []  # 이미 제거됨 → no-op

        entry, exit_ = self._delete_endpoints(node0, fcfg)
        if entry is exit_:
            region = {entry}
        else:
            region = (nx.descendants(G, entry) & nx.ancestors(G, exit_)) | {entry, exit_}

        def _is_shared_sink(n: CFGNode) -> bool:
            # 공유 sink(RETURN/ERROR/EXIT)·loop_exit 은 다른 분기도 참조 → 엣지 드롭, 노드 보존
            return (getattr(n, "function_exit_node", False)
                    or getattr(n, "return_exit_node", False)
                    or getattr(n, "error_exit_node", False)
                    or getattr(n, "loop_exit_node", False))

        entry_preds = [p for p in G.predecessors(entry) if p not in region]
        exit_succs = [s for s in G.successors(exit_)
                      if s not in region and not _is_shared_sink(s)]

        # crossing 엣지 condition(True/False) 보존하며 pred→succ 재연결
        for p in entry_preds:
            ed = G.get_edge_data(p, entry) or {}
            data = {"condition": ed["condition"]} if "condition" in ed else {}
            for s in exit_succs:
                if not G.has_edge(p, s):
                    G.add_edge(p, s, **data)

        for n in list(region):
            if G.has_node(n):
                G.remove_node(n)

        seeds = [s for s in exit_succs if G.has_node(s)]
        return seeds

    def _line_nodes_at(self, line_no: int, fcfg: FunctionCFG) -> list[CFGNode]:
        info = self.an.sa.line_info.get(line_no, None)
        if not info:
            return []
        G = fcfg.graph
        out = []
        if isinstance(info.get("cfg_nodes"), list) and info["cfg_nodes"]:
            out.extend([n for n in info["cfg_nodes"] if n in G.nodes])
        else:
            cfg_nodes = info.get("cfg_nodes", [])
            if cfg_nodes and cfg_nodes[0] in G.nodes:
                out.append(cfg_nodes[0])
        return out

    def _pick_first_join(self, nodes: list["CFGNode"]) -> "CFGNode | None":
        for n in nodes:
            if getattr(n, "join_point_node", False):
                return n
        return nodes[0] if nodes else None

    def find_outer_join_near(
            self,
            *,
            anchor_line: int,
            fcfg: FunctionCFG,
            direction: str = "backward",  # "backward" | "forward" | "both"
            include_anchor: bool = True
    ) -> "CFGNode | None":
        """
        '}' 같은 토큰에 의존하지 않고, line_info를 스캔하여
        anchor_line에서 가장 가까운 join-point 노드를 찾는다.
        - direction: 기본 backward (위로 올라가며 탐색)
        - include_anchor=False면 anchor_line 자체는 건너뛴다.
        """
        lines = list(self.an.sa.line_info.keys())
        if not lines:
            return None
        lo, hi = 1, max(lines)

        def _scan(lo_line, hi_line, step, start_line, skip_first):
            started = False
            for ln in range(start_line, hi_line + step, step):
                if not started:
                    started = True
                    if skip_first:
                        continue
                nodes = self._line_nodes_at(ln, fcfg)
                j = self._pick_first_join(nodes)
                if j is not None:
                    return j
            return None

        if direction == "backward":
            start = anchor_line if include_anchor else anchor_line - 1
            end = lo
            return _scan(lo, anchor_line, -1, start, False)  # 위로
        elif direction == "forward":
            start = anchor_line if include_anchor else anchor_line + 1
            end = hi
            return _scan(anchor_line, hi, +1, start, False)  # 아래로
        else:  # both
            j = self.find_outer_join_near(anchor_line=anchor_line, fcfg=fcfg,
                                          direction="backward", include_anchor=include_anchor)
            if j is not None:
                return j
            return self.find_outer_join_near(anchor_line=anchor_line, fcfg=fcfg,
                                             direction="forward", include_anchor=include_anchor)

    def _outer_join_from_graph(self, prev_cond: "CFGNode", fcfg: FunctionCFG) -> "CFGNode | None":
        """
        폴백: 그래프만으로 outer-join 추정 (prev_cond True-브랜치 → succ 중 join).
        """
        G = fcfg.graph
        for s in G.successors(prev_cond):
            if G[prev_cond][s].get("condition") is True:
                for j in G.successors(s):
                    if getattr(j, "join_point_node", False):
                        return j
        return None
