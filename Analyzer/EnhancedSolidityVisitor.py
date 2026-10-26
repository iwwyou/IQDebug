from Parser.SolidityParser import SolidityParser
from Parser.SolidityVisitor import SolidityVisitor
# 맨 위 import 부분
from antlr4.tree.Tree import TerminalNodeImpl

from fractions import Fraction
from Domain.Variable import (Variables, GlobalVariable, ArrayVariable, StructVariable,
                            EnumVariable, MappingVariable, StructDefinition, EnumDefinition)
from Domain.Type import SolType
from Domain.Interval import IntegerInterval, UnsignedIntegerInterval, BoolInterval
from Domain.IR import Expression

KEYWORD_IDENTIFIERS = {
    "from", "to", "payable", "returns",      # 필요 시 계속 추가
}

TIME_VALUE = {
    "seconds": 1,
    "minutes": 60,
    "hours":   60 * 60,
    "days":    24 * 60 * 60,
    "weeks":   7  * 24 * 60 * 60,
    "years":   365 * 24 * 60 * 60,
    "wei":     1,
    "gwei":    10 ** 9,
    "ether":   10 ** 18,
}

# ContractAnalyzer (or util module) ──────────────────────────
READONLY_MEMBERS = {
    # Array / bytes
    "length", "slot", "offset",
    # Address
    "balance", "code", "codehash",
    # Function
    "selector",
    # type(T) meta
    "max", "min", "size", "name"
}

READONLY_GLOBAL_BASES = {"block", "msg", "tx"}


def _is_contract_cfg(cfg) -> bool:
    """이 CFG 가 (라이브러리가 아닌) contract/interface CFG 인가.

    `make_library_cfg` 는 호환성을 위해 LibraryCFG 를 `contract_cfgs` 에도 등록한다.
    따라서 "contract_cfgs 에 있다"만으로는 contract 타입이라고 단정할 수 없다.
    이름으로 라이브러리를 배제하면 동명이인에서 틀리므로 **객체 종류**로 판정한다.
    """
    if cfg is None:
        return False
    from Utils.CFG import LibraryCFG
    return not isinstance(cfg, LibraryCFG)


class EnhancedSolidityVisitor(SolidityVisitor):

    def __init__(self, contract_analyzer):
        self.contract_analyzer = contract_analyzer

    # Visit a parse tree produced by SolidityParser#sourceUnit.
    def visitSourceUnit(self, ctx:SolidityParser.SourceUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#pragmaDirective.
    def visitPragmaDirective(self, ctx:SolidityParser.PragmaDirectiveContext):
        """
        pragma solidity ^0.8.0; 형태의 pragma directive 처리
        ContractAnalyzer에 pragma 정보 등록
        """
        pragma_name = None
        pragma_value = None

        # pragmaName과 pragmaValue 추출
        if ctx.pragmaName():
            pragma_name = ctx.pragmaName().getText()
        if ctx.pragmaValue():
            pragma_value = ctx.pragmaValue().getText()

        # ContractAnalyzer에 등록
        if pragma_name:
            self.contract_analyzer.process_pragma_directive(pragma_name, pragma_value or "")

        return None

    # Visit a parse tree produced by SolidityParser#pragmaName.
    def visitPragmaName(self, ctx:SolidityParser.PragmaNameContext):
        return ctx.getText()

    # Visit a parse tree produced by SolidityParser#pragmaValue.
    def visitPragmaValue(self, ctx:SolidityParser.PragmaValueContext):
        return ctx.getText()

    # Visit a parse tree produced by SolidityParser#version.
    def visitVersion(self, ctx:SolidityParser.VersionContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#versionOperator.
    def visitVersionOperator(self, ctx:SolidityParser.VersionOperatorContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#versionConstraint.
    def visitVersionConstraint(self, ctx:SolidityParser.VersionConstraintContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#importDeclaration.
    def visitImportDeclaration(self, ctx:SolidityParser.ImportDeclarationContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#importDirective.
    def visitImportDirective(self, ctx:SolidityParser.ImportDirectiveContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#importPath.
    def visitImportPath(self, ctx:SolidityParser.ImportPathContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#symbolAliases.
    def visitSymbolAliases(self, ctx:SolidityParser.SymbolAliasesContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#contractDefinition.
    def visitContractDefinition(self, ctx:SolidityParser.ContractDefinitionContext):
        contract_name = ctx.identifier().getText()

        # abstract 키워드 확인
        is_abstract = False
        if ctx.getChildCount() > 0:
            first_token = ctx.getChild(0).getText()
            if first_token == 'abstract':
                is_abstract = True

        # 상속 관계 추출 (is A, B, C)
        parent_contracts = []
        if ctx.inheritanceSpecifier():
            for inherit_ctx in ctx.inheritanceSpecifier():
                # identifierPath에서 부모 컨트랙트 이름 추출
                parent_name = inherit_ctx.identifierPath().getText()
                parent_contracts.append(parent_name)

        # ContractAnalyzer에서 해당 컨트랙트의 CFG 생성
        if is_abstract:
            self.contract_analyzer.make_abstract_contract_cfg(contract_name, parent_contracts)
        else:
            self.contract_analyzer.make_contract_cfg(contract_name, parent_contracts)
        # 컨트랙트 내부의 함수, 변수 등을 방문하기 위해 visitChildren 호출
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interfaceDefinition.
    def visitInterfaceDefinition(self, ctx:SolidityParser.InterfaceDefinitionContext):
        interface_name = ctx.identifier().getText()

        # 상속 관계 추출 (interface도 다른 interface 상속 가능)
        parent_interfaces = []
        if ctx.inheritanceSpecifier():
            for inherit_ctx in ctx.inheritanceSpecifier():
                parent_name = inherit_ctx.identifierPath().getText()
                parent_interfaces.append(parent_name)

        # ContractAnalyzer에서 Interface CFG 생성
        self.contract_analyzer.make_interface_cfg(interface_name, parent_interfaces)
        return

    # Visit a parse tree produced by SolidityParser#libraryDefinition.
    def visitLibraryDefinition(self, ctx:SolidityParser.LibraryDefinitionContext):
        library_name = ctx.identifier().getText()
        
        # ContractAnalyzer에서 해당 라이브러리의 CFG 생성
        self.contract_analyzer.make_library_cfg(library_name)
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#inheritanceSpecifier.
    def visitInheritanceSpecifier(self, ctx:SolidityParser.InheritanceSpecifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#callArgumentList.
    def visitCallArgumentList(self, ctx:SolidityParser.CallArgumentListContext):
        argument_list = []

        # 1. 명명되지 않은 인자 리스트 (표현식 목록) 처리
        if ctx.expression():
            for expr in ctx.expression():
                # expression 각각을 방문하여 처리한 결과를 리스트에 추가
                argument_list.append(self.visitExpression(expr))

        # 2. 명명된 인자 처리 (identifier: expression 쌍)
        elif ctx.identifier() and ctx.expression():
            named_arguments = {}
            for identifier, expression in zip(ctx.identifier(), ctx.expression()):
                # identifier와 해당 expression을 각각 방문한 결과로 dictionary에 추가
                named_arguments[identifier.getText()] = self.visitExpression(expression)

            argument_list.append(named_arguments)

        return argument_list


    # Visit a parse tree produced by SolidityParser#identifierPath.
    def visitIdentifierPath(self, ctx:SolidityParser.IdentifierPathContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#constantVariableDeclaration.
    # ---------------------------------------------------------------------------
    # ① constant 변수 선언 방문      (예:  uint256 constant DECIMALS = 18;)
    # ---------------------------------------------------------------------------
    def visitConstantVariableDeclaration(self,
                                         ctx: SolidityParser.ConstantVariableDeclarationContext):

        var_name = ctx.identifier().getText()

        # 1) 타입 분석 → SolType 객체
        type_ctx = ctx.typeName()
        type_obj = SolType()
        type_obj = self.visitTypeName(type_ctx, type_obj)

        # 2) 변수 객체 생성 (state 변수와 동일한 분기)
        if type_obj.typeCategory == "array":
            variable_obj = ArrayVariable(identifier=var_name,
                                         base_type=type_obj.arrayBaseType,
                                         array_length=type_obj.arrayLength,
                                         scope="state")
        elif type_obj.typeCategory == "struct":
            variable_obj = StructVariable(identifier=var_name,
                                          struct_type=type_obj.structTypeName,
                                          scope="state")
        elif type_obj.typeCategory == "mapping":
            variable_obj = MappingVariable(identifier=var_name,
                                           key_type=type_obj.mappingKeyType,
                                           value_type=type_obj.mappingValueType,
                                           scope="state")
        elif type_obj.typeCategory == "enum":
            variable_obj = EnumVariable(identifier=var_name,
                                        enum_type=type_obj.enumTypeName,
                                        scope="state")
        else:  # elementary
            variable_obj = Variables(identifier=var_name, scope="state")
            variable_obj.typeInfo = type_obj

        variable_obj.isConstant = True  # ← 상수 표시

        # 3) 초기화식 (Expression) 파싱
        init_expr = self.visitExpression(ctx.expression()) if ctx.expression() else None

        # 4) ContractAnalyzer 로 위임
        self.contract_analyzer.process_constant_variable(variable_obj, init_expr)

    # Visit a parse tree produced by SolidityParser#contractBodyElement.
    def visitContractBodyElement(self, ctx:SolidityParser.ContractBodyElementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#constructorDefinition.
    def visitConstructorDefinition(self, ctx: SolidityParser.ConstructorDefinitionContext):
        constructor_name = "constructor"

        # 파라미터 리스트 처리
        parameters = {}
        if ctx.parameterList():
            parameters = self.visitParameterList(ctx.parameterList())

        # Modifier(및 기반 생성자 호출) — (이름, 인자 식) 으로 수집 (Phase 4)
        modifiers = self._modifier_invocations(ctx.modifierInvocation() or [])

        # ContractAnalyzer로 전달하여 처리
        self.contract_analyzer.process_constructor_definition(
            name=constructor_name,
            params=parameters,
            modifiers=modifiers
        )

    # Visit a parse tree produced by SolidityParser#fallbackFunctionDefinition.
    # (2026-10-23) fallback/receive 를 함수로 등록한다. 예전엔 visitChildren 뿐이라 CFG 가 없었고 본문 문장이 전부
    #   "No active function CFG" 로 실패했다(n_Nokon·w61 CreditLine·w79 LaunchEvent receive). 이름 `fallback`/`receive` 는
    #   Solidity 키워드라 사용자 함수 이름과 겹치지 않는다.
    def visitFallbackFunctionDefinition(self, ctx:SolidityParser.FallbackFunctionDefinitionContext):
        has_returns = any(isinstance(ch, TerminalNodeImpl) and ch.getText() == "returns" for ch in ctx.getChildren())
        plists = list(ctx.parameterList())
        params_ctx = plists[0] if (plists and not (has_returns and len(plists) == 1)) else None
        returns_ctx = plists[-1] if has_returns else None
        mutability = next((ch.getText() for ch in ctx.getChildren()
                           if isinstance(ch, SolidityParser.StateMutabilityContext)), None)
        mods = self._modifier_invocations(
            [m for m in ctx.getChildren() if isinstance(m, SolidityParser.ModifierInvocationContext)])
        self.contract_analyzer.process_function_definition(
            function_name="fallback",
            parameters=self.visitParameterList(params_ctx) if params_ctx else [],
            modifiers=mods,
            returns=self.visitParameterList(returns_ctx) if returns_ctx else [],
            mutability=mutability,
            has_body=ctx.block() is not None,
        )

    # Visit a parse tree produced by SolidityParser#receiveFunctionDefinition.
    def visitReceiveFunctionDefinition(self, ctx:SolidityParser.ReceiveFunctionDefinitionContext):
        mods = self._modifier_invocations(
            [m for m in ctx.getChildren() if isinstance(m, SolidityParser.ModifierInvocationContext)])
        self.contract_analyzer.process_function_definition(
            function_name="receive", parameters=[], modifiers=mods, returns=[],
            mutability="payable",                      # receive 는 언제나 payable
            has_body=ctx.block() is not None,
        )

    # Visit a parse tree produced by SolidityParser#stateVariableDeclaration.
    # EnhancedSolidityVisitor.py
    def visitStateVariableDeclaration(self,
                                      ctx: SolidityParser.StateVariableDeclarationContext):

        var_name = ctx.identifier().getText()

        # ── ① 타입 해석 ─────────────────────────────────────────────
        type_ctx = ctx.typeName()
        type_info = self.visitTypeName(type_ctx, SolType())  # ← SolType 객체

        # ── ② 변수 object  생성 (array / struct / mapping / enum / elementary) ──
        if type_info.typeCategory == "array":
            all_structs, all_enums = self.contract_analyzer.get_full_struct_enum_defs()
            var_obj = ArrayVariable(var_name, type_info.arrayBaseType,
                                    type_info.arrayLength, scope="state",
                                    is_dynamic=type_info.isDynamicArray,
                                    struct_defs=all_structs, enum_defs=all_enums)
        elif type_info.typeCategory == "struct":
            var_obj = StructVariable(var_name, type_info.structTypeName, scope="state",
                                     )
        elif type_info.typeCategory == "mapping":
            all_structs, all_enums = self.contract_analyzer.get_full_struct_enum_defs()
            var_obj = MappingVariable(var_name,
                                      type_info.mappingKeyType,
                                      type_info.mappingValueType,
                                      scope="state",
                                      struct_defs=all_structs, enum_defs=all_enums)
        elif type_info.typeCategory == "enum":
            var_obj = EnumVariable(var_name, type_info.enumTypeName, scope="state")
        else:  # elementary / address / bool …
            var_obj = Variables(var_name, scope="state")
            var_obj.typeInfo = type_info

        # ── ③ 초기화식 (있을 수도, 없을 수도) ────────────────────────
        init_expr = self.visitExpression(ctx.expression()) if ctx.expression() else None

        # ── ④ ‘constant’ 토큰 존재 여부 판별 ────────────────────────
        #     antlr4 는 토큰 이름으로 <rule>.<TokenName>() 메서드를 준다.
        has_constant = len(ctx.ConstantKeyword()) > 0

        if has_constant:
            # `constant`이면 별도 로직으로
            self.contract_analyzer.process_constant_variable(var_obj, init_expr)
        else:
            # 일반 state-var
            self.contract_analyzer.process_state_variable(var_obj, init_expr)

    # Visit a parse tree produced by SolidityParser#errorDefinition.
    def visitErrorDefinition(self, ctx:SolidityParser.ErrorDefinitionContext):
        # (2026-10-23) 엔진은 `revert E(...)` 를 ERROR 로만 모델링하므로 error 정의에서 기록할 정보가 없다.
        #   예전엔 visitChildren 이 파라미터 타입을 기본 방문해 `visitBasicType(type_obj 없음)` 으로 죽었다
        #   (Fixed18/UFixed18 라이브러리 안 error, PRBMath 파일 수준 error).
        return None

    # Visit a parse tree produced by SolidityParser#errorParameter.
    def visitErrorParameter(self, ctx:SolidityParser.ErrorParameterContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#usingDirective.
    def visitUsingDirective(self, ctx:SolidityParser.UsingDirectiveContext):
        # using LibraryName for TypeName; 또는 using LibraryName for *;

        # 라이브러리 이름 추출 (identifierPath는 리스트로 반환됨)
        id_paths = ctx.identifierPath()
        if id_paths:
            # 첫 번째 identifierPath 사용 (단일 라이브러리인 경우)
            library_name = id_paths[0].getText() if isinstance(id_paths, list) else id_paths.getText()
        else:
            return self.visitChildren(ctx)

        # 대상 타입 추출 (for 다음에 오는 부분)
        target_type = None
        if ctx.typeName():
            # 특정 타입에 대한 using directive
            target_type = ctx.typeName().getText()
        # '*'인 경우 target_type = None (모든 타입에 적용)

        # ContractAnalyzer에 using directive 등록
        self.contract_analyzer.process_using_directive(library_name, target_type)

        return

    # Visit a parse tree produced by SolidityParser#userDefinableOperators.
    def visitUserDefinableOperators(self, ctx:SolidityParser.UserDefinableOperatorsContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#structDefinition.
    def visitStructDefinition(self, ctx:SolidityParser.StructDefinitionContext):
        if self.contract_analyzer.current_context_type == "fileLevelStruct":
            struct_name = ctx.identifier().getText()
            self.contract_analyzer.sa.process_file_level_struct_definition(struct_name)
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#structMember.
    def visitStructMember(self, ctx: SolidityParser.StructMemberContext):
        var_name = ctx.identifier().getText()

        # 1. 타입 분석
        type_ctx = ctx.typeName()
        type_obj = SolType()
        type_obj = self.visitTypeName(type_ctx, type_obj)  # SolType 객체 반환

        # 2. context에 따라 라우팅
        if self.contract_analyzer.current_context_type == "fileLevelStructMember":
            self.contract_analyzer.sa.process_file_level_struct_member(var_name, type_obj)
        else:
            self.contract_analyzer.process_struct_member(var_name, type_obj)

    # Visit a parse tree produced by SolidityParser#modifierDefinition.
    def visitModifierDefinition(self, ctx: SolidityParser.ModifierDefinitionContext):
        # 1. Modifier 이름을 가져옴
        modifier_name = ctx.identifier().getText()

        # 2. 파라미터가 존재하는지 확인
        # visitParameterList returns list[tuple[SolType, str | None]]
        # process_modifier_definition expects dict[str, SolType]
        parameters = None
        if ctx.parameterList():
            param_list = self.visitParameterList(ctx.parameterList())
            # list[tuple[SolType, name]] → dict[name, SolType]
            parameters = {}
            for sol_type, param_name in param_list:
                if param_name:
                    parameters[param_name] = sol_type

        self.contract_analyzer.process_modifier_definition(modifier_name, parameters)

    # Visit a parse tree produced by SolidityParser#visibility.
    def visitVisibility(self, ctx:SolidityParser.VisibilityContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#modifierInvocation.
    def visitModifierInvocation(self, ctx:SolidityParser.ModifierInvocationContext):
        return self.visitChildren(ctx)

    def _modifier_invocations(self, ctxs) -> list:
        """(Phase 4, D6) modifierInvocation 들 → [(이름, [인자 Expression])]. 예전엔 이름만 모아 인자가 버려졌다."""
        out = []
        for m in ctxs:
            name = m.identifierPath().getText()
            if name in {"override", "virtual"}:
                continue
            args = []
            if m.callArgumentList() is not None:
                args = self.visitCallArgumentList(m.callArgumentList()) or []
                if isinstance(args, dict):
                    raise ValueError(f"modifier '{name}': named arguments are not supported")
            out.append((name, list(args)))
        return out

    # Visit a parse tree produced by SolidityParser#functionDefinition.
    def visitFunctionDefinition(self, ctx: SolidityParser.FunctionDefinitionContext):
        fname = ctx.identifier().getText() if ctx.identifier() else None
        if not fname:
            raise ValueError("function name missing")

        # ------------------------------------------------------------
        # ① returns 절 존재 여부를 먼저 확인한다
        # ------------------------------------------------------------
        #
        # ‣ 'returns' 는 리터럴 토큰이라 ANTLR 에서 T__n 형태의 TerminalNode 로 들어옵니다.
        #   따라서 children 중 텍스트 비교로 존재 여부를 판단하는 편이 가장 단순-안전합니다.
        #
        has_returns = any(
            isinstance(ch, TerminalNodeImpl) and ch.getText() == "returns"
            for ch in ctx.getChildren()
        )

        # ------------------------------------------------------------
        # ② parameterList() 들을 파라미터 / 리턴으로 분리
        # ------------------------------------------------------------
        #
        #   * returns 가 있으면  ⟶  parameterList() 가
        #       • 1 개 :   그것이 returns
        #       • 2 개 :   (0) 파라미터, (1) returns
        #   * returns 가 없으면 ⟶  parameterList() 가
        #       • 0 개 :   모두 없음
        #       • 1 개 :   그것이 파라미터
        #
        plists = list(ctx.parameterList())
        params_ctx, returns_ctx = None, None

        if has_returns:
            if len(plists) == 1:  # 파라미터 없음, returns 만 존재
                returns_ctx = plists[0]
            elif len(plists) == 2:  # 둘 다 존재
                params_ctx, returns_ctx = plists
        else:
            if len(plists) == 1:  # 파라미터만 존재
                params_ctx = plists[0]

        # ------------------------------------------------------------
        # ③ 실제 파라미터 / 리턴 목록 추출
        # ------------------------------------------------------------
        params = self.visitParameterList(params_ctx) if params_ctx else []
        rets = self.visitParameterList(returns_ctx) if returns_ctx else []

        # ------------------------------------------------------------
        # ④ stateMutability 추출
        # ------------------------------------------------------------
        mutability = None
        for ch in ctx.getChildren():
            if isinstance(ch, SolidityParser.StateMutabilityContext):
                mutability = ch.getText()  # "view", "pure", "payable"
                break

        # ------------------------------------------------------------
        # ⑤ modifierInvocation(override/virtual 제외) 수집
        # ------------------------------------------------------------
        mods = self._modifier_invocations(
            [m for m in ctx.getChildren() if isinstance(m, SolidityParser.ModifierInvocationContext)])

        # ------------------------------------------------------------
        # ⑥ ContractAnalyzer 로 전달
        # ------------------------------------------------------------
        self.contract_analyzer.process_function_definition(
            function_name=fname,
            parameters=params,
            modifiers=mods,
            returns=rets,
            mutability=mutability,
            has_body=ctx.block() is not None,    # (2026-10-23) `function f() virtual;` 는 선언뿐
        )

    # Visit a parse tree produced by SolidityParser#eventDefinition.
    def visitEventDefinition(self, ctx:SolidityParser.EventDefinitionContext):
        """
        event Transfer(address indexed from, address indexed to, uint256 value);
        형태의 event 정의 처리
        """
        # 1. event 이름 추출
        event_name = ctx.identifier().getText()

        # 2. event 파라미터 추출
        parameters = []
        if ctx.eventParameter():
            for param_ctx in ctx.eventParameter():
                param_info = self.visitEventParameter(param_ctx)
                if param_info:
                    parameters.append(param_info)

        # 3. ContractAnalyzer에 등록
        self.contract_analyzer.process_event_definition(event_name, parameters)

        return None

    # Visit a parse tree produced by SolidityParser#enumDefinition.
    def visitEnumDefinition(self, ctx:SolidityParser.EnumDefinitionContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#parameterList.
    def visitParameterList(self,
                           ctx: SolidityParser.ParameterListContext
                           ) -> list[tuple[SolType, str | None]]:
        params: list[tuple[SolType, str | None]] = []

        cur: list = []
        for ch in ctx.children:
            if ch.getText() == ',':
                if cur:
                    params.append(self._param_from_group(cur))
                    cur = []
            else:
                cur.append(ch)
        if cur:
            params.append(self._param_from_group(cur))
        return params

    def _param_from_group(self, group):
        sol_type = SolType()
        name = None

        for el in group:
            if isinstance(el, SolidityParser.TypeNameContext):
                sol_type = self.visitTypeName(el, sol_type)

            elif isinstance(el, SolidityParser.IdentifierContext):
                name = el.getText()

            elif isinstance(el, TerminalNodeImpl):
                txt = el.getText()
                if txt in KEYWORD_IDENTIFIERS:  # ← 소문자 문자열 비교
                    name = txt

        return sol_type, name

    # Visit a parse tree produced by SolidityParser#eventParameter.
    def visitEventParameter(self, ctx:SolidityParser.EventParameterContext):
        """
        event 파라미터 처리
        반환: (SolType, param_name, is_indexed)
        """
        # 1. 타입 정보 추출
        type_obj = SolType()
        if ctx.typeName():
            type_obj = self.visitTypeName(ctx.typeName(), type_obj)

        # 2. indexed 여부 확인
        is_indexed = False
        if ctx.IndexedKeyword():
            is_indexed = True

        # 3. 파라미터 이름 추출 (선택적)
        param_name = None
        if ctx.identifier():
            param_name = ctx.identifier().getText()

        return (type_obj, param_name, is_indexed)

    # Visit a parse tree produced by SolidityParser#variableDeclaration.
    def visitVariableDeclaration(self, ctx:SolidityParser.VariableDeclarationContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#variableDeclarationTuple.
    def visitVariableDeclarationTuple(self, ctx:SolidityParser.VariableDeclarationTupleContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#typeName.
    def visitTypeName(self, ctx: SolidityParser.TypeNameContext, type_obj):

        if isinstance(ctx, SolidityParser.BasicTypeContext):  # elementaryTypeName (BasicType)
            return self.visitBasicType(ctx, type_obj)
        elif isinstance(ctx, SolidityParser.FunctionTypeContext):  # functionTypeName (FunctionType)
            return self.visitFunctionType(ctx, type_obj)
        elif isinstance(ctx, SolidityParser.MapTypeContext):  # mapping (MapType)
            return self.visitMapType(ctx, type_obj)
        elif isinstance(ctx, SolidityParser.UserDefinedTypeContext):  # identifierPath (StructType)
            return self.visitUserDefinedType(ctx, type_obj)
        elif isinstance(ctx, SolidityParser.ArrayTypeContext):  # typeName '[' expression? ']' (ArrayType)
            return self.visitArrayType(ctx, type_obj)

    # Visit a parse tree produced by SolidityParser#ArrayType.
    def visitArrayType(
            self,
            ctx: SolidityParser.ArrayTypeContext,  # ← 변경
            type_obj: SolType
    ) -> SolType:
        # 배열의 기본 타입 처리
        base_type_ctx = ctx.typeName()
        base_type_obj = SolType()
        base_type_obj = self.visitTypeName(base_type_ctx, base_type_obj)

        # 배열 크기 확인
        if ctx.expression():
            array_size_expr = ctx.expression()
            # (2026-10-23) 길이 = 컴파일 타임 상수식(리터럴·`constant` 상태변수·`Lib.CONST`·산술). 예전엔 리터럴만 받아
            #   `uint256[N_COINS]`(w17)·`Observation[MAX_CARDINALITY]`(w34) 가 "Unsupported literal type" 이었다.
            if array_size_expr.getText().isdigit():
                array_size = self.evaluate_literal_expression(array_size_expr)
            else:
                array_size = self.contract_analyzer.compile_time_constant(self.visitExpression(array_size_expr))
            is_dynamic = False
        else:
            array_size = 0
            is_dynamic = True

        type_obj.typeCategory = "array"
        type_obj.arrayBaseType = base_type_obj  # 재귀적으로 타입 표현
        type_obj.arrayLength = array_size
        type_obj.isDynamicArray = is_dynamic

        return type_obj

    # Visit a parse tree produced by SolidityParser#BasicType.
    def visitBasicType(self, ctx: SolidityParser.ElementaryTypeNameContext, type_obj):
        var_type = ctx.getText()
        # (Phase 4b) `address payable` 은 문법상 한 토큰이다. 추상 도메인(AddressSet)에서 payable 여부는
        #   의미가 없으므로 `address` 로 정규화한다. 예전엔 "address payable" 그대로 두어 `== "address"` 로
        #   분기하는 모든 초기화(⊤ AddressSet)를 빗나가 `"symbol_x"` 문자열 값이 됐고, `.transfer/.send` 가
        #   해석 불가 멤버로 터졌다.
        if var_type == "address payable":
            var_type = "address"
            type_obj.isPayable = True
        type_obj.typeCategory = "elementary"
        type_obj.elementaryTypeName = var_type

        if var_type.startswith('int'):
            if var_type == 'int':
                type_obj.intTypeLength = 256  # 기본 길이는 256
            else:
                # 'int' 뒤에 붙은 숫자를 추출하여 비트 길이 설정
                try:
                    type_obj.intTypeLength = int(var_type[3:])  # 'int' 뒤의 숫자를 추출
                except ValueError:
                    raise ValueError(f"Invalid integer type length in '{var_type}'")

        elif var_type.startswith('uint'):
            if var_type == 'uint':
                type_obj.intTypeLength = 256  # 기본 길이는 256
            else:
                # 'uint' 뒤에 붙은 숫자를 추출하여 비트 길이 설정
                try:
                    type_obj.intTypeLength = int(var_type[4:])  # 'uint' 뒤의 숫자를 추출
                except ValueError:
                    raise ValueError(f"Invalid unsigned integer type length in '{var_type}'")

        return type_obj

    # Visit a parse tree produced by SolidityParser#FunctionType.
    def visitFunctionType(self, ctx: SolidityParser.FunctionTypeNameContext, type_obj):
        # (2026-10-23) 함수 타입: 파라미터·반환 타입·가시성을 기록한다 — 함수 포인터 호출의 반환값 ⊤ 와
        #   효과 범위(internal = 같은 컨트랙트 상태를 바꿀 수 있음 / external = 외부 호출)를 정하는 데 쓴다.
        type_obj.typeCategory = "function"
        ftn = ctx.functionTypeName() if hasattr(ctx, "functionTypeName") else ctx
        lists = list(ftn.parameterList() or [])
        has_returns = any(getattr(ch, "getText", lambda: "")() == "returns" for ch in ftn.children or [])
        ret_list = lists.pop() if has_returns else None
        type_obj.functionParamTypes = [t for t, _ in self.visitParameterList(lists[0])] if lists else []
        type_obj.functionReturnTypes = [t for t, _ in self.visitParameterList(ret_list)] if ret_list is not None else []
        vis = [v.getText() for v in (ftn.visibility() or [])]
        type_obj.functionVisibility = "external" if "external" in vis else "internal"
        return type_obj

    # Visit a parse tree produced by SolidityParser#StructType.
    def visitUserDefinedType(self, ctx: SolidityParser.UserDefinedTypeContext, type_obj):
        """
            사용자 정의 타입(Struct, Enum 등)을 처리합니다.
            :param ctx: IdentifierPathContext
            :param type_obj: SolType 객체
            :return: 수정된 type_obj
            """
        # 타입 이름 추출
        type_name = ctx.getText()

        # 현재 타겟 컨트랙트 이름 가져오기
        contract_name = self.contract_analyzer.current_target_contract

        # 현재 컨트랙트의 CFG 가져오기 (file-level이면 None)
        contract_cfg = self.contract_analyzer.contract_cfgs.get(contract_name) if contract_name else None

        # 타입이 enum인지 struct인지 확인 (contract scope + parent chain)
        def _find_in_chain(cfg, name, attr):
            """contract CFG + parent chain에서 struct/enum 정의 검색 (없으면 None)"""
            defs = getattr(cfg, attr, {})
            if name in defs:
                return defs[name]
            for pcfg in getattr(cfg, 'parent_cfgs', {}).values():
                d = _find_in_chain(pcfg, name, attr)
                if d is not None:
                    return d
            return None

        _enum_def = _find_in_chain(contract_cfg, type_name, 'enumDefs') if contract_cfg else None
        _struct_def = _find_in_chain(contract_cfg, type_name, 'structDefs') if contract_cfg and _enum_def is None else None
        if _enum_def is not None:
            type_obj.typeCategory = "enum"
            type_obj.enumTypeName = type_name
            type_obj.enumOwner = getattr(_enum_def, "owner", None)       # 파일 수준 enum 사본은 None
        elif _struct_def is not None:
            type_obj.typeCategory = "struct"
            type_obj.structTypeName = type_name
            type_obj.structOwner = getattr(_struct_def, "owner", None)
        elif type_name in self.contract_analyzer.interface_names:
            # Interface 타입: 원본 이름 보존 + address 하위 호환
            type_obj.typeCategory = "interface"
            type_obj.interfaceName = type_name
            type_obj.elementaryTypeName = "address"
            type_obj.intTypeLength = 160
        elif _is_contract_cfg(self.contract_analyzer.contract_cfgs.get(type_name)):
            # Contract 타입 (부모 컨트랙트 등): interface와 동일하게 address 취급
            # ★ 예전 조건은 `type_name in contract_cfgs and type_name not in library_cfgs`
            #   였다. `make_library_cfg` 가 라이브러리를 contract_cfgs 에도 넣기 때문에
            #   둔 가드인데, **이름 기반이라 동명이인에서 오작동**한다:
            #   `contracts/Roles.sol`(contract) 과 `contracts/112/Roles.sol`(library) 이
            #   공존하면 library 쪽이 library_cfgs 에 있다는 이유로 contract 분기가 막힌다.
            #   객체의 실제 종류로 판정하면 정확하고 rename 이 불필요하다.
            type_obj.typeCategory = "interface"
            type_obj.interfaceName = type_name
            type_obj.elementaryTypeName = "address"
            type_obj.intTypeLength = 160
        else:
            # file-level struct 확인
            file_struct = self.contract_analyzer.sa.get_file_level_struct(type_name)
            if file_struct is not None:
                type_obj.typeCategory = "struct"
                type_obj.structTypeName = type_name
            # ★ file-level enum 확인 (Solidity 0.8+ 는 파일 최상위 enum 을 허용한다)
            #   `sa.file_level_enums` 는 {이름: [멤버,...]} 이고, 이 분기가 없어서
            #   `enum AssetClass {...}` + `returns (AssetClass)` 가 해석되지 않았다.
            #   ⚠ typeCategory 만 세우면 안 된다 — 하류가
            #     `ccf.enumDefs.get(enumTypeName)` 을 하고 없으면 "undefined enum" 으로
            #     터진다(ContractAnalyzer:1277/1324/1427/1520). 그래서 정의도 등록한다.
            elif type_name in (getattr(self.contract_analyzer.sa, 'file_level_enums', None) or {}):
                type_obj.typeCategory = "enum"
                type_obj.enumTypeName = type_name
                if contract_cfg is not None and type_name not in contract_cfg.enumDefs:
                    _ed = EnumDefinition(type_name)
                    for _m in self.contract_analyzer.sa.file_level_enums[type_name]:
                        _ed.add_member(_m)
                    contract_cfg.enumDefs[type_name] = _ed
            else:
                # type alias 확인 (user-defined value type)
                underlying = self.contract_analyzer.sa.resolve_type(type_name)
                if underlying is not None:
                    type_obj.typeCategory = "elementary"
                    type_obj.elementaryTypeName = underlying
                    type_obj.aliasName = type_name  # 원래 타입 이름 보존
                    # int/uint 비트 길이 설정
                    if underlying.startswith("int"):
                        type_obj.intTypeLength = int(underlying[3:]) if len(underlying) > 3 else 256
                    elif underlying.startswith("uint"):
                        type_obj.intTypeLength = int(underlying[4:]) if len(underlying) > 4 else 256
                elif '.' in type_name:
                    # Qualified name: Library.Struct or Library.Enum (e.g., FixedPointMath.FixedDecimal,
                    # Perpetuals.Side) - despite its name, resolve_library_struct looks up both structDefs
                    # and enumDefs, so the result's actual kind must be checked here rather than assumed.
                    lib_name, member_name = type_name.split('.', 1)
                    resolved = self.contract_analyzer.resolve_library_struct(lib_name, member_name)
                    if isinstance(resolved, EnumDefinition):
                        type_obj.typeCategory = "enum"
                        type_obj.enumTypeName = type_name
                        type_obj.enumOwner = getattr(resolved, "owner", None)
                        if contract_cfg and type_name not in contract_cfg.enumDefs:
                            contract_cfg.enumDefs[type_name] = resolved
                    elif isinstance(resolved, StructDefinition):
                        type_obj.typeCategory = "struct"
                        type_obj.structTypeName = type_name
                        type_obj.structOwner = getattr(resolved, "owner", None)
                        # struct 정의를 현재 CFG에 등록 (qualified name으로, 이후 멤버 접근용)
                        if contract_cfg and type_name not in contract_cfg.structDefs:
                            contract_cfg.structDefs[type_name] = resolved
                    else:
                        # (2026-10-23, 레지스트리) 한정자 쪽 선언이 아직 없거나 그 선언에 멤버가 없다 — 이름 = 한정자
                        from Utils.Errors import UnresolvedName
                        raise UnresolvedName(lib_name, f"Qualified type '{type_name}': '{member_name}' not found in library '{lib_name}'.")
                else:
                    scope = f"contract '{contract_name}'" if contract_name else "file level"
                    # (2026-10-22) 사용자 소스 오류 — 현재 소스·로드된 의존성 어디에도 선언이 없다(편집 중 흔함).
                    #   SourceError 면 증분 업데이트가 이 변경만 반영하지 않고 진단으로 돌려준다(세션 유지).
                    from Utils.Errors import UndefinedType
                    raise UndefinedType(type_name, f"Type '{type_name}' is not defined as struct, enum, or type alias in {scope}.")

        return type_obj

    # Visit a parse tree produced by SolidityParser#MapType.
    def visitMapType(
            self,
            ctx: SolidityParser.MapTypeContext,  # ✔ MapTypeContext!
            type_obj: SolType
    ) -> SolType:
        # ctx.mapping() 는 이제 정적으로도 인식된다
        return self.visitMapping(ctx.mapping(), type_obj)

    # Visit a parse tree produced by SolidityParser#mapping.
    def visitMapping(self, ctx: SolidityParser.MappingContext, type_obj):
        # 키 타입 처리
        key_type_ctx = ctx.mappingKeyType()
        key_type_obj = self.visitMappingKeyType(key_type_ctx)

        # 값 타입 처리
        value_type_ctx = ctx.typeName()
        value_type_obj = SolType()
        value_type_obj = self.visitTypeName(value_type_ctx, value_type_obj)

        type_obj.typeCategory = "mapping"
        type_obj.mappingKeyType = key_type_obj
        type_obj.mappingValueType = value_type_obj

        return type_obj

    # Visit a parse tree produced by SolidityParser#mappingKeyType.
    def visitMappingKeyType(self, ctx:SolidityParser.MappingKeyTypeContext):
        # 키 타입: elementaryTypeName 외에, contract/interface(-> address)와 enum(-> 정수)도
        # 유효한 mapping key 타입임 (Solidity 문법도 identifierPath 를 허용, Solidity.g4:203-205)
        if ctx.elementaryTypeName() is not None:
            key_type_obj = SolType()
            self.visitBasicType(ctx.elementaryTypeName(), key_type_obj)
            return key_type_obj
        elif ctx.identifierPath() is not None:
            key_type_obj = SolType()
            return self.visitUserDefinedType(ctx.identifierPath(), key_type_obj)
        else:
            raise ValueError("Invalid key type in mapping: {}".format(ctx.getText()))

    # Visit a parse tree produced by SolidityParser#functionTypeName.
    def visitFunctionTypeName(self, ctx:SolidityParser.FunctionTypeNameContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveSourceUnit.
    def visitInteractiveSourceUnit(self, ctx:SolidityParser.InteractiveSourceUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveEnumUnit.
    def visitInteractiveEnumUnit(self, ctx:SolidityParser.InteractiveEnumUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveStructUnit.
    def visitInteractiveStructUnit(self, ctx:SolidityParser.InteractiveStructUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveBlockUnit.
    def visitInteractiveBlockUnit(self, ctx:SolidityParser.InteractiveBlockUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveDoWhileUnit.
    def visitInteractiveDoWhileUnit(self, ctx:SolidityParser.InteractiveDoWhileUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveIfElseUnit.
    def visitInteractiveIfElseUnit(self, ctx:SolidityParser.InteractiveIfElseUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveCatchClauseUnit.
    def visitInteractiveCatchClauseUnit(self, ctx:SolidityParser.InteractiveCatchClauseUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#intentUnit.
    def visitIntentUnit(self, ctx: SolidityParser.IntentUnitContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#debugInput.
    def visitDebugInput(self, ctx: SolidityParser.DebugInputContext):
        return self.visitChildren(ctx)

    def visitDebugGlobalVar(self, ctx: SolidityParser.DebugGlobalVarContext):
        # grammar가 GlobalVarSimple / GlobalVarAddressBalance로 분리됨 → 하위 visitor로 위임
        return self.visitChildren(ctx)

    def visitGlobalVarSimple(self, ctx: SolidityParser.GlobalVarSimpleContext):
        # 1) 식별자
        left = ctx.identifier(0).getText()
        right = ctx.identifier(1).getText() if ctx.identifier(1) else None
        gname = f"{left}.{right}" if right else left

        valid = {
            "block.basefee", "block.blobbasefee", "block.chainid", "block.coinbase",
            "block.difficulty", "block.gaslimit", "block.number", "block.prevrandao",
            "block.timestamp", "msg.sender", "msg.value", "tx.gasprice", "tx.origin"
        }
        if gname not in valid:
            raise ValueError(f"invalid global '{gname}'")

        value = self._parse_debug_value(ctx.debugValue())

        # elementary 타입 정보
        is_addr = gname in {"block.coinbase", "msg.sender", "tx.origin"}
        bit_len = 160 if is_addr else 256
        st = SolType()
        st.typeCategory = "elementary"
        st.elementaryTypeName = "address" if is_addr else "uint"
        st.intTypeLength = bit_len

        gv = GlobalVariable(identifier=gname, value=value, typeInfo=st)
        self.contract_analyzer.process_global_var_for_debug(gv)
        return None

    def visitGlobalVarAddressBalance(self, ctx: SolidityParser.GlobalVarAddressBalanceContext):
        # @GlobalVar address(X).balance = [value, value]
        # 문법상 identifier 가 이 룰에 두 번 나옴('address(X)'의 X, '.balance'의 balance) —
        # ctx.identifier() 는 리스트를 반환하므로 인덱스로 첫 번째(X)를 지정해야 함.
        addr_identifier = ctx.identifier(0).getText()

        if addr_identifier != "this":
            raise ValueError(
                f"address({addr_identifier}).balance는 현재 미지원. "
                f"address(this).balance만 지원됩니다.")

        value = self._parse_debug_value(ctx.debugValue())

        # address(this).balance → uint256
        st = SolType()
        st.typeCategory = "elementary"
        st.elementaryTypeName = "uint"
        st.intTypeLength = 256

        gname = "address(this).balance"
        gv = GlobalVariable(identifier=gname, value=value, typeInfo=st)
        self.contract_analyzer.process_global_var_for_debug(gv)
        return None

    def visitDebugStateVar(self, ctx: SolidityParser.DebugStateVarContext):
        lhs = self.visitVarRef(ctx.varRef())
        rhs = self._parse_debug_value(ctx.debugValue())
        self.contract_analyzer.process_state_var_for_debug(lhs, rhs)
        return None

    def visitDebugParam(self, ctx: SolidityParser.DebugParamContext):
        lhs = self.visitVarRef(ctx.varRef())
        rhs = self._parse_debug_value(ctx.debugValue())
        self.contract_analyzer.process_param_for_debug(lhs, rhs)
        return None

    def _parse_ireturn_access_chain(self, chain_ctx) -> tuple:
        """ireturnAccessChain → tuple of ("member", name), ("index", int), or ("call", name)"""
        if chain_ctx is None:
            return ()
        from Parser.SolidityParser import SolidityParser
        result = []
        for acc in chain_ctx.ireturnAccess():
            if isinstance(acc, SolidityParser.IReturnChainedCallContext):
                result.append(("call", acc.identifier().getText()))
            elif isinstance(acc, SolidityParser.IReturnMemberAccessContext):
                result.append(("member", acc.identifier().getText()))
            elif isinstance(acc, SolidityParser.IReturnIndexAccessContext):
                result.append(("index", int(acc.numberLiteral().getText())))
        return tuple(result)

    def visitIReturnPatternA(self, ctx):
        contract_var = ctx.identifier(0).getText()
        func_name = ctx.identifier(1).getText()
        access_chain = self._parse_ireturn_access_chain(ctx.ireturnAccessChain())
        value = self._parse_debug_value(ctx.debugValue())
        self.contract_analyzer.process_ireturn(contract_var, func_name, access_chain, value)
        return None

    def visitIReturnPatternB(self, ctx):
        interface_name = ctx.identifier(0).getText()
        addr_var = ctx.identifier(1).getText()
        func_name = ctx.identifier(2).getText()
        access_chain = self._parse_ireturn_access_chain(ctx.ireturnAccessChain())
        value = self._parse_debug_value(ctx.debugValue())
        self.contract_analyzer.process_ireturn_cast(interface_name, addr_var, func_name, access_chain, value)
        return None

    # Visit a parse tree produced by SolidityParser#duringIntent.
    def visitDuringIntent(self, ctx: SolidityParser.DuringIntentContext):
        """
        새 문법: duringIntent : '//' '@During' duringClause (logicOp duringClause)*
        모든 clause와 논리 연산자를 수집해서 한꺼번에 처리
        """
        clauses = []
        logic_ops = []

        # 모든 duringClause 수집
        num_clauses = len(ctx.duringClause()) if ctx.duringClause() else 0

        for i in range(num_clauses):
            clause_ctx = ctx.duringClause(i)
            clause_dict = self._build_during_clause_dict(clause_ctx)
            clauses.append(clause_dict)

            # 논리 연산자 수집 (마지막 제외)
            if i < num_clauses - 1 and ctx.logicOp(i):
                logic_ops.append(ctx.logicOp(i).getText())  # '&&' or '||'

        # ContractAnalyzer에 한꺼번에 전달 — (2026-10-22) 대상 함수는 호출자가 명시(intent_target)
        if clauses:
            self.contract_analyzer.process_during(clauses, logic_ops, fcfg=getattr(self, "intent_target", None))

        return None

    def _build_common_clause_dict(self, clause_ctx) -> dict:
        """
        commonClause를 dict로 변환 (During/Post 공통)
        """
        P = SolidityParser

        if isinstance(clause_ctx, P.ReturnExprCmpContext):
            return {
                "kind": "retExpr",
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue())
            }
        elif isinstance(clause_ctx, P.ReturnIndexCmpContext):
            return {
                "kind": "retIndex",
                "index": int(clause_ctx.numberLiteral().getText()),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue())
            }
        elif isinstance(clause_ctx, P.ReturnVarCmpContext):
            return {
                "kind": "retVar",
                "lhs": self.visit(clause_ctx.intentValue(0)),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue(1))
            }
        elif isinstance(clause_ctx, P.RelationalCmpContext):
            return {
                "kind": "direct",
                "lhs": self.visit(clause_ctx.intentValue(0)),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue(1))
            }
        elif isinstance(clause_ctx, P.PercentOfContext):
            return {
                "kind": "percentOf",
                "lhs": self.visit(clause_ctx.intentValue(0)),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue(1)),
                "percentage": int(clause_ctx.numberLiteral().getText())
            }
        elif isinstance(clause_ctx, P.CeilContext):
            return {
                "kind": "ceil",
                "lhs": self.visit(clause_ctx.intentValue(0)),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue(1)),
                "unit": int(clause_ctx.numberLiteral().getText())
            }
        elif isinstance(clause_ctx, P.FloorContext):
            return {
                "kind": "floor",
                "lhs": self.visit(clause_ctx.intentValue(0)),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue(1)),
                "unit": int(clause_ctx.numberLiteral().getText())
            }
        elif isinstance(clause_ctx, P.ImplicationContext):
            # Implication: intentValue '=>' intentValue
            # 각 intentValue를 nonzero 판정으로 래핑
            return {
                "kind": "implication",
                "antecedent": {"kind": "nonzero", "expr": self.visit(clause_ctx.intentValue(0))},
                "consequent": {"kind": "nonzero", "expr": self.visit(clause_ctx.intentValue(1))}
            }
        elif isinstance(clause_ctx, P.VarChangedEvalContext):
            # changed(x, true/false)
            text = clause_ctx.getText()
            expect_changed = "true" in text.split(",")[-1]
            return {
                "kind": "changed",
                "var": self.visit(clause_ctx.intentValue()),
                "expect_changed": expect_changed
            }
        else:
            raise ValueError(f"Unknown common clause type: {type(clause_ctx)}")

    def _build_during_clause_dict(self, clause_ctx) -> dict:
        """
        duringClause를 dict로 변환 (새 문법 기반)

        Before/After, Assign/Current 는 더 이상 별도 clause 가 아니라 commonClause 안의
        arithFactor 항(varRef(Before)/varRef(After)/varRef(Assign))으로 표현된다
        (Solidity.g4) → DuringCommon 으로 위임된다.
        """
        P = SolidityParser

        if isinstance(clause_ctx, P.DuringFunctionArgContext):
            # @During transfer.arg[0] > 0
            return {
                "kind": "functionArg",
                "func_name": clause_ctx.identifier().getText(),
                "arg_index": int(clause_ctx.numberLiteral().getText()),
                "op": self._relop_from_ctx(clause_ctx),
                "rhs": self.visit(clause_ctx.intentValue())
            }
        elif isinstance(clause_ctx, P.DuringFeasibleContext):
            # @During require feasible / assert feasible
            text = clause_ctx.getText()
            target = "require" if "require" in text else "assert"
            return {
                "kind": "feasible",
                "target": target
            }
        elif isinstance(clause_ctx, P.DuringCommonContext):
            # commonClause로 위임
            return self._build_common_clause_dict(clause_ctx.commonClause())
        else:
            raise ValueError(f"Unknown during clause type: {type(clause_ctx)}")

    # ───────────────── POST ────────────────────────────────────────
    # Visit a parse tree produced by SolidityParser#postIntent.
    def visitPostIntent(self, ctx: SolidityParser.PostIntentContext):
        """
        새 문법: postIntent : '//' '@Post' postClause (logicOp postClause)*
        모든 clause와 논리 연산자를 수집해서 한꺼번에 처리
        """
        clauses = []
        logic_ops = []

        # 모든 postClause 수집
        num_clauses = len(ctx.postClause()) if ctx.postClause() else 0

        for i in range(num_clauses):
            clause_ctx = ctx.postClause(i)
            clause_dict = self._build_post_clause_dict(clause_ctx)
            clauses.append(clause_dict)

            # 논리 연산자 수집 (마지막 제외)
            if i < num_clauses - 1 and ctx.logicOp(i):
                logic_ops.append(ctx.logicOp(i).getText())  # '&&' or '||'

        # ContractAnalyzer에 한꺼번에 전달 — (2026-10-22) 대상 함수는 호출자가 명시(intent_target)
        if clauses:
            self.contract_analyzer.process_post(clauses, logic_ops, fcfg=getattr(self, "intent_target", None))

        return None

    def _build_post_clause_dict(self, clause_ctx) -> dict:
        """
        postClause를 dict로 변환 (새 문법 기반)

        Entry/Exit 는 더 이상 별도 clause 가 아니라 commonClause 안의 arithFactor 항
        (varRef(Entry)/varRef(Exit))으로 표현된다 (Solidity.g4).
        """
        P = SolidityParser

        if isinstance(clause_ctx, P.PostCommonContext):
            # commonClause로 위임
            return self._build_common_clause_dict(clause_ctx.commonClause())
        else:
            raise ValueError(f"Unknown post clause type: {type(clause_ctx)}")

    # Visit a parse tree produced by SolidityParser#debugValue.
    def visitDebugValue(self, ctx: SolidityParser.DebugValueContext):
        # debugValue can be: DebugIntInterval, DebugSymbolicAddress, DebugIntArray, DebugAddressArray, etc.
        if isinstance(ctx, SolidityParser.DebugIntIntervalContext):
            return self.visitDebugIntInterval(ctx)
        elif isinstance(ctx, SolidityParser.DebugSymbolicAddressContext):
            return self.visitDebugSymbolicAddress(ctx)
        elif isinstance(ctx, SolidityParser.DebugIntArrayContext):
            return self.visitDebugIntArray(ctx)
        elif isinstance(ctx, SolidityParser.DebugAddressArrayContext):
            return self.visitDebugAddressArray(ctx)
        else:
            # Fallback
            return self.visitChildren(ctx)

    def visitDebugIntInterval(self, ctx: SolidityParser.DebugIntIntervalContext):
        # '[' signedNumberLiteral ',' signedNumberLiteral ']'
        start_val = ctx.signedNumberLiteral(0).getText()
        end_val = ctx.signedNumberLiteral(1).getText()

        start_expr = Expression(literal=start_val, expr_type='int', context='DebugIntIntervalStart')
        end_expr = Expression(literal=end_val, expr_type='int', context='DebugIntIntervalEnd')

        return Expression(
            elements=[start_expr, end_expr],
            expr_type='interval',
            context='DebugIntIntervalContext'
        )

    # Visit a parse tree produced by SolidityParser#DebugIntArray.
    def visitDebugIntArray(self, ctx: SolidityParser.DebugIntArrayContext):
        # 'array' '[' ( signedNumberLiteral (',' signedNumberLiteral)* )? ']'
        if ctx.signedNumberLiteral():
            return [int(n.getText(), 0) for n in ctx.signedNumberLiteral()]
        else:
            return []  # array[] → empty list

    # Visit a parse tree produced by SolidityParser#DebugAddressArray.
    def visitDebugAddressArray(self, ctx: SolidityParser.DebugAddressArrayContext):
        # 'arrayAddress' '[' ( numberLiteral (',' numberLiteral)* )? ']'
        addr_mgr = self.contract_analyzer.addr_mgr
        if ctx.numberLiteral():
            ids = [int(n.getText(), 0) for n in ctx.numberLiteral()]
            return [addr_mgr.make_symbolic_address(nid) for nid in ids]
        else:
            return []  # arrayAddress[] → empty list

    # Visit a parse tree produced by SolidityParser#DebugSymbolicAddress.
    def visitDebugSymbolicAddress(self, ctx: SolidityParser.DebugSymbolicAddressContext):
        # 'symbolicAddress' numberLiteral
        address_value = ctx.numberLiteral().getText()
        return Expression(
            literal=address_value,
            expr_type='symbolic_address',
            context='DebugSymbolicAddressContext'
        )

    # Visit a parse tree produced by SolidityParser#DebugSymbolicBytes.
    def visitDebugSymbolicBytes(self, ctx: SolidityParser.DebugSymbolicBytesContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#DebugSymbolicString.
    def visitDebugSymbolicString(self, ctx: SolidityParser.DebugSymbolicStringContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#DebugBoolToken.
    def visitDebugBoolToken(self, ctx: SolidityParser.DebugBoolTokenContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#DebugEnumLiteral.
    def visitDebugEnumLiteral(self, ctx: SolidityParser.DebugEnumLiteralContext):
        return self.visitChildren(ctx)
    def visitVarRef(self, ctx):
        # NormalVarRefContext가 직접 전달될 수 있음
        if isinstance(ctx, SolidityParser.NormalVarRefContext):
            return self.visitNormalVarRef(ctx)
        elif isinstance(ctx, SolidityParser.LengthVarRefContext):
            return self.visitLengthVarRef(ctx)
        elif hasattr(ctx, 'normalVarRef') and ctx.normalVarRef():
            return self.visitNormalVarRef(ctx.normalVarRef())
        elif hasattr(ctx, 'lengthVarRef') and ctx.lengthVarRef():
            return self.visitLengthVarRef(ctx.lengthVarRef())
        return None

    def visitNormalVarRef(self, ctx: SolidityParser.NormalVarRefContext):
        # 첫 식별자
        cur = Expression(
            identifier=ctx.identifier().getText(),
            context="VarRefBase",
        )

        # subAccess*  (없으면 for-loop 건너뜀)
        for sa in ctx.subAccess():
            if isinstance(sa, SolidityParser.IntentMemberAccessContext):
                cur = Expression(
                    base=cur,
                    member=sa.identifier().getText(),
                    operator=".",
                    context="VarRefMemberAccess",
                )
            else:  # IntentIndexAccess
                idx = self.visitExpression(sa.expression())
                cur = Expression(
                    base=cur,
                    index=idx,
                    access="index_access",
                    context="VarRefIndexAccess",
                )
        return cur

    # Visit a parse tree produced by SolidityParser#IntentMemberAccess.
    def visitIntentMemberAccess(self, ctx: SolidityParser.IntentMemberAccessContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#IntentIndexAccess.
    def visitIntentIndexAccess(self, ctx: SolidityParser.IntentIndexAccessContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#intentValue.
    def visitIntentValue(self, ctx: SolidityParser.IntentValueContext):
        # intentValue : arithExpr ;
        return self.visit(ctx.arithExpr())

    # Visit a parse tree produced by SolidityParser#IntentShiftOp.
    def visitIntentShiftOp(self, ctx: SolidityParser.IntentShiftOpContext):
        # Binary shift: arithExpr (<<|>>|>>>) arithAdd
        left = self.visit(ctx.arithExpr())
        right = self.visit(ctx.arithAdd())
        operator = ctx.getChild(1).getText()  # <<, >>, or >>>
        return Expression(
            left=left,
            operator=operator,
            right=right,
            expr_type='int',
            context='IntentShiftContext'
        )

    # Visit a parse tree produced by SolidityParser#IntentShiftRoot.
    def visitIntentShiftRoot(self, ctx: SolidityParser.IntentShiftRootContext):
        return self.visit(ctx.arithAdd())

    # Visit a parse tree produced by SolidityParser#AddSub.
    def visitAddSub(self, ctx: SolidityParser.AddSubContext):
        # Binary addition/subtraction: arithAdd (+|-) arithTerm
        left = self.visit(ctx.arithAdd())
        right = self.visit(ctx.arithTerm())
        operator = ctx.getChild(1).getText()  # + or -
        return Expression(
            left=left,
            operator=operator,
            right=right,
            expr_type='int',
            context='AddSubContext'
        )

    # Visit a parse tree produced by SolidityParser#AddSubRoot.
    def visitAddSubRoot(self, ctx: SolidityParser.AddSubRootContext):
        # Recursively visit the arithTerm
        return self.visit(ctx.arithTerm())

    # Visit a parse tree produced by SolidityParser#MulDivModRoot.
    def visitMulDivModRoot(self, ctx: SolidityParser.MulDivModRootContext):
        # Recursively visit the arithExp
        return self.visit(ctx.arithExp())

    # Visit a parse tree produced by SolidityParser#MulDivMod.
    def visitMulDivMod(self, ctx: SolidityParser.MulDivModContext):
        # Binary multiplication/division/modulo: arithTerm (*|/|%) arithExp
        left = self.visit(ctx.arithTerm())
        right = self.visit(ctx.arithExp())
        operator = ctx.getChild(1).getText()  # *, /, or %
        return Expression(
            left=left,
            operator=operator,
            right=right,
            expr_type='int',
            context='MulDivModContext'
        )

    # Visit a parse tree produced by SolidityParser#IntentExponentiation.
    def visitIntentExponentiation(self, ctx: SolidityParser.IntentExponentiationContext):
        # Exponentiation: arithFactor ** arithExp  (right-assoc)
        left = self.visit(ctx.arithFactor())
        right = self.visit(ctx.arithExp())
        return Expression(
            left=left,
            operator='**',
            right=right,
            expr_type='int',
            context='IntentExponentiationContext'
        )

    # Visit a parse tree produced by SolidityParser#IntentExpRoot.
    def visitIntentExpRoot(self, ctx: SolidityParser.IntentExpRootContext):
        return self.visit(ctx.arithFactor())

    # Visit a parse tree produced by SolidityParser#NumLiteral.
    @staticmethod
    def _split_tuple_slots(ctx):
        """
        `'(' slot? (',' slot?)* ')'` 형태의 튜플류 rule context(tupleExpression,
        variableDeclarationTuple 등)를 콤마 경계로 순회해서, 슬롯 하나당 항목
        하나씩(빈 슬롯은 None) 있는 리스트를 만든다.

        ANTLR4 Python 런타임의 리스트 accessor(예: `ctx.expression()`,
        `ctx.variableDeclaration()`)는 옵셔널-in-loop 슬롯 중 매칭 안 된 것은
        None으로 채우지 않고 그냥 리스트에서 빠뜨린다(직접 테스트로 확인,
        `(, x)`에서 `ctx.expression()`이 길이 1 리스트를 반환함) — 그래서
        위치 정보(몇 번째 슬롯이 비었는지)가 사라진다. `elements[i]`/
        `enumerate(var_declarations)`처럼 위치 기반으로 튜플 원소를 소비하는
        다운스트림 코드에겐 이게 치명적이라(web3bugs_79_H_02의
        `(, , lpSupply) = router.addLiquidity(...)`, web3bugs_29_H_08의
        `(, bytes memory _output) = bento.staticcall(...)`에서 각각 발견),
        `ctx.children`을 직접 순회해서 콤마 경계로 슬롯을 재구성한다.
        """
        slots = []
        current = None
        for child in ctx.children:
            if isinstance(child, TerminalNodeImpl):
                text = child.getText()
                if text == '(':
                    continue
                if text == ',':
                    slots.append(current)
                    current = None
                    continue
                if text == ')':
                    slots.append(current)
                    continue
                # 이 두 문법에선 다른 터미널이 나올 일 없음
                continue
            else:
                current = child
        return slots

    def visitNumLiteral(self, ctx: SolidityParser.NumLiteralContext):
        # signedNumberLiteral
        literal_val = ctx.signedNumberLiteral().getText()
        # 값의 부호로 int/uint 판정 (GuardianVerificationEngine._evaluate_inline_interval의
        # `cls = IntegerInterval if lo < 0 else UnsignedIntegerInterval`와 동일 관례).
        # 예전엔 무조건 'int'로 고정해서, `(uint - uint) << 128`처럼 unsigned 피연산자와
        # 양의 정수 리터럴을 섞어 쓰는 shift가 Evaluation.py의 shift dispatch에서
        # "Shift operands must both be int/uint intervals" 예외로 죽었음
        # (web3bugs_35_H_12, `@Post ... (block.timestamp - lastObservation(Entry)) << 128 ...`).
        # 부호만 필요하므로 `int(x, 0)`으로 전체를 파싱하지 않음 — 과학적 표기
        # 리터럴(`1e18` 등, signedNumberLiteral 문법상 허용됨)은 `int(x, 0)`이
        # ValueError를 던져서 크래시함(web3bugs_79_H_02, `@During tokenAllocated
        # == wavaxReserve * 1e18 / floorPrice`에서 발견) — signedNumberLiteral은
        # `'-'? numberLiteral`이라 부호는 항상 맨 앞 '-' 문자 하나로 판정 가능.
        is_negative = literal_val.lstrip().startswith('-')
        return Expression(
            literal=literal_val,
            expr_type='int' if is_negative else 'uint',
            context='NumLiteralContext'
        )
    # Visit a parse tree produced by SolidityParser#InlineInterval.
    def visitInlineInterval(self, ctx: SolidityParser.InlineIntervalContext):
        """
        [a , b]  형태 인라인 구간 리터럴 → Expression(interval)
        """
        s = ctx.signedNumberLiteral(0).getText()
        e = ctx.signedNumberLiteral(1).getText()

        start = Expression(literal=s, expr_type="int", context="InlineIntStart")
        end = Expression(literal=e, expr_type="int", context="InlineIntEnd")

        return Expression(elements=[start, end],
                          expr_type="interval",
                          context="InlineInterval")

    # Visit a parse tree produced by SolidityParser#NumVarRef.
    def visitNumVarRef(self, ctx: SolidityParser.NumVarRefContext):
        # varRef
        return self.visitVarRef(ctx.varRef())

    # ── varRef(Entry) / varRef(Exit) / varRef(Before) / varRef(After) / varRef(Assign) ──
    # 안쪽 varRef 를 그대로 쓰되, 어떤 스냅샷 환경(sigma_entry/sigma_exit/sigma_before/
    # sigma_pt/sigma_assign)에서 평가해야 하는지를 context 태그로 감싼다. 실제 해석은
    # GuardianVerificationEngine._evaluate_snapshot_var_ref 가 담당.
    def visitVarRefAtEntry(self, ctx: SolidityParser.VarRefAtEntryContext):
        return Expression(elements=[self.visitVarRef(ctx.varRef())], context='VarRefAtEntry')

    def visitVarRefAtExit(self, ctx: SolidityParser.VarRefAtExitContext):
        return Expression(elements=[self.visitVarRef(ctx.varRef())], context='VarRefAtExit')

    def visitVarRefAtBefore(self, ctx: SolidityParser.VarRefAtBeforeContext):
        return Expression(elements=[self.visitVarRef(ctx.varRef())], context='VarRefAtBefore')

    def visitVarRefAtAfter(self, ctx: SolidityParser.VarRefAtAfterContext):
        return Expression(elements=[self.visitVarRef(ctx.varRef())], context='VarRefAtAfter')

    def visitVarRefAtAssign(self, ctx: SolidityParser.VarRefAtAssignContext):
        return Expression(elements=[self.visitVarRef(ctx.varRef())], context='VarRefAtAssign')

    # Visit a parse tree produced by SolidityParser#PercentOf.
    def visitPercentOf(self, ctx: SolidityParser.PercentOfContext):
        # intentValue relOp 'PercentOf' '(' intentValue ',' numberLiteral ')'
        lhs_expr = self.visit(ctx.intentValue(0))
        rhs_expr = self.visit(ctx.intentValue(1))
        percentage_val = ctx.numberLiteral().getText()

        func_name = Expression(identifier='PercentOf', context='PercentOfFuncContext')
        percentage_expr = Expression(literal=percentage_val, expr_type='int', context='PercentOfFuncContext')

        return Expression(
            function=func_name,
            arguments=[rhs_expr, percentage_expr],
            lhs=lhs_expr,
            operator='()',
            context='PercentOfFuncContext'
        )

    # Visit a parse tree produced by SolidityParser#Ceil.
    def visitCeil(self, ctx: SolidityParser.CeilContext):
        # intentValue relOp 'ceil' '(' intentValue ',' numberLiteral ')'
        lhs_expr = self.visit(ctx.intentValue(0))
        rhs_expr = self.visit(ctx.intentValue(1))
        precision_val = ctx.numberLiteral().getText()

        func_name = Expression(identifier='ceil', context='CeilFuncContext')
        precision_expr = Expression(literal=precision_val, expr_type='int', context='CeilFuncContext')

        return Expression(
            function=func_name,
            arguments=[rhs_expr, precision_expr],
            lhs=lhs_expr,
            operator='()',
            context='CeilFuncContext'
        )

    # Visit a parse tree produced by SolidityParser#Floor.
    def visitFloor(self, ctx: SolidityParser.FloorContext):
        # intentValue relOp 'floor' '(' intentValue ',' numberLiteral ')'
        lhs_expr = self.visit(ctx.intentValue(0))
        rhs_expr = self.visit(ctx.intentValue(1))
        precision_val = ctx.numberLiteral().getText()

        func_name = Expression(identifier='floor', context='FloorFuncContext')
        precision_expr = Expression(literal=precision_val, expr_type='int', context='FloorFuncContext')

        return Expression(
            function=func_name,
            arguments=[rhs_expr, precision_expr],
            lhs=lhs_expr,
            operator='()',
            context='FloorFuncContext'
        )

    # Visit a parse tree produced by SolidityParser#Parenthesized.
    def visitParenthesized(self, ctx: SolidityParser.ParenthesizedContext):
        # '(' arithExpr ')'
        return self.visit(ctx.arithExpr())

    # Visit a parse tree produced by SolidityParser#AddrLiteralExpr.
    def visitAddrLiteralExpr(self, ctx):
        # 'address' numberLiteral
        address_val = ctx.numberLiteral().getText()
        return Expression(
            literal=address_val,
            expr_type='address',
            context='AddrLiteralExprContext'
        )

    # Visit a parse tree produced by SolidityParser#SymAddrLiteralExpr.
    def visitSymAddrLiteralExpr(self, ctx):
        # 'symbolicAddress' numberLiteral
        address_val = ctx.numberLiteral().getText()
        return Expression(
            literal=address_val,
            expr_type='symbolic_address',
            context='SymAddrLiteralExprContext'
        )

    # Visit a parse tree produced by SolidityParser#AddrVarExpr.
    def visitAddrVarExpr(self, ctx):
        # varRef
        return self.visitVarRef(ctx.varRef())

    # Visit a parse tree produced by SolidityParser#BoolLiteralExpr.
    def visitBoolLiteralExpr(self, ctx):
        # booleanLiteral
        bool_val = ctx.booleanLiteral().getText()
        return Expression(
            literal=bool_val,
            expr_type='bool',
            context='BoolLiteralExprContext'
        )

    # Visit a parse tree produced by SolidityParser#BoolVarExpr.
    def visitBoolVarExpr(self, ctx):
        # varRef
        return self.visitVarRef(ctx.varRef())

    # Visit a parse tree produced by SolidityParser#signedNumberLiteral.
    def visitSignedNumberLiteral(self, ctx: SolidityParser.SignedNumberLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#logicOp.
    def visitLogicOp(self, ctx: SolidityParser.LogicOpContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#relOp.
    def visitRelOp(self, ctx: SolidityParser.RelOpContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveSimpleStatement.
    def visitInteractiveSimpleStatement(self, ctx:SolidityParser.InteractiveSimpleStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveVariableDeclarationStatement.
    def visitInteractiveVariableDeclarationStatement(self, ctx:SolidityParser.InteractiveVariableDeclarationStatementContext):
        # 튜플 변수 선언인지 확인: (uint256 a, uint256 b) = func();
        if ctx.variableDeclarationTuple():
            tuple_ctx = ctx.variableDeclarationTuple()
            init_expr = self.visitExpression(ctx.expression()) if ctx.expression() else None

            # 튜플 내 변수 정보 수집: [(type_obj, var_name), ...]
            var_declarations = []
            # ★ ANTLR 리스트 accessor 는 빈 슬롯을 None 으로 채우지 않고 빠뜨려 위치 정보가
            # 깨짐 — _split_tuple_slots 로 콤마 경계 기준 재구성(아래 None 분기가 예전엔
            # 이 이유로 죽은 코드였음).
            for var_decl in self._split_tuple_slots(tuple_ctx):
                if var_decl:
                    type_ctx = var_decl.typeName()
                    var_name = var_decl.identifier().getText()
                    type_obj = SolType()
                    type_obj = self.visitTypeName(type_ctx, type_obj)
                    var_declarations.append((type_obj, var_name))  # (type_obj, var_name) 순서
                else:
                    var_declarations.append(None)  # (,uint256 x) 같은 경우

            # 튜플 전체를 한번에 처리
            self.contract_analyzer.process_variable_declaration_tuple(
                var_declarations=var_declarations,
                init_expr=init_expr
            )
            return

        # 단일 변수 선언: uint256 x = value;
        # 1. 변수 선언 정보 가져오기
        type_ctx = ctx.variableDeclaration().typeName()
        var_name = ctx.variableDeclaration().identifier().getText()

        # dataLocation?  (memory / storage / calldata)
        data_loc = None
        #if ctx.dataLocation():
        #    data_loc = ctx.dataLocation().getText()  # 'storage' 등

        # 2. 초기화 값이 있는 경우 처리
        init_expr = None
        if ctx.expression():
            init_expr = self.visitExpression(ctx.expression())

        # 3. 변수 타입 정보 분석 및 적절한 Variables 객체 생성
        type_obj = SolType()
        type_obj = self.visitTypeName(type_ctx, type_obj)  # 타입 정보 분석

        # 5. ContractAnalyzer로 Variables 객체 및 lineComment 전달
        self.contract_analyzer.process_variable_declaration(
            type_obj=type_obj,
            var_name=var_name,
            init_expr=init_expr
        )

    # Visit a parse tree produced by SolidityParser#interactiveExpressionStatement.
    def visitInteractiveExpressionStatement(self, ctx:SolidityParser.InteractiveExpressionStatementContext):
        # 1. 표현식 방문
        expr_ctx = ctx.expression()
        expr = self.visitExpression(expr_ctx)

        # Handle assignment expressions
        if isinstance(expr_ctx, SolidityParser.AssignmentContext):
            self.contract_analyzer.process_assignment_expression(expr)
        elif isinstance(expr_ctx, SolidityParser.UnaryPrefixOpContext):
            self.contract_analyzer.process_unary_prefix_operation(expr)
        elif isinstance(expr_ctx, SolidityParser.UnarySuffixOpContext):
            self.contract_analyzer.process_unary_suffix_operation(expr)
        elif isinstance(expr_ctx, SolidityParser.FunctionCallContext):
            self.contract_analyzer.process_function_call(expr)
        elif isinstance(expr_ctx, SolidityParser.PayableFunctionCallContext):
            self.contract_analyzer.process_payable_function_call(expr)
        elif isinstance(expr_ctx, SolidityParser.FunctionCallOptionsContext):
            self.contract_analyzer.process_function_call_options(expr)
        elif isinstance(expr_ctx, SolidityParser.IdentifierExpContext) :
            self.contract_analyzer.process_identifier_expression(expr)
        else:
            # (2026-10-23) 값만 있는 식 문장(비교·멤버 접근·산술 등). 예전엔 미지원 예외였다(w71 `insurances[_id].status == false;`).
            self.contract_analyzer.process_expression_statement(expr)

    # Visit a parse tree produced by SolidityParser#interactiveStateVariableElement.
    def visitInteractiveStateVariableElement(self, ctx:SolidityParser.InteractiveStateVariableElementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveEnumDefinition.
    def visitInteractiveEnumDefinition(self, ctx:SolidityParser.InteractiveEnumDefinitionContext):
        enum_name = ctx.identifier().getText()
        # ★ 바로 아래 visitInteractiveStructDefinition 과 대칭 (F28).
        #   Solidity 0.8+ 는 contract/interface 밖 파일 최상위 enum 을 허용한다.
        if self.contract_analyzer.current_target_contract is None:
            # file-level enum → SA에서 처리
            self.contract_analyzer.sa.process_file_level_enum_definition(enum_name)
        else:
            self.contract_analyzer.process_enum_definition(enum_name)
        return

    # Visit a parse tree produced by SolidityParser#interactiveStructDefinition.
    def visitInteractiveStructDefinition(self, ctx:SolidityParser.InteractiveStructDefinitionContext):
        struct_name = ctx.identifier().getText()
        if self.contract_analyzer.current_target_contract is None:
            # file-level struct → SA에서 처리
            self.contract_analyzer.sa.process_file_level_struct_definition(struct_name)
        else:
            self.contract_analyzer.process_struct_definition(struct_name)

    # Visit a parse tree produced by SolidityParser#interactiveEnumItems.
    def visitInteractiveEnumItems(self, ctx:SolidityParser.InteractiveEnumItemsContext):
        enum_items = [identifier.getText() for identifier in ctx.identifier()]

        # ★ visitStructMember 의 fileLevelStructMember 분기와 대칭 (F28)
        if self.contract_analyzer.current_context_type == "fileLevelEnumMember":
            self.contract_analyzer.sa.process_file_level_enum_item(enum_items)
        else:
            self.contract_analyzer.process_enum_item(enum_items)

    # Visit a parse tree produced by SolidityParser#interactiveFunctionElement.
    def visitInteractiveFunctionElement(self, ctx:SolidityParser.InteractiveFunctionElementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveBlockItem.
    def visitInteractiveBlockItem(self, ctx:SolidityParser.InteractiveBlockItemContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#dataLocation.
    def visitDataLocation(self, ctx:SolidityParser.DataLocationContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#stateMutability.
    def visitStateMutability(self, ctx:SolidityParser.StateMutabilityContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#block.
    def visitBlock(self, ctx:SolidityParser.BlockContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#uncheckedBlock.
    def visitUncheckedBlock(self, ctx:SolidityParser.UncheckedBlockContext):
        return self.contract_analyzer.process_unchecked_indicator()

    # Visit a parse tree produced by SolidityParser#statement.
    def visitStatement(self, ctx:SolidityParser.StatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#expressionStatement.
    def visitExpressionStatement(self, ctx:SolidityParser.ExpressionStatementContext):
        # 1. 표현식 방문
        expr_ctx = ctx.expression()
        return self.visitExpression(expr_ctx)

    # Visit a parse tree produced by SolidityParser#ifStatement.
    def visitIfStatement(self, ctx:SolidityParser.IfStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#tryStatement.
    def visitTryStatement(self, ctx:SolidityParser.TryStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#catchClause.
    def visitCatchClause(self, ctx:SolidityParser.CatchClauseContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#whileStatement.
    def visitWhileStatement(self, ctx:SolidityParser.WhileStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#VDContext.
    def visitVDContext(self, ctx:SolidityParser.VDContextContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#EContext.
    def visitEContext(self, ctx:SolidityParser.EContextContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#forStatement.
    def visitForStatement(self, ctx:SolidityParser.ForStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#inlineArrayExpression.
    def visitInlineArrayExpression(self, ctx:SolidityParser.InlineArrayExpressionContext):
        elements = []

        # 배열의 각 요소들을 순회하며 Expression으로 방문
        for expr_ctx in ctx.expression():
            element_expr = self.visitExpression(expr_ctx)  # 각 요소에 대해 Expression 객체 생성
            elements.append(element_expr)  # 리스트에 추가

        # Expression 객체로 배열을 표현
        array_expr = Expression(
            elements=elements,  # 배열의 요소들 저장
            expr_type='array',  # 표현식 타입을 배열로 지정
            context='InlineArrayExpressionContext'
        )

        return array_expr

    # ═══════════════════════════════════════════════════════════
    #  Assembly / Yul 처리 — 별도 Yul.g4 파서 + EnhancedYulVisitor 사용
    # ═══════════════════════════════════════════════════════════

    def visitAssemblyStatement(self, ctx: SolidityParser.AssemblyStatementContext):
        """Assembly 블록: 내부 Yul 코드를 별도 Yul 파서로 재파싱하여 처리"""
        from antlr4 import InputStream, CommonTokenStream
        from Parser.YulLexer import YulLexer
        from Parser.YulParser import YulParser as YP
        from Analyzer.EnhancedYulVisitor import EnhancedYulVisitor

        # assembly { ... } 에서 { } 내부 텍스트 추출
        full_text = ctx.getText()
        # 'assembly' + optional flags + '{' ... '}'
        brace_start = full_text.index('{')
        brace_end = full_text.rindex('}')
        yul_body = full_text[brace_start + 1 : brace_end]

        if not yul_body.strip():
            return None

        # Yul 파서로 파싱
        inp = InputStream(yul_body)
        lexer = YulLexer(inp)
        tokens = CommonTokenStream(lexer)
        parser = YP(tokens)
        parser.removeErrorListeners()  # Yul 파싱 에러 억제

        tree = parser.yulUnit()

        # EnhancedYulVisitor로 처리
        yul_visitor = EnhancedYulVisitor(self.contract_analyzer)
        yul_visitor.visit(tree)
        return None

    def visitAssemblyFlags(self, ctx: SolidityParser.AssemblyFlagsContext):
        return None

    def visitAssemblyFlagString(self, ctx: SolidityParser.AssemblyFlagStringContext):
        return None

    # Solidity parser의 Yul visitor들은 사용하지 않음 (별도 Yul.g4 사용)
    def visitYulStatement(self, ctx): return None
    def visitYulBlock(self, ctx): return None
    def visitYulVariableDeclaration(self, ctx): return None
    def visitYulAssignment(self, ctx): return None
    def visitYulIfStatement(self, ctx): return None
    def visitYulForStatement(self, ctx): return None
    def visitYulSwitchStatement(self, ctx): return None
    def visitYulFunctionDefinition(self, ctx): return None
    def visitYulPath(self, ctx): return None
    def visitYulFunctionCall(self, ctx): return None
    def visitYulBoolean(self, ctx): return None
    def visitYulLiteral(self, ctx): return None
    def visitYulExpression(self, ctx): return None

    # Visit a parse tree produced by SolidityParser#doWhileStatement.
    def visitDoWhileStatement(self, ctx:SolidityParser.DoWhileStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#continueStatement.
    def visitContinueStatement(self, ctx:SolidityParser.ContinueStatementContext):
        return self.contract_analyzer.process_continue_statement()

    # Visit a parse tree produced by SolidityParser#breakStatement.
    def visitBreakStatement(self, ctx:SolidityParser.BreakStatementContext):
        return self.contract_analyzer.process_break_statement()

    # Visit a parse tree produced by SolidityParser#returnStatement.
    def visitReturnStatement(self, ctx:SolidityParser.ReturnStatementContext):
        # 1. 반환되는 expression 처리
        if ctx.expression():
            return_expr = self.visitExpression(ctx.expression())
        else:
            return_expr = None

        # 2. ContractAnalyzer에 반환 표현식 전달
        self.contract_analyzer.process_return_statement(return_expr)

    # Visit a parse tree produced by SolidityParser#emitStatement.
    def visitEmitStatement(self, ctx:SolidityParser.EmitStatementContext):
        """
        emit Transfer(msg.sender, recipient, amount); 형태의 emit 문 처리
        문법: 'emit' expression callArgumentList ';'
        """
        # 1. event 이름 추출 (expression에서)
        event_name = None
        arguments = []

        if ctx.expression():
            event_name = ctx.expression().getText()

        # 2. callArgumentList에서 인자 추출
        if ctx.callArgumentList():
            arguments = self.visitCallArgumentList(ctx.callArgumentList())

        # 3. ContractAnalyzer에 emit 문 등록 (상태 변경 없으므로 skip)
        if event_name:
            self.contract_analyzer.process_emit_statement(event_name, arguments)

        return None

    # Visit a parse tree produced by SolidityParser#revertStatement.
    def visitRevertStatement(self, ctx:SolidityParser.RevertStatementContext):
        # 1. identifier와 expression 둘 중 하나를 처리 (문법 변경으로 stringLiteral -> expression)
        revert_identifier = None
        expression_arg = None

        if ctx.identifier():
            revert_identifier = self.visitIdentifier(ctx.identifier())
        elif ctx.expression():
            expression_arg = self.visitExpression(ctx.expression())

        # 2. callArgumentList가 존재하는지 여부 확인 및 처리
        call_argument_list = []
        if ctx.callArgumentList():
            call_argument_list = self.visitCallArgumentList(ctx.callArgumentList())

        # 3. ContractAnalyzer의 process_revert_statement 메소드 호출
        self.contract_analyzer.process_revert_statement(revert_identifier, expression_arg, call_argument_list)

    # Visit a parse tree produced by SolidityParser#requireStatement.
    def visitRequireStatement(self, ctx:SolidityParser.RequireStatementContext):
        # 1. 'require'의 조건식(첫 번째 expression)을 방문하여 추출
        condition_expr = self.visitExpression(ctx.expression(0))

        # 2. 에러 메시지(두 번째 expression) 처리 - 선택적 (문법 변경으로 stringLiteral -> expression)
        error_message_expr = None
        if len(ctx.expression()) > 1:
            error_message_expr = self.visitExpression(ctx.expression(1))

        # 3. ContractAnalyzer에서 process_require_statement 호출
        self.contract_analyzer.process_require_statement(condition_expr, error_message_expr)

    # Visit a parse tree produced by SolidityParser#assertStatement.
    def visitAssertStatement(self, ctx:SolidityParser.AssertStatementContext):
        # 1. expression을 방문해서 조건식을 가져옴
        condition_expr = self.visitExpression(ctx.expression())

        # 2. ContractAnalyzer에서 process_assert_statement 호출
        self.contract_analyzer.process_assert_statement(condition_expr)

    # Visit a parse tree produced by SolidityParser#variableDeclarationStatement.
    def visitVariableDeclarationStatement(self, ctx:SolidityParser.VariableDeclarationStatementContext):
        # 1. 변수 선언 정보 가져오기
        type_ctx = ctx.variableDeclaration().typeName()
        var_name = ctx.variableDeclaration().identifier().getText()

        # 2. 초기화 값이 있는 경우 처리
        init_expr = None
        if ctx.expression():
            init_expr = self.visitExpression(ctx.expression())

        # 3. 변수 타입 정보 분석 및 적절한 Variables 객체 생성
        type_obj = SolType()
        type_obj = self.visitTypeName(type_ctx, type_obj)  # 타입 정보 분석

        return type_obj, var_name, init_expr

    # Visit a parse tree produced by SolidityParser#interactiveStatement.
    def visitInteractiveStatement(self, ctx:SolidityParser.InteractiveStatementContext):
        if ctx.interactiveSimpleStatement():
            return self.visitInteractiveSimpleStatement(ctx.interactiveSimpleStatement())
        elif ctx.interactiveIfStatement():
            return self.visitInteractiveIfStatement(ctx.interactiveIfStatement())
        elif ctx.interactiveForStatement():
            return self.visitInteractiveForStatement(ctx.interactiveForStatement())
        elif ctx.interactiveWhileStatement():
            return self.visitInteractiveWhileStatement(ctx.interactiveWhileStatement())
        elif ctx.interactiveDoWhileDoStatement():
            return self.visitInteractiveDoWhileDoStatement(ctx.interactiveDoWhileDoStatement())
        elif ctx.continueStatement():
            return self.visitContinueStatement(ctx.continueStatement())
        elif ctx.breakStatement():
            return self.visitBreakStatement(ctx.breakStatement())
        elif ctx.interactiveTryStatement():
            return self.visitInteractiveTryStatement(ctx.interactiveTryStatement())
        elif ctx.returnStatement():
            return self.visitReturnStatement(ctx.returnStatement())
        elif ctx.emitStatement():
            return self.visitEmitStatement(ctx.emitStatement())
        elif ctx.revertStatement():
            return self.visitRevertStatement(ctx.revertStatement())
        elif ctx.assemblyStatement():
            # assembly 블록 처리 - 변수들을 Top으로 설정
            return self.visitAssemblyStatement(ctx.assemblyStatement())
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveIfStatement.
    def visitInteractiveIfStatement(self, ctx:SolidityParser.InteractiveIfStatementContext):
        # 1. 조건식 표현식 방문
        condition_expr = self.visitExpression(ctx.expression())

        # 부모 컨텍스트가 else문에서 온 경우인지 확인
        if ctx.parentCtx and isinstance(ctx.parentCtx, SolidityParser.InteractiveElseStatementContext):
            # else if 문 처리
            self.contract_analyzer.process_else_if_statement(condition_expr)
        else:
            # if 문 처리
            self.contract_analyzer.process_if_statement(condition_expr)

    # Visit a parse tree produced by SolidityParser#interactiveElseStatement.
    def visitInteractiveElseStatement(self, ctx:SolidityParser.InteractiveElseStatementContext):
        # 'else if' 블록인지 아니면 'else' 블록인지를 판단
        if ctx.interactiveIfStatement():
            # 'else if' 문이 존재하는 경우
            return self.visitInteractiveIfStatement(ctx.interactiveIfStatement())
        else:
            # 'else' 블록을 처리
            self.contract_analyzer.process_else_statement()

    # Visit a parse tree produced by SolidityParser#interactiveForStatement.
    def visitInteractiveForStatement(
            self, ctx: SolidityParser.InteractiveForStatementContext):

        # init ----------------------------------------------------------------
        init_stmt = {}
        init_ctx = ctx.simpleStatement()
        if init_ctx:  # simpleStatement 존재
            if isinstance(init_ctx, SolidityParser.VDContextContext):
                t, n, v = self.visitVDContext(init_ctx)
                init_stmt = {'context': 'VariableDeclaration',
                             'initVarType': t, 'initVarName': n, 'initValExpr': v}
            else:  # expressionStatement
                # ★ F31. init_ctx 는 `simpleStatement` 의 라벨 대안 `# EContext` 다.
                #   ANTLR 은 **각 대안의 직접 자식만** 접근자로 만들므로
                #   `EContextContext` 에는 `.expressionStatement()` 하나만 있고
                #   `.expression()` 은 없다(문법상 한 단계 더 아래다).
                #   예전 코드가 `init_ctx.expression()` 을 불러
                #   `for (y >>= 1; y > 0; y >>= 1)` 처럼 **init 이 식인 for 문**이
                #   AttributeError 로 통째로 실패했다(코퍼스 34건).
                #   ※ 아래 cond_expr 쪽은 `ctx.expressionStatement()` 가 곧
                #     ExpressionStatementContext 라서 바로 `.expression()` 이 유효하다.
                init_stmt = {'context': 'Expression',
                             'initExpr': self.visitExpressionStatement(init_ctx.expressionStatement())}

        # condition --- expressionStatement 안의 expression ----------------------
        cond_expr = None
        expr_stmt = ctx.expressionStatement()
        if expr_stmt:
            cond_expr = self.visitExpression(expr_stmt.expression())

        # increment --- for문의 expression? (update) ----------------------------
        inc_expr = None
        update_expr_ctx = ctx.expression()
        if update_expr_ctx is not None:
            inc_expr = self.visitExpression(update_expr_ctx)

        # ContractAnalyzer 로 전달 -------------------------------------------
        self.contract_analyzer.process_for_statement(
            initial_statement=init_stmt,
            condition_expr=cond_expr,
            increment_expr=inc_expr
        )

    # Visit a parse tree produced by SolidityParser#interactiveWhileStatement.
    def visitInteractiveWhileStatement(self, ctx:SolidityParser.InteractiveWhileStatementContext):
        # 1. 조건식 표현식 방문
        condition_expr = self.visitExpression(ctx.expression())

        # 2. ContractAnalyzer의 process_while_statement 호출
        self.contract_analyzer.process_while_statement(condition_expr)

    # Visit a parse tree produced by SolidityParser#interactiveDoWhileDoStatement.
    def visitInteractiveDoWhileDoStatement(self, ctx:SolidityParser.InteractiveDoWhileDoStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveDoWhileWhileStatement.
    def visitInteractiveDoWhileWhileStatement(self, ctx:SolidityParser.InteractiveDoWhileWhileStatementContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#interactiveTryStatement.
    def visitInteractiveTryStatement(self, ctx:SolidityParser.InteractiveTryStatementContext):
        # (2026-10-23) 예전엔 visitChildren 뿐이라 try 가 CFG 에 없었다(본문 줄은 try 밖 문장처럼 붙었다).
        #   `ContractAnalyzer.process_try_statement` → try_cond(성공/실패 두 분기 모두 진행) + returns 지역(⊤).
        function_expr = self.visitExpression(ctx.expression())
        returns = self.visitParameterList(ctx.parameterList()) if ctx.parameterList() else []
        self.contract_analyzer.process_try_statement(function_expr, returns)

    # Visit a parse tree produced by SolidityParser#interactiveCatchClause.
    def visitInteractiveCatchClause(self, ctx:SolidityParser.InteractiveCatchClauseContext):
        # (2026-10-23) 예전엔 존재하지 않는 `add_control_flow_node` 를 불러 빌드가 실패했다(ERC721·ERC1155Upgradeable).
        ident = ctx.identifier().getText() if ctx.identifier() else None
        params = self.visitParameterList(ctx.parameterList()) if ctx.parameterList() else []
        self.contract_analyzer.process_catch_clause(ident, params)

    # Visit a parse tree produced by SolidityParser#elementaryTypeName.
    def visitElementaryTypeName(self, ctx:SolidityParser.ElementaryTypeNameContext):
        return self.visitChildren(ctx)

    def visitExpression(self, ctx):
        # IndexAccess (배열 인덱스 접근)
        if isinstance(ctx, SolidityParser.IndexAccessContext):
            return self.visitIndexAccess(ctx)

        # IndexRangeAccess (배열 범위 접근)
        elif isinstance(ctx, SolidityParser.IndexRangeAccessContext):
            return self.visitIndexRangeAccess(ctx)

        # MemberAccess (객체 멤버 접근)
        elif isinstance(ctx, SolidityParser.MemberAccessContext):
            return self.visitMemberAccess(ctx)

        # FunctionCallOptions (함수 호출 옵션)
        elif isinstance(ctx, SolidityParser.FunctionCallOptionsContext):
            return self.visitFunctionCallOptions(ctx)

        # FunctionCall (함수 호출)
        elif isinstance(ctx, SolidityParser.FunctionCallContext):
            return self.visitFunctionCall(ctx)

        # PayableFunctionCall (Payable 함수 호출)
        elif isinstance(ctx, SolidityParser.PayableFunctionCallContext):
            return self.visitPayableFunctionCall(ctx)

        # TypeConversion (타입 변환)
        elif isinstance(ctx, SolidityParser.TypeConversionContext):
            return self.visitTypeConversion(ctx)

        elif isinstance(ctx, SolidityParser.MetaTypeContext):
            return self.visitMetaType(ctx)

        # UnaryPrefixOp (단항 연산자 - 전위)
        elif isinstance(ctx, SolidityParser.UnaryPrefixOpContext):
            return self.visitUnaryPrefixOp(ctx)

        # UnarySuffixOp (단항 연산자 - 후위)
        elif isinstance(ctx, SolidityParser.UnarySuffixOpContext):
            return self.visitUnarySuffixOp(ctx)

        # Exponentiation (지수 연산)
        elif isinstance(ctx, SolidityParser.ExponentiationContext):
            return self.visitExponentiation(ctx)

        # MultiplicativeOp (곱셈/나눗셈/나머지 연산)
        elif isinstance(ctx, SolidityParser.MultiplicativeOpContext):
            return self.visitMultiplicativeOp(ctx)

        # AdditiveOp (덧셈/뺄셈 연산)
        elif isinstance(ctx, SolidityParser.AdditiveOpContext):
            return self.visitAdditiveOp(ctx)

        # ShiftOp (비트 시프트 연산)
        elif isinstance(ctx, SolidityParser.ShiftOpContext):
            return self.visitShiftOp(ctx)

        # BitAndOp (비트 AND 연산)
        elif isinstance(ctx, SolidityParser.BitAndOpContext):
            return self.visitBitAndOp(ctx)

        # BitXorOp (비트 XOR 연산)
        elif isinstance(ctx, SolidityParser.BitXorOpContext):
            return self.visitBitXorOp(ctx)

        # BitOrOp (비트 OR 연산)
        elif isinstance(ctx, SolidityParser.BitOrOpContext):
            return self.visitBitOrOp(ctx)

        # RelationalOp (관계 연산자)
        elif isinstance(ctx, SolidityParser.RelationalOpContext):
            return self.visitRelationalOp(ctx)

        # EqualityOp (동등성 연산자)
        elif isinstance(ctx, SolidityParser.EqualityOpContext):
            return self.visitEqualityOp(ctx)

        # AndOperation (논리 AND 연산자)
        elif isinstance(ctx, SolidityParser.AndOperationContext):
            return self.visitAndOperation(ctx)

        # OrOperation (논리 OR 연산자)
        elif isinstance(ctx, SolidityParser.OrOperationContext):
            return self.visitOrOperation(ctx)

        # ConditionalExp (삼항 연산자)
        elif isinstance(ctx, SolidityParser.ConditionalExpContext):
            return self.visitConditionalExp(ctx)

        # Assignment (할당 연산자)
        elif isinstance(ctx, SolidityParser.AssignmentContext):
            return self.visitAssignment(ctx)

        # NewExp (new 연산자)
        elif isinstance(ctx, SolidityParser.NewExpContext):
            return self.visitNewExp(ctx)

        # TupleExp (튜플)
        elif isinstance(ctx, SolidityParser.TupleExpContext):
            return self.visitTupleExp(ctx)

        # InlineArrayExp (배열 리터럴)
        elif isinstance(ctx, SolidityParser.InlineArrayExpContext):
            return self.visitInlineArrayExp(ctx)

        # IdentifierExp (식별자)
        elif isinstance(ctx, SolidityParser.IdentifierExpContext):
            return self.visitIdentifierExp(ctx)

        # LiteralExp (리터럴)
        elif isinstance(ctx, SolidityParser.LiteralExpContext):
            return self.visitLiteralExp(ctx)

        # LiteralSubDenomination (리터럴 서브 단위)
        elif isinstance(ctx, SolidityParser.LiteralSubDenominationContext):
            return self.visitLiteralSubDenomination(ctx)

        # TypeNameExp (타입 이름)
        elif isinstance(ctx, SolidityParser.TypeNameExpContext):
            return self.visitTypeNameExp(ctx)

        else:
            raise NotImplementedError(f"Unhandled expression context: {type(ctx).__name__}")

    def visitIndexAccess(self, ctx):
        # 1. 배열 또는 매핑 표현식 방문
        base_expr = self.visitExpression(ctx.expression(0))

        # 2. 인덱스 표현식 방문 (optional)
        if ctx.expression(1):
            index_expr = self.visitExpression(ctx.expression(1))
        else:
            index_expr = None  # 인덱스가 없을 수도 있습니다 (예: array[])

        # 3. Expression 객체 생성
        result_expr = Expression(
            base=base_expr,
            index=index_expr,
            access='index_access',
            context='IndexAccessContext'
        )

        return result_expr

    def visitIndexRangeAccess(self, ctx):
        # 1. 베이스 표현식 방문 (예: array)
        base_expr = self.visitExpression(ctx.expression(0))

        # 2. 시작 인덱스 방문 (선택적)
        start_expr = None
        end_expr = None

        # 자식 노드의 개수
        child_count = ctx.getChildCount()

        # 구조 파악:
        # - ctx.getChild(0): base expression (array)
        # - ctx.getChild(1): '['
        # - 이후의 자식들은 다음과 같은 패턴을 가짐:
        #   - 만약 시작 인덱스가 존재하면 ctx.expression(1)이 있음
        #   - ':' 토큰은 반드시 존재함
        #   - 끝 인덱스가 존재하면 ctx.expression(2)이 있음
        #   - ']' 토큰은 마지막에 위치함

        # 3. 시작 인덱스와 끝 인덱스의 위치 파악
        # 표현식의 개수를 확인합니다.
        expression_count = len(ctx.expression())

        if expression_count == 1:
            # 시작 인덱스와 끝 인덱스가 모두 없는 경우 (예: array[:])
            # 이 경우는 슬라이스의 모든 요소를 선택하는 것을 의미합니다.
            start_expr = None
            end_expr = None
        elif expression_count == 2:
            # 시작 인덱스나 끝 인덱스 중 하나만 존재하는 경우
            # ':'의 위치를 찾아서 구분합니다.
            colon_index = None
            for i in range(child_count):
                if ctx.getChild(i).getText() == ':':
                    colon_index = i
                    break

            if colon_index is not None:
                # ':' 앞의 표현식을 확인하여 시작 인덱스인지 끝 인덱스인지 결정
                if ctx.getChild(colon_index - 1) in ctx.expression():
                    # ':' 앞에 표현식이 있으면 시작 인덱스
                    start_expr = self.visitExpression(ctx.expression(1))
                    end_expr = None
                else:
                    # ':' 앞에 표현식이 없으면 시작 인덱스 없음
                    start_expr = None
                    end_expr = self.visitExpression(ctx.expression(1))
            else:
                # ':'가 없으면 잘못된 구조이므로 예외 처리
                raise SyntaxError("Invalid index range access syntax.")
        elif expression_count == 3:
            # 시작 인덱스와 끝 인덱스가 모두 존재하는 경우
            start_expr = self.visitExpression(ctx.expression(1))
            end_expr = self.visitExpression(ctx.expression(2))
        else:
            # 예상치 못한 경우 예외 처리
            raise SyntaxError("Invalid number of expressions in index range access.")

        # 4. Expression 객체 생성
        result_expr = Expression(
            base=base_expr,
            start_index=start_expr,
            end_index=end_expr,
            operator='[:]',
            context='IndexRangeAccessContext'
        )

        return result_expr

    def visitMemberAccess(self, ctx):
        # 1. 베이스 표현식 방문
        base_expr = self.visitExpression(ctx.expression())

        # 2. 멤버 이름 추출
        if ctx.identifier():
            member_name = ctx.identifier().getText()
        else:
            # 'address' 키워드인 경우
            member_name = ctx.getChild(2).getText()

        # 3. Expression 객체 생성
        result_expr = Expression(
            base=base_expr,
            member=member_name,
            operator='.',
            context='MemberAccessContext'
        )

        return result_expr

    def visitMetaType(self, ctx: SolidityParser.MetaTypeContext):
        """
        grammar:
            MetaType : 'type' '(' typeName ')'   (#에 해당)
        반환값은 이후의 MemberAccess(.max / .min 등)를 처리하기 위해
        base-expression 역할만 하면 되므로 ‘identifier’ 하나만 넣어둔다.
        """
        # ① 안쪽 typeName 을 소스 그대로 추출
        type_name_txt = ctx.typeName().getText()  # 예: 'uint256'

        # ② Expression 생성
        #    identifier = 'type(uint256)'  로 두고
        #    context    = 'MetaTypeContext' 로 구분만 해둔다.
        expr = Expression(
            identifier=f"type({type_name_txt})",
            context="MetaTypeContext"
        )
        expr.typeName = type_name_txt  # evaluator가 type(T).max/min 처리 시 사용
        return expr

    def visitFunctionCallOptions(self, ctx):
        # 1. 베이스 표현식 방문
        base_expr = self.visitExpression(ctx.expression(0))

        # 2. 옵션 매개변수 처리
        options = {}

        # 옵션 매개변수가 존재하는지 확인
        # 중괄호 안에 있는 자식 노드들을 순회하여 옵션들을 추출합니다.
        # 구조:
        # - '{' 토큰은 ctx.getChild(1)
        # - 옵션 매개변수들은 그 이후의 자식 노드들에 위치
        # - '}' 토큰은 마지막 자식 노드

        child_count = ctx.getChildCount()
        # 옵션 매개변수가 시작되는 인덱스 ( '{' 다음 )
        options_start_index = 2
        # 옵션 매개변수가 끝나는 인덱스 ( '}' 이전 )
        options_end_index = child_count - 1

        i = options_start_index
        while i < options_end_index:
            # 옵션 이름 추출 (identifier)
            option_name = ctx.getChild(i).getText()
            i += 1  # ':' 토큰으로 이동
            if ctx.getChild(i).getText() != ':':
                raise SyntaxError("Expected ':' after option name.")
            i += 1  # 옵션 값 표현식으로 이동
            # 옵션 값 표현식 방문
            option_value_expr = self.visitExpression(ctx.getChild(i))
            i += 1  # 다음 토큰으로 이동

            # 옵션 매개변수 딕셔너리에 추가
            options[option_name] = option_value_expr

            # 다음 옵션이 있는지 확인 (',' 또는 끝)
            if i < options_end_index and ctx.getChild(i).getText() == ',':
                i += 1  # 다음 옵션으로 이동
            else:
                break  # 옵션 매개변수의 끝

        # 3. Expression 객체 생성
        result_expr = Expression(
            function=base_expr,
            options=options,
            operator='{}',
            context='FunctionCallOptionContext'
        )

        return result_expr

    def visitFunctionCall(self, ctx):
        # 1. 함수 표현식 방문
        function_expr = self.visitExpression(ctx.expression())

        # 2. 인자 목록 처리
        arguments, named_arguments = self.process_arguments(ctx.callArgumentList())

        # 2.5. type alias wrap/unwrap 감지 (Fixed18.wrap(x), UFixed18.unwrap(a))
        if (hasattr(function_expr, 'base') and hasattr(function_expr, 'member') and
            function_expr.base is not None and function_expr.member in ('wrap', 'unwrap')):
            base_name = getattr(function_expr.base, 'identifier', None)
            if base_name and self.contract_analyzer.sa.resolve_type(base_name) is not None:
                # type alias의 wrap/unwrap → 별도 경로
                arg_expr = arguments[0] if arguments else None
                return Expression(
                    function=function_expr,
                    arguments=arguments,
                    operator='()',
                    context='TypeWrapUnwrapContext',
                    identifier=base_name,
                    member=function_expr.member,
                )

        # 2.7. interface/contract type cast 감지: IERC20(_token) 등
        func_id = getattr(function_expr, 'identifier', None)
        if func_id and (func_id in self.contract_analyzer.interface_names
                        or func_id in self.contract_analyzer.contract_cfgs):
            # user-defined type cast → TypeConversion과 동일 구조
            arg_expr = arguments[0] if arguments else None
            return Expression(
                typeName=func_id,
                expression=arg_expr,
                operator='typecast',
                context='TypeConversion',
            )

        # 3. Expression 객체 생성
        result_expr = Expression(
            function=function_expr,
            arguments=arguments,
            named_arguments=named_arguments,
            operator='()',
            context='FunctionCallContext'
        )

        return result_expr

    def visitPayableFunctionCall(self, ctx):
        # 1. 'payable' 키워드 처리
        payable_keyword = ctx.PayableKeyword().getText()
        function_expr = Expression(identifier=payable_keyword)

        # 2. 인자 목록 처리
        arguments, named_arguments = self.process_arguments(ctx.callArgumentList())

        # 3. Expression 객체 생성
        result_expr = Expression(
            function=function_expr,
            arguments=arguments,
            named_arguments=named_arguments,
            operator='()',
            context='PayableFunctionCallContext'
        )

        return result_expr

    def process_arguments(self, call_args_ctx):
        arguments = []
        named_arguments = {}

        if call_args_ctx:
            child_count = call_args_ctx.getChildCount()
            if child_count < 2:
                raise SyntaxError("Invalid function call syntax.")

            if child_count == 2:
                # This means we have '()' with no arguments
                pass  # No arguments
            else:
                # Check if the first token after '(' is '{'
                if call_args_ctx.getChild(1).getText() == '{':
                    # Handle named arguments
                    i = 2  # Start after '{'
                    while i < child_count - 2:
                        arg_name = call_args_ctx.getChild(i).getText()
                        i += 1  # Move to ':'
                        if call_args_ctx.getChild(i).getText() != ':':
                            raise SyntaxError("Expected ':' after argument name.")
                        i += 1  # Move to expression
                        arg_expr_ctx = call_args_ctx.getChild(i)
                        arg_expr = self.visitExpression(arg_expr_ctx)
                        named_arguments[arg_name] = arg_expr
                        i += 1  # Move to next token
                        if i < child_count - 1 and call_args_ctx.getChild(i).getText() == ',':
                            i += 1  # Move to next argument
                        elif call_args_ctx.getChild(i).getText() == '}':
                            i += 1  # Skip '}'
                            break
                        else:
                            raise SyntaxError("Expected ',' or '}' in named arguments.")
                else:
                    # Handle positional arguments
                    i = 1  # Start after '('
                    while i < child_count - 1:
                        arg_ctx = call_args_ctx.getChild(i)
                        if arg_ctx.getText() == ',':
                            i += 1
                            continue
                        arg_expr = self.visitExpression(arg_ctx)
                        arguments.append(arg_expr)
                        i += 1
        else:
            pass  # No arguments (shouldn't occur with the adjusted grammar)

        return arguments if arguments else None, named_arguments if named_arguments else None

    # Visit a parse tree produced by SolidityParser#IdentifierExp.
    def visitIdentifierExp(self, ctx:SolidityParser.IdentifierExpContext):
        # 식별자 이름 추출
        identifier_name = ctx.getText()
        result_expr = Expression(identifier=identifier_name,
                                 context='IdentifierExpContext')
        return result_expr

    # Visit a parse tree produced by SolidityParser#LiteralExp.
    def visitLiteralExp(self, ctx:SolidityParser.LiteralExpContext):
        # 리터럴 값 추출
        literal_value = ctx.getText()
        result_expr = Expression(literal=literal_value,
                                 context='LiteralExpContext')

        # ★ (2026-10-21) 10진 숫자 리터럴은 **문법 규칙(numberLiteral)으로** 판정하고 값을 정확히 계산한다.
        #   예전엔 `isdigit()` 로만 숫자를 골라 `0.0005e16`·`1e18`·`1_000` 이 'string' 으로 분류됐고, 그 문자열이
        #   그대로 상수 값이 되어 비교식이 불가능 판정 → 경로 ⊥ (web3bugs_45_H_01 BORROW_RATE_MAX_MANTISSA).
        #   Solidity 는 숫자 리터럴을 임의정밀도 유리수로 평가하고, 정수가 아니면 정수 타입에 쓸 수 없다(컴파일 에러)
        #   → 정수로 떨어지지 않으면 예외. 단위 리터럴(`365.25 days`)은 visitLiteralSubDenomination 이 같은 방식으로 처리.
        lit_ctx = ctx.literal()
        num_ctx = lit_ctx.numberLiteral() if lit_ctx is not None else None
        if num_ctx is not None and num_ctx.DecimalNumber() is not None:
            exact = Fraction(literal_value.replace("_", ""))
            if exact.denominator != 1:
                raise ValueError(f"numeric literal “{literal_value}” does not evaluate to an integer")
            result_expr.literal = str(exact.numerator)
            result_expr.expr_type = 'uint'
            result_expr.type_length = 256
            return result_expr

        # 리터럴 값이 숫자인 경우 (16진)
        if literal_value.isdigit() or literal_value.startswith(('0x', '0X')):
            # ★ Solidity 숫자 리터럴은 10진·16진 모두 **부호 없는 값**이다.
            #   음수는 단항 '-' 로 표현되지 리터럴 자체가 음수가 되지 않는다.
            #   예전엔 16진만 'int'(signed)로 찍어서:
            #     absTick & 0x1   -> UnsignedIntegerInterval & IntegerInterval
            #   가 Bitwise/Shift dispatch 의 타입 일치 검사에서 거부됐고,
            #   `uint256 ratio = 0xfffcb933...` 처럼 16진 리터럴을 대입받은 변수까지
            #   signed 로 오염돼 이후 `ratio >> 128` 도 줄줄이 실패했다
            #   (TickMath/PRBMath/FixedPoint/BitMath 등 156건).
            #   ※ 주소 리터럴을 여기서 구분하지 않는 이유: Solidity 주소는
            #     address(0x..) **타입 변환**으로 들어오고(별도 경로), 맨몸 40-hex 는
            #     이 리포에선 전부 비트마스크였다(SafeTransferLib 의 0xff..ff 4건).
            #     길이로 판정하면 그걸 주소로 오분류한다.
            result_expr.expr_type = 'uint'
            result_expr.type_length = 256  # 기본적으로 256비트로 가정

        # Boolean 리터럴인 경우
        elif literal_value.lower() == 'true' or literal_value.lower() == 'false':
            result_expr.expr_type = 'bool'

        # 그 외 문자열 리터럴 등
        else:
            result_expr.expr_type = 'string'  # 문자열 또는 기타 리터럴 값

        return result_expr

    # Visit a parse tree produced by SolidityParser#ConditionalExp.
    def visitConditionalExp(self, ctx:SolidityParser.ConditionalExpContext):
        # 조건식 방문
        condition_expr = self.visitExpression(ctx.expression(0))

        # 참일 때의 표현식 방문
        true_expr = self.visitExpression(ctx.expression(1))

        # 거짓일 때의 표현식 방문
        false_expr = self.visitExpression(ctx.expression(2))

        # Expression 객체 생성
        result_expr = Expression(
            condition=condition_expr,
            true_expr=true_expr,
            false_expr=false_expr,
            operator='?:',
            context='ConditionalExpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#Exponentiation.
    def visitExponentiation(self, ctx:SolidityParser.ExponentiationContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정
        operator = '**'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='ExponentiationContext'
        )

        return result_expr

    def visitLiteralSubDenomination(
            self, ctx: SolidityParser.LiteralSubDenominationContext):
        """
        1 weeks  → 604 800
        5 ether → 5 * 10**18
        """

        # ── ① wrapper 노드가 있으면 한 번 더 파고들기 ─────────────────
        if isinstance(ctx.getChild(0), SolidityParser.LiteralWithSubDenominationContext):
            ctx = ctx.getChild(0)  # 실제 numberLiteral · SubDenomination 가 있는 노드

        # ── ② 토큰 추출 ────────────────────────────────────────────────
        num_txt = ctx.numberLiteral().getText()  # '1', '0xFF', …
        denom_tok = ctx.getToken(SolidityParser.SubDenomination, 0)  # 토큰 객체
        denom_txt = denom_tok.getText()  # 'weeks', 'ether', …

        # ── ③ 숫자 → 값 변환 ---------------------------------------------------
        #     Solidity는 subdenomination 리터럴을 임의정밀도 유리수로 평가한다
        #     (예: 365.25 days == 31557600, 정수로 정확히 떨어지면 유효함).
        try:
            base_val = int(num_txt, 0)  # 정수/hex ('0x…') 형태
        except ValueError:
            try:
                base_val = Fraction(num_txt)  # 소수 리터럴 ('365.25' 등)
            except ValueError:
                raise ValueError(f"invalid numeric literal “{num_txt}”")

        # ── ④ 단위 매핑 -------------------------------------------------------
        if denom_txt not in TIME_VALUE:
            raise ValueError(f"unknown sub-denomination “{denom_txt}”")
        product = base_val * TIME_VALUE[denom_txt]
        if isinstance(product, Fraction):
            if product.denominator != 1:
                raise ValueError(
                    f"fractional literal “{num_txt} {denom_txt}” does not evaluate to an integer"
                )
            product = product.numerator
        final_val = product
        # ── ⑤ uint256 상수 Expression 반환 -----------------------------------
        return Expression(
            literal=str(final_val),  # 예: '604800'
            var_type="uint256",
            type_length=256,
            context="LiteralSubDenomination"
        )

    def visitTupleExp(self,
                      ctx: SolidityParser.TupleExpContext):
        inner = ctx.tupleExpression()  # ← 먼저 꺼냄
        # ★ (Phase 3b) 빈 슬롯 위치 보존 — `inner.expression()` 은 빈 슬롯을 빠뜨려 `(, , x) = f()` 의 x 가
        #   0 번째 값을 받았다(선언 쪽은 이미 _split_tuple_slots 를 썼지만 식 쪽은 아니었다).
        elems = [None if e is None else self.visit(e) for e in self._split_tuple_slots(inner)]
        return Expression(
            context="TupleExpressionContext",
            elements=elems
        )

    # Visit a parse tree produced by SolidityParser#Assignment.
    def visitAssignment(self, ctx:SolidityParser.AssignmentContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='AssignmentOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#TypeConversion.
    def visitTypeConversion(self, ctx: SolidityParser.TypeConversionContext):
        ty = ctx.elementaryTypeName().getText()  # 'address', 'uint256', …
        expr = self.visitExpression(ctx.expression())

        return Expression(
            typeName=ty,
            expression=expr,
            operator='typecast',
            context='TypeConversion'
        )

    # Visit a parse tree produced by SolidityParser#UnaryPrefixOp.
    def visitUnaryPrefixOp(self, ctx:SolidityParser.UnaryPrefixOpContext):
        operator = ctx.getChild(0).getText()
        expression = self.visitExpression(ctx.expression())
        result_expr = Expression(
            operator=operator,
            expression=expression,
            is_postfix=False,
            context='UnaryPrefixOpContext'
        )
        return result_expr

    # Visit a parse tree produced by SolidityParser#BitXorOp.
    def visitBitXorOp(self, ctx:SolidityParser.BitXorOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정 ('^')
        operator = '^'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='BitXorOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#AdditiveOp.
    def visitAdditiveOp(self, ctx:SolidityParser.AdditiveOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출 ('+', '-')
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='AdditiveOpContext'
        )

        return result_expr

    def _array_length_expr(self, type_ctx):
        """
        type_ctx : SolidityParser.TypeNameContext
        반환      : length  Expression  (없으면 None)
        """
        # 배열이 중첩되어도 가장 바깥쪽 ‘[]’ 부터 검사
        while isinstance(type_ctx, SolidityParser.ArrayTypeContext):
            # '[' expression? ']'  → 자식 0 = baseType, 1 = expression?
            if type_ctx.expression():
                return self.visit(type_ctx.expression())  # Expression 객체
            type_ctx = type_ctx.typeName()  # 더 안쪽으로…
        return None

    # Visit a parse tree produced by SolidityParser#NewExp.
    def visitNewExp(self, ctx: SolidityParser.NewExpContext):
        type_obj = SolType()
        self.visitTypeName(ctx.typeName(), type_obj)  # 타입 파싱

        length_expr = self._array_length_expr(ctx.typeName())

        return Expression(
            context="NewExpContext",
            typeName=type_obj,
            arguments=[length_expr] if length_expr else []  # ← 여기에만 넣음
        )

    # Visit a parse tree produced by SolidityParser#BitAndOp.
    def visitBitAndOp(self, ctx:SolidityParser.BitAndOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정 ('&')
        operator = '&'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='BitAndOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#BitOrOp.
    def visitBitOrOp(self, ctx:SolidityParser.BitOrOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정 ('|')
        operator = '|'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='BitOrOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#UnarySuffixOp.
    def visitUnarySuffixOp(self, ctx:SolidityParser.UnarySuffixOpContext):
        # 피연산자 표현식 방문
        expr = self.visitExpression(ctx.expression())

        # 연산자 추출
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            operator=operator,
            expression=expr,
            is_postfix=True,  # 후위 연산자임을 표시
            context='UnarySuffixOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#MultiplicativeOp.
    def visitMultiplicativeOp(self, ctx:SolidityParser.MultiplicativeOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출 ('*', '/', '%')
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='MultiplicativeOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#EqualityOp.
    def visitEqualityOp(self, ctx:SolidityParser.EqualityOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출 ('==', '!=')
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='EqualityOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#AndOperation.
    def visitAndOperation(self, ctx:SolidityParser.AndOperationContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정 ('&&')
        operator = '&&'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='AndOperationOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#RelationalOp.
    def visitRelationalOp(self, ctx:SolidityParser.RelationalOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출 ('<', '>', '<=', '>=')
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='RelationalOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#OrOperation.
    def visitOrOperation(self, ctx:SolidityParser.OrOperationContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 설정 ('||')
        operator = '||'

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='OrOperationContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#ShiftOp.
    def visitShiftOp(self, ctx:SolidityParser.ShiftOpContext):
        # 좌측 표현식 방문
        left_expr = self.visitExpression(ctx.expression(0))

        # 우측 표현식 방문
        right_expr = self.visitExpression(ctx.expression(1))

        # 연산자 추출 ('<<', '>>', '>>>')
        operator = ctx.getChild(1).getText()

        # Expression 객체 생성
        result_expr = Expression(
            left=left_expr,
            operator=operator,
            right=right_expr,
            context='ShiftOpContext'
        )

        return result_expr

    # Visit a parse tree produced by SolidityParser#TypeNameExp.
    def visitTypeNameExp(self, ctx:SolidityParser.TypeNameExpContext):
        # elementaryTypeName이 값이 아니라 "타입 이름 그 자체"로 쓰인 경우
        # (예: abi.decode(data, (uint256, address))의 타입-리스트 원소).
        # 이전에는 visitChildren()이 터미널 노드까지 내려가 None을 반환했음.
        type_name_txt = ctx.elementaryTypeName().getText()
        return Expression(
            identifier=type_name_txt,
            context="TypeNameExprContext"
        )

    # Visit a parse tree produced by SolidityParser#InlineArrayExp.
    def visitInlineArrayExp(self, ctx:SolidityParser.InlineArrayExpContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#literal.
    def visitLiteral(self, ctx:SolidityParser.LiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#literalWithSubDenomination.
    def visitLiteralWithSubDenomination(self, ctx:SolidityParser.LiteralWithSubDenominationContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#numberLiteral.
    def visitNumberLiteral(self, ctx:SolidityParser.NumberLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#identifier.
    def visitIdentifier(self, ctx:SolidityParser.IdentifierContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#userDefinedValueTypeDefinition.
    def visitUserDefinedValueTypeDefinition(self, ctx:SolidityParser.UserDefinedValueTypeDefinitionContext):
        # type Fixed18 is int256; → alias_name="Fixed18", underlying="int256"
        alias_name = ctx.identifier().getText()
        underlying_type = ctx.elementaryTypeName().getText()
        self.contract_analyzer.sa.process_type_alias(alias_name, underlying_type)
        return None

    # Visit a parse tree produced by SolidityParser#booleanLiteral.
    def visitBooleanLiteral(self, ctx:SolidityParser.BooleanLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#hexStringLiteral.
    def visitHexStringLiteral(self, ctx:SolidityParser.HexStringLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#unicodeStringLiteral.
    def visitUnicodeStringLiteral(self, ctx:SolidityParser.UnicodeStringLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#stringLiteral.
    def visitStringLiteral(self, ctx:SolidityParser.StringLiteralContext):
        return self.visitChildren(ctx)

    # Visit a parse tree produced by SolidityParser#overrideSpecifier.
    def visitOverrideSpecifier(self, ctx:SolidityParser.OverrideSpecifierContext):
        return self.visitChildren(ctx)

    def evaluate_literal_expression(self, expr):
        """
        literal 표현식 (숫자, 문자열, boolean 등)을 처리하는 함수.
        LiteralExpContext에서 리터럴 값을 분석하여 처리합니다.
        """
        literal_text = expr.getText()  # 리터럴 텍스트 가져오기

        # 숫자 리터럴인지 확인
        if literal_text.isdigit():
            return self.evaluate_number_literal(literal_text)

        # 불리언 리터럴인지 확인
        elif literal_text == 'true' or literal_text == 'false':
            return self.evaluate_boolean_literal(literal_text)

        # 기타 리터럴 타입 (16진수 문자열, 유니코드 등)
        elif literal_text.startswith("0x"):
            return self.evaluate_hex_string_literal(literal_text)

        elif literal_text.startswith('"') and literal_text.endswith('"'):
            return self.evaluate_string_literal(literal_text)

        else:
            raise ValueError(f"Unsupported literal type: {literal_text}")

    def evaluate_number_literal(self, literal_text):
        """
        숫자 리터럴 처리
        """
        return int(literal_text)

    def evaluate_boolean_literal(self, literal_text):
        """
        boolean 리터럴 처리
        """
        if literal_text == 'true':
            return True
        elif literal_text == 'false':
            return False

    def evaluate_hex_string_literal(self, literal_text):
        """
        16진수 리터럴 처리
        """
        return int(literal_text, 16)

    def evaluate_string_literal(self, literal_text):
        """
        문자열 리터럴 처리
        """
        return literal_text.strip('"')  # 따옴표 제거 후 반환

    # ──────────────────────────────────────────────────────────────
    # 0.  intentValue 파서  ─  Pre/ During/ Post 모두 재사용
    # ──────────────────────────────────────────────────────────────
    def _parse_debug_value(self, iv_ctx):
        """
        debugValue
            → [lo,hi]                → IntegerInterval / UnsignedIntegerInterval
            → symbolicAddress N      → AddressSet (symbolic ID)
            → symbolicBytes …        → str 그대로
            → symbolicString …       → str 그대로
            → true / false / any     → BoolInterval
            → Enum.Literal           → "Enum.Member"  (str)
            → array[...]             → list of ints
            → arrayAddress[...]      → list of AddressSet
        """
        first = iv_ctx.getChild(0).getText()

        # ① [lo , hi]
        if first == '[':
            lo = int(iv_ctx.signedNumberLiteral(0).getText(), 0)
            hi = int(iv_ctx.signedNumberLiteral(1).getText(), 0)
            cls = IntegerInterval if lo < 0 else UnsignedIntegerInterval
            return cls(lo, hi, 256)

        # ② symbolicAddress N
        if isinstance(iv_ctx, SolidityParser.DebugSymbolicAddressContext):
            nid = int(iv_ctx.numberLiteral().getText(), 0)
            addr_mgr = self.contract_analyzer.addr_mgr
            return addr_mgr.make_symbolic_address(nid)

        # ③ symbolicBytes / symbolicString
        if isinstance(iv_ctx, (SolidityParser.DebugSymbolicBytesContext,
                               SolidityParser.DebugSymbolicStringContext)):
            return iv_ctx.getText()  # 토큰 그대로

        # ④ bool
        if isinstance(iv_ctx, SolidityParser.DebugBoolTokenContext):
            tok = first  # true / false / any
            return {
                'true': BoolInterval(1, 1),
                'false': BoolInterval(0, 0),
                'any': BoolInterval(0, 1)
            }[tok]

        # ⑤ enum
        if isinstance(iv_ctx, SolidityParser.DebugEnumLiteralContext):
            if iv_ctx.identifier(1):
                return f"{iv_ctx.identifier(0).getText()}.{iv_ctx.identifier(1).getText()}"
            else:
                return iv_ctx.identifier(0).getText()

        # ⑥ array[1,2,3]
        if isinstance(iv_ctx, SolidityParser.DebugIntArrayContext):
            if iv_ctx.signedNumberLiteral():
                return [int(n.getText(), 0) for n in iv_ctx.signedNumberLiteral()]
            else:
                return []  # array[] → empty list

        # ⑦ arrayAddress[1,2,3]
        if isinstance(iv_ctx, SolidityParser.DebugAddressArrayContext):
            addr_mgr = self.contract_analyzer.addr_mgr
            if iv_ctx.numberLiteral():
                ids = [int(n.getText(), 0) for n in iv_ctx.numberLiteral()]
                return [addr_mgr.make_symbolic_address(nid) for nid in ids]
            else:
                return []  # arrayAddress[] → empty list

        raise ValueError("unsupported debugValue")

    def _relop_from_ctx(self, ctx) -> str:
        """
        각 alt context(Temporal*, Return*Cmp, RelationalCmp)에는 relOp()가 존재.
        'not in'이 토큰화될 때 'notin'처럼 붙을 수 있어 정규화.
        """
        raw = ctx.relOp().getText()
        raw = raw.replace(" ", "")
        return "not in" if raw == "notin" else raw
