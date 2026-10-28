# [논문 관점] IQDebug 디버깅 탐색 절의 논리·서술 및 operational semantics 구조 논의

당신은 프로그래밍 언어/소프트웨어 공학 박사논문의 설계 장(chapter)을 함께 다듬는 공동 저자다.
대상 절은 "Source-Level Debugging over Abstract Program States"(탐색 연산: forward/backward, step into/out,
breakpoint, 분기·루프 처리)이며, 이 절을 **operational semantics** 로 정식화하려 한다.
아래 확정 사항은 다시 논쟁하지 말고, 미결 사항은 논리적 명분과 함께 선택지를 제시해 달라(최종 결정은 내가 한다).
LaTeX 조판은 필요 없다 — **논리 구조, 정식화 뼈대(구성·관계·규칙 목록), 서술 방향, 피해야 할 주장**을 달라.

## 1. 도구와 핵심 주장

- IQDebug = Solidity 용 intent-aware abstract debugger. 구체 실행 trace 를 재생하지 않고, abstract interpretation 이 CFG 노드마다
  저장한 추상 상태(구간, 주소집합 등)를 **소스 수준 프로그램 지점** 단위로 탐색한다. 개발자 intent(`@During`/`@Post`)는 같은 지점에서
  Satisfied / Warning / Violated / Unreachable 로 판정된다.
- 핵심 주장(유지): 탐색은 추상 해석기를 다시 호출하지 않는다(상태 재사용). 하나의 구체 경로를 고르지 않고, 추상 분석이 feasible 하다고 본
  제어 흐름 관계를 그대로 드러낸다. 루프는 iteration 단위가 아니라 fixpoint 요약으로 다룬다.
- 업데이트 3분류(유지하되 서술 수정 필요): intent 만 변경 → 재검증만 / 디버깅 입력 변경 → 타깃 함수를 entry 부터 재해석 /
  **코드 변경 → CFG 만 증분 수정, 상태는 무효화되어 다음 입력 적용 시 entry 부터 해석**.
  ⚠ 현재 원고는 "코드 변경 시 영향받은 지점부터 해석을 재개"라고 쓰는데, 설계 변경(CFG 구성과 추상 해석 분리)으로 더 이상 사실이 아니다.

## 2. 확정된 설계 (서술에 반영할 것)

- 루프: 본문에 **들어가지 않는다**. forward·backward 모두 루프를 하나의 요약 전이로 접고, 루프 헤더에서 loop delta
  (진입 전 상태 ↔ fixpoint 상태에서 달라진 변수)를 보인다. 루프 본문 intent 는 언어 차원에서 금지.
- 분기 연산(초점 선택이지 경로 선택이 아님):
  Step Forward(후속 feasible 이 하나면 이동, 여럿이면 후보 제시) / Select Successor / Step Backward / Select Predecessor /
  Step Over Branch(조건 → join 의 합쳐진 상태) / 그 역방향 짝(join → 조건). 선택은 탐색 기록에 의존하지 않는다(history-independent).
  선택 착지점 = 그 분기의 첫 문장. infeasible(⊥) 후속·선행은 후보에서 제외.
- join 노드와 loop exit 노드도 착지 대상(join = 선행 상태들의 합, loop exit = fixpoint ∧ ¬조건).
- Step Into 대상 = 같은 컨트랙트 internal, 상속(부모·super), 라이브러리 호출. 주소/인터페이스/컨트랙트 타입 변수를 통한 외부 호출은
  opaque(반환값 = 선언 타입 ⊤ 또는 `@IReturn`, `@IReturn` 은 caller 쪽 해석을 잇게 할 뿐 진입을 가능하게 하지 않음).
  ⚠ 현재 원고의 `Token token; token.transfer(...)` 로 진입 가능하다는 예시는 **삭제/수정 대상**(컨트랙트 타입은 런타임 검사가 없어
  주소의 실제 코드를 보장하지 않음).
- 재귀: 재진입 지점은 보수적 근사이므로 opaque 와 같이 진입 불가.
- 한 statement 의 여러 호출: 같은 지점에서 Step Into 를 반복하면 엔진의 평가 순서대로 진입(Solidity 는 인자 평가 순서를 명세하지 않으므로
  "엔진의 평가 순서"로 정의).
- Step Out: **호출 지점 노드**로 복귀(그 노드의 상태가 callee 의 반환값·상태 효과를 반영). ⚠ 원고는 "호출 지점 다음 지점"이라고 씀 — 수정.
- modifier: 실제 modifier 코드를 보이며 탐색(합성된 실행 흐름: 앞부분 → `_` 위치에서 본문 → 뒷부분).
- breakpoint 연산: Continue(진입 가능한 callee 안의 breakpoint 포함), Next/Prev Breakpoint, Next/Prev Intent(판정별), Restart.
  breakpoint 는 실행을 멈추는 것이 아니라 미리 계산된 상태 위의 탐색 목표. 도달 불가 breakpoint 는 표시만 하고 멈추지 않음.
- 도달 불가의 원인(⊥ 를 만든 조건)은 연산이 아니라 출력으로 제시.
- 제외: 기록 기반 Navigate Back/Forward, Run to Line(breakpoint 와 동치), 루프 진입.

## 3. 미결 사항 (명분과 함께 선택지 제시)

- P1. **표시 상태 규약**: 커서 지점에 (a) 실행 후 상태 (b) 실행 전 상태 (c) 둘 다. 일반 디버거는 "이 줄 실행 직전"이다.
  어떤 선택이든 명분이 필요하다. 고려할 점: 저장 상태가 노드의 out-state 라는 구현 사실 / intent 언어가 이미 Before·After 를 구분 /
  분기 첫 문장 착지 시 "정제됐지만 아직 실행 전"인 상태는 (b)/(c) 에서만 드러남 / join·loop exit 착지의 자연스러움.
- P2. **분기점에서 Continue**: 여러 feasible 분기에 breakpoint 가 있을 때 멈추고 선택을 요구할지, 결정적 순서로 하나를 고를지
  (후자면 나머지 분기 breakpoint 는 그 탐색에서 도달 불가). "구체 경로를 고르지 않는다"는 주장과의 정합성을 따져 달라.
- P3. modifier 를 평평한 흐름으로 볼지 별도 프레임으로 볼지(정식화에서의 표현 차이).
- P4. Next/Prev Breakpoint·Intent 의 **순서**를 무엇으로 정의할지(경로 선택 없이 결정적이어야 함; 예: 루프를 접은 CFG 의 위상 순서).
- (후속) 외부 호출 중 재진입(a → 외부 b → a) 모델.

## 4. Operational semantics 뼈대 (초안 — 빠진 것·과한 것을 지적해 달라)

두 층으로 분리하는 것을 제안한다: **분석 층**(코드/입력/intent 변경이 구조·상태·판정을 바꿈)과
**탐색 층**(고정된 구조·상태·판정 위에서 디버거 구성만 바뀜; 탐색은 상태를 바꾸지 않는다 — 이것 자체가 보일 성질).

- 정적 구조(analyze 산출): 함수별 CFG G_f = (N, E, entry, exit, error_exit, return_exit), 간선 종류(true/false/back/normal),
  노드 종류(조건, join, 루프 join, loop exit, 호출 …), 라인 대응 src: N → Line / L: Line → 𝒫(N), 루프 구조(헤더·join·exit·본문, 중첩),
  호출 정보 calls: N → [(callee, enterable, 평가 순서)], modifier 합성 관계.
- 분석 결과(evaluate/verify 산출, 탐색 중 고정): 디버깅 입력 D, 상태 σ: N → AbsEnv ∪ {⊥}, 호출 지점별 callee 상태
  Σ: (site, callee) → (N' → AbsEnv), loop delta Δ: 헤더 → 변경 변수, 판정 V: Intent → {Sat, Warn, Viol, Unreach},
  도달 불가 원인 cause: N ⇀ N(조건 노드).
- 유도 함수: feasible(n) ⇔ σ(n) ≠ ⊥ ∧ n ≠ error_exit; back edge 제외 feasible 후속/선행; 착지 가능 노드 집합 Land;
  분기 착지 first(e)(간선 e 를 따라 처음 나오는 문장 노드); 루프 접기 fold(loop) / unfold 없음; join(cond)(분기 구조의 join);
  pre(n), post(n)(P1 에 따라); 순서 ≺(P4); enterable(n, k)(k 번째 호출).
- 디버거 구성: ⟨스택 [(frame, node)], 후보 집합 C(선택 대기), 호출 진입 색인, breakpoint 집합 B(소스 위치), 표시 정보⟩.
- 전이: 탐색 층 ⟨config⟩ —op→ ⟨config'⟩, op ∈ {Forward, SelectSucc, Backward, SelectPred, OverBranch, BackOverBranch,
  Into, Out, Continue, NextBP, PrevBP, NextIntent, PrevIntent, Restart}.
  분석 층: analyze(코드 변경 → G', σ 무효화), evaluate(D → σ, Σ, Δ; 구성 리셋: 스택 = [타깃], 커서 = 첫 도달 breakpoint 또는 함수 끝),
  verify(intent → V).
- 보일 만한 성질 후보: (1) 탐색은 σ·V 를 바꾸지 않는다 (2) 커서는 항상 feasible 한 착지 노드 (3) backward·select 는 탐색 기록과 무관
  (4) 루프 내부 노드는 어떤 탐색 연산으로도 착지되지 않는다 (5) opaque·재귀 재진입 지점에서 Into 는 항등.

## 5. 엔진 리팩터링 방향 (설계 장 전반에 영향 — 서술 반영 필요)

원고의 분석 절(Section "analysis", 증분 CFG 구성·추상 해석)은 SolQDebug 에서 물려받은 **"편집 시점 즉시 평가"** 모델을 기술하고 있다.
엔진을 다음과 같이 리팩터링 중이며(2026-10-21 확정, 구현 진행 중), 탐색 절뿐 아니라 분석·intent 절의 서술과 정식화에도 반영되어야 한다.

**동기**: 즉시 평가 모델에서는 (i) 뒤에 정의된 callee/modifier(forward reference)가 구성 중 CFG·상태를 손상시켰고
(ii) 이를 메우려는 pending 재해석 메커니즘이 복잡·비결정적이었으며 (iii) 재귀 호출 비용이 무제한이었고
(iv) 해석 불가 지점을 ⊤ 로 조용히 덮어 **unsound 하거나 원인 불명인 결과**가 나왔고 (v) 디버깅 실행 사이에 상태가 새었다.
우선순위는 복잡한 interprocedural summary 나 Solidity 전 기능 지원이 아니라 **정확성·속도·유지보수성**이다.

**확정 결정 (D1–D11)**
- D1. **CFG 구성과 추상 해석의 분리**. 편집 시점에는 증분 파싱·CFG 구조·IR·선언·스코프·modifier 참조·디버깅 슬롯·intent 등록만 한다
  (식 평가·호출 해석·fixpoint·값 전파 없음). 추상 해석은 **디버깅 입력이 주어졌을 때 타깃 함수 entry 부터** 수행한다.
  → 분석 절의 "증분 해석 재개" 서술과 업데이트 3분류의 "코드 변경" 항목이 바뀐다.
- D2. **⊥ = 도달 가능한 구체 상태 없음(γ(⊥)=∅)으로만** 쓴다. 미평가·미지정 입력·계산 불가·미지원 식은 ⊥ 가 아니다.
  모르는 정상 값 = typed ⊤, Solidity 기본값이 정해진 경우 = 정확한 기본값. "미평가"는 별도 상태이며 해석이 끝나면 남지 않는다
  (그래프상 도달 가능한데 미평가면 내부 오류, 경로가 없으면 명시적 ⊥). 루프는 수렴 후 상태와 loop delta 로만 노출.
  → Unreachable 판정과 탐색의 feasibility 가 같은 정의(σ(n)=⊥)를 공유한다.
- D3. **내부 호출은 디버깅 시점 on-demand 해석**. 구성 시점엔 호출 식만 보존하고 callee 연결·재해석 없음. 정적 이름·타입 해석
  (오버로드, 상속/override, library·using-for, super, cast/호출 판정)은 선언이 모두 들어온 뒤 수행하고, 해석 중 호출을 만나면 callee 를
  inline 해석한다. 미해결 호출의 ⊤ 폴백은 제거(→ 오류).
- D4. **진단 최소화**: 사용자 진단은 (a) 컴파일러가 거부할 의미·타입 오류(해석 중 확인) (b) annotation 규칙 위반(루프 본문 intent 등)
  두 가지뿐. 편집 중 callee 미정의는 오류가 아님. infeasible 경로의 미해결 callee 는 분석하지 않으므로 보고하지 않음(path-insensitive 의미와 일관).
  미지원 기능은 ⊤ 로 숨기지 않고 드러낸다.
- D5. 상태변수 초기 환경: 미지정 mutable 상태변수 = typed ⊤(배포 후 트랜잭션으로 바뀌었을 수 있음; multi-tx 는 범위 밖),
  constant 는 정적 값, immutable 은 알려지면 유지. 생성자를 타깃으로 할 때의 배포 전 의미론은 별도(마지막 단계).
- D6. modifier 는 디버깅 시점에 실행 사본 위에서 **합성**(인자 바인딩, 선언 순서대로 바깥→안쪽 중첩, 본문 return 후 modifier 나머지 실행,
  `_` 없는 경로 = 본문 미도달). try/catch·do-while 지원.
- D7. **재귀: 보수적 재귀 호출 근사**. 활성 호출 스택에 같은 함수가 재진입하면 즉시 근사(반환값 typed ⊤, mutable 상태 havoc,
  pure/view callee 면 storage 유지, 인자에서 닿는 memory 객체·storage 포인터 havoc; 인자 부수효과는 근사 전에 평가). fixpoint 가 아니며
  정상 반환 후 상태의 과대근사이다. 그로 인한 Warning 은 정밀도 손실이지 unsound 가 아니다.
- D8. 지역변수 **lexical block scope**(0.5+): 블록 exit 에서 그 블록 선언 제거, shadowing 시 바깥 binding 보관·복원.
- D9. 함수 포인터 호출 = D7 과 같은 근사. Yul 은 지원 연산(산술·비교 등)은 정밀, 그 외만 보수적 근사(값 연산 ⊤, `sstore` → storage havoc,
  `mstore` → memory havoc).
- D10. 루프 본문 `@During` 금지(연구 범위상 의도된 제한, annotation 단계에서 거부).
- D11. 판정 = Satisfied / Warning / Violated / Unreachable. Warning = 만족 증명 불가·위반 배제 불가, ⊤ 는 Warning.

**추가 확정 (2026-10-22)**
- 입력 전제: **입력 1개 = statement 1개**(`if (x) { return; }` 는 `if (x) {`⏎`}` 와 `return;` 두 입력). 타깃 컨트랙트·함수는 입력 쪽 책임
  (위반 시 명시적 오류), 의존성 소스는 빌드 시 정규화 가능.
- 같은 파일 안에서 **선언 전 참조**(예: 뒤에 선언된 라이브러리를 `using`)는 증분 입력 순서 전제상 미지원(오류) — solc 자체는 허용하므로
  "컴파일 오류"가 아니라 **도구의 입력 전제**로 서술해야 한다.
- `continue`/`break` 의 대상 = 문법적으로 감싸는 가장 안쪽 루프(Solidity 정의 그대로).
- 외부 호출: 반환값만 사용(⊤ 또는 `@IReturn`), 외부 컨트랙트가 타깃을 호출하는 경우는 고려하지 않음.
  타깃 → 외부 b → 타깃 재진입 모델은 **미결**.
- 평가는 프로젝트별 의존성으로 수행(공용 의존성 풀 사용 시 같은 이름의 다른 판이 섞이는 문제 배제).

**범위 밖 / limitation (서술 시 구분 필요)**: 정밀 재귀 분석(summary fixpoint 등), 함수 포인터 정밀 dispatch, Yul 저수준 의미 전체,
정밀 alias 분석, multi-transaction, 루프 본문 intent, 길이를 모르는 동적 배열의 정밀 처리.
논문에서는 **"보수적 근사로 지원" / "정밀 분석만 미지원" / "입력이 지원 범위 밖" / "intent 언어에서 의도적 금지"** 를 구분해 기술하고자 한다.

## 6. 요청 사항

0. §5 리팩터링 방향을 반영할 때 **분석 절·intent 절·탐색 절 각각에서 바뀌어야 할 논리**(무엇이 새 주장이고 무엇이 철회되는지),
   그리고 동기(i)~(v)를 설계 기여로 어떻게 서술할지(단순 구현 개선이 아니라 의미론 정리 — 특히 D1·D2·D4 를 "분석 결과의 의미가
   명확하다"는 성질로 묶을 수 있는지). D7·D9 같은 근사의 soundness 주장 범위도 검토해 달라.
1. 이 절의 **논리 흐름**(무엇을 먼저 정의하고 어떤 순서로 연산을 소개할지)과 섹션 구성안.
2. operational semantics 뼈대의 검토: 빠진 구성 요소/파라미터, 불필요한 것, 두 층 분리의 타당성, 규칙 목록(연산별 1개 이상).
3. 미결 사항 P1~P4 각각의 선택지와 **논문에서 쓸 수 있는 명분**.
4. 일반(구체) 디버거와의 대응·차이를 어떻게 서술할지(특히 "초점 선택 ≠ 경로 선택", 루프 요약, 표시 상태 규약).
5. 원고에서 **피해야 할 주장** 목록(사실과 다르거나 과장될 수 있는 것)과 용어 통일 제안
   (program point / node / landing node / focus / feasible 등).
