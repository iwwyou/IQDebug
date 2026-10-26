// Yul (inline assembly) grammar for IntentChecker
// Separate from Solidity.g4 to avoid lexer token conflicts
//
// Compile: antlr4 -Dlanguage=Python3 -visitor Yul.g4

grammar Yul;

// ── Entry rule ──
yulUnit
  : yulStatement* EOF
  ;

// ── Statements ──
yulStatement
  : yulBlock
  | yulVariableDeclaration
  | yulAssignment
  | yulExpressionStatement
  | yulIfStatement
  | yulForStatement
  | yulSwitchStatement
  | yulFunctionDefinition
  | 'leave'
  | 'break'
  | 'continue'
  ;

yulBlock
  : '{' yulStatement* '}'
  ;

yulVariableDeclaration
  : 'let' IDENTIFIER (':=' yulExpression)?
  | 'let' IDENTIFIER (',' IDENTIFIER)+ (':=' yulFunctionCall)?
  ;

yulAssignment
  : IDENTIFIER ':=' yulExpression
  | IDENTIFIER (',' IDENTIFIER)+ ':=' yulFunctionCall
  ;

yulExpressionStatement
  : yulFunctionCall
  ;

yulIfStatement
  : 'if' yulExpression yulBlock
  ;

yulForStatement
  : 'for' yulBlock yulExpression yulBlock yulBlock
  ;

yulSwitchStatement
  : 'switch' yulExpression
    ( ('case' yulLiteral yulBlock)+ ('default' yulBlock)?
    | 'default' yulBlock
    )
  ;

yulFunctionDefinition
  : 'function' IDENTIFIER '(' (IDENTIFIER (',' IDENTIFIER)*)? ')'
    ('->' IDENTIFIER (',' IDENTIFIER)*)? yulBlock
  ;

// ── Expressions ──
yulExpression
  : yulFunctionCall
  | IDENTIFIER
  | yulLiteral
  ;

yulFunctionCall
  : IDENTIFIER '(' (yulExpression (',' yulExpression)*)? ')'
  ;

yulLiteral
  : DECIMAL_NUMBER
  | HEX_NUMBER
  | STRING_LITERAL
  | 'true'
  | 'false'
  ;

// ── Lexer rules ──
DECIMAL_NUMBER
  : '0'
  | [1-9] [0-9]*
  ;

HEX_NUMBER
  : '0x' [0-9a-fA-F]+
  ;

STRING_LITERAL
  : '"' (~["\r\n\\] | '\\' .)* '"'
  ;

IDENTIFIER
  : [a-zA-Z_$] [a-zA-Z0-9_$]*
  ;

// ── Skip ──
WS : [ \t\r\n]+ -> skip ;
LINE_COMMENT : '//' ~[\r\n]* -> skip ;
BLOCK_COMMENT : '/*' .*? '*/' -> skip ;
