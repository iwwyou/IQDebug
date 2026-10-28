# IQDebug 리팩터링 계획 — CFG 구성 / 추상 해석 분리

> 확정일 2026-10-21. 사용자·Claude·다른 LLM 논의 결과. 진행 상황은 이 문서 §6 과 `PROGRESS.md` 에 기록.
> 엔진 수정 이력은 `(to_solidityguardian)_engine_fixes.md`(F55~)에 함께 남긴다.

## 0. 목표

SolQDebug 에서 물려받은 "편집 시점 즉시 평가" CFG 구성을
**증분 구조 구성(편집 시점) + 디버깅 시점 추상 해석**으로 분리한다.

- forward reference(뒤에 정의된 callee/modifier)로 인한 CFG 손상 제거
- pending reinterpretation 제거
- 재귀 호출 비용 제한
- 변수 스코프와 추상 상태 의미론 정리
- 디버깅 실행 간 상태 격리
- 기존 intent 검증 체계 유지 (intent 문법은 변경 금지)

복잡한 interprocedural summary 나 Solidity 전 기능 지원보다 **정확·빠름·유지보수성**을 우선한다.
지원 대상 Solidity = **0.5 이상** (0.5 와 의미가 같은 0.4.x 1건 허용, 0.4 고유 의미는 미지원).

## 1. 확정된 결정

### D1. CFG 구성과 추상 해석 분리
- 구성 단계(편집 시점): 증분 파싱, CFG 노드·엣지, Statement/Expression IR 저장,
  함수·변수 선언 정보, lexical scope, modifier 참조 등록, 디버깅 슬롯 도출, intent 등록.
- 구성 단계에서 **하지 않는 것**: 식 평가, 내부 호출 inline 해석, `reinterpret_from`, 루프 fixpoint, 노드별 값 전파.
- 추상 해석은 디버깅 입력이 주어졌을 때 타깃 함수 진입점부터 수행한다.
- CFG 제어 흐름 구조 결정을 위한 구문·구조 분석은 구성 단계에서 수행한다.

### D2. ⊥ 의미
- ⊥ = **도달 가능한 concrete 상태가 없음**(γ(⊥)=∅). 그 외 용도로 쓰지 않는다.
- ⊥ 가 아닌 것: 미평가, 미지정 입력, 정밀 계산 불가한 정상 값, 배열 미지정 원소, 미지원 식, 엔진 오류.
- 모르는 정상 값 = typed ⊤. Solidity 기본값이 정해진 경우 = 정확한 기본값(0, false 등).
- 엔진이 처리 못 했다는 이유로 ⊤ 를 반환하지 않는다(그건 버그 또는 지원 범위 문제).
- 별도 reachable boolean 은 두지 않는다. 도달 불가 = env 가 ⊥. 판정은 서버 한 곳(`_is_bottom_env`)으로 모은다.
- 루프는 fixpoint 로 한꺼번에 계산하고, 수렴 후 상태와 loopDelta 로만 노출한다.

**미평가 상태(`None`) 불변조건**
1. 실행 사본 생성 시 모든 노드 `variables = None`(미방문).
2. "미방문"과 "⊥" 판정 헬퍼를 각각 하나로 모으고, 흩어진 판정
   (`_is_bottom_env({})`=False / narrowing `if not in_vars[node]` / `_pred_src` 빈 env 제외 / `x.variables or {}`)을 교체.
   해석 중 `None` 은 join 에서 제외, ⊥ 로 취급하지 않는다.
3. 해석 종료 후: ENTRY 에서 그래프상 도달 가능한데 `None` 인 노드 → 내부 버그로 예외.
   그래프상 경로가 없는 노드(return 뒤 dead code 등) → 명시적 ⊥.
   → 직렬화·Guardian 은 `None` 을 보지 않는다.

### D3. 내부 호출: 디버깅 시점 on-demand 해석
- 구성 시점엔 호출 식 IR 만 보존. callee CFG 존재 불필요. caller↔callee 사전 연결·재해석 없음.
- 함수 선언·FunctionCFG 는 소스를 읽을 때 정상 등록한다.
- 정적 이름/타입 해석(오버로드, 정적 인자 타입, 상속/override, library·using-for, super, `X(a)` cast/호출 판정, modifier 이름)은
  선언이 모두 들어온 뒤(analyze 이후) 언제든 가능하며, 해석 중 호출을 만나면 그 결과로 callee 를 inline 해석한다.
- pending reinterpretation 메커니즘 제거. 미해결 호출 ⊤ 폴백 전부 제거.
- 호출 dispatcher·context 관리는 통합하되 호출 종류별 Solidity 의미는 구분한다.

### D4. 진단 최소화
- 사용자 진단은 두 경우만:
  (a) 컴파일러가 거부할 의미·타입 오류(해석 중 확인된 미해결 callee/modifier, 오버로드 모호, 미선언 식별자 등) → evaluate 오류.
  (b) IQDebug annotation 규칙 위반(예: 루프 본문 intent) → annotation 거부.
- 편집 중 callee 미정의는 오류가 아니다.
- infeasible 경로의 미해결 callee 는 분석하지 않으므로 보고하지 않는다(입력별 결정적, path-insensitive 의미와 일관).
- 미지원 기능·내부 버그는 ⊤ 로 숨기지 않고 예외로 드러낸다(수정 대상).
- 공식 보수적 근사(D7, 함수 포인터, 미지원 Yul)는 정상 정책 — 진단 없음(내부 provenance 기록은 가능).

### D5. 상태변수 초기 환경
- 미지정 mutable 상태변수 = typed ⊤ (배포 후 트랜잭션으로 바뀌었을 수 있음, multi-tx 는 범위 밖).
- constant = 정적 값 유지. immutable = 알려진 값이면 유지, 아니면 ⊤. 전역 = 디버깅 값 또는 정의된 sound 초기값
  (non-payable 함수의 msg.value = 0).
- 현황: elementary 상태변수는 2026-10-20 에 이미 ⊤ 로 변경됨(`ContractAnalyzer.process_state_variable`).
  남은 것: 배열 inline initializer 평가값 사용, 동적 배열 길이 0, `_sync_constructor_state` 의 생성자 효과 반영.
- **생성자 타깃 예외(배포 전 의미론: 기본값·initializer·base constructor·상속 초기화 순서)는 맨 마지막 단계.**

### D6. modifier, try/catch, do-while 지원
- modifier: 구성 시점엔 독립 ModifierCFG + FunctionCFG 의 modifier 참조(이름, 인자 식)만 등록. splice 하지 않는다.
  뒤에 정의된 modifier 도 구성은 정상 진행.
- 디버깅 시점에 **실행 사본 위에서 합성(splice)**한다(continuation 모델 대신 기존 단일 그래프 해석기 재사용):
  - modifier 인자 = 합성 진입부의 `param = arg` 대입 노드
  - 여러 modifier = 선언 순서대로 바깥→안쪽 중첩
  - 본문 `return` = placeholder 후속으로 연결(반환 변수 유지, modifier 나머지 실행)
  - `_` 없는 경로 = 본문 미도달(자연 처리)
  - `_` 여러 개 = 본문을 placeholder 마다 복제(같은 줄 다중 노드의 상태 표시 방식은 미결)
  - modifier 지역변수는 D8 스코프로 분리
- try/catch, do-while: visitor ↔ analyzer ↔ 해석기 연결.

### D7. 재귀: Conservative Recursive Call Approximation
- 일반 내부 호출 = inline 해석. 활성 호출 스택에 같은 함수 identity 재진입 시(자기·상호 재귀) 즉시 근사, 추가로 펼치지 않음.
- 반환값 typed ⊤ / mutable 상태 havoc / constant·immutable 유지 / callee 가 pure·view 면 storage 유지 /
  인자에서 닿는 memory 객체·storage 포인터 인자 havoc.
- 인자 식의 부수효과는 근사 전에 정상 평가한다. 검사 위치 = 통합 호출 진입점, 인자 바인딩·callee 상태 초기화보다 먼저.
- fixpoint 가 아니다. 정상 반환 후 상태의 과대근사이며 정상 종료 보장과는 구분한다.

### D8. 지역변수 스코프와 join
- 0.5 lexical block scope. 블록 exit(분기 join, 루프 exit, back-edge)에서 그 블록 선언을 env 에서 제거(정규화).
- union join 코드는 유지. 정규화 후 키 집합 일치는 assert 로 검증.
- shadowing: 선언 시 바깥 binding 을 저장, 블록 exit 에서 복원(bare block 에서의 unsound 덮어쓰기 방지).

### D9. 함수 포인터·Yul
- 함수 타입 변수 호출 = D7 과 같은 보수적 근사. 함수 타입의 mutability 가 view/pure 면 storage 유지.
- Yul: 기존 지원 연산(산술·비교 `gt`/`lt` 등)은 정밀 유지. 그 외 연산만 보수적 근사:
  값만 내는 연산 = 결과 typed ⊤ / `sstore` = storage 전체 havoc / `mstore` 등 = memory 지역 객체 havoc.
  지원 목록·근사 목록을 코드에 명시한다.

### D10. 루프 본문 intent
- 루프 본문 `@During` 금지(연구 범위상 의도된 제한). annotation 단계에서 거부(D4-b).
  inline 해석과 standalone verify 에 동일하게 적용.

### D11. 판정
- 판정 = `Satisfied / Warning / Violated / Unreachable` (`engine_api.py:68`).
  Warning = 만족 증명 불가·위반 배제 불가(potential violation). ⊤ 는 Warning. 재귀 근사로 인한 Warning 은 정밀도 손실이지 unsound 가 아니다.

## 2. 의존성 빌드 (`evaluation/Dataset/build_project_deps.py`)
- D3·D6 으로 callee/modifier 선언 순서가 무관해지므로 `reorder_members` 는 **struct/enum 순서로 축소**
  (callable 재배치·오버로드 arity 처리 제거).
- 위상정렬 순환(F-E, interface↔구현) 문제는 별도로 남는다.

## 3. 범위 밖 / limitation
- 정밀 재귀 분석(summary fixpoint 등), 함수 포인터 정밀 dispatch, Yul 저수준 의미 전체, 정밀 alias 분석,
  multi-transaction, 루프 본문 intent.
- **외부 호출의 잔액 효과 미모델링** (2026-10-23, 사용자 결정 (나)): 외부 호출(라우터 swap 등)이 이 컨트랙트에 ETH 를
  보내거나 빼도 `address(this).balance` 는 디버그 입력값(진입 시점) 그대로다. 그래서 잔액 변화에 의존하는 intent 는
  **sound 하지 않은 Satisfied** 가 나올 수 있다(실측: numscout_EthereumGod — `fromSwap = 0` 으로 계산돼 Satisfied, 실제로는
  swap 수령액이 있어 내림/올림 차이로 위반 가능; IntentChecker 도 같은 이유로 Satisfied). 논문 기술 시 이 판정은 limitation 사례.
- 논문 기술 시 구분: 보수적 근사로 지원 / 정밀 분석만 미지원 / 입력이 지원 범위 밖 / intent 언어에서 의도적 금지.

## 4. 구현 순서

| Phase | 내용 | 불변조건 |
|---|---|---|
| 0 | 기준선: 48 케이스 노드 상태·판정·디버깅 슬롯 JSON 덤프, 구문별 마이크로 케이스, 48 케이스 시간 프로파일 | — (알려진 버그도 기록만, 정답 취급 안 함) |
| 1 | 실행 격리: 1a = 타깃 실행용 fcfg 사본, 누수 제거(ireturn_registry, return_vals, before_envs, snapshot 의미, _states_cache 스코프), try/finally / 1b = library·this 호출의 오류 삼키기 제거 | 1a: 기준선과 동일(누수 차이 제외), A→B→A 동일 / 1b: 드러난 오류 보고 |
| 2 | IR 보존·기본 의미: `--`, 튜플 initializer, emit, unchecked, ⊥ 오용 정리(D2) | 항목별 차이 개별 확인 |
| 3 | 구성 단계 평가 제거: 스코프 트리(Scope(parent, decls), 노드 = (scope, 선언 수)), `process_*`·삭제 경로의 평가·`reinterpret_from` 제거, pending 제거, D8, `None`(미방문) 불변조건(D2) | CFG 구조 동일, 결과 = Phase 2 (스코프 밖 변수 제거만 허용), 슬롯 동일, 편집 순서 무관 동일 CFG |
| 4 | on-demand 호출 해석(D3): ⊤ 폴백 제거, cast/호출 정적 판정, 호출 진입점 통합 / modifier 실행 시 합성(D6) | |
| 5 | 재귀 근사(D7), 함수 포인터·Yul 근사(D9), 루프 본문 intent 거부(D10) | |
| 6 | try/catch, do-while 연결, 나머지 회귀 | |
| 7 | 생성자 pre-state(D5 예외) | |

## 5. 필수 테스트
- 구성 동등성: 같은 최종 소스에 대해 편집 순서(순서대로 추가 / caller 먼저 / callee 삭제 후 재추가 / 중간 삽입 후 삭제 /
  `} else {` 수정)가 달라도 구조·해석 결과 동일.
- 실행 격리: 입력 A → B → A 에서 첫 A = 마지막 A.
- 스코프: 한쪽 분기 선언, 블록 exit 소멸, shadowing(bare block 포함), 루프 지역변수, catch 파라미터, named return.
- 내부 호출: forward reference, 중첩 호출 식, overload, library, 상속, 자기·상호 재귀, 상태 변경, tuple 반환, memory/storage 참조.
- modifier: 함수 앞/뒤 정의, 다중 modifier, 다중 `_`, 인자, 본문 return, `_` 없음, 조건부 `_`.
- soundness: 작은 concrete 입력에 대해 실제 실행 결과가 추상 결과에 포함되는지(feasible/infeasible/정상 반환/revert/재귀 근사).

## 6. 진행 기록
- 2026-10-21: 계획 확정. Phase 0 착수.
  - 하네스: `evaluation/refactor/baseline_cases.py`(48 케이스, flush 마다 freeze fcfg → 노드 상태·슬롯·callee·시간),
    `evaluation/refactor/micro_cases.py`(구문별 36 케이스 + 실행 격리 A→B→A). 결과 `evaluation/refactor/baseline/`.
  - ⚠ `evaluation/intent_validation/run_cases.py:163` 이 커밋 상태에서 SyntaxError(정규식 `\n` 이 실제 개행으로 들어감).
    수정 = `r"//[^\n]*"` 한 줄. 하네스는 헬퍼를 복사해 의존하지 않음. (보고, 미수정)
  - 마이크로 기준선(micro_p0)에서 확인된 현 동작:
    - 틀린 값: `x--` 가 +1(incdec b=[14,24], 기대 [10,20]) / 튜플 선언 ⊤ / emit 인자 호출 효과 누락(counter [1,1], 기대 [2,2]) /
      modifier 미실행(counter [5,5], 기대 [6,6], a 미refine) / shadowing bare block unsound(x=[2,2], 기대 [1,1]) /
      break·continue 루프 s=[0,1] (기대 ⊇ [0,3], unsound 의심) / unchecked 언더플로 래핑 누락(b=[0,4]) /
      블록 밖 변수 잔류(x, for 의 i).
    - 예외: assert(`process_assert_statement` 인자 누락 TypeError) / do-while / try-catch / 뒤에 정의된 modifier /
      forward internal call(`logical_not` on UnsignedIntegerInterval = ⊤ 폴백) / struct 생성자 `P(a)` /
      자기 재귀(RecursionError) / 상호 재귀 / 함수 뒤 선언 상태변수.
    - 실행 격리 A→B→A 는 이 케이스에선 동일.
  - 코드 읽기 중 발견: library 호출(`Evaluation.py:2971`)·this 호출(`:3132`) 의 `except Exception` 이 **모든 내부 오류를
    심볼 문자열로 삼키고 컨텍스트 복원 없이 진행** — D4 위반, Phase 1b 에서 제거(결과 변화는 보고).
  - Phase 1 세부: 1a = 동작 보존 격리(타깃 실행용 fcfg 사본, snapshot 의미 수정, try/finally, ireturn_registry 소비 후 비움,
    `_states_cache` 키 스코프), 1b = 오류 삼키기 제거. `None`(미방문) 불변조건은 구성 단계가 값을 안 채우게 되는 Phase 3 로 이동
    (지금 넣으면 모든 `.variables` 읽기 지점을 먼저 정리해야 해서 동작 보존이 안 됨).
  - Phase 1a 완료(F55): legacy 46/48 동일. 3_H_04 Warning→Violated = 구성 시점 RETURN.return_vals(⊤ 입력)로 @Post 반환값을
    검증하던 버그 해소(입력 기준 3000≠2000). @IReturn 다음 회차 누수 해소. 시간 565s→432s.
  - Phase 1b 완료(F56): legacy 48/48 동일, rebuilt 42_H_01 실제 오류 노출 — `'denominator' not in struct
    'IDiscountProfile.discount_ret'` (보고, 미수정).
  - Phase 2 완료(F57): 48 legacy 판정 변화 0, 상태 변화는 전부 원인 확인(의도). rebuilt numscout_HIT 결과 생김(Violated, 일치).
    드러난 결함: BoostToken `_rOwned[_excluded[i]]` → AddressSet (길이 모르는 동적 배열 원소 접근, D5 동적 배열 과제와 연결).
    deps(프로젝트별) pkl 11개가 옛 unary 토큰 → Phase 3 후 deps 전체 재빌드 때 해소.
  - Phase 3a 완료(F58): 구성 단계 평가·재해석·pending 제거. 시간 451s→130s. 드러난 결함 다수(F58 ⚠, 아래 '보고 목록').
  - Phase 3b 완료(F59): D8 블록 스코프·shadowing, 지역변수 기본값, 튜플/다중반환/삼항식 침묵 처리 제거, env 복사 성능.
    48 판정 = 3a 와 동일.
  - **보고 목록(미수정, 사용자 결정 대기)**: (1) `} else {` 중괄호 소실(rebuilt 58_H_02) (2) for 루프 `continue` 후 흐름 unsound
    (3) callee 의 memory struct 참조 수정이 caller 에 미반영(memory_alias) (4) 한 줄 블록 `{ stmt; }` slicer 미지원
    (5) `process_payable_function_call`/`process_function_call_options` 가 `pass`(옵션 호출 문장 소실) (6) `address.transfer/send`
    미해결 멤버 (7) BoostToken `_rOwned[_excluded[i]]` → AddressSet (8) 42_H_01 `'denominator' not in struct 'cf'`
    (9) rebuilt 42_H_01 이전 오류 `IDiscountProfile.discount_ret`.
  - Phase 3c 완료: 미방문 노드 = 도달 불가면 명시적 ⊥, 도달 가능한데 미방문이면 예외. 루프 노드는 들어오는 흐름이 전부
    infeasible/⊥ 일 때만 ⊥.
  - Phase 4a 완료(F61): 호출 진입점 통합(`invoke_callee`, memory 참조 write-back), 미해결 호출 → 예외, modifier 디버깅 시점 합성.
    legacy 33/48 동일(판정 변화 = HIT 컴파일 불가 contraction, SwordCrowdsale 중복 `Ownable`). 시간 legacy 113s / rebuilt 35s.
- 2026-10-22: 결정 (사용자)
  - 보고 목록 (1)(2)(5)(6) 수정 승인, (7) 길이 모르는 동적 배열은 후속 과제로 미룸, (8) 원인 조사, `_` 다중 modifier 는 Phase 6 이후.
  - **평가 기준 = rebuilt(프로젝트별 deps) 모드로 일원화**, legacy 는 회귀 참고용. pkl 은 전부 재빌드(IR 영향 수정 확정 후 1회).
  - 원칙: 의미론적으로 올바른 설계가 기본, 임시방편은 선택지로만 제시.
  - 입력 전제: **입력 1개 = statement 1개**. `if (x) { return; }` 는 `if (x) {`⏎`}` 와 `return;` 두 입력이어야 한다.
  - using-for: 타깃과 같은 파일에서 라이브러리가 **뒤에 선언**된 경우는 오류로 취급(pkl 빌드는 위상정렬이라 무관).
  - 재진입: 외부 컨트랙트 → 타깃 호출은 고려 안 함. a → (외부) b → a 재진입은 **미정 — 후속 결정 목록에 모음**.
- Phase 4b (진행 중): (1) `} else {` — 저장소에 원문 보관(`SolidityAnalyzer._stored_text`), 문맥 판정은 앞 `}` 무시,
  `_function_span` 보정 제거. (2) for `continue` → 증감 노드, 증감 없는 for 의 body→join. (5) 옵션 호출 문장은 원래 정상
  (진단 오류) — 호출 없는 옵션식만 컴파일 오류 예외. (6) 원인 = `address payable` 타입명 불일치 → 방문자에서 `address` 로
  정규화; `.call/.delegatecall/.staticcall` = (bool ⊤, bytes) 튜플; 옵션 호출은 옵션을 벗긴 호출로 평가.
  (8) 원인 = interface 반환 조회가 `Dependencies/objectfile` 경로 하드코딩 → 프로젝트 deps 와 다른 판(`FloatStruct`) 혼입.
  하드코딩 pkl 폴백 4곳 제거(로드된 의존성만 조회).
  - 드러난 것: w36·w58·w65 deps 빌드가 `_topo_order` 예외로 pkl 0개였음(폴백이 공용 트리 SafeERC20 로 가림),
    Nokon 은 `using SafeMath` 뒤 선언, 70_H_04/10 `Cannot join intervals of different type lengths`(미조사),
    31_H_01 `.mul` 미해결.
  - 새로 발견: 중첩 루프 뒤 `continue`/`break` 가 안쪽 루프로 연결(감싸는 루프를 역방향 DFS 로 찾음) — 수정 방식 결정 대기.
- **후속 결정 목록(모아서 논의)**: a → b → a 재진입 모델, 길이 모르는 동적 배열, `_` 다중 modifier(Phase 6 이후).
- 2026-10-22 (2): 결정 (사용자) — **아직 미적용, 재개 시 이 순서로**
  1. 중첩 루프 `continue`/`break` 대상 = **문법적으로 감싸는 가장 안쪽 루프**(중괄호 체인 `_enclosing_open_brace_line` 을
     올라가 처음 나오는 for/while/do 줄의 조건 노드). forward/역방향 그래프 탐색 방식 폐기. 마이크로 `continue_nested` 로 검증.
  2. 입력 전제(1 입력 = 1 statement): **의존성 소스는 정규화 가능**(빌드 시 한 줄 블록·본문 없는 for/while 을 펴기 —
     대상: deps 의 `HIT.sol:140`, `LiquidityBasedTWAP.sol:25` 등). **타깃 컨트랙트·함수는 입력 쪽 책임** — 엔진은 위반 입력에
     명시적 오류(조용한 오분석 금지).
  3. using-for 뒤 선언: 오류 취급 유지(현 코드). 표현은 "증분 입력 순서 전제상 선언 전 참조 미지원". numscout_Nokon 은
     **contraction 에서 `library SafeMath` 를 컨트랙트 앞으로 이동**(데이터 수정).
  4. 미결: numscout_HIT 컴파일 불가 contraction 처리, 70_H_04/10 `Cannot join intervals of different type lengths` 원인 조사 허가.
  5. 그 뒤: w36·w58·w65 `_topo_order` 실패 조사 → deps/objectfile pkl 전체 재빌드(`reorder_members` struct/enum 축소 포함) →
     Phase 5.
  - 현재 작업 트리 상태: Phase 4b 수정(위 항목) 적용됨, temp 회귀 16/16 통과, 덤프 `baseline/p4b_{legacy,rebuilt}.json`,
    `micro_p4b.json`. 2026-10-22 프론트 기능(논문 main.tex 2957-3098) 검토로 잠시 전환.
- 2026-10-22 (3): 위 1~3 적용.
  1. `DynamicCFGBuilder._enclosing_loop`(중괄호 체인의 최내곽 for/while; do-while 안은 Phase 6 까지 명시적 오류, 루프 밖이면
     컴파일 오류) — continue/break 공용. `find_loop_condition` 삭제. 마이크로 `continue_nested`·`break_nested`·
     `continue_then_inner`·`while_continue` 모두 바깥/올바른 루프로 연결 확인.
  2. `Dependencies.main._one_statement_violation` — 슬라이서가 한 줄 블록·다중 문장·중괄호 없는 제어문을 명시적 오류로 거부
     (예전엔 다음 줄과 조용히 합쳐짐). 의존성 정규화 = `preprocess_contraction.normalize_one_statement_per_line`
     (`expand_single_line_blocks` 끝에서 호출 → `build_project_deps.prep` 경유). 전수 점검: 현재 평가 소스 중 위반은
     deps 3줄(HIT.sol:140, w5 Utils.sol:120, LiquidityBasedTWAP.sol:25) — 재빌드 시 정규화됨. target_contraction_auto 0건.
  3. `contraction_target.order_declarations` — 최상위 선언 안정 위상정렬(순환은 예외). 74 케이스 중 변화는 Nokon(SafeMath) 뿐.
     Nokon contraction·case 재생성 → rebuilt Warning 복귀.
  - **보고(결정 대기)**: (A) 정수 폭 암묵 변환 부재 — 70_H_04/10 원인. 선언 타입 폭으로 변환하지 않고 산술은 왼쪽 피연산자 폭을
    씀 → uint112 반환값을 uint256 변수에 담은 뒤 곱셈이 112비트에서 overflow → 112비트 ⊤ (**unsound**).
    (B) deps 빌드 순환(w34·w36·w58·w65) — 원인은 interface 끼리의 **타입 전용 참조** 간선(합법적 상호 참조).
- 2026-10-22 (4): (A) 정수 암묵 변환·공통 타입 승인·적용(F63). (B) deps 빌드 순서는 사용자가 다른 LLM 과 논의 중.
  - 결정 대기: ① 초기화식 있는 mutable 상태변수 = 초기값 유지 vs D5 대로 ⊤ ② 범위 인덱스 쓰기 unsound·다중 반환 join 폴백 수정 시점
    ③ `_pow_fits` 정밀도. 재빌드 대기: legacy 3_H_05(옛 pkl 초기값 폭).
  - 덤프: `baseline/p4c_{legacy,rebuilt}.json`, `micro_p4c.json`. legacy 41/48·rebuilt 44/48 동일(p4b 대비).
- 2026-10-22 (5): 의존성·증분 편집 정리 (커밋 `622e754` 이후 작업 트리; 복원 지점 = git tag `pre-global-deps-removal`).
  1. **전역 공용 의존성 트리 폐기 (사용자 결정: "모든 경우에 프로젝트 단위")** — 삭제: `Dependencies/{objectfile,interfaces,
     libraries,contracts}`, `Libraries/` 전체, `Analyzer/CFGSerializer{,Pickle}.py`(Libraries 전용). 유지: `evaluation/RQ1/cases/`
     (rebuild_cases.py 어노테이션 원본), `cases_rebuilt`, 기존 baseline JSON.
     - 로더 통합: `Dependencies.main.load_project_deps(sa, ca, deps_root, exclude)` 하나 — CLI(`main.load_dependencies`, env
       `IQ_DEPS_ROOT`/`IQ_DEPS_EXCLUDE`; 미지정이면 의존성 없이 시작)와 서버(`EngineSession(deps_root, exclude)`) 공용.
       DepSpec 이름 선별 로드 폐기(`DepSpec/DepInfo/DependencyStore` 선언은 프론트 업로드 API 자리로 유지).
     - `Dependencies/main.py` 는 `--root` 필수(전역 Phase 1~3·`--type` 제거). `check_pkl.py` 도 `--root`.
     - `run_cases.py`·`baseline_cases.py` 는 rebuilt 전용(`--rebuilt` 플래그 제거, 출력명 `<tag>_rebuilt.json` 유지).
     - 회귀: temp 16/16, micro 기대 실패 3개만, rebuilt 48/48 p4c 와 동일(`baseline/g1_rebuilt.json`).
       ※ rebuilt 48 중 23 은 원래부터 verdict None(오류) — 레지스트리·재빌드 뒤 원인별 정리 예정.
  2. **증분 수술 fail loud (사용자 결정)** — `engine_api.update()` 의 전체 재빌드 폴백 두 경로 제거(`_full_rebuild` 삭제).
     미지원 수술(try/catch·do-while·break/continue 의 삭제·수정)은 수술 전에 `IncrementalSurgeryError`(세션 불변),
     수술 중 예외는 op 정보와 함께 `IncrementalSurgeryError` + 세션 오염(`_poisoned`; 이후 모든 공개 메서드 거부).
     `engine_server` 는 오염 세션을 캐시에서 제거하고 오류를 올린다. `temp/test_safety_net.py` 를 거부 검증으로 변경(8/8).
  3. **의존성 빌드 순서 재설계 방향 (사용자 합의, 구현 전)** — 컨트랙트별 **선언 header 레지스트리**(visitor 로 파싱된 선언만:
     kind·부모·상태변수(초기화식 IR)·struct/enum·using·함수/modifier/event/error 시그니처). 2패스(선언 → 본문, 순서 무관),
     부모·라이브러리는 이름으로 연결하고 본문·바인딩·초기화식은 디버깅 시점, 빌드 후 링크 검사. 위상정렬·`_type_refs_in`·
     `reorder_members`·`_load_type_ref_pkls` 폐기 예정. 레지스트리는 **의존성 전용**(타깃은 개발 순서상 전방 참조가 없다).
     조사 결과: 빌드 시점에 타 선언 **본문**은 쓰지 않음 — 순서 의존의 원인은 자식이 부모·라이브러리 CFG 객체를 참조로 보관하는 것.
     부수 발견(미수정): 생성자 CFG 에 부모 상태변수 미주입(함수·modifier 는 주입), `get_full_struct_enum_defs` 가 직속 부모만 봄.
     상세 설계는 별도 보고 후 승인. 사용자가 다른 LLM 과 검토 중(정수 폭·결정 대기 3건과 함께).
  - **결정 대기**: (가) return/revert/break/continue 삭제 수술을 소스 구조 기반 fall-through 계산으로 일반화해 지원
    (나) builder 수술 함수의 조용한 `return []` 를 "이미 제거됨"/"처리 불가"로 구분해 후자를 오류로 (다) try/catch·do-while 은
    Phase 6 에서 빌드 경로 복구(마이크로 실패: do-while 루프변수 미선언 오류, try `add_control_flow_node` 부재) → 수술 지원 순.
    + 위 (4)의 ①~③.
- 2026-10-22 (6): **프론트 탐색 재구성 (사용자 결정 F1~F4, U2~U6)** — 프론트 작업을 엔진 리팩터링보다 먼저.
  - 엔진: 노드 고유 `uid`(생성 시 1회, 줄 이동 불변; 의존성 pkl 은 uid 없으면 로드 거부), 실행 전 상태 `in_variables`
    (worklist 의 join·scope 정리 직후 사본, 루프 헤더 = 루프 밖에서 들어오는 상태 F2), if/else-if join 의 표시 줄
    `display_line`(체인을 닫는 `}`; `Utils/DisplayLines.py`, 의존성은 빌드 시점에 노드에 저장), callee 캡처 키에
    노드 안 호출 순번(같은 노드의 같은 함수 두 호출이 덮이던 문제) + callee 복귀 시 호출 지점 복원·조건식 호출 지점 지정
    (두 번째 호출이 callee 의 마지막 노드를 호출 지점으로 잡던 결함).
  - 직렬화: `CFGNode.id = uid`, `name`(라벨), `display_line`, `in_state`. 이름 키 맵(intent 대응·callee·TopVar) 전부 uid.
  - 프론트: `navGraph.ts`(순수 규칙) + `useNavigation.ts` 재작성 — 착지 규칙(merge·loop exit 허용, 더미·φ·루프 본문·
    연속 join 제외), 루프 미진입(헤더 → exit), Select Successor/Predecessor, Into 반복 = 평가 순서, Continue 가 callee 안
    breakpoint 까지·분기별 서로 다른 bp 면 분기점에서 정지(U2), bp 키 (파일, 줄)(U6), 루프 본문 줄 bp 금지(U5),
    Next/Prev Intent, Restart, why-unreachable 출력. Step Over Branch 제외(F3). `tsc` 통과, 규칙은 엔진 출력으로 node 검증.
  - 의존성 전체 재빌드(uid·display_line 포함): 42 프로젝트 pkl 수 동일, 빌드 ERR 대폭 감소(예: w3 160→5). w36/w58/w65 는
    여전히 순환 실패(레지스트리 대상).
  - 회귀: temp 16/16, micro 동일. rebuilt 48: **엔진 변경 효과 48/48 동일**(커밋 622e754 엔진 + 새 deps 와 비교),
    **의존성 재빌드 효과 10건 변화**(16_H_04/06, 70_H_04/05/10 오류, 31_H_01·52_H_23 오류 메시지 변화, 45_H_01·47_H_02·
    60_H_01 상태 변화) — 원인 조사·결정 대기. 덤프 `baseline/g2_rebuilt.json`, `baseline/h2_rebuilt.json`.
  - 정정: 초기화식 있는 mutable 상태변수는 2026-10-20 부터 이미 ⊤(결정 대기 ① 은 사실상 해결). immutable 은 초기화식이
    있어도 ⊤(sound, 비정밀).
- 2026-10-22 (7): **증분 업데이트 트랜잭션 (사용자 승인)** — "마지막 정상(committed) 상태 유지, 업데이트 전체를 후보에 적용해
  전부 성공하면 확정". `EngineSession._run_transaction`: 세션 deepcopy(의존성 CFG 는 memo 로 공유, 측정 6~10ms) 위에서
  연산 적용 → `SourceError`(사용자 소스 오류, `Utils/Errors.py`; 현재는 미정의 타입 1곳) = 진단(category=semantic)으로 반환·
  세션 유지, 그 밖의 예외 = `IncrementalSurgeryError`(내부 실패, 숨기지 않음)·committed 불변이라 세션 오염 없음(`_poisoned`
  제거). committed records 는 성공 시에만 갱신. 현재 소스에 오류가 있으면 evaluate/verify 거부. analyze 도 같은 트랜잭션.
  한 입력 = 한 문장 전제상 같은 업데이트 안의 선언·사용 순서 문제는 다루지 않음(사용자 판단).
  - 함께 고침: 서버 `/evaluate` 가 callee CFG 를 안 보내고 store 도 결과에 안 넣어 실제 UI 에서 Step Into 불가였다.
  - 테스트 `temp/test_transaction.py` 24항목 중 23 통과. **실패 1 = 기존 결함(보고)**: `ContractAnalyzer._shift_cfg_meta` ③ 이
    줄 이동 때 **의존성 CFG 까지** 같은 줄 번호 노드를 옮긴다(w70: 63개 함수 src_line 오염, 예 Context._msgSender 5→82).
    트랜잭션의 "의존성 공유" 전제(타깃 분석이 의존성을 바꾸지 않음)도 깨진다. 수정안: ③ 에서 from_dependency CFG 제외.
  - 추가 발견(보고): ① 컨트랙트 수준 선언(struct·멤버·enum·event·modifier·함수)을 지워도 엔진 정의가 남는다 → 후보
    타입 참조 재검증의 전제. ② 슬라이서가 한 줄 enum(`enum E { A, B }`)을 다음 문장과 한 record 로 합친다(deps 전처리가
    enum 을 한 줄로 접으므로 deps 영향 가능). ③ `}    function f(...) {` 처럼 한 줄 두 문장을 1-statement 검사가 못 잡고,
    ANTLR 오류는 출력만 하고 진행해 본문 없는 함수가 조용히 생긴다.

- 2026-10-22 (8): 사용자 결정 반영 — ① 컨트랙트 수준 선언 줄의 삭제·수정 = 후보를 **현재 소스 전체 재분석**(의존성만 올린
  빈 세션 `_pristine` 사본에서; 함수 본문 편집·선언 추가는 증분 그대로) → 지운 선언이 엔진에 남던 문제 해소, 참조가
  남으면 SourceError 로 거부. ② `_shift_cfg_meta` 는 의존성 CFG 를 옮기지 않음. ③ 입력 전제 검사 확장(`}` 뒤 문장,
  한 줄 블록 — 호출 옵션 `{value: x}(` 제외), 위반 = SourceError(진단, `analyze_source`/update), deps 정규화에 한 줄
  assembly 전개 추가 → deps 재빌드(보고서 동일).
  - 회귀: temp 16/16, `temp/test_transaction.py` 35/35, micro 동일, rebuilt 45/48 동일(35_H_08/10/12 은 오류 이름만
    ValueError→SourceError). 덤프 `baseline/g3_rebuilt.json`.
  - **의존성 재빌드로 바뀐 10건 원인** (g1 → g3):
    · 70_H_04/05/10: 빌드 순서 — 한정 타입 `FixedPoint.uq112x112` 이 위상정렬 간선이 아니라 FixedPoint 가
      ILiquidityBasedTWAP 보다 나중에 빌드 → 멤버 줄 ERR → **멤버 하나 빠진 struct 가 조용히 pkl 로 저장**.
      (예전엔 Phase 4b 에서 제거한 pkl 직접 로드 폴백이 가렸다.)
    · 31_H_01, 52_H_23: 앞 단계 오류가 풀려 진행 → 새 정지점 = 인터페이스 타입 수신자의 멤버 호출을 using-for 라이브러리
      (SafeERC20) 확인 없이 외부 호출로 처리하는 **분기 순서 결함**(기존).
    · 16_H_04/06: PRBMath `mulDivFixedPoint` 의 Yul `not` 미지원 → 값 None 으로 진행 → `%` 에서 AttributeError
      (조용한 None — D9 Yul 근사 미구현). 예전 deps 는 이 assembly 본문이 빌드 오류로 빠져 있었다.
    · 45_H_01·47_H_02·60_H_01: 판정 동일, 상태가 더 완전·정밀(상속 `__gap`, callee 값 해석, shortfall [150,150]).
  - 기타: 35_H_08/10/12 = 타깃 contraction 에서 `struct Position` 이 그것을 쓰는 mapping 선언(51행)보다 뒤(53행) —
    개발 순서 전제 위반(Nokon 과 같은 유형). 의존성 빌드의 줄 단위 오류 무시: 42 중 21 프로젝트, 135 줄
    (simpleStatement 82, functionDefinition 15, event 14, structMember 6, assembly 6 …) 이 빠진 채 pkl 저장.
- 2026-10-22 (9): 사용자 결정 — 2(멤버 호출 분기) 수정, 3 = (b) D9 로(아래 정정 참고·보류), 4(35_H 데이터) 수정, 1·5 는 레지스트리와 함께.
  - **멤버 호출 분기 수정**: 인터페이스/컨트랙트 타입 수신자(변수·캐스트 `T(x).f()`)의 멤버 호출은 T(부모 포함)가 그
    멤버를 선언했을 때만 외부 호출, 아니면 using-for 라이브러리 함수(`_type_declares_member`, `_using_for_on_cast`).
    결과: 52_H_23 진행(새 정지 = `getReserves` 인자 개수 불일치), 62_H_03/10 끝까지 실행 → **Unreachable**(이전엔 항상 오류 —
    판정 타당성 조사 필요), 31_H_01 진행(새 정지 = SafeERC20 라이브러리 안 `address(token).functionCall(...)`:
    `address(...)` 변환 뒤에도 인터페이스 태그가 남고, 라이브러리 함수 안 using-for 조회가 라이브러리가 아니라 타깃
    컨트랙트의 using 목록을 본다 — 기존 결함 2건, 미수정).
  - **35_H_08/10/12 데이터**: contraction 에서 `struct Position` 을 `positions` mapping 앞으로(원본 백업 scratchpad/bak35),
    cases_rebuilt 재생성. Position 오류는 해소, 새 정지 = 아래 Yul `_not`.
  - **정정 — 16_H 원인**: Yul `not` 은 지원 연산(→ `~`)이다. 실제 원인은 의존성 전처리 `rename_grammar_clashes` 가 intent
    문법 키워드(`not` 등)와 겹치는 식별자를 `_` 접두로 바꾸면서 **assembly 안의 Yul 내장 `not` 까지 `_not` 으로** 바꾼 것.
    `_not(0)` = 미지원 함수 → None → `%` 에서 AttributeError. 영향: 16_H_04/06, 35_H_08/10/12 (deps w16 PRBMath, w35 FullMath).
  - 회귀: temp 16/16, transaction 35/35, micro 동일, rebuilt g3 대비 변화 7건(위 내용). 덤프 `baseline/g4_rebuilt.json`.
- 2026-10-22 (10): 사용자 승인 3건 처리.
  - 31_H_01 기존 결함 2건 수정: ① `address(x)`/`payable(x)` 변환은 인터페이스 태그를 뗀 **새 값**(`AddressSet.retagged`),
    `IERC20(x)` 캐스트도 새 값에 태그(예전엔 원래 값 객체에 태그를 덮어써 x 의 값까지 바뀜). ② using-for 조회 범위 =
    **실행 중인 함수가 속한** 컨트랙트/라이브러리(`Evaluation._executing_contract_cfg`, 소속을 못 찾으면 오류).
    → 31_H_01 진행, 새 정지 = `unresolved member '.length'`(미조사).
  - 전처리 `rename_grammar_clashes` 가 assembly 블록 안을 건너뜀 → deps 재빌드(보고서 동일, `_not(` 0건).
    → 16_H_04 None→**Violated**, 16_H_06 None→**Violated**(g1 판정 복귀), 35_H_08/10/12 오류 해소.
  - **62_H_03/10 Unreachable 원인**: solmate SafeTransferLib.safeTransfer 의 `success := and(or(...), call(gas(), ...))` —
    미지원 Yul 내장(`call`·`returndatasize`·`mload`·`gas`)이 None 으로 평가돼 ⊥ 로 번짐 → `require(success)` 불가능 →
    호출 이후 전부 ⊥ → @Post Unreachable(**거짓 판정**). D9(b)(미지원 Yul 값 연산 = typed ⊤, sstore/mstore havoc) 대상.
  - **새로 드러남**: 35_H_08/10/12 은 끝까지 실행(EXIT 도달, ⊥ 없음)되지만 @Post 판정이 **기록되지 않는다**
    (legacy 는 Warning/Warning/Violated). rebuilt 모드에선 그동안 앞 단계 오류로 이 경로에 온 적이 없다 — 조사 필요.
  - 회귀: temp 16/16, transaction 35/35, micro 동일, rebuilt g4 대비 변화 6건(위). 덤프 `baseline/g5_rebuilt.json`.
- 2026-10-22 (11): **@Post 판정 누락의 원인과 평가 드라이버 이전** (사용자 결정: intent 는 함수 지정으로 명시 부착, 평가는 engine_api 경로).
  - 원인: `process_post` 가 `current_target_function_cfg`(줄 위치로 갱신되지 않고 **마지막으로 처리한 문장의 함수**)에 붙임 →
    코드를 다 쓴 뒤 들어오는 @Post 가 파일의 마지막 함수에 붙어 record 재생 평가에서 12 케이스 @Post 판정 누락(p0 부터).
  - 수정: `process_during/process_post(…, fcfg=)` + `_intent_function_cfg`(명시 함수, 없으면 줄 문맥의 함수 이름+파라미터
    타입으로 조회). @During 노드도 대상 함수 안의 노드로 한정. engine_api `_attach_intents` 는 방문자에 `intent_target` 으로 명시.
  - 새 평가 드라이버: `evaluation/intent_validation/api_driver.py` + `run_cases_api.py` (소스 = target_contraction_auto,
    대상 = cases74 contract/function, 디버그 값 = 케이스 키 → engine_api 슬롯 키 → RangeValue, intent = IntentItem).
    결과 `results_api_a1.json`, 덤프 `baseline/a1_api.json`. temp 16/16·transaction 35/35 통과.
  - **드러난 도구 결함 — 디버그 슬롯 도출 범위**: 케이스가 지정한 디버그 입력 중 엔진이 슬롯을 만들지 않는 것이 172개(37 케이스)
    → 프론트 사용자도 입력할 수 없는 값. 분류: 타깃 본문에 없는(callee·modifier 에서만 쓰는) 변수 63, 본문에 루트는 있으나
    복합 접근 경로(mapping·struct 멤버·`address(this).balance`) 미도출 96, 기타(constant·`totalSupply` 등) 13.
    그 결과 기대 Violated 가 Warning 으로, 디버그 값이 하나도 안 들어간 케이스는 verify 소프트 게이트로 판정 없음
    (56_H_02, 60_H_01). 판정 일치: api 15/48 vs record 경로(g5) 20/48 — **슬롯 도출을 고치기 전엔 평가 수치로 쓰면 안 된다**.
  - 그 밖의 api 경로 오류: 36_H_02·58_H_02·65_H_01(deps 순환으로 pkl 없음 → `using SafeERC20` 실패, 레지스트리 대상),
    51_H_02(`event RampTargetPrice(...)` 줄 실패, 미조사), 31_H_01·52_H_23(`.length` 미해석), 70_H(레지스트리 대상).
- 2026-10-23 (12): **mapping/array 저장소 의미론 재작성** (사용자 승인 — 이름 기반 디버그 입력 유지 + sound, 다른 LLM 검토 반영).
  - 입력의 의미: `@StateVar m[a] = v` = "이 실행에서 선택자 a 가 고르는 위치의 진입 값" (∀σ⊨D: σ(m)[σ(a)] ∈ v). 모든 키를 지정할
    필요 없이 소스의 접근식에 값을 준다. 엔진은 그 제약을 만족하는 모든 실행을 보수적으로 분석한다.
  - 재현(`temp/test_storage_soundness.py`, 수정 전 15 중 FAIL 9·ERR 1): 정수 범위 키 mapping 쓰기 무시, 범위 인덱스 정적 배열
    강한 갱신·20 초과 범위 쓰기 무시, 범위 키 읽기가 첫 구체 키 하나 선택(입력값 무시), 표본 21개만 join, ⊤ 주소 두 선택자
    alias 무시, 미확정 인덱스 배열 입력을 경고 후 버림, 동적 배열 길이 = 구체화한 원소 수, 리터럴 키 읽기 크래시
    (`update_mapping_in_cfg` 의 없는 속성 — 읽기가 영속 CFG 를 바꾸던 경로).
  - 구현: `Interpreter/Semantics/Storage.py` 하나로 키 해석(Concrete/Symbolic(순수 선택자 식)/Range(부수효과)/Bottom)·선택·
    읽기·쓰기 후 전파·진입 제약. 읽기(`evaluate_index_access_context`)·쓰기(`Update._update_indexed`)·디버그 입력
    (`DebugInitializer._assume_indexed`)이 같은 경로. 도메인(`Domain/Variable.py`): 선택자 셀 메타 `sym`, `summary`(구체화
    안 된 위치 요약 — 새 셀은 여기서 만든다), 배열 `sym_cells`·추상 `length`(`array_length`).
    · 쓰기 = 고른 위치 strong, 최상위 대입이 끝난 뒤 겹칠 수 있는 다른 셀의 **같은 LHS 경로 위치**(`s[a].v` 면 .v)에 weak,
      미확정 선택자면 summary 에도 weak. refine 은 assume 모드(전파·무효화 없음, Range 는 안 건드림).
    · 진입 제약은 쓰기와 별개(서로 weak 하지 않음). 같은 위치로 해석되는 두 슬롯은 meet, 비면 입력 모순 오류. 슬롯은 접근
      경로가 얕은 것부터 적용(선택자 값이 먼저 놓이도록 — 입력 순서 무관).
    · 무효화: 선택자가 읽는 변수에 대입·선언하면 그 셀의 바인딩만 버림(쓰기 효과는 다른 셀·summary 에 이미 있음 —
      불변식 "구체화 안 된 위치의 값 ∈ summary"). callee 진입·복귀 때 그 함수 프레임의 셀을 버림. 분기 join 에서 한쪽에만
      있는 셀은 버림.
    · push/pop 은 추상 길이 갱신(길이 미정이면 새 원소 위치가 미확정 → weak). `new T[](k)` 길이 = k 의 구간(예전엔 상한).
  - 함께 고친 기존 결함: ① `VariableEnv.variables_equal` 이 Struct/Mapping 을 `.value`(None) 로만 비교 → **내용 비교 안 함**
    → fixpoint 가 저장소만 바뀐 후속 노드를 재계산하지 않을 수 있었다. ② mapping join 에서 한쪽에만 있는 키를 그 값
    그대로 유지(다른 경로의 ⊤ 누락), 배열 join 에서 길이가 다르면 긴 쪽만 반환. ③ `@StateVar` 입력 오류를 출력만 하고
    삼킴(@Param 은 올림) → 올림. ④ 디버그 입력 스냅샷이 leaf 만 등록 → 새로 만든 키 엔트리가 restore 뒤 mapping 에 남음
    → 컨테이너 등록(디버깅 후 초기 상태 복원 메커니즘은 그대로). ⑤ 배열 표시 `array(len=N)` 이 원소 수/선언 0 → 추상 길이.
    ⑥ callee 복귀·참조 인자 되쓰기·통짜 재할당이 필드를 하나씩 옮김 → `VariableEnv.adopt_storage` 하나로.
  - 표시: summary 는 선언 기본값에서 바뀌었을 때만 `x[*]` 행으로 보인다(사용자 결정 (나)).
  - 검증: storage 29/29(재현 15 + 다른 LLM 제안 11 + 이력 무관·push·new 길이), temp 18 종 통과, transaction 35/35,
    micro 47 동일(기대 실패 3건 traceback 줄 번호만). api 48 판정 48/48 동일(s0 → s1), 상태 변화 11건 = 모두 기존 unsound
    해소 또는 표시 정리 — Nokon `balances[address(this)] -= x` 쓰기가 예전엔 다른 키로 가 사라짐(→ [1,2e12]), callee 프레임
    셀(`spender`·`token`·`lower/upper`)이 caller 에 남던 것 제거, 호출 결과 키(`ticks[func_result_<id>]`)·⊤ 주소 문자열 키
    (`_aggregators[AddressSet(⊤)]`) 같은 가짜 구체 키 제거, bytes32 상수 키가 이름 대신 값. 덤프 `baseline/s1_api.json`,
    `results_api_s1.json` (s0 = 수정 직전 기준선).
- 2026-10-23 (13): **디버그 슬롯 도출 확장 + 호출 대응** (사용자 승인 순서: 저장소 → 슬롯 도출).
  - 원인(172 누락, 37 케이스): ① 스캐너가 **타깃 본문만** 봄(callee·modifier·부모 함수의 상태변수·전역·경로 누락),
    ② **상속 조상(의존성) 상태변수**를 보지 않음(`totalSupply`·`balanceOf`·`isPaused`·`pairInfo` …), ③ using-for 호출
    `x.add(y)` 가 스칼라 `x` 를 경로 루트로 잡아 `x.add()` 슬롯만 냄, ④ 구조체 값 경로·구조체 파라미터를 멤버로 펼치지 않음
    (`tokenLoanParams[tokenId].valuation`, `accepted.valuation`, `self.balances[account]`), ⑤ 인터페이스 핸들 상태변수의 주소
    슬롯 없음(`nativeAsset`), ⑥ 파라미터 슬롯 valueKind 가 타입과 무관하게 interval.
  - 수정 `engine_api._derive_debug_slots`: 타깃 + 내부 호출·modifier·라이브러리(using-for 포함)·super/this 를 따라가는
    footprint(이름이 가리킬 수 있는 오버로드 전부 — 과대 근사), callee 접근식은 호출 지점 인자로 **타깃 진입 이름으로 번역**
    (`_mint(foreignAsset)` 안 `pairInfo[asset]` → `pairInfo[foreignAsset]`, callee 지역변수 기반 경로는 슬롯 아님),
    조상 상태변수 포함, constant 제외(값 확정), 구조체 경로·파라미터 멤버 leaf 전개, 스칼라 루트의 멤버(`to.balance`)는 경로 아님,
    IReturn 튜플 반환은 원소별 `[k]`, IReturn 키는 호출 지점 **원문**(ireturn_registry 가 원문 텍스트로 조회하므로).
  - 호출 대응(실행): `Storage.callee_aliases` — 파라미터가 호출자의 순수 선택자 식을 받았으면 callee 의 `m[param]` = 호출자
    프레임 `m[식]` 같은 셀(must-alias). 파라미터 재대입·의존 변수 대입이면 대응 끊김. 프레임 스택은 Engine 이 push/pop.
    → 타깃 이름으로 준 입력이 callee 접근에 그대로 쓰인다(예전엔 callee 이름 키가 따로 생겨 ⊤).
  - 결과(api 48, s2 → s4): 누락 172 → 70, 기대 일치 15 → 19. 판정 변화 11건 — HIT·WANGMI·101_H_01·113_H_05·62_H_08·56_H_02
    Warning/None → Violated(기대 일치), **16_H_04·3_H_04 Warning → Violated(기대 Warning 과 다름, 그러나 계산상 정확**:
    16_H_04 long 에서 fee 를 더해 newQuote 980.2e18 > 980e18, 3_H_04 반환 1000+2000 ≠ 2000 원금 이중 합산 — 기대값은 예전 도구가
    입력을 못 넣어 나온 Warning), **EthereumGod Warning → Satisfied = sound 하지 않음**(외부 호출 뒤 address(this).balance 불변 — §3 limitation 으로 결정).
  - 남은 누락 70 (최종 s5 — s4 와 판정 동일): 구체 인덱스 입력 48(배열 원소 `x[0]`·`details[2].debt` 등 — 소스엔 `x[i]` 만 있음,
    제공 방식 결정 대기, 70_H 16 은 레지스트리 오류도 겹침), 케이스 데이터 불일치 22(52_H_04/34 IReturn 6 은 케이스가 옛 판
    텍스트 `chainlinkFeed`, 소스는 `_aggregators[pairData.token1]`; 192_H_01 `_asset` 은 대상 함수 파라미터 아님, 5_H_12 소스는
    `[_pool]`, 62_H_10·SwordCrowdsale·35_H_10/12 는 대상이 읽지 않는 변수, 47_H_02 소스는 `_msgSender()`, 60_H_01 cases74 의
    contract 가 Collateral 이지만 함수는 `library OptimisticLedgerLib`, 45_H_01 `WAD` 는 constant).
  - 회귀: temp 18 종, storage 32/32(호출 대응 A14~16 추가), transaction 35/35, micro 상태 동일·슬롯 변화 3건(인터페이스 핸들
    `foo`, 부모 상태변수 `s` 추가, constant `K` 제외).
  - 사용자 결정(2026-10-23): "끝까지 분석됐고 결과 자체가 맞으면 기대값 갱신" → 16_H_04·3_H_04 기대 Warning → Violated.
    재검토: 오류·진단 없음, EXIT 도달, 입력 전부 적용, 손 계산 일치(본문 위). 원본 xlsx(Guardian)는 수정 금지라
    `evaluation/intent_validation/expected_overrides.json`(사유·근거 포함) + `run_cases.expected()` 가 반영. s5 일치 19 → 21/48.
- 2026-10-23 (14): **구체 인덱스 디버그 입력** (사용자 결정: 기본 = 소스에 쓴 접근식, 원하면 인덱스를 구체값으로; 범위 = (A)
  타깃 함수 진입 상태만 — callee 지역 인덱스 경로는 슬롯 아님, IReturn 은 callee 포함).
  - 엔진: `DebugSlot.specializable`(인덱스 자리 있는 경로) / `requiresIndex`(인덱스가 타깃 지역변수 — `pegObservations[index]`
    의 루프 변수처럼 진입 시점 값이 없어 이 키 그대로는 입력 불가). `EngineSession.match_slot(key)` = 슬롯 키 그대로 또는
    인덱스 자리만 바꾼 키(바꾼 인덱스에 지역변수 금지). `_queue_debug_values`: 슬롯이 아닌 키·지역 인덱스 템플릿 그대로 =
    진단 오류(예전엔 슬롯 아닌 키를 조용히 @Param 으로 넣었다), 빈 칸은 미입력.
  - 드라이버: `api_driver` 가 같은 `match_slot` 으로 케이스 키 매칭.
  - 프론트: `slotTemplate.ts`(엔진과 같은 라벨 파서·대조), `DebugInputPanel` 경로 슬롯에 `+ 원소` → 인덱스 자리만 편집하는 행
    (값 유지 키 변경 `renameDebugKey`, 제거 `removeDebugValue`), 지역 인덱스 슬롯은 값 입력 대신 "원소 지정 필요",
    슬롯 아닌 입력은 별도 표시. `tsc` 통과(브라우저 확인은 아직).
  - 테스트 `temp/test_slot_template.py` 11/11.
  - (검토 반영) 바꾼 인덱스 허용 범위 = **진입 시점 이름 허용**: 진입 시점에 부작용 없이 평가되는 식(리터럴·파라미터·
    상태변수·전역·형 변환과 그 접근식 — `[0]`, `[_token]`, `[address(vaderPairs[0])]`). 지역변수·형 변환 아닌 호출·`++`/대입
    거부(`_entry_index_ok`), 최종 순수성은 값을 놓을 때 Storage.resolve 가 실제 식으로 검사. 의미 = 그 식의 진입 시점 값이
    고르는 위치(선택자가 뒤에 바뀌어도 제약은 원래 위치 — 선택자 무효화로 보장, 테스트). 식이 정의되지 않는 진입 상태(길이 0
    배열의 `[0]` 등)에 대해선 함의 의미론(그 상태에선 이 제약이 쓰이지 않음 — sound)으로 진단하지 않는다(bounds 는 Phase 5).
    프론트: 추가 행 `custom` 표시, 소스 인덱스가 진입 시점 값인데 다른 이름을 쓰면 설명성 안내(지역 인덱스 구체화는 안내 없음).
    테스트 `test_slot_template` 23/23(파라미터 치환·`i++`·호출 거부·중첩 인덱스·선택자 변경 후 비고정·입력 순서 독립·
    같은 위치 모순). 한계(sound·비정밀): 지역 복사 `p = pairs[0]` 뒤의 `tw[p]` 는 입력 셀 `tw[pairs[0]]` 과 다른 이름이라
    pairs[0] 이 ⊤ 이면 연결되지 않는다(값 identity 아님 — 이름 기반 입력 모델의 범위).
- 2026-10-23 (15): **남은 구현 1~4 원인 조사·수정안 (수정 없음, 사용자 지시)** — 결정 대기.
  - 레지스트리: 순환 = `_type_refs_in` 타입 참조 간선(w36/w65 IAuction↔IBasket↔IFactory, w58 IVault↔IVaultGovernance↔
    IVaultFactory), 70_H = `_typedecl_re` 가 한정 타입(`FixedPoint.uq112x112 x;`)을 간선으로 못 봐 FixedPoint 가 나중에
    빌드 → structMember ERR. 조용히 빠지는 줄 166(ERR 135 + UPDATE_CODE 31): 레지스트리로 풀릴 것(한정 타입·struct 미정의·
    상수 `N_COINS` 14) vs 엔진 결함(library event, function 타입 6, catch 5, ExpressionContext 11, 메시지 None 30).
    `get_full_struct_enum_defs`(ContractAnalyzer:1273) 직속 부모만 + 병합 우선순위 반대. 설계: sourceUnit 전체 파싱 1A 등록 →
    1B 해석(C3·한정 이름·상수·커버리지 검사) → 2 CFG 구성(상속 순서만), 폐기 `_topo_order` 타입/라이브러리 간선 등.
  - 실패 케이스: 47_H_02·78_H_02 거짓 Unreachable = **빈 본문 callee**(`_beforeTokenTransfer`)의 EXIT 를 force_join 이
    ENTRY(env 없음)만 보고 ⊥ 로 → 되쓰기로 호출자 storage ⊥(Engine.py:979/982/1861-1886). 51_H_02 = `LibraryCFG.events` 없음
    (+ cases74 contract 가 Swap 인데 소스는 library SwapUtils). 52_H_23 = 상속 탐색이 소스 순서 깊이 우선이라 인터페이스의
    본문 없는 선언을 구현으로 고름 + 인터페이스 함수 CFG 에 파라미터 미기록(시그니처 전부 `()`). 31_H_01 = `address(x).f()`
    수신자 타입을 값(AddressSet)으로 못 정해 using-for 를 놓치고 `return f"address.{member}"` 문자열 폴백(Evaluation:2045),
    이어 동적 bytes `.length` 분기 없음.
  - Phase 5/B/A: Yul 미지원 내장 → None(YulUnsupportedContext 분기 없음 — 62_H 원인, 마이크로는 크래시), Yul 문장
    (sstore/mstore/if/for/switch/return/revert) 조용히 무시(**unsound**), 재귀 n=⊤ RecursionError, 정적 배열 범위 밖 디버그 입력
    조용히 사라짐, B2 다중 반환 tuple 첫 경로만(**unsound**, Engine:1755-1776), checked 오버플로 [0,MAX](Interval:737-746),
    `_pow_fits` 비트수 과대(2**200 → ⊤), immutable 상수 초기화식 버림, 함수 포인터 크래시, D10 루프 본문 intent 허용,
    `1 << x`/`2 ** x` 리터럴 mobile type 의심(solc 확인 필요), 부호 혼합 항목은 폐기 후보(Solidity 가 암묵 변환 불허).
- 2026-10-23 (16): **남은 구현 + Yul P0-a (사용자 승인 — 의견 일치 항목만)**. 기록 = engine_fixes F68.
  - 빈 본문 callee: 진입 환경을 ENTRY 에(타깃·callee 둘 다) → 47_H_02·78_H_02 Violated.
  - 다중 return tuple 원소별 join(B2), 정적 배열 **전부** 범위 밖 디버그 입력 = 입력 오류(일부만 범위 밖은 미결정),
    재귀 = 쓰기 집합(전이적, 컨테이너 단위) ⊤ + 반환 ⊤ 요약(D7), 함수 포인터 = ⊤(`Domain/FunctionRef.py`, internal 은 상태변수
    전체 ⊤, D9 일부), library event(51_H_02 엔진 오류 해소 — cases74 메타데이터 Swap↔SwapUtils 불일치는 남음).
  - using-for 수신자 = **정적 타입**(`_static_type_of` 확장: 전역·`.length`·`.balance`·멤버 호출 반환 타입), `"address.x"` 문자열
    폴백 제거, 동적 bytes/string `.length` = ⊤.
  - 함수 소유 컨트랙트 `owner_contract`(등록 시점) — pkl 이 부모·using 라이브러리 CFG 를 사본으로 중복 로드해 identity 역색인이
    실패하던 문제(callee Step Into 누락 포함).
  - 상속 디스패치: 인터페이스 함수 파라미터 기록, `has_body`, C3 선형화, 오버로드 해소(선언 포함·전 계층) → 본문 있는 첫 구현
    디스패치, super/`Base.f()`/`this.f()` 도 선형화 기준 → 52_H_23 Warning.
  - StructDefinition.members = {이름: SolType}(A-1) — 정적 타입의 struct 멤버 분기가 늘 죽어 있던 결함.
  - **Yul P0-a** (`Interpreter/Semantics/YulEval.py`): builtin 을 Solidity 연산으로 낮추지 않고 EVM word 의미(mod 2^256,
    0 나누기 = 0, 부호 연산, `shl/shr` 인자 순서)로 평가, 미지원 반환 builtin = ⊤ word, 효과 표(`sstore`·`delegatecall` = 상태변수
    ⊤, `mstore`류 = 메모리 객체 ⊤ — 상수 주소 [0,0x60) 제외, call 출력 메모리), `revert`/`invalid` = ERROR, 좁은 타입 읽기 = ⊤ word,
    쓰기 = cleanup. 임시: Yul `if/switch/for/function/return/stop/leave/break/continue`·파싱 실패 줄 = **실행되면 명시적 미지원
    오류**(본문 줄은 CFG 에 만들지 않음). 슬라이서: assembly 안 여러 줄 식을 괄호가 닫힐 때까지 한 레코드로.
  - 결과: rebuilt 48 일치 **23 → 26**(s13). 16_H_04 는 Violated → Warning — PRBMath mulDiv 의 `sub(sub(mm, prod0), …)` 를 구간
    도메인이 관계 없이 wrap ⊤ 로 봄(예전 Violated 는 Yul 을 checked 산술로 잘못 평가한 결과). temp 전 스크립트 통과.
  - 결정: A-2 = 부모 정의는 Complete 승격 시점에 가져오기 + 소유자 표시 + 한정 이름은 소유자 조회(승인). B(파라미터가 상태변수를
    가림, 31_H_01) = 다른 LLM 검토 중. 16_H_04 정밀도(Yul 값 번호) = 나중. 다음 = **레지스트리(Temporary/Complete pkl)**.
- 2026-10-23 (17): **의존성 레지스트리 R0~R3 (사용자 승인)** — `Dependencies/registry.py`. 단위 = 원본 소스 파일 하나의
  전처리본(contraction 과 같은 전처리, 선언별 조각 폐기), 프로젝트별 이름 공간(같은 이름 두 단위 = 둘 다 Invalid — 현재 0건).
  의존 = 본문 식별자 중 다른 단위가 선언한 이름(어휘 과근사), SCC 역위상 순서로 분석하고 의존 폐포의 결과가 바뀌면(지문)
  다시 분석하는 고정점(합의안의 SCC 승격). 상태 Pending(미해석 이름 `UnresolvedName`)/Invalid(그 밖 오류)/Complete.
  저장 = `deps/<p>/builds/build_NNN/{manifest.json, units/*.pkl}` + `CURRENT`, CFG 참조는 전부 이름(persistent id) → 로드 때
  선언마다 객체 하나(사본 중복 제거). 로드 = 타깃 선언 집합과 같은 단위를 타깃 파일이 통째로 대신하고 그 의존 폐포만.
  `_topo_order`·`_load_parent_pkls`·`_load_type_ref_pkls`·정규식 사전 수집·`split_decls` 폐기.
  - 코어: 부모·using 라이브러리를 못 찾으면 조용히 건너뛰던 것 → `UnresolvedName`, 미정의 타입 → `UndefinedType`(SourceError).
  - 새로 드러난 결함과 처리: F1 같은 파일 안 뒤쪽 최상위 선언 참조(w42 `enum Status`, w44 interface) → (a) 전처리 최상위
    재배치(`reorder_top_level`, 사용자 승인; 근본 해법 (b) = 지연 해석, §7.3). F2 부모(인터페이스) struct 를 자식 상태변수
    타입으로 사용(w36·w65 Basket, w58 VaultGovernance) → **A-2 구현**(사용자 승인): 부모 생성 시 struct·enum 정의를 자식 표로
    가져옴(`ContractCFG.inherit_definitions`, 정의 객체에 `owner`), 소유자가 다른 같은 이름 = 오류.
  - 결과: 42 프로젝트 전부 빌드(w34·w36·w58·w65 포함, 순환 프로젝트 3라운드). Complete 아닌 단위 = 기존 분석기 오류(Invalid 23).
    rebuilt 48 일치 26 → **30** (s15: 42_H_01 회복, 65_H_01·70_H_04/05/10 해결, 36_H_02 판정 생김).
    temp 전부 통과(transaction 35, session_flow 7, update 10, delete_cfg 7, smoke).
  - 남음(보고): 58_H_02 `vg.internalParams().protocolGovernance.protocolTreasury()` — 부모 인터페이스 struct 를 반환하는 외부 호출의
    멤버 정적 타입을 실행 컨트랙트 기준으로만 찾음. 31_H_01 = B 결함. R4(업로드 API)·R5(Complete 게이트) 미착수.

- 2026-10-23 (18): **R4·R5 + Invalid 의존성·불일치 케이스 정리 (사용자 승인)**.
  - 레지스트리: R4 증분(`ProjectRegistry.from_build`/`apply`, CLI `--add/--remove`, 영향 단위만 재분석), R5 게이트
    (`load(require_complete)`, `DependencyNotReady`; `REQUIRE_COMPLETE` 는 아직 False — 케이스가 쓰는 의존 폐포에 Invalid 0).
    타깃이 타입 순환(IA↔IB)에 걸려도 허용(객체 참조가 있을 때만 로드 오류). 단위 하나의 슬라이스 오류 = 그 단위만 Invalid.
  - 58_H_02 정적 타입: struct·enum 타입에 선언 컨트랙트(`structOwner`/`enumOwner`) — 멤버 타입을 그 컨트랙트 정의 표에서.
  - 파일 수준 error/event, `errorDefinition` 문법(`(p (',' p)*)?` — `*` 누락으로 1·3개 파라미터 불가), `visitErrorDefinition`.
  - try/catch 빌드 경로 연결(방문자 빈 껍데기·try 줄 함수헤더 오분류·catch 다중·name mangling·join 줄 등록·catch 줄 밀림).
  - Invalid 원인 6종: I1 함수 문맥 = 블록 시작 줄에 **등록된** 함수(텍스트 추측 폐기: `received` → receive 오인, 오버로드
    타입 표기 불일치) + receive/fallback 등록, I2 배열 길이 = 컴파일 타임 상수식(`compile_time_constant`) + 의존성 멤버
    재배치에서 constant 를 struct 앞으로, I3 `revert()`(문법), I4 값만 있는 식 문장(`expression` 문장), I5 슬라이서가
    괄호가 닫힐 때까지 줄을 잇는다(여러 줄 for 헤더), I6 struct 이름 정규식.
  - 케이스: S2 구간 비트 연산(`|`·`^` 상한 unsound → [max lo, 2^k−1]/[0, 2^k−1], 단일값 정확), M1 인자 intent = 호출 직전
    상태(before_envs) + intent 평가 중 callee 캡처 끔, M2 한정 라이브러리 오버로드 반환 타입, M3 명시한 타깃 컨트랙트·함수
    없음 = 오류(조용한 빈 결과 폐기), M4 intent 의 미선언 이름 = 사용자 진단, M5 IReturn = 받는 타입(부모 포함)이 선언한
    view/pure 만(fail-open 폐기), M6 지역·파라미터 **인터페이스** 핸들도 IReturn + struct 반환 leaf 펼침·leaf 값 종류,
    M8 analyze 가 intent 를 받아 intent 가 참조하는 상태변수·전역도 슬롯(`set_intents` = 슬롯만 재도출), 빈 줄 @During =
    프로그램 지점(앞 문장 / 합류·출구 / 블록 진입). M7(callee 지역 인덱스)은 사용자 결정으로 하지 않음(⊤ 허용).
  - 데이터(사용자 승인): override 16_H_06·36_H_02 = Violated(손계산), Nokon `@StateVar ethRateFix` 추가, 31_H_01 @During 앵커
    L100 → L102(원본 작성 때 빈 줄 삽입으로 앵커가 `currentLockRatio` 선언 앞으로 밀렸다 — 원본 도구도 같은 위치로 해석).
  - 결과: rebuilt 48 일치 30 → **39** (s18). 의존성 41 프로젝트 전부 Complete(n_HIT 제외). temp 전부 통과(서버 필요 2개 제외),
    새 테스트 `temp/test_try_catch.py`·`temp/test_blank_during.py`·`temp/test_registry.py`.
  - 후속(사용자 결정): **F49 폐기** — IReturn 은 인터페이스 호출 전용(컨트랙트 타입 캐스트·상태변수는 슬롯 아님, `process_ireturn`
    도 거부). 다른 컨트랙트 인스턴스 storage 모델링은 future work(그때까지 컨트랙트 타입 외부 호출 반환 = ⊤).
    cases74.csv 51_H_02 → SwapUtils, 60_H_01 → OptimisticLedgerLib(원본 intent 가 그 라이브러리 함수 안). rebuilt 48 일치 **41** (s19).
  - 45_H_01 (사용자 결정: Warning 유지): 리포트 H-01 은 "borrow 가 maxBorrow·신용한도 검사를 accrueInterest **전에** 한다"(순서
    버그)인데 케이스 intent `@Post borrowIndex(Entry) < borrowIndex(Exit)` 는 이를 표현하지 못한다 — 양의 이율이면 Satisfied,
    0 이면 Violated(이자가 붙지 않는 정상 동작). `@During accrualBlockNumber == block.number`(L123 검사 줄) 은 Violated 로 버그를
    표현함을 확인(케이스는 바꾸지 않음). n_HIT: 의존성 소스에서 0.4 이름 없는 fallback 제거(`SOURCE_EDITS`) → Complete.
  - ⚠ w34 미빌드: 상수 길이가 평가되자 `Observation[16777215]` 정적 배열을 원소 단위로 만들다 메모리 8GB 초과(강제 종료).
    정적 배열 표현 결정 대기(§7.1). w34 를 쓰는 케이스는 없다.

- 2026-10-23 (19): **희소 원소별 배열 + 지연 widening 정리 (사용자 승인)**.
  - 배열 A = (N 길이, D summary, M `elements: {index: 셀}`, S 선택자 셀). 정적 배열도 선언 때 원소를 만들지 않는다(예전
    `_init_recursive` 가 `Observation[16777215]` 을 원소 단위로 만들어 w34 빌드가 8GB 초과). Read(k) = M[k] 또는 D,
    join = 키 합집합 Read₁ ⊔ Read₂ + D₁ ⊔ D₂, 비교 = 의미상 Read. 0 초기화(지역 정적 배열·new·delete) = D 를 0 셀로.
    지연 생성 경로의 qualified struct 정의 조회 통일(못 찾으면 오류 — 예전엔 조용히 멤버 없는 struct).
    정적 범위 밖 디버그 입력·다른 길이의 값 목록 = 잘못된 디버깅 입력.
  - 원소별 strong update 로 정밀해지자 3_H_05 가 160 초 → 900 초 초과. 원인 = 길이가 ⊤ 인 루프(`i < tokens.length - 1`)의
    widening 지연이 [1,20] 상한 20 이었고, 그 callee 가 바깥 루프에서 79 회 재해석.
  - 지연 widening(다른 LLM 검토 반영): d(L) = 조건에서 N̂ 를 얻으면 min(N̂, 300), 못 얻으면(평가 불가·`!=`·정보 없는 경계 —
    폭 ≥ 타입 범위 절반) 2. 지연 횟수 = 루프 join 노드 방문 수(사용자 결정 유지). W_MAX(300) 에 닿으면 **강제 widening 으로 계속**,
    600 에서도 수렴 안 하면 오류 — 예전엔 작업 목록이 남은 채 빠져나와 고정점이 아닌 환경을 썼다(unsound).
  - 결과: rebuilt 48 일치 41 (s21, s19 와 판정·메시지 동일), 48 케이스 총 시간 640 초 → **55 초**, w34 빌드 최대 90MB,
    의존성 42 프로젝트 전부 Complete. 새 테스트 `temp/test_sparse_array.py`.
  - 남음: 선택자 셀(읽기로 만든 셀이 join 에서 버려졌다 다시 생김 — 수렴은 하지만 반복 낭비) → 다른 LLM 검토 프롬프트 전달.

- 2026-10-24 (20): **R5 게이트 켬 + 프론트 M8 + 기록 정리 (사용자 승인)**.
  - `REQUIRE_COMPLETE = True` — rebuilt 48 일치 41(s22, s21 과 판정·메시지 동일, 막힌 케이스 0), 총 49 초.
  - M8 프론트: 서버 `_build` 가 요청 intent 를 `analyze_source` 에 넘김, 증분 `/analyze` 도 요청 intent 로 슬롯 도출,
    새 `/set_intents`(CFG 그대로 슬롯만 재도출). 프론트 intent 추가·삭제 = `setIntents` → `verify`. tsc 통과,
    새 테스트 `temp/test_set_intents.py`(7).
  - 기록 정리: §7.4 아래에 잘못 붙어 있던 (8)~(16) 을 §6 으로 옮김, §7 완료 항목 삭제, 선택자 셀 계획(다른 LLM 검토) 기록.
  - 발견(결정 대기): 52_H_04/34 IReturn 입력이 조용히 적용되지 않음 — case 키 `AggregatorV3Interface(chainlinkFeed)…` 가
    소스(`AggregatorV3Interface(_aggregators[pairData.token1])…`)와 다름(s13 부터). 엔진이 내는 슬롯 키는 디버그 입력 문법이
    파싱하지 못하고(캐스트 안 인덱스 식) 이어서 AttributeError.

- 2026-10-25 (21): **어노테이션 파싱 실패 = 명시적 오류 + 52 입력 정리 (사용자 승인)**. 기록 = engine_fixes F72.
  - 52_H_04/34: 소스와 맞지 않던 `AggregatorV3Interface(chainlinkFeed).latestRoundData()[0/1/4]` 입력 삭제(사용자 결정: 입력이 안
    들어가면 지우고 Warning). 판정 Warning 그대로, slot_missing 0.
  - 디버그 입력·intent 한 줄(`intentUnit`)에 문법 오류가 있으면 `AnnotationSyntaxError` — 예전엔 ANTLR 오류를 출력만 하고 복구된
    트리로 진행해 visitor 가 None 으로 죽었다(`IF(feeds[t]).get()[k]`). engine_api 는 슬롯 입력 오류 Diagnostic 으로 돌려준다.
  - 새 테스트 `temp/test_slot_roundtrip.py`: 48 케이스의 모든 슬롯 키가 입력 문법으로 다시 읽히는지. 못 읽는 키 = 52_H_04/34·192_H_01
    (`IERC20(assets[i])`)·79_H_02(`IJoePair(IJoeFactory(factory).getPair())`) — 캐스트 인자가 식(KNOWN, future work). 29_H_08 의
    `IBentoBoxMinimal.toAmount.selector` 는 호출이 아닌데 IReturn 슬롯으로 나옴 → (22) 에서 해결.
  - rebuilt 48 일치 41 (s23 — s22 대비 52 두 케이스 slot_missing 만 사라짐), temp 전부 통과.
  - 조사: `Evaluation.py` 의 IReturn 대체 조회(인터페이스·함수 이름만 비교, 주소 무시)가 실제 주 경로 — 31_H_01·52_H_04/34·5_H_12·
    78_H_02·79_H_02 의 입력이 이 경로로 적용된다(B 패턴 조회가 잡지 못함) → (22) 에서 주소 비교 조회로 교체.

- 2026-10-25 (22): **IReturn 캐스트 조회 = 주소까지 비교 + `.selector` 슬롯 제거 (사용자 승인)**. 기록 = engine_fixes F73.
  - 원인: visitor 가 인터페이스 캐스트를 `TypeConversion(typeName, expression)` 으로 만드는데, Pattern B 조회는 `FunctionCallContext`
    를 기대해 한 번도 실행되지 않았다 → 인터페이스·함수 이름만 맞추는 대체 조회가 모든 캐스트 호출을 처리(주소 무시).
  - 수정: Pattern B 를 `TypeConversion` 으로 고치고(키 = 인터페이스, 주소 식별자, 함수), 대체 조회 삭제. 캐스트 인자가 식이면 ⊤.
    호출 인자는 지금처럼 무시(사용자 결정). 슬롯 스캐너: 호출 없는 함수 멤버 참조(`I.f.selector`)는 IReturn 슬롯 아님.
  - 결과: rebuilt 48 일치 41 (s24 — s23 과 판정·메시지 완전히 동일, 대체 조회로 받던 6 케이스도 Pattern B 로 같은 값),
    `temp/test_slot_roundtrip.py` 48/48(29_H_08 해소), 새 `temp/test_ireturn_cast.py`(7), temp 전부 통과.
  - 이어서(사용자 승인): 디버그 입력 Pattern B(`process_ireturn_cast`)도 본문 있는 컨트랙트·라이브러리 캐스트를 명시적 오류로
    (Pattern A 와 같은 규칙 — 예전엔 받아 두고 실행 시 조회가 인터페이스만 봐서 조용히 무시). s25 = s24 동일, 테스트 10.

- 2026-10-25 (23): **B 결함 = 명시적 미지원 + 31_H_01 rename (사용자 결정, DeclID = future work)**. 기록 = engine_fixes F74.
  - 파라미터·named return 이 같은 이름의 상태변수를 가리면 `UnsupportedShadowing` — 함수 CFG 생성(`StaticCFGFactory.make_function_cfg`,
    예전엔 상태변수 주입이 파라미터를 덮어씀)과 나중에 선언된 상태변수 주입(`ContractAnalyzer` 상태변수·상수) 두 곳. 지역변수 가림은
    기존 D8(보관·복원)이 처리.
  - 31_H_01: `wantToCVX(uint256 want)` → `_want`(타깃 contraction·case JSON·deps w31 `SOURCE_EDITS`). 결과 Violated(기대 일치).
  - rebuilt 48 일치 **42** (s26 — s25 대비 31_H_01 만 바뀜).
  - 재빌드에서 새로 Invalid 4 단위(48 케이스 의존 폐포 밖): w8 NFTXVaultUpgradeable·w110 StakedCitadel(`_name`), w61 CreditLine·
    YearnYield(`_owner`) — 가려지는 상태변수가 모두 **부모의 private**(Solidity 이름 해석으로는 보이지 않아 실제 가림이 아님).
    이름 하나짜리 환경에선 그래도 충돌한다 → (사용자 결정) `SOURCE_EDITS` 로 파라미터 rename(`nameArg`/`symbolArg`/`ownerArg` —
    w8·w110 은 `_symbol` 도 같은 이유). 42 프로젝트 전부 Complete, s27 = s26 동일(42/48).
  - 환경 구조(상태변수를 함수마다 이름으로 복사해 넣는 방식 vs 이름 해석 + 소유자 키 storage 조회)는 다른 LLM 검토 프롬프트 작성.

## 7. 추후 구현·결정 대기 목록 (2026-10-22 정리 — §6 에 흩어진 항목을 모음, 완료 시 여기서 지운다)

### 7.1 엔진 의미론 (Phase 5~7)
- Phase 5: D10 루프 본문 intent 거부. (D7 재귀·D9 함수 포인터 ⊤·Yul P0-a 는 2026-10-23 (16) 완료)
- Yul P0-b: 블록 단위 파싱 + if/switch/for CFG화, Yul 함수, `return`/`stop` 종료 sink, `.slot`/`.offset` 접근.
- Yul 정밀도: 블록 안 값 번호(같은 식 = 같은 값). (16_H_04 의 실제 원인은 구간 비트 연산이었다 — (18) 에서 해결)
- **future work (사용자 결정 2026-10-23)**: 외부 호출 뒤 `address(this).balance` 효과 미반영 — 외부 호출이 이 컨트랙트로 ETH 를
  보내도 값이 진입 값 그대로라 numscout_EthereumGod 이 Satisfied(unsound). 외부 호출 뒤 havoc 이 해법 후보.
- 배열 선택자 셀 (2026-10-24 사용자 결정: **지금은 수렴하므로 그대로 둔다** — 반복 낭비만 있음. 손댈 때 계획 = 다른 LLM 검토 의견):
  - 1차 = (a) 읽기는 선택자 셀을 만들지 않는다(기존 유효 셀은 재사용, 쓰기·디버그 입력 제약만 생성). (b) 한쪽 셀을 의미상 읽기와
    병합 / (c) 의미적 동치 비교는 별도 정밀도 작업으로 보류.
  - join 에서 한쪽에만 있는 셀을 버리는 것은 "바인딩이 끊김"이 아니라 **sound 한 보수적 셀 제거**로 정의(쓰기 효과는 구체 셀·summary 에 남아야 함).
  - 선택자 identity = (위치, 정규화 식, 바인딩 문맥 — 식별자가 가리키는 선언·호출 프레임) → B 결함의 DeclID 와 함께 설계.
    vals 메타데이터는 보존 상태가 아니라 의존 변수 환경에서 재계산되는 파생 정보로. 불안정 선택자는 summary 로 흡수(구조적 widening).
  - 회귀 시나리오 6: ① `m[i]=9; x=m[i]` → 9 ② `m[i]=9; i=j; x=m[i]` → 옛 셀 재사용 금지 ③ 한쪽 분기에서만 읽기 → join 뒤 셀 없음
    ④ 한쪽 분기에서만 쓰기 → 셀을 버려도 쓰기 효과 포함 ⑤ `amounts[i+1]` 반복 쓰기 → widening 뒤 값·저장소 표현 함께 안정
    ⑥ 내부 호출로 같은 선택자 전달 → 같은 위치 유지, 프레임 종료 뒤 옛 셀 재사용 금지.
- callee 결과 재사용(같은 입력 환경이면 재해석 생략) — future work.
- **future work (사용자 결정 2026-10-25)**: IReturn 키 문법 확장 — 캐스트 인자를 식별자에서 일반 식으로(`IF(feeds[t]).get()`,
  중첩 캐스트). 호출 인자는 지금처럼 무시(함수당 슬롯 하나 — 어떤 인자든 준 값), 인자별 구분이 필요하면 그때.
- **future work**: 다른 컨트랙트 인스턴스의 storage 모델 — 본문 있는 컨트랙트 타입 외부 호출을 그 본문으로 실행(지금은 반환 ⊤).
- **future work**: B 결함(이름 충돌) — 환경 구조 변경. 설계안은 §7.5.
- 정적 배열 디버그 입력 일부만 범위 밖(i∈[0,5], 길이 3) 처리 — 미결정.
- Phase 6: do-while **빌드 경로 복구**(마이크로 실패: do-while 루프변수 미선언; try/catch 는 (18) 완료),
  do-while 안 continue/break, 다중 `_` modifier.
- Phase 7: 생성자 pre-state(D5 예외), 생성자 CFG 에 부모 상태변수 미주입(함수·modifier 는 주입).
- 정수(A): 오류 3분류(리터럴 = 컴파일 오류 / 디버그 입력 = 입력 검증 오류 / 그 외 = 엔진 불변식 위반), 부호 혼합 공통 타입
  (`uintM → intN`, N>M), 정확한 상수 평가기(리터럴 임의 정밀도 + `constant` 선언, 순환 = 오류 — C-6 과 하나로),
  비교 연산 폭 처리·`1 << x`/`2 ** x` 리터럴 타입 규칙 확인.
- B1: immutable 초기화식이 컴파일 타임 상수면 그 값(지금은 항상 ⊤ — sound·비정밀).
- B3: 상수 ** 상수 정확 계산(`_pow_fits`), 구간 지수는 보수 유지.
- 배열 bounds: 정상 접근 뒤 `length > i` 로 좁히기·범위 밖 접근 경로 차단(지금은 ⊤ 셀) — Phase 5. a → (외부) b → a 재진입 모델 (논의 대기).
- 저장소 후속: enum 값 mapping 의 기본값이 첫 멤버(D5 의 ⊤ 아님 — 기존), `from` 을 식별자로 쓴 줄 파싱 실패(기존 문법 한계).
- 루프 정밀도 (원인 확인 2026-10-23, 사용자 결정: **시간 되면** 해결 — 우선순위 낮음): 한 번만 도는 루프(n=1)에서도 `total += 1` 누적 변수가 [0,MAX].
  ① 주원인 = non-relational 구간 도메인 — 헤더 join 에서 i·total 상관(i=1 ⇒ total=101)이 사라져 본문이 매 반복 total 을
  키운다 → widening 필수(늦춰도 수렴 안 함 — "이른 widening" 아님). 저장소·weak update 무관(`total += 1` 로 재현).
  후보: 작은 상수 반복 횟수 unrolling / widening threshold / 관계 도메인. ② 보조 = checked 산술 오버플로를 [0,MAX] 로 근사
  (0.8 에서 `a + 1`, a∈[100,MAX] → [101,MAX] 가 정확 — 넘치는 상태는 revert) → 하한이 0 으로 붕괴.

### 7.2 증분 편집
- 미지원 수술(try/catch·do-while·break/continue 의 삭제·수정): 지금은 `IncrementalSurgeryError`. 구현 방향 = return/revert/
  break/continue 삭제를 **소스 구조 기반 fall-through** 로 일반화, builder 수술 함수의 조용한 `return []` 를 "이미 제거됨"/
  "처리 불가" 로 구분해 후자는 오류. (사용자: 추후 전부 구현)
- SourceError 분류 확대(지금은 미정의 타입 1곳). 애매하면 내부 실패가 기본.
- 프론트 revision 표시(현재 소스 revision ≠ committed revision 이면 CFG 결과를 "직전 정상 코드 기준"으로 — 지금은 진단 배너로 대신).

### 7.3 의존성 (C: 선언 header 레지스트리)
- 1A 등록 → 1B 해석(정적 타입·struct/enum·C3 선형화·using·시그니처) → 2 CFG 구성(순서 무관), 부모·라이브러리는 이름 연결,
  본문·바인딩은 디버깅 시점, 빌드 후 **해석 커버리지 검사**(의존성은 forge 가 컴파일한 소스라 Solidity 유효성은 보장됨).
- DeclarationId = 프로젝트 안의 이름 + 모호하면 오류(import alias 로 같은 이름 다른 선언 = limitation).
- (2026-10-23 R0~R3 완료: `_topo_order`·`_type_refs_in`·`_load_type_ref_pkls` 폐기, w36/w58/w65 순환 해소.)
- 남은 레지스트리: R4 서버 엔드포인트(업로드 UI 와 함께 — API `from_build`/`apply` 는 완료). (R5 게이트는 2026-10-24 켬)
- F1 (b) 지연 해석: 선언 header 단계(이름·종류 → struct/enum 정의 → 나머지) 로 같은 파일·다른 파일 참조를 한 방식으로 —
  되면 `reorder_members`·`reorder_top_level` 폐기.

### 7.4 프론트
- 브라우저에서 실제 탐색 동작 확인(Continue 의 callee 진입·분기점 정지 포함).
- 의존성 업로드 빌드 API(`DependencyStore` 자리), 업로드만으로 오류가 풀리는 경우 재검증.
- solc 진단(선택): 엔진이 못 잡는 컴파일 오류(인자 타입 등)를 추가 진단으로.

### 7.5 환경 구조 — 선언 정체성(DeclID) 기반 이름 해석 + Locals/Storage 분리 (future work, 설계안 2026-10-25)
배경: 노드 env 가 `dict[이름 → 변수]` 하나라 storage 와 지역이 같은 키 공간에 있다. 함수 CFG 생성이 자기·부모 상태변수(private 포함)를
이름 키로 미리 넣고, callee 진입·복귀도 이름으로 복사한다 → 파라미터·named return 과 상태변수 이름이 같으면 덮어씀(31_H_01 `want`,
OZ upgradeable 부모 private `_name/_symbol/_owner`). 지금은 `UnsupportedShadowing` + 소스 rename(§6 (23)). 다른 LLM 검토 2회
(10-23: 전면 DeclID 권고, 10-25: 아래 절충안 찬성 + 보완 3가지). 사용자 의견: DeclID 쪽이 나아 보임.

**목표 모델** — E_n = (L_n, S_n), 실행별 불변 S_0
- **정적 Resolver**: 식 참조마다 `Resolve(e, 소스 위치) → Ref`(LocalRef / StateRef(StateDeclID) / GlobalRef / ConstantRef /
  StoragePathRef(root, path)). **이름 해석과 값 조회를 분리** — "env 에 없으면 부모 조회"(동적)가 아니라 CFG 구성 때 소스 위치의
  lexical scope 로 결정(지역 선언이 아직 실행되지 않았다고 상태변수로 떨어지면 안 됨). 상속 함수 본문의 참조는 **그 본문의 lexical
  context**(Base.g() 안의 Base private x)로, `super`/`Base.f()` 는 먼저 실행 본문을 C3 로 정한 뒤 그 본문 기준. 필요한 메타데이터 =
  선언 ID·소유 컨트랙트·이름·타입·**가시성**(지금 미기록)·constant/immutable·소스 위치·scope, 상속 그래프, 컴파일러 버전.
- **Storage S_n**: `StateDeclID → 값` 희소 override. Read(S,k) = S[k] 또는 S_0[k]. join/widening = 키 합집합에서 Read₁ ⊔/∇ Read₂,
  비교 = 의미상(S_0 와 같은 override 제거 정규화 후 dict 비교 재사용). "키 없음 ≠ ⊤" (S_0 값 보존). S_0 = D5 정책(디버그 입력 >
  mutable 은 ⊤, initializer 는 진입값 아님) — 같은 위치에 모순 입력이면 오류. constant/immutable 은 별도 정적 값 테이블. S_0 객체는
  절대 수정하지 않는다(경로·실행 간 누수 금지 — 테스트).
- **Locals L_n**: 당분간 이름 키 + D8(가림 보관·복원) 유지, 단 Resolver 의 LocalRef 는 선언 정체성을 기록 → 나중에 지역도 DeclID
  저장으로 바꿀 때 저장 방식만 교체.
- **전역 Γ**: msg/block/tx 는 Storage 가 아닌 별도 실행 문맥(지금은 이름에 `.` 이 있으면 공유 — 의미 기준 아님).
- **호출**: callee = 새 Locals + caller 의 Storage 스냅샷 그대로 → 정상 복귀 경로들의 Storage join 을 caller 로. 이름 필터·파라미터
  예외 제거. 재귀 근사 쓰기 집합 = StateDeclID. 경로 간 공유 mutable dict 금지(copy-on-write).

**검토 의견(내 판단, 결정 필요)**
- **StateDeclID 형태**: 리뷰어는 "(컨트랙트 이름, 변수 이름)" 대신 고유 선언 ID 를 권했으나, 레지스트리가 프로젝트 전역 선언 이름 유일성을
  이미 강제한다(같은 이름 두 단위 = Invalid). 그래서 **(소유 컨트랙트, 변수 이름) 은 이 프로젝트에서 유일한 선언 ID** 다(0.5 부모·자식
  동명 상태변수도 소유자가 달라 구분). 장점 = 피클·레지스트리 로드·타깃 단위 교체에도 안정적(줄 번호·카운터 기반 ID 는 증분 편집·
  재빌드마다 바뀜). → 구조화 ID `StateDeclID(owner, name)` + "유일성은 레지스트리 불변식" 명시를 권함.
- **지역까지 전면 DeclID 를 지금 하지 않는 이유**: 지역 선언 ID 는 함수 안 위치(scope 경로)가 필요한데 **증분 편집이 라인 이벤트마다
  CFG 를 수술**하므로 ID 가 편집에 흔들리고, D8 재작성이 필요. Resolver 가 LocalRef 에 선언을 기록해 두면 이후 전환은 저장 교체만 남는다.
- **리뷰어가 다루지 않은 이 프로젝트 고유 쟁점**: (1) 증분 편집 — 선언 추가·삭제·수정(지역 `want` 를 앞에 추가 등)은 뒤 참조의 해석을
  바꾸므로 Resolver 결과를 해당 함수(상태변수 선언 변경이면 컨트랙트·자식 전체) 단위로 다시 계산해야 한다 — 트랜잭션 후보 적용 경로에
  포함. (2) 의존성 pkl 의 CFG 는 IR 에 Ref 를 실어 저장하므로 Ref 직렬화가 레지스트리의 이름 기반 persistent-id 와 맞아야 한다.
  (3) 프론트 노드별 상태 표시는 지금 env 전체를 내보낸다 — 희소 Storage 에선 "함수·callee·intent 가 참조하는 상태변수"의 Read 값만
  내보내고, 표시 이름은 Locals / State(`BaseStrategy.want`) 영역 분리(평면 dict 로 합치면 충돌 재발).
- **디버그 슬롯·intent**: 슬롯 문자열(`@Param want` / `@StateVar want`)은 그대로, 서버가 슬롯 종류로 Ref 를 정함. 같은 원문이 서로 다른
  상태변수로 해석되면(0.5 동명) 명시적 모호성 오류. intent 는 그 위치의 Solidity 해석을 따르고(파라미터 우선), 가려진 상태변수 직접
  참조는 미지원(문법 동결). 부모 함수에 단 intent 는 부모 lexical scope 로 해석. 복합 슬롯 `poolInfo[_pid].x` = 루트 StateRef + 인덱스
  식의 Ref — 선택자 셀 identity(§7.1)와 같은 기반.
- **경계**: storage reference alias(`S storage p = m[a]`, library `S storage s` 인자)는 이 변경으로 해결되지 않는다 — LocalRef 값이
  StoragePathRef 를 가리키게 하거나 참조 인자 효과 전달이 필요(별도 과제).

**이행 순서(안)**
1. **Phase 1 — Resolver 도입, 표현 유지**: `ref = resolve(expr, ctx)` / `env.read(ref)` / `env.write(ref, v)` 로 이름 접근을 모두 이전
   (Evaluation·Update·Refine·modifier 합성 `_rename_in_expr`·재귀 쓰기 집합·선택자 셀 무효화 `purge_scope`·intent·디버그 주입·슬롯·
   직렬화). Ref 는 아직 기존 이름 키로 변환, 두 선언이 같은 키가 되면 `UnsupportedShadowing` 유지. 기대 = 48 케이스·temp 결과 불변.
   modifier 이름 치환은 이 단계에서 선언 기준으로(잘못된 치환 검출).
2. **Phase 2 — E = (L, S) 교체**: 상태변수 주입·늦은 주입·callee 이름 필터 제거, Storage = StateDeclID 희소 + S_0, Γ 분리.
   이 단계부터 `UnsupportedShadowing` 제거. 평면 dict 직접 접근이 남지 않도록 Environment API 외 접근 금지 검사.
3. 이후 선택: 지역 DeclID 저장, storage alias.

**회귀 테스트(최소)**: 48 케이스(Phase 1 불변 / Phase 2 변화는 모두 설명) · Param vs State 같은 이름 · 부모 private vs 자식 Param(내부 호출
뒤 둘 다 보존) · named return vs State · 블록 지역 가림(D8 동일) · modifier 지역 vs 상태변수 · super/부모 함수의 private · 재귀 havoc =
StateID 만 · 배열·매핑 선택자 must-alias 보존 · 디버그 입력 A→B→A 격리 · join/widening 희소 Storage 의미 · S_0 불변.

**평가 기록**: 소스 rename 은 실험 자산 변경 — 원본 그대로 / 의미 보존 rename / `UnsupportedShadowing` 사례를 구분해 기록.
현재 rename = 31_H_01 타깃(`want`→`_want`), deps `SOURCE_EDITS` w31·w8·w110·w61(`build_project_deps.py`). 리뷰어 권고 = 논문 최종 평가 전
우선순위 상향.
