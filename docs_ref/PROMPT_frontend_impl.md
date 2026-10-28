# [구현 관점] IQDebug 디버거 탐색(navigation) 기능 설계 논의

당신은 정적 분석 기반 디버거의 프론트엔드·엔진 인터페이스 설계를 함께 검토하는 엔지니어다.
아래 맥락과 이미 확정된 결정을 전제로, **구현 설계안**을 제시해 달라. 확정된 결정은 다시 논쟁하지 말고,
"미결 사항"에 대해서는 선택지와 trade-off 를 제시하되 최종 결정은 내가 한다.
원칙: **의미론적으로 올바른 동작이 기본**이다. 임시방편(특정 케이스만 통과시키는 처리, 조용한 폴백)은 제안하지 말고,
불가피하면 "임시방편"이라고 명시해 선택지로만 제시하라.

## 1. 시스템 맥락

- IQDebug = Solidity 용 **intent-aware abstract debugger**. 구체 트랜잭션을 재생하지 않고, 함수 CFG 위에서
  abstract interpretation(구간/주소집합 등 도메인, 루프는 widening/narrowing fixpoint)으로 계산한 **노드별 추상 상태**를
  탐색한다. 개발자는 `@During`/`@Post` intent 를 달고, 엔진이 Satisfied / Warning / Violated / Unreachable 판정을 낸다.
- 엔진 API(백엔드, Python) 3단 계약 — 체인 종속 analyze → evaluate → verify:
  - `analyze`(코드 변경): 소스를 **1 입력 = 1 statement** 레코드로 받아 CFG 를 **증분** 구성 → 구조 + 디버깅 입력 슬롯.
  - `evaluate`(디버깅 입력 변경): 타깃 함수를 entry 부터 해석 → 노드별 상태(frozen) + callee 상태 + loop delta.
  - `verify`(intent 변경): 보존된 상태 위에서 intent 판정만.
  - 코드 변경 시 CFG 만 증분 수정되고 상태는 무효화된다(다음 evaluate 에서 entry 부터 재해석).
- 프론트(React/TS)는 `AnalysisResult` 를 받은 뒤 **탐색은 전부 로컬**로 한다(탐색 중 엔진 호출 0 — "quick" 의 근거).
  데이터 계약(`frontend/src/types/result.ts` ↔ `engine_api.py` 1:1):
  - `CFG = {entry, exit, error_exit?, nodes[], edges[]}`
  - `CFGNode = {id, src_line(-1=없음), flags[], cond_type?, state{변수: 직렬화값}, callees?[CalleeRef], loop_delta?}`
    - flags 예: `condition`, `merge`(join), `loop_exit`, `fixpoint_evaluation`(루프 join), `exit`, `call`, `opaque`
  - `edges = {from, to, kind: normal|true|false|back_edge}`
  - `CalleeRef = {key, label, enterable, reason?, contract, name, start_line, end_line}`; `result.callees[key]` = callee CFG(호출 지점별 상태 포함)
- 노드 상태 = 그 노드 문장의 **실행 후(out) 상태**. 조건 노드 상태는 분기 정제 전, 분기 진입 노드(`if_true_*`, `else_block_*`)가 정제된 상태를 가진다.
- 현재 구현(`frontend/src/debug/useNavigation.ts`) 요지:
  - stepForward: back edge 제외 후속 중 feasible 첫째로 이동 + 나머지 하이라이트. 루프 헤더/루프 join 이면 루프를 접고 루프 다음 문장으로.
    루프 꼬리면 감싸는 루프 탈출. stepBackward: feasible 선행 첫째로 이동 + 하이라이트(루프 접기 없음).
  - stepInto: 노드의 enterable callee 중 첫째로 프레임 push. stepOut: pop(호출 지점 노드로 복귀).
  - continue / next·prev breakpoint: 분기에서 **첫 feasible 후속만** 따라감(다른 분기의 breakpoint 를 놓침 — 결함).
  - breakpoint 는 **프레임별 라인 집합**. merge/loop_exit/fixpoint/exit 노드는 착지·breakpoint 금지.
  - feasibility = 상태 문자열에 `'⊥'` 포함 여부 + error_exit(sink).

## 2. 확정된 결정 (전제)

탐색 대상·범위
1. 루프 본문에는 **절대 들어가지 않는다**. 루프는 fixpoint 요약으로만 다룬다(헤더에서 루프 효과 = loop delta 표시).
   forward·backward 모두 루프를 접는다. (루프 본문 intent 도 금지되어 있음)
2. Step Into 대상 = 같은 컨트랙트 internal 호출, 상속(부모·`super`) 함수, 라이브러리 호출. 주소/인터페이스/컨트랙트 타입 변수를
   통한 외부 호출은 **opaque**(진입 불가; 반환값 = 선언 타입 ⊤ 또는 `@IReturn`). 상태 효과: 상속·라이브러리(같은 storage 문맥)는
   상태변수 변경 반영, 외부 호출은 반환값만.
3. 재귀: 재진입 지점은 근사(⊤·havoc)이므로 opaque 와 동일하게 Step Into 불가(첫 호출은 진입 가능).
4. 한 statement 에 진입 가능한 호출이 여럿이면 같은 노드에서 Step Into 를 반복할 때 **엔진의 평가 순서**대로 다음 호출로 진입.
5. Step Out 은 **호출 지점 노드**로 복귀(그 노드 상태 = 호출 효과 반영된 실행 후 상태).
6. modifier 는 실제 modifier **코드가 화면에 보여야** 한다(같은 컨트랙트 또는 부모 컨트랙트, 다른 파일일 수 있음). 의존성 코드는
   프론트에 업로드되어 엔진이 소스에서 CFG 를 만든다고 가정한다(현재 프론트 업로드 UI 는 스텁, 엔진은 pkl 로드만 지원).

연산 집합
7. 분기: **Step Forward / Select Successor / Step Backward / Select Predecessor / Step Over Branch / (backward) Step Back Over Branch**.
   - Step Forward 에서 feasible 후속이 2개 이상이면 이동하지 않고 후보를 제시 → Select Successor 로 하나를 고른다.
     이것은 "경로 선택"이 아니라 **초점 선택**이다(그 분기가 실제로 실행됐다는 의미 아님). 선택 기록에 의존하지 않는다.
   - Select Successor/Predecessor 의 착지점 = 그 간선을 따라 처음 나오는 **문장 노드**(분기 첫 문장).
   - Step Over Branch: 분기 조건 → 그 분기 구조의 join(합쳐진 상태)으로. 역방향 짝은 join → 조건 노드.
8. merge(join) 노드와 loop exit 노드도 **착지 대상**이다(join = 합쳐진 상태, loop exit = fixpoint ∧ ¬조건).
9. Step Into / Step Out, Continue(진입 가능한 callee 안에 도달 가능한 breakpoint 가 있으면 callee 로 진입, 없으면 복귀 후 계속),
   Next/Prev Breakpoint, **Next/Prev Intent**(판정별: 예 Violated/Warning), **Restart**(entry 로).
10. evaluate 직후 커서 = 첫 도달 breakpoint, 없으면 함수 끝. 스택은 타깃 프레임 하나로 리셋.
11. 제외: Navigate Back/Forward(기록 기반), Run to Line(breakpoint 와 동일), Select Call, 루프 진입.
12. "왜 도달 불가인가"는 연산이 아니라 **출력**으로 보여준다(⊥ 를 만든 조건 노드 표시) — 엔진이 그 원인을 기록해야 함.
13. feasibility 는 문자열 탐지가 아니라 엔진이 노드 상태 분류(reachable / unreachable(⊥) / error-sink)를 **명시적으로** 내려준다.

## 3. 미결 사항 (선택지와 trade-off 를 제시해 달라)

- U1. **표시 상태 규약**: 커서 위치에 실행 후 상태를 보여주면 일반 디버거("이 줄 실행 직전")와 반대다.
  선택지: (a) 실행 후만 (b) 실행 전만(= 선행 out 의 join = in-state, 엔진이 in-state 를 내려줘야 함) (c) 둘 다.
  참고: intent 언어의 `@During` 은 이미 Before/After 를 구분한다(`varRef(Before)`). 분기 첫 문장 착지 시 "정제됐지만 아직 실행 전"
  상태는 (b)/(c) 에서만 보인다는 점도 고려.
- U2. **분기점에서 Continue**: feasible 분기가 여럿일 때 (i) breakpoint 가 한 분기에만 있으면 그쪽으로 자동 진행할지
  (ii) 여러 분기에 있으면 분기점에서 멈추고 Select 를 요구할지 vs 고정 순서로 하나를 고를지(그러면 나머지는 도달 불가가 됨).
  breakpoint 가 join 이후에만 있으면 경로 무관하게 도달.
- U3. **join 표시 라인**: 현재 if/else-if 체인은 **단계마다 join 이 하나씩** 있고(중첩: 안쪽 join → 바깥 join), 모두 `} else if (...) {`
  줄에 등록되어 있다(예: 4행 if, 6행 else if, 8행 else, 10행 `}` 인데 join 둘 다 6행). 이 등록은 증분 편집(else 삽입)에 쓰이므로
  옮기기 어렵다. 착지용 **표시 라인**(체인 끝 `}`)을 따로 둘지, 연속된 join 들을 한 step 으로 접을지.
- U4. **modifier 흐름 모델**: 합성 CFG 를 평평하게 따라가며(앞부분 → `_` → 본문 → 뒷부분) 위치(파일/컨트랙트)를 전환해 보여줄지,
  modifier 를 프레임으로 다룰지. modifier 본문 intent 허용 여부.
- U5. 루프 본문 라인에 breakpoint 를 금지할지(루프 진입이 없으므로 걸려도 멈추지 않음; D10 과 일관).
- U6. breakpoint 키: 프레임별 라인 → **소스 위치(파일, 라인)** 로 바꿔야 Continue 가 callee 로 진입할 수 있다. 같은 callee 를 여러 지점에서
  부르면 상태가 호출 지점별로 다르다.
- (후속) a → (외부) b → a 재진입 모델은 별도 논의 예정.

## 4. 요청 사항

1. 탐색을 **상태 머신**으로 설계: 구성(스택[(frame, node)], 후보 집합, breakpoint, 표시 정보)과 연산별 전이 규칙(전제·결과·불변조건).
   특히 루프 접기(forward/backward), 분기 후보 계산(구조 노드 건너뛰기), join 연속 처리, 프레임 경계(callee 첫 문장에서 backward 등).
2. 데이터 계약 변경안(엔진 → 프론트): 노드 상태 분류, join 표시 라인, (U1 에 따라) in-state, 분기 착지 노드, 호출 평가 순서,
   도달 불가 원인, modifier 소스 위치, 의존성 소스 업로드 → CFG 빌드 흐름.
3. 각 연산의 알고리즘과 엣지 케이스(빈 분기, else 없는 if, 분기 안 중첩 분기, return/revert 로 끝나는 분기, error_exit,
   루프 직후/직전, 함수 끝, callee 가 다른 파일, 재귀·opaque 호출, 도달 불가 노드).
4. Continue/Next·Prev Breakpoint/Next·Prev Intent 의 **순서 정의**(경로 선택 없이 결정적이어야 함).
5. 검증 계획: 연산별 테스트 케이스 목록과 불변조건(예: 탐색은 상태를 바꾸지 않는다, 커서는 항상 feasible 착지 노드).
6. 미결 사항 U1~U6 에 대한 선택지·trade-off·권장안.
