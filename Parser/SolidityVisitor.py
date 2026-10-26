# Generated from Solidity.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .SolidityParser import SolidityParser
else:
    from SolidityParser import SolidityParser

# This class defines a complete generic visitor for a parse tree produced by SolidityParser.

class SolidityVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by SolidityParser#sourceUnit.
    def visitSourceUnit(self, ctx:SolidityParser.SourceUnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#pragmaDirective.
    def visitPragmaDirective(self, ctx:SolidityParser.PragmaDirectiveContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#pragmaName.
    def visitPragmaName(self, ctx:SolidityParser.PragmaNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#pragmaValue.
    def visitPragmaValue(self, ctx:SolidityParser.PragmaValueContext):
        return self.visitChildren(ctx)


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
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interfaceDefinition.
    def visitInterfaceDefinition(self, ctx:SolidityParser.InterfaceDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#libraryDefinition.
    def visitLibraryDefinition(self, ctx:SolidityParser.LibraryDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#inheritanceSpecifier.
    def visitInheritanceSpecifier(self, ctx:SolidityParser.InheritanceSpecifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#callArgumentList.
    def visitCallArgumentList(self, ctx:SolidityParser.CallArgumentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#identifierPath.
    def visitIdentifierPath(self, ctx:SolidityParser.IdentifierPathContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#constantVariableDeclaration.
    def visitConstantVariableDeclaration(self, ctx:SolidityParser.ConstantVariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#contractBodyElement.
    def visitContractBodyElement(self, ctx:SolidityParser.ContractBodyElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#constructorDefinition.
    def visitConstructorDefinition(self, ctx:SolidityParser.ConstructorDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#fallbackFunctionDefinition.
    def visitFallbackFunctionDefinition(self, ctx:SolidityParser.FallbackFunctionDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#receiveFunctionDefinition.
    def visitReceiveFunctionDefinition(self, ctx:SolidityParser.ReceiveFunctionDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#stateVariableDeclaration.
    def visitStateVariableDeclaration(self, ctx:SolidityParser.StateVariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#errorDefinition.
    def visitErrorDefinition(self, ctx:SolidityParser.ErrorDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#errorParameter.
    def visitErrorParameter(self, ctx:SolidityParser.ErrorParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#usingDirective.
    def visitUsingDirective(self, ctx:SolidityParser.UsingDirectiveContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#userDefinableOperators.
    def visitUserDefinableOperators(self, ctx:SolidityParser.UserDefinableOperatorsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#structDefinition.
    def visitStructDefinition(self, ctx:SolidityParser.StructDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#structMember.
    def visitStructMember(self, ctx:SolidityParser.StructMemberContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#modifierDefinition.
    def visitModifierDefinition(self, ctx:SolidityParser.ModifierDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#visibility.
    def visitVisibility(self, ctx:SolidityParser.VisibilityContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#modifierInvocation.
    def visitModifierInvocation(self, ctx:SolidityParser.ModifierInvocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#functionDefinition.
    def visitFunctionDefinition(self, ctx:SolidityParser.FunctionDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#eventDefinition.
    def visitEventDefinition(self, ctx:SolidityParser.EventDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#enumDefinition.
    def visitEnumDefinition(self, ctx:SolidityParser.EnumDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#parameterList.
    def visitParameterList(self, ctx:SolidityParser.ParameterListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#eventParameter.
    def visitEventParameter(self, ctx:SolidityParser.EventParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#variableDeclaration.
    def visitVariableDeclaration(self, ctx:SolidityParser.VariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#variableDeclarationTuple.
    def visitVariableDeclarationTuple(self, ctx:SolidityParser.VariableDeclarationTupleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ArrayType.
    def visitArrayType(self, ctx:SolidityParser.ArrayTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#BasicType.
    def visitBasicType(self, ctx:SolidityParser.BasicTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#FunctionType.
    def visitFunctionType(self, ctx:SolidityParser.FunctionTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#UserDefinedType.
    def visitUserDefinedType(self, ctx:SolidityParser.UserDefinedTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MapType.
    def visitMapType(self, ctx:SolidityParser.MapTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#mapping.
    def visitMapping(self, ctx:SolidityParser.MappingContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#mappingKeyType.
    def visitMappingKeyType(self, ctx:SolidityParser.MappingKeyTypeContext):
        return self.visitChildren(ctx)


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
    def visitIntentUnit(self, ctx:SolidityParser.IntentUnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#debugInput.
    def visitDebugInput(self, ctx:SolidityParser.DebugInputContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#GlobalVarSimple.
    def visitGlobalVarSimple(self, ctx:SolidityParser.GlobalVarSimpleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#GlobalVarAddressBalance.
    def visitGlobalVarAddressBalance(self, ctx:SolidityParser.GlobalVarAddressBalanceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#debugStateVar.
    def visitDebugStateVar(self, ctx:SolidityParser.DebugStateVarContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#debugParam.
    def visitDebugParam(self, ctx:SolidityParser.DebugParamContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IReturnPatternA.
    def visitIReturnPatternA(self, ctx:SolidityParser.IReturnPatternAContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IReturnPatternB.
    def visitIReturnPatternB(self, ctx:SolidityParser.IReturnPatternBContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ireturnAccessChain.
    def visitIreturnAccessChain(self, ctx:SolidityParser.IreturnAccessChainContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IReturnMemberAccess.
    def visitIReturnMemberAccess(self, ctx:SolidityParser.IReturnMemberAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IReturnIndexAccess.
    def visitIReturnIndexAccess(self, ctx:SolidityParser.IReturnIndexAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IReturnChainedCall.
    def visitIReturnChainedCall(self, ctx:SolidityParser.IReturnChainedCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#duringIntent.
    def visitDuringIntent(self, ctx:SolidityParser.DuringIntentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DuringFeasible.
    def visitDuringFeasible(self, ctx:SolidityParser.DuringFeasibleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DuringFunctionArg.
    def visitDuringFunctionArg(self, ctx:SolidityParser.DuringFunctionArgContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DuringCommon.
    def visitDuringCommon(self, ctx:SolidityParser.DuringCommonContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#postIntent.
    def visitPostIntent(self, ctx:SolidityParser.PostIntentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#PostCommon.
    def visitPostCommon(self, ctx:SolidityParser.PostCommonContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ReturnExprCmp.
    def visitReturnExprCmp(self, ctx:SolidityParser.ReturnExprCmpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ReturnIndexCmp.
    def visitReturnIndexCmp(self, ctx:SolidityParser.ReturnIndexCmpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ReturnVarCmp.
    def visitReturnVarCmp(self, ctx:SolidityParser.ReturnVarCmpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#PercentOf.
    def visitPercentOf(self, ctx:SolidityParser.PercentOfContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Ceil.
    def visitCeil(self, ctx:SolidityParser.CeilContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Floor.
    def visitFloor(self, ctx:SolidityParser.FloorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#RelationalCmp.
    def visitRelationalCmp(self, ctx:SolidityParser.RelationalCmpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Implication.
    def visitImplication(self, ctx:SolidityParser.ImplicationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarChangedEval.
    def visitVarChangedEval(self, ctx:SolidityParser.VarChangedEvalContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugIntInterval.
    def visitDebugIntInterval(self, ctx:SolidityParser.DebugIntIntervalContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugSymbolicAddress.
    def visitDebugSymbolicAddress(self, ctx:SolidityParser.DebugSymbolicAddressContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugSymbolicBytes.
    def visitDebugSymbolicBytes(self, ctx:SolidityParser.DebugSymbolicBytesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugSymbolicString.
    def visitDebugSymbolicString(self, ctx:SolidityParser.DebugSymbolicStringContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugBoolToken.
    def visitDebugBoolToken(self, ctx:SolidityParser.DebugBoolTokenContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugEnumLiteral.
    def visitDebugEnumLiteral(self, ctx:SolidityParser.DebugEnumLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugIntArray.
    def visitDebugIntArray(self, ctx:SolidityParser.DebugIntArrayContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#DebugAddressArray.
    def visitDebugAddressArray(self, ctx:SolidityParser.DebugAddressArrayContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#intentValue.
    def visitIntentValue(self, ctx:SolidityParser.IntentValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentShiftOp.
    def visitIntentShiftOp(self, ctx:SolidityParser.IntentShiftOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentShiftRoot.
    def visitIntentShiftRoot(self, ctx:SolidityParser.IntentShiftRootContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#AddSub.
    def visitAddSub(self, ctx:SolidityParser.AddSubContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#AddSubRoot.
    def visitAddSubRoot(self, ctx:SolidityParser.AddSubRootContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MulDivModRoot.
    def visitMulDivModRoot(self, ctx:SolidityParser.MulDivModRootContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MulDivMod.
    def visitMulDivMod(self, ctx:SolidityParser.MulDivModContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentExponentiation.
    def visitIntentExponentiation(self, ctx:SolidityParser.IntentExponentiationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentExpRoot.
    def visitIntentExpRoot(self, ctx:SolidityParser.IntentExpRootContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#NumLiteral.
    def visitNumLiteral(self, ctx:SolidityParser.NumLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#InlineInterval.
    def visitInlineInterval(self, ctx:SolidityParser.InlineIntervalContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarRefAtEntry.
    def visitVarRefAtEntry(self, ctx:SolidityParser.VarRefAtEntryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarRefAtExit.
    def visitVarRefAtExit(self, ctx:SolidityParser.VarRefAtExitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarRefAtBefore.
    def visitVarRefAtBefore(self, ctx:SolidityParser.VarRefAtBeforeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarRefAtAfter.
    def visitVarRefAtAfter(self, ctx:SolidityParser.VarRefAtAfterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#VarRefAtAssign.
    def visitVarRefAtAssign(self, ctx:SolidityParser.VarRefAtAssignContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#NumVarRef.
    def visitNumVarRef(self, ctx:SolidityParser.NumVarRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Parenthesized.
    def visitParenthesized(self, ctx:SolidityParser.ParenthesizedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#NormalVarRef.
    def visitNormalVarRef(self, ctx:SolidityParser.NormalVarRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentMemberAccess.
    def visitIntentMemberAccess(self, ctx:SolidityParser.IntentMemberAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IntentIndexAccess.
    def visitIntentIndexAccess(self, ctx:SolidityParser.IntentIndexAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#signedNumberLiteral.
    def visitSignedNumberLiteral(self, ctx:SolidityParser.SignedNumberLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#logicOp.
    def visitLogicOp(self, ctx:SolidityParser.LogicOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#relOp.
    def visitRelOp(self, ctx:SolidityParser.RelOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveSimpleStatement.
    def visitInteractiveSimpleStatement(self, ctx:SolidityParser.InteractiveSimpleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveVariableDeclarationStatement.
    def visitInteractiveVariableDeclarationStatement(self, ctx:SolidityParser.InteractiveVariableDeclarationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveExpressionStatement.
    def visitInteractiveExpressionStatement(self, ctx:SolidityParser.InteractiveExpressionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveStateVariableElement.
    def visitInteractiveStateVariableElement(self, ctx:SolidityParser.InteractiveStateVariableElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveEnumDefinition.
    def visitInteractiveEnumDefinition(self, ctx:SolidityParser.InteractiveEnumDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveStructDefinition.
    def visitInteractiveStructDefinition(self, ctx:SolidityParser.InteractiveStructDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveEnumItems.
    def visitInteractiveEnumItems(self, ctx:SolidityParser.InteractiveEnumItemsContext):
        return self.visitChildren(ctx)


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
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#statement.
    def visitStatement(self, ctx:SolidityParser.StatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#expressionStatement.
    def visitExpressionStatement(self, ctx:SolidityParser.ExpressionStatementContext):
        return self.visitChildren(ctx)


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
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#assemblyStatement.
    def visitAssemblyStatement(self, ctx:SolidityParser.AssemblyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#assemblyFlags.
    def visitAssemblyFlags(self, ctx:SolidityParser.AssemblyFlagsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#assemblyFlagString.
    def visitAssemblyFlagString(self, ctx:SolidityParser.AssemblyFlagStringContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#assemblyBlock.
    def visitAssemblyBlock(self, ctx:SolidityParser.AssemblyBlockContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#doWhileStatement.
    def visitDoWhileStatement(self, ctx:SolidityParser.DoWhileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#continueStatement.
    def visitContinueStatement(self, ctx:SolidityParser.ContinueStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#breakStatement.
    def visitBreakStatement(self, ctx:SolidityParser.BreakStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#returnStatement.
    def visitReturnStatement(self, ctx:SolidityParser.ReturnStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#emitStatement.
    def visitEmitStatement(self, ctx:SolidityParser.EmitStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#revertStatement.
    def visitRevertStatement(self, ctx:SolidityParser.RevertStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#requireStatement.
    def visitRequireStatement(self, ctx:SolidityParser.RequireStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#assertStatement.
    def visitAssertStatement(self, ctx:SolidityParser.AssertStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#variableDeclarationStatement.
    def visitVariableDeclarationStatement(self, ctx:SolidityParser.VariableDeclarationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveStatement.
    def visitInteractiveStatement(self, ctx:SolidityParser.InteractiveStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveIfStatement.
    def visitInteractiveIfStatement(self, ctx:SolidityParser.InteractiveIfStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveElseStatement.
    def visitInteractiveElseStatement(self, ctx:SolidityParser.InteractiveElseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveForStatement.
    def visitInteractiveForStatement(self, ctx:SolidityParser.InteractiveForStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveWhileStatement.
    def visitInteractiveWhileStatement(self, ctx:SolidityParser.InteractiveWhileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveDoWhileDoStatement.
    def visitInteractiveDoWhileDoStatement(self, ctx:SolidityParser.InteractiveDoWhileDoStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveDoWhileWhileStatement.
    def visitInteractiveDoWhileWhileStatement(self, ctx:SolidityParser.InteractiveDoWhileWhileStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveTryStatement.
    def visitInteractiveTryStatement(self, ctx:SolidityParser.InteractiveTryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#interactiveCatchClause.
    def visitInteractiveCatchClause(self, ctx:SolidityParser.InteractiveCatchClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#elementaryTypeName.
    def visitElementaryTypeName(self, ctx:SolidityParser.ElementaryTypeNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IdentifierExp.
    def visitIdentifierExp(self, ctx:SolidityParser.IdentifierExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#LiteralExp.
    def visitLiteralExp(self, ctx:SolidityParser.LiteralExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ConditionalExp.
    def visitConditionalExp(self, ctx:SolidityParser.ConditionalExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Exponentiation.
    def visitExponentiation(self, ctx:SolidityParser.ExponentiationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#LiteralSubDenomination.
    def visitLiteralSubDenomination(self, ctx:SolidityParser.LiteralSubDenominationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#TupleExp.
    def visitTupleExp(self, ctx:SolidityParser.TupleExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#Assignment.
    def visitAssignment(self, ctx:SolidityParser.AssignmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#TypeConversion.
    def visitTypeConversion(self, ctx:SolidityParser.TypeConversionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#UnaryPrefixOp.
    def visitUnaryPrefixOp(self, ctx:SolidityParser.UnaryPrefixOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#BitXorOp.
    def visitBitXorOp(self, ctx:SolidityParser.BitXorOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#AdditiveOp.
    def visitAdditiveOp(self, ctx:SolidityParser.AdditiveOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#PayableFunctionCall.
    def visitPayableFunctionCall(self, ctx:SolidityParser.PayableFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#FunctionCall.
    def visitFunctionCall(self, ctx:SolidityParser.FunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#NewExp.
    def visitNewExp(self, ctx:SolidityParser.NewExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#BitAndOp.
    def visitBitAndOp(self, ctx:SolidityParser.BitAndOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IndexRangeAccess.
    def visitIndexRangeAccess(self, ctx:SolidityParser.IndexRangeAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#BitOrOp.
    def visitBitOrOp(self, ctx:SolidityParser.BitOrOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#UnarySuffixOp.
    def visitUnarySuffixOp(self, ctx:SolidityParser.UnarySuffixOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MultiplicativeOp.
    def visitMultiplicativeOp(self, ctx:SolidityParser.MultiplicativeOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#IndexAccess.
    def visitIndexAccess(self, ctx:SolidityParser.IndexAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#EqualityOp.
    def visitEqualityOp(self, ctx:SolidityParser.EqualityOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#AndOperation.
    def visitAndOperation(self, ctx:SolidityParser.AndOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#RelationalOp.
    def visitRelationalOp(self, ctx:SolidityParser.RelationalOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#OrOperation.
    def visitOrOperation(self, ctx:SolidityParser.OrOperationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MemberAccess.
    def visitMemberAccess(self, ctx:SolidityParser.MemberAccessContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#FunctionCallOptions.
    def visitFunctionCallOptions(self, ctx:SolidityParser.FunctionCallOptionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#ShiftOp.
    def visitShiftOp(self, ctx:SolidityParser.ShiftOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#TypeNameExp.
    def visitTypeNameExp(self, ctx:SolidityParser.TypeNameExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#MetaType.
    def visitMetaType(self, ctx:SolidityParser.MetaTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#InlineArrayExp.
    def visitInlineArrayExp(self, ctx:SolidityParser.InlineArrayExpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#literal.
    def visitLiteral(self, ctx:SolidityParser.LiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#literalWithSubDenomination.
    def visitLiteralWithSubDenomination(self, ctx:SolidityParser.LiteralWithSubDenominationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#tupleExpression.
    def visitTupleExpression(self, ctx:SolidityParser.TupleExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#numberLiteral.
    def visitNumberLiteral(self, ctx:SolidityParser.NumberLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#identifier.
    def visitIdentifier(self, ctx:SolidityParser.IdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by SolidityParser#userDefinedValueTypeDefinition.
    def visitUserDefinedValueTypeDefinition(self, ctx:SolidityParser.UserDefinedValueTypeDefinitionContext):
        return self.visitChildren(ctx)


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



del SolidityParser