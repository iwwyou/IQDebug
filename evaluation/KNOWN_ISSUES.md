# 알려진 엔진 오류

구현 세션 소유. 재현이 확인됐지만 아직 고치지 않은 오류를 적는다. 지원 범위 밖으로 **결정한** 구문은 여기가
아니라 `evaluation/reviews/round9_operations.md` §9 에 있다. 할 일 순서는 `evaluation/ROADMAP.md`.

수치 기준(I3~I16): 타깃 486(`evaluation/Dataset/targets_final.json`, 2026-11-19 폐기 · `temp/dataset_legacy/` 로 옮김), 2026-11-13 빌드·파일럿.
새 데이터셋(415 파일, `evaluation/Dataset/target_functions.json`) 기준 재측정은 `deps/` 재빌드 뒤에 한다.
번호는 바꾸지 않는다. 해결된 항목은 맨 아래 "해결됨" 으로 옮기고 번호를 남긴다.

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
중복이 남아 임시방편으로 판단해 보류했다(2026-11-13 사용자). 근본 설계(SourceMap·LineAnchor)를 외부 검토했고
합의된 이행 순서는 ① I1 분리 수정(완료) ② 불변식·죽은 앵커 후보·증분↔새분석 차분 하네스 ③ Shadow SourceMap
④ SourceMap + 호환 View ⑤⑥ 노드·문장 앵커 + 죽은 앵커 재바인딩(같은 브랜치) ⑦ 평가 캐시 수명(ledger 의 문장 없는
줄·`_states_cache`) ⑧ 차분 하네스 재실행. `before_envs`·`return_vals` 는 평가마다 실행 사본에서 새로 만들어지고
(`Engine.interpret_function_cfg_for_debug`), `ContractAnalyzer.during_annotations`·`post_annotations` 는 쓰기만 하고
읽는 곳이 없다 — 둘 다 앵커 대상이 아니다.

---

## I6. 여러 줄 문장 안의 라인 주석이 뒤를 삼킨다 — 에디터 경로

**증상.** 슬라이서가 줄을 공백으로 잇는데 `//` 를 지우지 않아 `g(n, // first` ⏎ `1, 2);` 가 `function(⊤)` 이 된다. 예외가
없다. 데이터셋 경로는 `normalize_source` 가 주석을 비워 해당 없다. **에디터 입력만** 남는다.

solc 컴파일 게이트로는 막을 수 없다. 이 소스는 합법이기 때문이다. `engine_api.Diagnostic` 주석에 따르면 solc
게이트는 구현됐다가 꺼진 것이 아니라 **보류**된 상태고 코드가 없다.

**더 넓은 증상 (2026-11-16 관찰).** 여러 줄 문장만이 아니다. 문장 **뒤에 붙은** 줄 주석도 같다. `total = 2; // c` 는
줄이 `;` 로 끝나지 않아 다음 줄들과 합쳐져, `total = 2; // c } g(1, 2);` 한 레코드(9–12행)가 됐다 — 닫는 `}` 와
다음 문장까지 삼켰다. 에디터 입력에서는 흔한 형태다. 미수정.

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

## I16. 변수를 안 쓰게 된 뒤에도 증분 쪽에 이전 입력이 남는다 — 의심, 미확인

`require(n > 3); total = 1;` → `require(n > 3);` 처럼 `total` 을 더 이상 참조하지 않게 되면, 새 분석은 `total` 슬롯이
없어 EXIT `total` 이 ⊤ 인데 증분은 입력값 `[0,0]` 이다. 이전 소스의 슬롯이나 `related_variables` 가 `update` 뒤에도
남는 것으로 보인다. 조사 전.

---

## I19. evaluate 실패 — 의존성이 풀린 뒤 드러난 것 (2026-11-25 표본, 함수 521 중 196)

표본 = 5 번째 파일마다(파일 83). 많은 순. 원인 조사 전이다.

1. `@IReturn: 'aclManager' is not an interface-typed variable` — 30 (Aave).
2. `using-for lookup: static type of receiver of '.mul(...)' is unknown` — 15.
3. `Cannot join intervals of different type lengths` — 12.
4. `This 'ERC721' is may be array or struct but may not be declared` — 9. 컨트랙트 이름을 식에 쓴 경우로 보인다.
5. `@IReturn: function 'balanceOf' not found in interface 'IERC20Metadata'` — 9. 부모 인터페이스의 함수를 찾지 않는다.
6. `states 없음 (입력 0개 또는 전부 입력 오류)` — 9. 입력이 하나도 없으면 해석하지 않는 구조(`evaluate({})` = 빈 상태) 때문일 수 있다.
7. `unresolved member '.sub' on UnsignedIntegerInterval` — 6, `Cannot resolve overload for 'mul_'` — 6.
8. 그 밖: Yul `if`·`for` 미지원(명시적 거부), `unsupported 'new' type`, `tuple assignment: right-hand side is not a tuple value`,
   `return join ... BytesSet with UnsignedIntegerInterval`, `'int' object has no attribute 'is_bottom'` 등 각 1~5.

시간 초과: `PeckShield-AaveV3 .../Pool.sol` 은 파일당 540 초 안에 7 함수가 끝나지 않는다(어느 함수인지 미확인).

---

## I20. 해석 결과가 부정확하거나 틀린 곳 — 2026-11-24~25 재현, 미수정

1. ~~빈 동적 배열 접근이 revert 하지 않는다~~ — ✅ 2026-11-26 길이 밖 접근 = 그 경로 ⊥, 빈 배열 pop 도. 함께 찾은 결함: `arr.push(v)` 의 v 를
   멤버 접근식에서 찾아 **push 한 원소가 늘 ⊤** 였다(이전부터) — 호출식의 인자를 넘기게 고쳤다. forge 15 경우 확인.
2. **루프 정밀도.** 길이 3·원소 1,2,3 을 넣은 합 루프가 6 이 아니라 ⊤(widening). mapping 칸 `m[0..2]` 도 같다. sound 하지만 부정확하다.
3. **memory 별칭.** `b = a; b[0] = 3; r = m[a[0]]` 이 m[3] 이 아니라 m[0] 을 읽는다 — unsound. 4단계.
4. ~~조건부 포인터~~ — ✅ 2026-11-26 후보 모음 join(I18).
5. ~~멤버 `++`~~ — ✅ 2026-11-26. `x++` 가 넘기는 정수 1 을 복합 대입이 왼쪽 타입의 값으로 바꾼다(`Update.compound_assignment`). `g.v++`·`m[k]++`·`a[1]++`·참조 경유·int·uint8 확인.
6. `new L.Inner[](2)` 의 원소가 0 이 아니라 ⊤. return 변수를 거친 struct 값(`z = s; return`)이 ⊤.
7. `abi.decode(d, (L.Inner))`·`abi.decode(d, (uint256[], uint256))` 가 예외("unrecognised type-list element").
8. **편집 중 struct 멤버 추가.** 기존 struct 에 멤버를 더하고 바로 쓰면 `Member 'w' not in struct 's'`. 선언 추가는 증분으로 적용하는데
   이미 만든 상태변수 객체가 옛 멤버를 가진다. 1단계 이전부터 같다.
9. **S2(한 줄에 착지 노드 둘)** — 새로 해석까지 간 `StakefishERC20Wrapper.mintTo`, `VariableDebtToken.initialize`.

---

## I18. storage 참조를 통한 쓰기가 원래 상태변수에 반영되지 않는다 — unsound, 2026-11-24 재현

**증상.** 진입 `s[x].v = [2,2]` 일 때 아래는 모두 마지막 `w` 가 `[1,1]` 이어야 하는데 `[2,2]` 다.

- 지역 storage 포인터: `D storage d = s[x]; d.v = 1; w = s[x].v;` (`d.v` 로 읽으면 `[1,1]` 로 맞다)
- 일반 상태 struct: `D storage d = g; d.v = 1; w = g.v;`
- callee 의 storage 파라미터: `function h(D storage p) internal { p.v = 1; }` 뒤 `h(s[x])`·`h(g)`
- 선택자 무효화도 빠진다: `m[s[x].v]` 를 읽고 `d.v = 1` 뒤 다시 읽으면 이전 칸의 값이 그대로 나온다.

직접 쓰기(`s[x].v = 1`)와 memory 파라미터(`h(D memory p)`)는 정상이다. 커밋 `0699ac7` 에서도 같다(2026-11-20 수정 이전).

**원인 (2026-11-24 확인).** (1) 노드 env 복사(`VariableEnv.copy_variables`)가 별칭을 끊는다 — 포인터와 상태변수가 서로 다른 사본이 된다.
(2) callee 는 `param_sources` 되쓰기 뒤에 exit env 의 상태변수 사본이 caller 를 다시 덮는다(`Engine._interpret_callee_body`).
(3) 선언의 데이터 위치가 기록되지 않았다(1단계에서 기록하게 했다). 2단계(storage 참조 = 위치)에서 고친다.

**진행 (2026-11-26).** 지역 포인터는 고쳤다 — `StorageRef`(Domain/Variable.py)가 묶을 때 고정한 경로를 갖고, 읽기·쓰기·선택자 해석에서
그 경로로 펼친다(`Storage.bind_ref`·`expand_refs`). 같은 날 callee storage 파라미터(위치로 넘김)·storage 반환·경로마다 다른 위치(후보 모음)도
고쳐 네 증상 모두 해결 — forge 예제로 확인, 규칙은 `SEMANTICS_LOCATIONS.md`. 이 항목은 해결됨으로 옮길 수 있다.

**규모.** 텍스트 집계로 선정 타깃 파일 중 지역 storage 포인터 선언이 있는 파일 41, 그 이름에 쓰기도 있는 파일 25.
callee storage 파라미터(EnumerableSet 류)는 세지 않았다. 실제 선정 함수에서 틀린 상태가 나오는지는 확인 전.

**상태.** 기록만(2026-11-24 사용자). 수정 전.

---

## 해결됨

### I5·I7. 의존성 빌드·분석 실패 — ✅ 2026-11-25

새 데이터셋 빌드에서 미완 83 타깃(선정 함수 913)이었다. 단위마다 첫 오류로 나누고 최소 예제로 재현해 고쳤다. 지금 415 / 415 Complete.

- **A. 본문이 빈 한 줄 함수**(`constructor() {}` 뒤의 선언, I7). `find_contract_context` 가 줄 안의 `{`·`}` 순서를 보지 않고 개수로 세어
  `contract C {` 를 닫힌 것으로 봤다 → 괄호 순서를 보는 `_enclosing_open_brace_line` 을 쓴다.
- **B. 여러 줄 enum.** 슬라이서가 멤버를 한 줄로 합치면서 레코드 끝 줄을 첫 줄로 적어 뒤의 선언이 한 줄씩 밀렸다(ErrorReporter 의 `event Failure`).
- **C. 소수 상수**(`0.95 * 1e27`). 리터럴만으로 된 식을 Solidity 처럼 유리수로 계산한다(`_fold_literals`). `1 / 2 * 4` = 2.
- **D. 뒤에 선언된 타입**(struct·enum·interface·contract, Aave `DataTypes`). 줄 분석 전에 소스 전체의 선언을 먼저 등록한다
  (`Analyzer/Declarations.py`, `analyze_source`·`update`·레지스트리). 서로를 쓰는 인터페이스처럼 순서로 풀 수 없는 경우도 된다.
- **E. 여러 줄 Yul `for`**(`} lt(i, n) {` · `} {`). 그 줄을 새 블록으로만 세어 assembly 가 끝나지 않았고, 뒤의 함수가 Yul 로 읽혔다
  (`else: target join not found` 도 이것). 오류 없이 Complete 로 판정되는 경우도 있었다.
- **F. 파일 수준 상수**(`uint256 constant OneWord = 0x20;`). 파일 수준에 등록하고 값은 읽을 때 계산한다(`ContractAnalyzer.file_constant`).
- **G. 컨트랙트 뒤의 `pragma`·`import`.** 앞 줄의 함수 컨텍스트가 남아 문장으로 읽혔다.
- **H. 배열 초기화**(`uint256[2] a = [1, 10000]`). 상태변수 초기화식은 진입값이 아니라는 원칙(⊤)을 모든 타입에 적용한다.

함께 고친 것: 1단계에서 struct 선언을 세션 사본에서 공유하게 해 **실패한 편집이 원래 세션의 선언을 바꿀 수 있었다** — 선언도 사본에서 함께 복사한다.


### I17. `this.f()` 의 callee 가 호출자의 `msg.sender` 를 쓴다 — ✅ 2026-11-16

`this.f()` 는 메시지 호출이라 callee 안의 `msg.sender` 는 컨트랙트 자신이어야 하는데, `Engine._interpret_callee_body` 가
호출자 환경의 전역(`msg.*` 포함)을 그대로 넘겼다. 그래서 `require(msg.sender == address(this))` 가 거짓 → EXIT ⊥
(거짓 unreachable), 반대로 callee 의 `require(msg.sender == owner)` 는 호출자 기준으로 통과해 실제로는 revert 하는 경로를
도달 가능으로 냈다(거짓 reachable). 1차 외부 검토(2.1절)가 지적해 실측으로 확인했다. 이제 `invoke_callee` 가 호출 종류를
`frame_kind` 로 넘기고, `kind == "this"` 이면 callee 프레임의 `msg.sender = address(this)`(symbolicAddress 1), `msg.value = 0` 으로
두며, 복귀 시 callee 의 `msg.*` 를 호출자에게 되쓰지 않는다(호출자의 값은 그대로). 내부 호출은 같은 프레임이라 바꾸지 않는다.
남은 한계: `this.f{value: x}()` 의 값 전달은 미지원(0). 타깃 486 중 `this.f()` 는 4 파일 5 회. 회귀: `temp/test_this_context.py`.

### I1. 증분 `update` 가 `⊥` 를 낸다 — ✅ 2026-11-16

원인은 줄 메타가 아니라 삭제 수술이었다. `DynamicCFGBuilder.delete_cfg_range` 가 지운 구간 뒤의 노드 중 공유
sink 를 모두 버렸는데 함수 EXIT 도 포함돼, 함수 끝 `}` 바로 앞 문장·블록을 지우면 앞 노드가 EXIT 에 다시 이어지지
않았다. 원래 사례(앞 추가 + 뒤 삭제)는 그 한 경우다. 이제 지운 문장의 제어 효과(require/assert → ERROR,
return → RETURN, break → loop exit)만 빼고 EXIT 로 흘러 지나가는 간선은 잇는다. 회귀: `temp/test_delete_fallthrough.py`.

### I13. 루프 안 블록을 지우면 바깥 루프가 통째로 사라진다 — ✅ 2026-11-16

삭제 구간 `descendants(entry) ∩ ancestors(exit_)` 가 exit_ 에서 멈추지 않아, 블록이 루프 안이면 바깥 루프 back edge 의
사이클 전체가 들어갔다(루프 안 if 삭제 → 함수 전체 ⊥). 반대로 블록 안 `return` 처럼 exit_ 에 닿지 않는 노드는 빠져
고아로 남았다. `DynamicCFGBuilder._delete_region` 이 entry 에서 따라가되 sink 로 가는 간선(return·revert·require/assert
실패)은 따라가지 않고, break·continue 노드와 exit_ 에서는 멈춘다. 블록 밖으로 나가는 간선은 이 둘뿐이다(sink 간선을
만드는 빌더는 return·revert·require·assert 넷). 단일 진입 검사로 새는 경우를 수술 실패로 올린다.

### I14. 감싸기·벗기기가 조용히 틀린다 — ✅ 2026-11-16

`x;` → `if (c) { x; }`(감싸기)는 diff 가 "if 헤더 추가"만 내 `x` 가 if 밖에 남았고, 반대(벗기기)는 "if 헤더 삭제"가 블록
전체를 지워 `x` 도 사라졌다. diff 가 텍스트가 같은 `x` 를 equal 로 짝지었기 때문이다. `incremental.diff_records` 가
추가·삭제된 블록 헤더 안의 equal 레코드를 delete + add 로 낸다 — 모든 op 가 연속 범위 하나만 건드린다. 블록 범위는
레코드에서 구한다(어떤 레코드도 덮지 않는 줄 = 단독 `}`). 못 구하면 `IncrementalSurgeryError`.

### I15. `} else {` 삭제가 줄을 하나 더 지운다 — ✅ 2026-11-16

else·else-if 절 삭제가 `ok` 로 끝나면서 엔진 줄 공간을 소스보다 한 줄 짧게 만들었다(`if_join` 이 `line_info` 에서 사라지고,
그 뒤 if 아래 추가가 `No CFG node found after line N` 로 실패). `_apply_delete` 가 레코드 범위 `[startLine, endLine]` 을 그대로
지웠는데, 헤더 레코드의 endLine 은 가짜 `}` 줄이고 본문이 먼저 지워진 뒤에는 그 줄이 절의 실제 닫는 `}` 와 겹친다. 일반 헤더는
그 줄까지 지우는 게 맞지만 `} else {`·`} else if (…) {` 는 그 줄의 `}` 가 앞 블록을 닫으므로 헤더 줄만 지운다
(`engine_api._apply_delete`). 회귀: `temp/test_delete_fallthrough.py` 의 else·else-if 삭제 사례(수정 전 5 실패). `} catch {` 는
증분 편집 자체가 미지원(`_is_risky_op`)이라 해당 없다.

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
