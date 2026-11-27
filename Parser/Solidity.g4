// Copyright 2020 Gonçalo Sá <goncalo.sa@consensys.net>
// Copyright 2016-2019 Federico Bond <federicobond@gmail.com>
// Licensed under the MIT license. See LICENSE file in the project root for details.

// pip3 install antlr4-python3-runtime
// antlr -Dlanguage=Python3 -visitor Solidity.g4

grammar Solidity;

@parser::members {
inDuring = False
}

@lexer::members {
intentMode = False
}

sourceUnit
  : (
    pragmaDirective
    | importDirective
    | usingDirective
    | contractDefinition
    | interfaceDefinition
    | libraryDefinition
    | functionDefinition
    | constantVariableDeclaration
    | structDefinition
    | enumDefinition
    | userDefinedValueTypeDefinition
    | errorDefinition
    | eventDefinition
  )* EOF ;

// libraryUnit - interactiveLibraryDefinition
// interfaceUnit - interactiveInterfaceDefinition

pragmaDirective
  : 'pragma' pragmaName pragmaValue ';' ;

pragmaName
  : identifier ;

pragmaValue
  : '*' | version | expression ;

version
  : versionConstraint ('||'? versionConstraint)* ;

versionOperator
  : '^' | '~' | '>=' | '>' | '<' | '<=' | '=' ;

versionConstraint
  : versionOperator? VersionLiteral
  | versionOperator? DecimalNumber ;

importDeclaration
  : identifier ('as' identifier)? ;

importDirective
  : 'import' importPath ('as' identifier)? ';'
  | 'import' (symbolAliases | '*' 'as' identifier) 'from' importPath ';' ;

importPath
  : stringLiteral ;

symbolAliases
  : '{' importDeclaration ( ',' importDeclaration )* '}' ;

contractDefinition
  : 'abstract'? 'contract' identifier
    ( 'is' inheritanceSpecifier (',' inheritanceSpecifier )* )?
    '{' contractBodyElement* '}' ;

interfaceDefinition
  : 'interface' identifier
   ( 'is' inheritanceSpecifier (',' inheritanceSpecifier )* )?
    '{' contractBodyElement* '}' ;

libraryDefinition
  : 'library' identifier '{' contractBodyElement* '}' ;

inheritanceSpecifier
  : identifierPath callArgumentList? ;   // 2026-11-13: 공식 문법대로 — callArgumentList 가 이미 괄호를 포함한다

callArgumentList
  : '(' (
        (expression (',' expression)*)
      | '{' (identifier ':' expression) (',' identifier ':' expression)* '}'
    )? ')';

identifierPath
  : identifier ('.' identifier)* ;

constantVariableDeclaration
  : typeName 'constant' identifier '=' expression ';' ;

contractBodyElement
  : constructorDefinition
  | functionDefinition
  | modifierDefinition
  | fallbackFunctionDefinition
  | receiveFunctionDefinition
  | structDefinition
  | enumDefinition
  | userDefinedValueTypeDefinition
  | stateVariableDeclaration
  | eventDefinition
  | errorDefinition
  | usingDirective ;

//interactiveEnumDefinition
  //: 'enum' enumIdentifier '{' enumIdentifier (',' enumIdentifier)* '}' ;

constructorDefinition
  : 'constructor' '(' parameterList? ')'
  ( modifierInvocation | PayableKeyword | InternalKeyword | PublicKeyword )* block;

fallbackFunctionDefinition
  : 'fallback' '(' parameterList? ')'
  (ExternalKeyword | stateMutability | modifierInvocation | VirtualKeyword | overrideSpecifier)*
  ('returns' '(' parameterList ')')?
  (';' | block);

receiveFunctionDefinition
  : 'receive' '(' ')' (ExternalKeyword | PayableKeyword | modifierInvocation | VirtualKeyword | overrideSpecifier)*
  (';' | block);

stateVariableDeclaration
  : typeName
    ( PublicKeyword | InternalKeyword | PrivateKeyword | ConstantKeyword | ImmutableKeyword | overrideSpecifier )*
    identifier ('=' expression)? ';' ;

errorDefinition
  : 'error' identifier '(' (errorParameter (',' errorParameter)*)? ')' ';' ;

errorParameter
  : typeName identifier? ;

usingDirective
  : 'using'
  (identifierPath
  | '{' (identifierPath ('as' userDefinableOperators)? (',' identifierPath ('as' userDefinableOperators)?) '}'))
  'for' ('*'|typeName) GlobalKeyword? ';';

userDefinableOperators
  : '|' | '&' | '^' | '~' | '+' | '-' | '*' | '/' | '%' | '==' | '!=' | '<' | '>' | '<=' | '>=' ;

structDefinition
  : 'struct' identifier
    '{' structMember+ '}' ;

structMember
  : typeName identifier ';' ;

modifierDefinition
  : 'modifier' identifier ('(' parameterList? ')')?
  ( VirtualKeyword | overrideSpecifier )*
  ( ';' | block ) ;

visibility
    : InternalKeyword
    | ExternalKeyword
    | PrivateKeyword
    | PublicKeyword ;

modifierInvocation
  : identifierPath (callArgumentList)? ;

functionDefinition
  : 'function' identifier '(' parameterList? ')'
  (visibility|stateMutability|modifierInvocation|VirtualKeyword|overrideSpecifier)*
  ('returns' '(' parameterList ')')?
  (';' | block) ;

eventDefinition
  : 'event' identifier '(' (eventParameter (',' eventParameter)*)? ')' AnonymousKeyword? ';' ;

enumDefinition
  : 'enum' identifier '{' identifier (',' identifier)* '}' ;

parameterList
  : (typeName dataLocation? identifier?) (',' typeName dataLocation? identifier?)* ;

eventParameter
  : typeName 'indexed'? identifier? ;

variableDeclaration
  : typeName dataLocation? identifier ;

variableDeclarationTuple
  : '(' (','+)? variableDeclaration
  (',' variableDeclaration?)*
  ')' ;

typeName
  : elementaryTypeName # BasicType
  | functionTypeName # FunctionType
  | mapping # MapType
  | identifierPath # UserDefinedType
  | typeName '[' expression? ']' # ArrayType
  ;

mapping
  : 'mapping' '(' mappingKeyType (identifier)? '=>' typeName (identifier)? ')' ;

mappingKeyType
:elementaryTypeName
| identifierPath;

functionTypeName
  : 'function' '(' parameterList? ')'
    ( visibility | stateMutability )*
    ( 'returns' '(' parameterList ')' )? ;

// for interactive parsing
// interactiveStructDefinition, interactiveEnumDefintion
// constantVariableDeclaration, userDefinedValueTypeDefinition
// errorDefinition 일단 빼야될듯
interactiveSourceUnit
  : (
    interactiveStateVariableElement
    | interactiveFunctionElement
    | interfaceDefinition
    | libraryDefinition
    | contractDefinition
    | pragmaDirective
    | importDirective
  )* EOF ;

interactiveEnumUnit
  : (
    interactiveEnumItems
  )* EOF;

interactiveStructUnit
  : (
    structMember
  )* EOF;

interactiveBlockUnit
  : (
    interactiveBlockItem
  )* EOF;

interactiveDoWhileUnit
  : (
    interactiveDoWhileWhileStatement
  )* EOF;

interactiveIfElseUnit
  : (
    interactiveElseStatement
  )* EOF;



interactiveCatchClauseUnit
  : (
    interactiveCatchClause
  )* EOF;

intentUnit
    : ( debugInput
      | duringIntent
      | postIntent
      ) EOF
    ;
debugInput
    : debugGlobalVar
    | debugStateVar
    | debugParam
    | debugIReturn
    ;

debugGlobalVar
    : '@GlobalVar' intentIdentifier ('.' intentMemberName)? '=' debugValue                          # GlobalVarSimple
    | '@GlobalVar' 'address' '(' intentIdentifier ')' '.' intentMemberName '=' debugValue            # GlobalVarAddressBalance
    ;

debugStateVar
    : '@StateVar' varRef '=' debugValue
    ;

debugParam
    : '@Param' varRef '=' debugValue
    ;

// (2026-11-27) 수신자 일반화 — Interpreter/Semantics/IReturnKey.py 의 키 규칙과 같다.
//   A: 수신자 = 경로(식별자·멤버·index)          `token.f()`, `s.token.f()`, `pools[pid].f()`
//   B: 수신자 = 캐스트, 안은 경로 또는 비움(`IFace()` = 원문 키로 이름 붙지 않는 그 인터페이스의 호출 전부)
debugIReturn
    : '@IReturn' varRef '.' intentMemberName '(' ')' ireturnAccessChain? '=' debugValue        # IReturnPatternA
    | '@IReturn' intentIdentifier '(' varRef? ')' '.' intentMemberName '(' ')' ireturnAccessChain? '=' debugValue   # IReturnPatternB
    ;

ireturnAccessChain
    : ireturnAccess+
    ;

ireturnAccess
    : '.' intentMemberName      # IReturnMemberAccess
    | '[' numberLiteral ']'     # IReturnIndexAccess
    | '.' intentMemberName '(' ')'   # IReturnChainedCall
    ;

duringIntent
    : '@During' {self.inDuring = True;} duringClause (logicOp duringClause)*
    ;

duringClause
    : ('require feasible' | 'assert feasible')                              # DuringFeasible
    | identifier '.' ARG_KW '[' numberLiteral ']' relOp intentValue          # DuringFunctionArg
    | commonClause                                                          # DuringCommon
    ;

postIntent
    : '@Post' {self.inDuring = False;} postClause (logicOp postClause)*
    ;

postClause
    : commonClause                                                        # PostCommon
    ;

commonClause
    : RETURN_EXPR_KW relOp intentValue                                  # ReturnExprCmp
    | 'return' '[' numberLiteral ']' relOp intentValue                      # ReturnIndexCmp
    | 'return' intentValue relOp intentValue                                # ReturnVarCmp
    | intentValue relOp PERCENT_OF_KW '(' intentValue ',' numberLiteral ')'   # PercentOf
    | intentValue relOp CEIL_KW '(' intentValue ',' numberLiteral ')'        # Ceil
    | intentValue relOp FLOOR_KW '(' intentValue ',' numberLiteral ')'       # Floor
    | intentValue relOp intentValue                                         # RelationalCmp
    | intentValue '=>' intentValue                                          # Implication
    | CHANGED_KW '(' intentValue ',' ('true'|'false') ')'                    # VarChangedEval
    ;

debugValue
    : '[' signedNumberLiteral ',' signedNumberLiteral ']'                          # DebugIntInterval
    | SYM_ADDRESS_KW numberLiteral                                              # DebugSymbolicAddress
    | SYM_BYTES_KW   hexStringLiteral                                           # DebugSymbolicBytes
    | SYM_STRING_KW  hexStringLiteral                                           # DebugSymbolicString
    | ('true' | 'false' | ANY_KW)                                                   # DebugBoolToken
    | identifier ('.' identifier)*                                                 # DebugEnumLiteral
    | ARRAY_KW '[' ( signedNumberLiteral (',' signedNumberLiteral)* )? ']'         # DebugIntArray
    | ARRAY_ADDRESS_KW '[' ( numberLiteral (',' numberLiteral)* )? ']'              # DebugAddressArray
    ;

intentValue
    : arithExpr
    ;

//────────── 6-1)  숫자 / 비율 / 모듈러 ──────────
arithExpr
    : arithExpr ('<<' | '>>' | '>>>') arithAdd     #IntentShiftOp
    | arithAdd                                     #IntentShiftRoot
    ;

arithAdd
    : arithAdd ('+' | '-') arithTerm               #AddSub
    | arithTerm                                    #AddSubRoot
    ;

arithTerm
    : arithTerm ('*' | '/' | '%') arithExp         #MulDivMod
    | arithExp                                     #MulDivModRoot
    ;

arithExp
    : arithFactor '**' arithExp                    #IntentExponentiation
    | arithFactor                                  #IntentExpRoot
    ;

arithFactor
    : signedNumberLiteral                                       #NumLiteral
    | '[' signedNumberLiteral ',' signedNumberLiteral ']'       #InlineInterval
    | {not self.inDuring}? varRef '(' ENTRY ')'                 #VarRefAtEntry
    | {not self.inDuring}? varRef '(' EXIT ')'                  #VarRefAtExit
    | {self.inDuring}?  varRef '(' BEFORE ')'                   #VarRefAtBefore
    | {self.inDuring}?  varRef '(' AFTER ')'                    #VarRefAtAfter
    | {self.inDuring}?  varRef '(' ASSIGN ')'                   #VarRefAtAssign
    | varRef                                                    #NumVarRef
    | '(' arithExpr ')'                                         #Parenthesized
    ;

//────────── 변수·멤버·인덱스 참조 (arithFactor에서 사용) ──────────
varRef
    : intentIdentifier subAccess*                       #NormalVarRef
    ;

subAccess
    : '.' intentMemberName      #IntentMemberAccess
    | '[' expression ']'        #IntentIndexAccess
    ;

//==================================================
// 7)  기타 공통
//--------------------------------------------------
signedNumberLiteral : '-'? numberLiteral ;
logicOp : '&&' | '||' ;
relOp   : '<' | '>' | '<=' | '>=' | '==' | '!=' | 'in' | NOT_KW 'in' ;

interactiveSimpleStatement
  : ( interactiveVariableDeclarationStatement | interactiveExpressionStatement ) ;

interactiveVariableDeclarationStatement
  : (variableDeclaration ('=' expression)?) ';'
  | (variableDeclarationTuple '=' expression) ';' ;

interactiveExpressionStatement
  : expression ';' ;

// for interactive parsing
interactiveStateVariableElement
  : interactiveEnumDefinition
  | interactiveStructDefinition
  | stateVariableDeclaration
  | userDefinedValueTypeDefinition
  | usingDirective
  | constantVariableDeclaration ;

// for interactive parsing
interactiveEnumDefinition
  : 'enum' identifier '{' '}' ;

interactiveStructDefinition
  : 'struct' identifier '{' '}' ;

interactiveEnumItems
  : identifier (',' identifier)*;

interactiveFunctionElement
  : constructorDefinition
  | eventDefinition
  | errorDefinition
  | functionDefinition
  | fallbackFunctionDefinition
  | modifierDefinition
  | receiveFunctionDefinition ;

interactiveBlockItem
  : interactiveStatement | uncheckedBlock;

dataLocation
  : 'memory' | 'storage' | 'calldata';

stateMutability
  : PureKeyword | ViewKeyword | PayableKeyword ;

block
  : '{' (statement|uncheckedBlock)* '}' ;

uncheckedBlock
  : 'unchecked' block ;

statement
  : block
  | simpleStatement
  | ifStatement
  | forStatement
  | whileStatement
  | doWhileStatement
  | continueStatement
  | breakStatement
  | tryStatement
  | returnStatement
  | emitStatement
  | revertStatement
  | requireStatement
  | assertStatement
  | assemblyStatement;

expressionStatement
  : expression ';' ;

ifStatement
  : 'if' '(' expression ')' statement ( 'else' statement )? ;

tryStatement
  : 'try' expression
  ('returns' '(' parameterList ')')? block catchClause+ ;

// In reality catch clauses still are not processed as below
// the identifier can only be a set string: "Error". But plans
// of the Solidity team include possible expansion so we'll
// leave this as is, befitting with the Solidity docs.
catchClause : 'catch' ( identifier? '(' parameterList ')' )? block ;

whileStatement
  : 'while' '(' expression ')' statement ;

simpleStatement
  : variableDeclarationStatement # VDContext
  | expressionStatement # EContext ;

forStatement
  : 'for' '(' ( simpleStatement | ';' ) ( expressionStatement | ';' ) expression? ')' statement ;

inlineArrayExpression
 : '[' (expression (',' expression)*) ']';

// Assembly 블록: 내부 Yul 코드는 별도 Yul.g4 파서가 처리
// Solidity parser는 balanced brace만 소비
assemblyStatement
  : 'assembly' assemblyFlags? assemblyBlock ;

assemblyFlags
  : '(' (assemblyFlagString (',' assemblyFlagString)) ')';

assemblyFlagString
 : stringLiteral ;

assemblyBlock
  : '{' (~('{' | '}') | assemblyBlock)* '}' ;

doWhileStatement
  : 'do' statement 'while' '(' expression ')' ';' ;

continueStatement
  : 'continue' ';' ;

breakStatement
  : 'break' ';' ;

returnStatement
  : 'return' expression? ';' ;

emitStatement
  : 'emit' expression callArgumentList ';' ;

revertStatement // must be modified in solidity language grammar
  : 'revert' expression callArgumentList ';'   // 2026-11-13: 공식 문법 그대로 (custom error, 경로 포함)
  | 'revert' '(' expression? ')' ';' ;         // 도구 확장: 공식 문법에서 내장 revert(...) 는 식 문장이다

requireStatement
  : 'require' '(' expression (',' expression)? ')' ';' ;

assertStatement
  : 'assert' '(' expression ')' ';' ;

variableDeclarationStatement
  : (variableDeclaration ('=' expression)?) ';' | (variableDeclarationTuple '=' expression) ';' ;

interactiveStatement
  : interactiveSimpleStatement
  | interactiveIfStatement
  | interactiveForStatement
  | interactiveWhileStatement
  | interactiveDoWhileDoStatement
  | continueStatement // 일반적인 파서와 동일
  | breakStatement // 일반적인 파서와 동일
  | interactiveTryStatement
  | returnStatement // 일반적인 파서와 동일
  | emitStatement // 일반적인 파서와 동일
  | revertStatement // 일반적인 파서와 동일
  | requireStatement
  | assertStatement
  | assemblyStatement; // assembly는 추후 확장 하는걸로 하자 일단

interactiveIfStatement
  : 'if' '(' expression ')' '{' '}' ;

// else if도 포함되어 있음
interactiveElseStatement
  : 'else' (interactiveIfStatement | '{' '}') ;

interactiveForStatement
  : 'for' '(' ( simpleStatement | ';' ) ( expressionStatement | ';' ) expression? ')' '{' '}' ;

interactiveWhileStatement
  : 'while' '(' expression ')' '{' '}' ;

interactiveDoWhileDoStatement
  : 'do' '{' '}' ;

interactiveDoWhileWhileStatement
  : 'while' '(' expression ')' ';' ;

interactiveTryStatement
  : 'try' expression
  ('returns' '(' parameterList ')')? '{' '}' ;

interactiveCatchClause
  : 'catch' ( identifier? '(' parameterList ')' )? '{' '}' ;

elementaryTypeName
  : 'address' | 'address payable' | 'bool' | 'string' | 'var' | Int | Uint | 'bytes' | Byte | Fixed | Ufixed ;

Int
  : 'int' | 'int8' | 'int16' | 'int24' | 'int32' | 'int40' | 'int48' | 'int56' | 'int64' | 'int72' | 'int80' | 'int88' | 'int96' | 'int104' | 'int112' | 'int120' | 'int128' | 'int136' | 'int144' | 'int152' | 'int160' | 'int168' | 'int176' | 'int184' | 'int192' | 'int200' | 'int208' | 'int216' | 'int224' | 'int232' | 'int240' | 'int248' | 'int256' ;

Uint
  : 'uint' | 'uint8' | 'uint16' | 'uint24' | 'uint32' | 'uint40' | 'uint48' | 'uint56' | 'uint64' | 'uint72' | 'uint80' | 'uint88' | 'uint96' | 'uint104' | 'uint112' | 'uint120' | 'uint128' | 'uint136' | 'uint144' | 'uint152' | 'uint160' | 'uint168' | 'uint176' | 'uint184' | 'uint192' | 'uint200' | 'uint208' | 'uint216' | 'uint224' | 'uint232' | 'uint240' | 'uint248' | 'uint256' ;

Byte
  : 'bytes' | 'bytes1' | 'bytes2' | 'bytes3' | 'bytes4' | 'bytes5' | 'bytes6' | 'bytes7' | 'bytes8' | 'bytes9' | 'bytes10' | 'bytes11' | 'bytes12' | 'bytes13' | 'bytes14' | 'bytes15' | 'bytes16' | 'bytes17' | 'bytes18' | 'bytes19' | 'bytes20' | 'bytes21' | 'bytes22' | 'bytes23' | 'bytes24' | 'bytes25' | 'bytes26' | 'bytes27' | 'bytes28' | 'bytes29' | 'bytes30' | 'bytes31' | 'bytes32' ;

Fixed
  : 'fixed' | ( 'fixed' [0-9]+ 'x' [0-9]+ ) ;

Ufixed
  : 'ufixed' | ( 'ufixed' [0-9]+ 'x' [0-9]+ ) ;

expression
  : expression '[' expression? ']'                        # IndexAccess
  | expression '[' expression? ':' expression? ']'        # IndexRangeAccess
  | expression '.' (identifier | 'address')               # MemberAccess
  | expression '{' (identifier ':' expression (',' identifier ':' expression)*)? '}'  # FunctionCallOptions
  | elementaryTypeName '(' expression ')'                 # TypeConversion
  | expression callArgumentList                           # FunctionCall
  | PayableKeyword callArgumentList                       # PayableFunctionCall
  | 'type' '(' typeName ')'                               # MetaType
  | ('++'|'--'|'!'|'~'|'delete'|'-') expression           # UnaryPrefixOp
  | expression ('++'|'--')                                # UnarySuffixOp
  | expression '**' expression                            # Exponentiation
  | expression ('*'|'/'|'%') expression                   # MultiplicativeOp
  | expression ('+' | '-') expression                     # AdditiveOp
  | expression ('<<' | '>>' | '>>>') expression           # ShiftOp
  | expression '&' expression                             # BitAndOp
  | expression '^' expression                             # BitXorOp
  | expression '|' expression                             # BitOrOp
  | expression ('<' | '>' | '<=' | '>=') expression       # RelationalOp
  | expression ('==' | '!=') expression                   # EqualityOp
  | expression '&&' expression                            # AndOperation
  | expression '||' expression                            # OrOperation
  | expression '?' expression ':' expression              # ConditionalExp
  | expression ('=' | '|=' | '^=' | '&=' | '<<=' | '>>=' | '>>>=' | '+=' | '-=' | '*=' | '/=' | '%=') expression  # Assignment
  | 'new' typeName                                        # NewExp
  | tupleExpression                                       # TupleExp
  | inlineArrayExpression                                 # InlineArrayExp
  | identifier                                            # IdentifierExp
  | literal                                               # LiteralExp
  | literalWithSubDenomination                            # LiteralSubDenomination
  | elementaryTypeName                                    # TypeNameExp
  ;

literal
  : (stringLiteral | numberLiteral | booleanLiteral | hexStringLiteral | unicodeStringLiteral);

literalWithSubDenomination
  : numberLiteral SubDenomination ;

tupleExpression
  : '(' expression? (',' expression?)* ')' ;

numberLiteral
  : (DecimalNumber | HexNumber) ;

// some keywords need to be added here to avoid ambiguities
// for example, "revert" is a keyword but it can also be a function name

intentIdentifier          // intent·디버그 입력에서 이름이 올 자리. 코드와 겹치는 intent 키워드도 이름으로 받는다.
  : identifier | ARRAY_KW | ARRAY_ADDRESS_KW | ANY_KW | ARG_KW | CEIL_KW | FLOOR_KW | CHANGED_KW | NOT_KW
  | RETURN_EXPR_KW | PERCENT_OF_KW | SYM_ADDRESS_KW | SYM_BYTES_KW | SYM_STRING_KW
  | BEFORE | AFTER | ENTRY | EXIT | ASSIGN ;

intentMemberName          // `.` 뒤 멤버 이름 — arg 는 `f.arg[i]`(DuringFunctionArg) 와 겹치므로 뺀다
  : identifier | ARRAY_KW | ARRAY_ADDRESS_KW | ANY_KW | CEIL_KW | FLOOR_KW | CHANGED_KW | NOT_KW
  | RETURN_EXPR_KW | PERCENT_OF_KW | SYM_ADDRESS_KW | SYM_BYTES_KW | SYM_STRING_KW
  | BEFORE | AFTER | ENTRY | EXIT | ASSIGN ;

identifier
  : Identifier
  | 'from'     // 2026-11-09: Solidity 에서 from 은 import 에만 쓰이는 문맥 키워드다 (address from 합법)
  | 'error' ;  // 2026-11-13: error 도 errorDefinition 에만 쓰이는 문맥 키워드다 (uint256 error 합법)

// Temporal keywords for intent verification
// These are reserved to prevent ambiguity in temporal predicates
// intent 전용 키워드 (B'): intent 줄(intentMode=True)에서만 키워드, 코드 줄에서는 Identifier 로 바뀐다.
// 반드시 Identifier 규칙보다 앞에 둔다 — 같은 길이면 먼저 선언된 규칙이 이긴다.
BEFORE : 'Before' {self.type = SolidityLexer.BEFORE if self.intentMode else SolidityLexer.Identifier} ;
AFTER : 'After' {self.type = SolidityLexer.AFTER if self.intentMode else SolidityLexer.Identifier} ;
ENTRY : 'Entry' {self.type = SolidityLexer.ENTRY if self.intentMode else SolidityLexer.Identifier} ;
EXIT : 'Exit' {self.type = SolidityLexer.EXIT if self.intentMode else SolidityLexer.Identifier} ;
ASSIGN : 'Assign' {self.type = SolidityLexer.ASSIGN if self.intentMode else SolidityLexer.Identifier} ;
ARRAY_KW : 'array' {self.type = SolidityLexer.ARRAY_KW if self.intentMode else SolidityLexer.Identifier} ;
ARRAY_ADDRESS_KW : 'arrayAddress' {self.type = SolidityLexer.ARRAY_ADDRESS_KW if self.intentMode else SolidityLexer.Identifier} ;
ANY_KW : 'any' {self.type = SolidityLexer.ANY_KW if self.intentMode else SolidityLexer.Identifier} ;
ARG_KW : 'arg' {self.type = SolidityLexer.ARG_KW if self.intentMode else SolidityLexer.Identifier} ;
CEIL_KW : 'ceil' {self.type = SolidityLexer.CEIL_KW if self.intentMode else SolidityLexer.Identifier} ;
FLOOR_KW : 'floor' {self.type = SolidityLexer.FLOOR_KW if self.intentMode else SolidityLexer.Identifier} ;
CHANGED_KW : 'changed' {self.type = SolidityLexer.CHANGED_KW if self.intentMode else SolidityLexer.Identifier} ;
NOT_KW : 'not' {self.type = SolidityLexer.NOT_KW if self.intentMode else SolidityLexer.Identifier} ;
RETURN_EXPR_KW : 'returnExpression' {self.type = SolidityLexer.RETURN_EXPR_KW if self.intentMode else SolidityLexer.Identifier} ;
PERCENT_OF_KW : 'PercentOf' {self.type = SolidityLexer.PERCENT_OF_KW if self.intentMode else SolidityLexer.Identifier} ;
SYM_ADDRESS_KW : 'symbolicAddress' {self.type = SolidityLexer.SYM_ADDRESS_KW if self.intentMode else SolidityLexer.Identifier} ;
SYM_BYTES_KW : 'symbolicBytes' {self.type = SolidityLexer.SYM_BYTES_KW if self.intentMode else SolidityLexer.Identifier} ;
SYM_STRING_KW : 'symbolicString' {self.type = SolidityLexer.SYM_STRING_KW if self.intentMode else SolidityLexer.Identifier} ;

userDefinedValueTypeDefinition
  : 'type' identifier 'is' elementaryTypeName ';' ;

booleanLiteral
  : 'true' | 'false' ;

hexStringLiteral
  : HexString+ ;

unicodeStringLiteral
  : UnicodeStringLiteral+;

stringLiteral
  : (NonEmptyStringLiteral|EmptyStringLiteral)+ ;

overrideSpecifier
  : 'override' ( '(' identifierPath (',' identifierPath)* ')' )? ;

DecimalNumber
  : ( DecimalDigits | (DecimalDigits? '.' DecimalDigits) ) ( [eE] '-'? DecimalDigits )? ;

fragment
DecimalDigits
  : [0-9] ( '_'? [0-9] )* ;

SubDenomination
  : 'wei' | 'gwei' | 'ether' | 'seconds' | 'minutes'
  | 'hours' | 'days' | 'weeks' | 'years' ;

HexString
  : 'hex'
 ( '"' ([0-9A-Fa-f] [0-9A-Fa-f] '_'? )* '"'
 | '\''([0-9A-Fa-f] [0-9A-Fa-f] '_'? )* '\'') ;

HexNumber
  : '0x' ([0-9A-Fa-f] '_'? )* ;

ReservedKeyword
  : 'abstract'
  | 'after'
  | 'case'
  | 'catch'
  | 'default'
  | 'final'
  | 'in'
  | 'inline'
  | 'let'
  | 'match'
  | 'null'
  | 'of'
  | 'relocatable'
  | 'static'
  | 'switch'
  | 'try'
  | 'typeof' ;

AnonymousKeyword : 'anonymous' ;
BreakKeyword : 'break' ;
ConstantKeyword : 'constant' ;
ImmutableKeyword : 'immutable' ;
ContinueKeyword : 'continue' ;
LeaveKeyword : 'leave' ;
ExternalKeyword : 'external' ;
IndexedKeyword : 'indexed' ;
InternalKeyword : 'internal' ;
PayableKeyword : 'payable' ;
PrivateKeyword : 'private' ;
PublicKeyword : 'public' ;
VirtualKeyword : 'virtual' ;
PureKeyword : 'pure' ;
TypeKeyword : 'type' ;
ViewKeyword : 'view' ;
GlobalKeyword : 'global' ;

ConstructorKeyword : 'constructor' ;
FallbackKeyword : 'fallback' ;
ReceiveKeyword : 'receive' ;

Identifier
  : IdentifierStart IdentifierPart* ;

fragment
IdentifierStart
  : [a-zA-Z$_] ;

fragment
IdentifierPart
  : [a-zA-Z0-9$_] ;

NonEmptyStringLiteral
  : '"' (DoubleQuotedPrintable | EscapeSequence)+ '"'
  | '\'' (SingleQuotedPrintable | EscapeSequence)+ '\'';

EmptyStringLiteral
  : '"' '"'
  | '\'' '\'';

UnicodeStringLiteral
  : 'unicode'
  ( '"' (~["\r\n\\] | EscapeSequence)* '"'
  | '\'' (~['\r\n\\] | EscapeSequence)* '\'');

fragment
DoubleQuotedPrintable
  : [\u0020-\u0021\u0023-\u005B\u005D-\u007E] ;

fragment
SingleQuotedPrintable   // 2026-11-13: 공식 문법대로 작은따옴표(0x27)를 뺀다. 예전엔 큰따옴표 범위를 복사해 'a', 'b' 가 한 토큰이 됐다
  : [\u0020-\u0026\u0028-\u005B\u005D-\u007E];

fragment
EscapeSequence
 : '\\'
 ( ['"\\nrt\n\r]
 | 'u' [0-9A-Fa-f] [0-9A-Fa-f] [0-9A-Fa-f] [0-9A-Fa-f]
 | 'x' [0-9A-Fa-f] [0-9A-Fa-f]);

VersionLiteral
  : [0-9]+ '.' [0-9]+ ('.' [0-9]+)? ;

WS
  : [ \t\r\n\u000C]+ -> skip ;

// Yul lexer rules removed — Yul.g4가 별도 처리

COMMENT
  : '/*' .*? '*/' -> channel(HIDDEN) ;

// 라인 주석 복원(2026-07-18). 과거 제거했던 이유 = 최장일치로 `// @During ...` 한 줄을
// 통째로 삼켜 intentUnit 이 매치되지 않았기 때문. 이제 어노테이션 규칙에서 '//' 를 없애
// (intent/debug 값은 소스 주석이 아니라 API 입력으로 전달) 충돌이 사라져 그냥 복원 가능.
// 이게 없으면 `// SPDX-License-Identifier` 로 시작하는 실제 .sol 이 sourceUnit 파스에서
// 거부되어 증분 편집 게이트(incremental.syntax_valid)가 항상 실패한다.
LINE_COMMENT
  : '//' ~[\r\n]* -> channel(HIDDEN) ;

