# Analyzer/EnhancedYulVisitor.py
"""
Yul (inline assembly) visitor.
Yul parse tree를 Expression IR로 변환하여 ContractAnalyzer에 전달.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from Analyzer.ContractAnalyzer import ContractAnalyzer

from Parser.YulParser import YulParser
from Parser.YulVisitor import YulVisitor
from Domain.IR import Expression


class EnhancedYulVisitor(YulVisitor):
    # (2026-10-23, Yul P0-a) Yul 식은 `Yul*Context` 컨텍스트로 만들고 `Evaluation._evaluate_yul` → `YulEval` 이
    #   256-bit word 의미로 평가한다. Solidity 연산 IR 로 낮추지 않는다.

    def __init__(self, contract_analyzer: "ContractAnalyzer"):
        self.contract_analyzer = contract_analyzer

    # ── Statement visitors ──────────────────────────────────

    def visitYulUnit(self, ctx: YulParser.YulUnitContext):
        # (2026-10-23) 미지원 Yul 블록(if/switch/for/function)의 본문 줄 — 머리 줄의 표식이 실행을 멈추므로 만들지 않는다
        ca = self.contract_analyzer
        if ca.in_nested_yul_block(ca.current_start_line):
            return None
        # `assembly {` 머리 줄 레코드도 문맥이 assembly 라 이리로 온다 — Yul 문장이 아니다(예전엔 파싱 오류를 무시해서 드러나지 않았다)
        if ca.sa.full_code_lines.get(ca.current_start_line, "").strip().startswith("assembly"):
            return None
        # (2026-10-23) 파싱 실패(대부분 여러 줄에 걸친 Yul 식 — 줄 단위 입력이 식을 자른다)는 조용히 넘기지 않는다.
        #   예전엔 오류 리스너를 꺼 둔 채 부분 트리를 그대로 방문해 `success := and(` 가 인자 없는 `and()` 가 됐다.
        parser = getattr(ctx, "parser", None)
        if parser is not None and parser.getNumberOfSyntaxErrors() > 0:
            return self._unsupported("statement that does not parse on its own line (여러 줄에 걸친 Yul 식)")
        for stmt in ctx.yulStatement():
            self.visitYulStatement(stmt)
        return None

    def visitYulStatement(self, ctx: YulParser.YulStatementContext):
        # (2026-10-23) leave/break/continue 는 하위 규칙이 없는 토큰 대안이라 여기서 잡는다
        txt = ctx.getText()
        if txt in ("leave", "break", "continue"):
            return self._unsupported(txt)
        return self.visitChildren(ctx)

    def visitYulBlock(self, ctx: YulParser.YulBlockContext):
        for stmt in ctx.yulStatement():
            self.visitYulStatement(stmt)
        return None

    def visitYulVariableDeclaration(self, ctx: YulParser.YulVariableDeclarationContext):
        """let mm := mulmod(a, b, not(0)) → 새 uint256 변수 선언"""
        identifiers = ctx.IDENTIFIER()
        if not identifiers:
            return None
        if len(identifiers) > 1:
            return self._unsupported("let a, b := f(...)")       # 다중 반환 = 사용자 정의 Yul 함수
        var_name = identifiers[0].getText()

        # := 이후 expression (없을 수도 있음: let x;)
        yul_expr = ctx.yulExpression()
        yul_func = ctx.yulFunctionCall()

        if yul_expr:
            rhs = self._build_expression(yul_expr)
        elif yul_func:
            rhs = self._build_function_call(yul_func)
        else:
            rhs = None

        self.contract_analyzer.process_yul_variable_declaration(var_name, rhs)
        return None

    def visitYulAssignment(self, ctx: YulParser.YulAssignmentContext):
        """prod0 := mul(a, b) → 기존 변수에 대입"""
        identifiers = ctx.IDENTIFIER()
        if not identifiers:
            return None

        rhs_expr = ctx.yulExpression()
        rhs_func = ctx.yulFunctionCall()

        if rhs_expr:
            rhs = self._build_expression(rhs_expr)
        elif rhs_func:
            rhs = self._build_function_call(rhs_func)
        else:
            return None

        if len(identifiers) > 1:
            return self._unsupported("a, b := f(...)")
        var_name = identifiers[0].getText()
        lhs = Expression(identifier=var_name, context="IdentifierExpContext")
        self.contract_analyzer.process_yul_assignment(lhs, rhs)
        return None

    def visitYulExpressionStatement(self, ctx: YulParser.YulExpressionStatementContext):
        """(2026-10-23, Yul P0-a) builtin 호출 문장. 예전엔 전부 무시했다(sstore·mstore·call 효과, revert 경로가 사라졌다).
          · revert / invalid → ERROR (Solidity revert 와 같은 CFG 배선)
          · return / stop / selfdestruct (실행 컨텍스트 종료) → 아직 미지원 — 실행되면 명시적으로 멈춘다
          · 그 밖 → 문장 노드(효과는 YulEval 의 효과 표)"""
        call = ctx.yulFunctionCall()
        name = call.IDENTIFIER().getText()
        if name in ("revert", "invalid"):
            self.contract_analyzer.process_revert_statement(None, None, None)
            return None
        if name in ("return", "stop", "selfdestruct"):
            return self._unsupported(name)
        self.contract_analyzer.process_yul_statement(self._build_function_call(call))
        return None

    # (2026-10-23) Yul 제어 흐름은 아직 CFG 로 만들지 않는다. 예전엔 조건을 버리고 **본문 줄을 무조건 실행**했다
    #   (`if iszero(a) { r := 7 }` 가 a=5 에서도 r=7, switch 는 모든 case 를 순서대로). 블록 단위 해석(P0-b) 전까지는
    #   그 자리에 미지원 표식을 둬서, 실행되면 명시적으로 멈춘다(표식이 본문보다 앞에 있으므로 본문은 실행되지 않는다).
    def visitYulIfStatement(self, ctx: YulParser.YulIfStatementContext):
        return self._unsupported("if")

    def visitYulForStatement(self, ctx: YulParser.YulForStatementContext):
        return self._unsupported("for")

    def visitYulSwitchStatement(self, ctx: YulParser.YulSwitchStatementContext):
        return self._unsupported("switch")

    def visitYulFunctionDefinition(self, ctx: YulParser.YulFunctionDefinitionContext):
        return self._unsupported("function")

    def _unsupported(self, what: str):
        ca = self.contract_analyzer
        where = f"{ca.current_target_contract}.{ca.current_target_function} line {ca.current_start_line}"
        ca.process_yul_statement(Expression(identifier=f"{what}` at `{where}", context="YulUnsupportedStatement"))
        return None

    # ── Expression IR builders ──────────────────────────────

    def _build_expression(self, ctx: YulParser.YulExpressionContext) -> Expression:
        """yulExpression → Expression IR"""
        if ctx.yulFunctionCall():
            return self._build_function_call(ctx.yulFunctionCall())
        elif ctx.IDENTIFIER():
            name = ctx.IDENTIFIER().getText()
            return Expression(identifier=name, context="IdentifierExpContext")
        elif ctx.yulLiteral():
            return self._build_literal(ctx.yulLiteral())
        return Expression(literal=0, context="LiteralExpContext")

    def _build_function_call(self, ctx: YulParser.YulFunctionCallContext) -> Expression:
        """yulFunctionCall → Expression IR.
        (2026-10-23, Yul P0-a) builtin 을 Solidity 연산으로 낮추지 않는다 — 이름과 인자를 그대로 두고
        `Interpreter/Semantics/YulEval.py` 가 EVM word 의미(mod 2^256, 0 나누기 = 0, 부호 있는 연산, 인자 순서)로 평가한다.
        예전엔 `add`→`+`(checked), `div`→`/`(0 나누기 → ⊥), `slt`→`<`(부호 없음), `shl(s, v)`→`s << v`(순서 뒤바뀜)였다."""
        func_name = ctx.IDENTIFIER().getText()
        args = [self._build_expression(e) for e in ctx.yulExpression()]
        return Expression(identifier=func_name, arguments=args, context="YulBuiltinContext")

    def _build_literal(self, ctx: YulParser.YulLiteralContext) -> Expression:
        """yulLiteral → Expression IR

        ★ expr_type 을 반드시 채운다. 예전엔 5개 반환 경로 전부 비워 둬서
          `Evaluation.evaluate_literal_context` 가 모든 분기를 빠져나가
          `Unsupported literal expr_type 'None'` 으로 죽었다(51건: TickMath 36,
          PRBMathCommon 7, PRBMathSD59x18 5, FullMath 2, UnsafeMath 1).

        ★ literal 은 반드시 **문자열**로 넣는다. 예전엔 `int(...)` 로 변환해 넣었는데,
          evaluate_literal_context 는 `int(txt, 0)` 로 파싱하므로 int 를 주면
          `TypeError: int() can't convert non-string with explicit base` 가 나고
          그 함수의 `except ValueError` 로는 잡히지 않는다. Solidity 쪽
          `visitLiteralExp` 도 `ctx.getText()` 문자열을 넣는다 — 같은 규약을 따른다.

        ★ 타입은 'uint'/256. Yul 은 타입 없는 256-bit word 언어다.
          (이 리포 소스에 slt/sdiv/smod/sar/signextend 는 0건, sgt 만 7건)
        """
        if ctx.DECIMAL_NUMBER():
            return Expression(literal=ctx.DECIMAL_NUMBER().getText(),
                              expr_type='uint', type_length=256,
                              context="LiteralExpContext")
        if ctx.HEX_NUMBER():
            # '0x..' 원문 그대로 — evaluate_literal_context 의 int(txt, 0) 이 해석한다.
            return Expression(literal=ctx.HEX_NUMBER().getText(),
                              expr_type='uint', type_length=256,
                              context="LiteralExpContext")
        if ctx.STRING_LITERAL():
            return Expression(literal=ctx.STRING_LITERAL().getText(),
                              expr_type='string', context="LiteralExpContext")
        text = ctx.getText()
        if text in ('true', 'false'):
            # Yul 에 bool 타입은 없다 — true/false 는 1/0 word 다.
            return Expression(literal='1' if text == 'true' else '0',
                              expr_type='uint', type_length=256,
                              context="LiteralExpContext")
        # 알 수 없는 리터럴을 조용히 0 으로 만들던 폴백 제거 — 그건 오분석의 원천이다.
        raise ValueError(f"Unknown yulLiteral: {text!r}")
