# Generated from Yul.g4 by ANTLR 4.13.1
from antlr4 import *
if "." in __name__:
    from .YulParser import YulParser
else:
    from YulParser import YulParser

# This class defines a complete generic visitor for a parse tree produced by YulParser.

class YulVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by YulParser#yulUnit.
    def visitYulUnit(self, ctx:YulParser.YulUnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulStatement.
    def visitYulStatement(self, ctx:YulParser.YulStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulBlock.
    def visitYulBlock(self, ctx:YulParser.YulBlockContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulVariableDeclaration.
    def visitYulVariableDeclaration(self, ctx:YulParser.YulVariableDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulAssignment.
    def visitYulAssignment(self, ctx:YulParser.YulAssignmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulExpressionStatement.
    def visitYulExpressionStatement(self, ctx:YulParser.YulExpressionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulIfStatement.
    def visitYulIfStatement(self, ctx:YulParser.YulIfStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulForStatement.
    def visitYulForStatement(self, ctx:YulParser.YulForStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulSwitchStatement.
    def visitYulSwitchStatement(self, ctx:YulParser.YulSwitchStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulFunctionDefinition.
    def visitYulFunctionDefinition(self, ctx:YulParser.YulFunctionDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulExpression.
    def visitYulExpression(self, ctx:YulParser.YulExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulFunctionCall.
    def visitYulFunctionCall(self, ctx:YulParser.YulFunctionCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by YulParser#yulLiteral.
    def visitYulLiteral(self, ctx:YulParser.YulLiteralContext):
        return self.visitChildren(ctx)



del YulParser