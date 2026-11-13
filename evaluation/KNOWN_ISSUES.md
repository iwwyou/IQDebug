# 알려진 엔진 오류

구현 세션 소유. 재현이 확인됐지만 아직 고치지 않은 오류를 적는다. 지원 범위 밖으로 **결정한** 구문은 여기가
아니라 `evaluation/reviews/round9_operations.md` §9 에 있다. 할 일 순서는 `evaluation/ROADMAP.md`.

수치 기준: 타깃 486(`evaluation/Dataset/targets_final.json`), 2026-11-13 빌드·파일럿.
번호는 바꾸지 않는다. 해결된 항목은 맨 아래 "해결됨" 으로 옮기고 번호를 남긴다.

---

## I1. 증분 `update` 가 `⊥` 를 낸다 — 조용한 오분석

**증상.** 앞에 문장을 추가하면서 뒤 문장을 지우는 편집을 한 번의 `update` 로 넣으면 EXIT 값이 `⊥` 다. 같은
소스를 새로 분석하면 정상이다. 예외도 진단도 없다.

```solidity
// 이전                               // 이후
if (n > 5) {                          total = 9;
    total = 1;                        if (n > 5) {
}                                         total = 1;
total = total + 2;                    }
```

`n=[0,10], total=[0,0]` 에서 `update` 후 `⊥`, 새 분석 `[1,9]`.

**확인한 것.** 한 줄·여러 줄 조건 모두 재현된다. 2026-11-06 의 `_insert_lines` 변경 전에도 재현됐다(무관).
앞에 추가만 하거나 뒤를 지우지 않으면 정상이다. **원인은 조사하지 않았다.** `update` 가 modify → delete →
add 순으로 적용하므로 그 순서와 CFG 수술의 연결부터 볼 것(`engine_api.update`). I3 의 줄 메타 불일치와
관련이 있을 수 있다.

**재현.** `temp/test_multiline.py` 의 `EDITS` 에 이 쌍을 넣으면 실패한다.

---

## I3. constructor 의 EXIT 줄이 따라오지 않는다 — S2 위반 153 함수

**증상.** constructor 본문이 있으면 EXIT 의 `src_line` 이 닫는 `}` 가 아니라 **첫 문장 줄**에 남아 착지 노드 둘이
한 줄에 놓인다. 일반 함수·modifier·fallback·receive 는 정상이다.

**원인 (2026-11-13 확정, 2026-11-06 의 추정을 정정).** EXIT 는 함수와 constructor 모두 `line_info` 의 `}` 줄에
**등록된다**(`ContractAnalyzer.py` 1345–1356, 1398–1408). 문제는 줄 이동 순서다. `_insert_lines`·delete 는 줄마다
`_shift_source_meta(old,new)` 로 `line_info[old]` 를 먼저 `new` 로 옮긴 뒤 `_shift_cfg_meta(old,new)` 를 부른다.
그래서 `_shift_cfg_meta` ①(“`line_info[old]` 에 등록된 노드의 `src_line` 보정”)은 **항상 조건이 거짓인 죽은
코드**다(계측: 모든 호출에서 `old in line_info` 가 False). `src_line` 은 ③(`iter_all_functions()` 전수 스캔)으로만
갱신되는데, constructor 는 `ccf.functions` 가 아니라 `ccf.constructor` 에 있어 ③ 에서 빠진다. 결과적으로
`line_info` 키는 맞는 `}` 줄, `src_line` 은 첫 문장 줄로 두 저장소가 어긋난다.

**더 넓은 문제.** 노드의 줄 위치가 `line_info` 키, `CFGNode.src_line`, `Statement.src_line`, `recorder.ledger`,
노드 이름(`Assign_4`)에 따로 저장된다. 작은 계약 실측에서 constructor 외에도 `catch_join`(src_line 20, line_info
22), 두 줄에 등록된 `if_join`, `line_info` 에 없는 `if_true`·`for_init`·`try_true`·`catch_true` 가 있었다.

**상태.** 국소 수정(① 이 `line_info[new]` 를 보게 하기 → 파일럿 S2 138 → 1, 또는 ③ 에 constructor 추가)은
중복이 남아 임시방편으로 판단해 보류했다(2026-11-13 사용자). 근본 설계를 외부 검토 중이다.

---

## I5. 빌드·분석 실패 — 원인 미분류

2026-11-13 빌드에서 남은 항목. 개별 조사 전이다.

- 파일 수준 struct 미해석: `ReserveConfigurationMap` 24 단위, `AdditionalRecipient` 5.
- `numeric literal "0.95" does not evaluate to an integer` 10 단위. 상수 식 평가.
- `Unhandled expression context` 약 10 단위. 예: `if` 줄, 단순 문장.
- `Type '…' is not defined as struct, enum, or contract` (`PotLike`, `Node`, `PoolInfo`, `RiskItem`, `BondData` …).
  부모 컨트랙트의 struct 로 보인다.
- `Function context not found` / `Contract context not found` 중 I7 이 아닌 것 51 단위(35 타깃). 예:
  `ErrorReporter` 의 여러 줄 enum 뒤 `event Failure(uint error, …);`.

---

## I6. 여러 줄 문장 안의 라인 주석이 뒤를 삼킨다 — 에디터 경로

**증상.** 슬라이서가 줄을 공백으로 잇는데 `//` 를 지우지 않아 `g(n, // first` ⏎ `1, 2);` 가 `function(⊤)` 이 된다. 예외가
없다. 데이터셋 경로는 `normalize_source` 가 주석을 비워 해당 없다. **에디터 입력만** 남는다.

solc 컴파일 게이트로는 막을 수 없다. 이 소스는 합법이기 때문이다. `engine_api.Diagnostic` 주석에 따르면 solc
게이트는 구현됐다가 꺼진 것이 아니라 **보류**된 상태고 코드가 없다.

---

## I7. 본문이 빈 한 줄 함수 뒤의 선언이 실패한다 — 24 단위 (24 타깃)

**증상.** `function g() external {}` / `constructor() {}` / `receive() external payable {}` 처럼 `{}` 가 한 줄에 있는
함수 **뒤에** 다른 함수가 오면 그 함수가 `Contract context not found for line N` 으로 실패한다. 같은 줄을
`{` ⏎ `}` 로 펼치면 정상이다. 함수가 마지막이면 정상이다.

**원인(추정).** 슬라이서가 `{}` 한 줄을 **1줄짜리 레코드**(startLine = endLine)로 내고, 나머지 블록 헤더는
`헤더 ⏎ }` 2줄 레코드로 낸다. 1줄 레코드 경로에서 컨트랙트 범위·괄호 집계가 어긋나는 것으로 보인다. 미검증.

**대응 선택지(미결정).** 정규화가 빈 블록도 펼치면 데이터셋은 해결되지만 에디터 경로의 엔진 버그는 남는다.

---

## I8. `} catch {}` 줄에 착지 노드 둘 — S2 위반 1 함수

**증상.** `try x() {` ⏎ `} catch {}` 에서 `try_join` 과 `catch_cond` 가 같은 줄에 착지 가능하다
(`IncentiveController.updateOracle`). `} catch {` ⏎ `}` 로 펼치면 정상이다. 원본 `try x() {} catch {}` 를 정규화가
펼친 결과다. I7 과 같은 "빈 블록 한 줄" 계열이다.

---

## I9. 코드 줄의 ANTLR 구문 오류가 콘솔에만 찍힌다 — 조용한 오분석의 통로

**증상.** `ParserHelpers.generate_parse_tree` 는 코드 줄에 `ConsoleErrorListener` 만 붙인다. 구문 오류가 나면 콘솔에
한 줄 찍고, ANTLR 이 복구한 트리를 방문자가 정상 입력처럼 분석한다. 어노테이션 줄은 반대로
`AnnotationSyntaxError` 로 멈춘다.

**예.** 문법 수정 전 `revert Errors.NotGov();` 는 `.` 에서 오류가 났고, 복구된 트리가 `revert Errors` + `.NotGov()`
호출 두 문장이 되어 노드 둘이 생긴 뒤 `evaluate` 에서 죽었다. 진단은 없었다.

**해결 방향(제안, 미착수).** 코드 줄도 구문 오류를 모아 `SourceError` 로 올린다. 그 전에 데이터셋에서 구문 오류가
나는 줄 수를 측정한다 — 지금 "오류를 낸 채 통과하던" 줄이 실패로 바뀐다.

---

## I10. Yul `x.slot` 등이 렉싱 오류를 낸다

**증상.** 파일럿 출력에 `token recognition error at: '.'` 가 약 25 줄 나온다. assembly 안의 `_preBytes.slot`,
`storageData.slot` 같은 Yul 멤버 접근을 Yul 렉서가 인식하지 못하는 것으로 보인다. I9 와 같은 경로로 분석은 계속된다.
미조사.

---

## 해결됨

### I2. 식별자 `from` 이 키워드로 파싱된다 — ✅ 2026-11-09

`identifier` 에 `'from'` 을 추가했다. 같은 문맥 키워드 `'error'`(`uint256 error`)도 2026-11-13 추가.

### I4. `revert CustomError()` 가 한 줄에 착지 노드 둘을 만든다 — ✅ 2026-11-13

원인은 `revert` 일반이 아니라 경로가 붙은 `revert Errors.X()` 였다. 문법이 `revert identifier …` 만 받아 I9 경로로
두 문장이 됐다. `revertStatement` 를 공식 문법 `'revert' expression callArgumentList ';'` + 도구 확장
`'revert' '(' expression? ')' ';'`(내장 `revert(...)`)로 바꿨다. 오류 이름은 저장만 되고 해석되지 않으므로
분석기·빌더·엔진은 고칠 것이 없었다.

### I11. 작은따옴표 문자열과 상속 지정 인자 — ✅ 2026-11-13

`SingleQuotedPrintable` 이 큰따옴표 범위를 복사해 `'a', 'b'` 가 한 토큰이 됐다(`x > '9' && y < 'a'` 가 조건 하나로
조용히 바뀜). 공식 범위로 고쳤다. `inheritanceSpecifier` 가 괄호를 두 번 요구하던 것(`ERC20(('a'))`)도 공식
`identifierPath callArgumentList?` 로 고쳤다.

### I12. intent 전용 키워드가 Solidity 이름과 충돌한다 — ✅ 2026-11-13

`array`(159 토큰)·`not`·`Entry` 등이 코드에서도 키워드로 렉싱됐다. 렉서 action(B′)으로 `intentMode=False` 일 때
토큰 타입을 `Identifier` 로 바꾸고, intent 의 이름 자리에는 `intentIdentifier`·`intentMemberName` 을 둔다(`arg` 는
`f.arg[i]` 와 겹쳐 멤버 자리에서 뺀다). 렉서는 `ParserHelpers.new_lexer(src, intent=…)` 로만 만든다. `Current`·
`Unchanged` 토큰은 삭제했다. 회귀: `temp/test_intent_keywords.py`. 외부 검토 반영.
