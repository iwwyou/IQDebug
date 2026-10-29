# ISSTA '27 — 할 일 전체 목록 · 순서

> 2026-10-29 작성. **구현 세션 소유** 문서다. `paper/` 항목은 소유자를 표시해 두었고 그쪽은
> 논문 세션이 처리한다.
>
> 마감: **abstract 2027-01-08 / full 2027-01-11** → 작성 시점 기준 약 **2.5개월**.
> 사용자가 일정을 조절한다. 이 문서는 "무엇을 어떤 순서로" 만 담는다.
>
> 근거 문서: `paper/ISSTA_PLAN.md`(기여 경계·RQ·계측·데이터셋),
> `paper/SEMANTICS_AUDIT.md`(의미론 정식화·미결 결정),
> `evaluation/reviews/round9_operations.md`(operation 계층·성질·결정 로그·완료 내역).

---

## 진행 상황 한눈에

| | 완료 | 남음 |
|---|---|---|
| 의미론·탐색 | `navEdges` 단일 관계, 루프 탈출 요약(K3), try/catch 조건 체인 재설계, EXIT 표시 줄(K2), `for_init` 비착지(K1), 프론트 P1 7건 | B1~B9 |
| 계측 | — | C1~C8 전부 |
| 하네스 | — | D1~D5 전부 |
| 데이터셋 | pragma 스캔, 타깃 선정(551 파일 / 1,941 실질 타깃) | E1~E4 |
| 측정 | 한 줄 다중 호출 빈도, try/for/while 구문 빈도 | F0~F4 전부 |

---

## A. 결정 (코드보다 먼저)

| | 항목 | 상태 |
|---|---|---|
| **A-D3** | Step Out 복귀 위치 | **확정 (2026-10-29)** — 호출 지점 유지 + 변화 집합 `Δ(n)` 인라인 표시. `phase` 좌표는 **도입하지 않는다**(설정 `C` 가 커지지 않는다). `paper/SEMANTICS_AUDIT.md` §5 에 근거·실측 기록 |
| A-D4 | Previous Breakpoint | **확정** — operation 목록에서 제거(전함수 아님) |
| A-D5 | revert/require 실패 경로 취급 | **확정** — `revert` 는 루프 탈출 **요약**에만 1급, ERROR 는 착지 대상 아님, try/catch 미매치 경로는 비표현. scope/threats 에 명시 |
| A-D8 | 루프 본문 안 호출의 Step Into | **확정**(불허 — 루프 본문은 양방향 블랙박스. P6 대칭성과 L2 불변식의 전제). **빈도 측정은 F1** |
| A-D10 | 스택 리셋 유지 | **확정**(근거: `depth=1` + 출발 문맥 독립성). 리셋 호출 지점 7곳 — Apply·Reconcile·Continue-Exhaust·Next/Prev Intent·Restart·JumpRoot |
| A-O11 | intent operation 포함 | **확정**(포함, ordered positioning 계층) |
| A-1 | IntentChecker 인용 방식 | **미결** (미게재 + URL 신원 노출) · 논문 세션 |
| A-2 | Forge/Hardhat 비교를 RQ2 에 넣을지 | **미결** (`ISSTA_PLAN` §2 "선택") |

---

## B. 구현 — 의미론·탐색

| | 항목 |
|---|---|
| **B1** | **`useNavigation` → 순수 모듈 추출** (`step(C, op) → C′`). `ISSTA_PLAN` §4: "실험 가능성을 위한 필수 리팩터". RQ1·RQ2 를 수천 함수에 돌리려면 필수 |
| B2 | `SelectFrame`(관찰, 스택 보존). 지금 Call Stack 클릭은 무동작 |
| B3 | `useNavigation` 테스트 가드 (현재 없음 — B1 의 부산물) |
| B4 | `isDead` 폴백 → 단정, 예외 위치를 `navEdges` 구성 시점으로 (렌더 중 throw → 화면 하얘짐) |
| B5 | `Feas` → `MayReach` 개명 |
| B6 | `Σ = (A_I, C)` 세션 계층 + `Reconcile` 분리 (snapshot immutability 성질이 여기서 닫힘) |
| ~~B7~~ | ~~`Δ(n) = {x | IN(n)(x) ≠ OUT(n)(x)}` 조회~~ → **완료 (Phase 0, A-D3 와 묶임)**. 전체 상태 목록에 `이전 → 이후` 인라인. 호출 지점의 Δ 는 반환값·부수효과를 모두 담고, 루프 헤더의 Δ 는 `loop_delta` 를 포함(중복 제거) |
| B8 | 슬롯 도메인 eligible/active/visible 분리 (원칙 승인) |
| B9 | A-D3 결정에 따른 `phase` 구현 |

---

## C. 구현 — 계측 (RQ2 의 전제)

| | 항목 |
|---|---|
| C1 | `evaluate` wall-clock 분해 (값 등록 / fixpoint / freeze / 직렬화) |
| C2 | fixpoint 지표 (join 방문 수, 지연값 N̂, forced widening, narrowing 통과, 600 방문 오류) |
| C3 | 분기 feasibility 로그 (조건별 ⊥ 여부 + 어떤 변수가 ⊥ 인가) — RQ1·RQ3·D2 가 모두 소비 |
| C4 | `update` 수술 종류 태그 + 단계별 시간 + 영향 노드 수 |
| C5 | 슬롯 도출 지표 (슬롯 수·종류·opaque/requiresIndex 비율) |
| C6 | callee 하강 지표 (하강 수·최대 깊이·`_frozen_callee_states` 크기) |
| C7 | `top_vars` ⊤ 사유 태깅(`provenance=None`) + **interval-⊤ 미탐지 수정** |
| C8 | 프론트 operation 응답 시간·호출 횟수·frontier 크기·Continue memo 적중률 |

---

## D. 구현 — 실험 하네스 (지금 전혀 없음)

| | 항목 | 비고 |
|---|---|---|
| **D1** | **입력 생성기** | `evaluate` 는 슬롯마다 debug 값을 요구한다. 1,941 타깃 자동 실행 수단이 없다. **범위를 잡은 적 없는 숨은 병목** |
| D2 | 커버리지 원장 (착지 줄/전체 줄, breakpoint 거부, Step Into 가능·opaque, frontier 분포) |
| D3 | operation oracle (S1~S4 + WellFormed 자동 검사) — B1 위에서 실행 |
| **D4** | **Forge/Hardhat 상태 주입 하네스** | RQ3 전용. 가장 큰 미지 |
| **D5** | **구체 실행 열거기** (유한 입력 영역 전수 실행 + 줄별 환경 덤프) | RQ3 의 γ-포함·도달성 검사 |

---

## E. 데이터셋 파이프라인

| | 항목 | 상태 |
|---|---|---|
| **E1a** | import 해소 가능성 측정 | ✅ **완료**. relative 해소 3,249 / suffix 17 / 미해소 62(스냅샷에 그 파일이 실제로 없음). 폐포 완전 해소 **489/522 (93.7%)**. 폐포 크기 중앙값 2 · 평균 4.8 · 최대 39. import 0개 파일 198/522 |
| **E1b** | `evaluation/Dataset/normalize_source.py` (입력 전제 정규화) | ✅ **완료**. 타깃 자체 게이트 통과 **71.5% → 100.0%**, 폐포까지 전부 **60.0% → 93.7%**(= import 상한), 개별 파일 **78.8% → 99.9%**. self-test 14 케이스 |
| **E1c** | 폐포를 `deps/<p>/sources/` 로 쓰고 `Dependencies/registry.py` 로 pkl 빌드 | **다음.** `load_project_deps` 가 `deps/<p>/builds/<CURRENT>` 레지스트리를 요구한다 |
| ~~E1-old~~ | ~~`build_project_deps.py` 완성~~ | ❌ **폐기.** 입력 파이프라인 전체가 부재 — forge `out/build-info/*.json` 과 `tools/{contraction_target,preprocess_contraction}.py`(998줄)가 IQDebug 에 없고, DAppSCAN-source 는 build-info 를 제공하지 않는다. 새 데이터셋에는 forge 가 **필요하지 않다**(import 만으로 93.7% 해소) |
| E2 | 타깃 선정 동결 (`targets_v5` 551 파일 / 1,941 실질 타깃) |
| E3 | `test_slot_roundtrip.py` 를 새 데이터셋 기준으로 재작성 (옛 48 케이스에 묶여 있음) |
| E4 | `test_transaction` T8 정리 (옛 데이터셋 파일 부재 — 기존 실패) |
| **E5** | (우선순위: **E1c 뒤**. 지배적 blocker 는 의존성 부재이고 주석은 2차 요인이다 — F0-6 참조) **게이트가 블록 주석을 추적하지 않는다** — `Dependencies.main._comment` 는 `^\s*//` 만 본다. GPL 라이선스 `/* */` 산문이 코드로 읽혀 거부된다(실측: WETH9.sol L284). 지금은 `normalize_source.blank_block_comments` 로 **데이터셋 쪽에서 우회** 중. 사용자가 라이선스 헤더 붙은 파일을 그대로 붙여 넣으면 같은 문제를 겪으므로 게이트 자체를 고치는 것이 옳다 — **공유 입력 경로 변경이라 승인 필요** |

---

## F. 측정

### F0 — 파일럿 (적용 범위를 먼저 확인. 전부 미수행)

| | 항목 | 왜 먼저인가 |
|---|---|---|
| F0-1 | `WellFormedSnapshot(A)` 위반 함수 수 (land-closure frontier 의 비착지 노드가 분기하는가) | 깨지면 그 함수는 navigation 자체가 불가능하고 지금은 UI 가 죽는다. assembly·중첩 modifier 가 후보. **RQ 적용 범위를 직접 깎는다** |
| F0-2 | 줄 단사성(S2) 위반 함수 수 | 합성 25 케이스는 통과, 실코드 미확인 |
| F0-3 | 부분 ⊥ 노드 수 (일부 변수만 ⊥) | `some(⊥)`/`all(⊥)` 쟁점이 실존하는지 |
| F0-4 | 빈 `in_state` 노드 수 (루프 본문 외에 있는가) | B4 의 전제 |
| F0-5 | 엔진 `line_info` ↔ 프론트 착지 색인 불일치 | 실측 불일치 1건 확인됨(지금은 identity 라 무해) |
| F0-6 | `analyze` 성공률 + 실패 사유 분포 | **1차 완료 (의존성 없는 하한)**. 아래 참조. `evaluate` 단계는 E1c 후 |

### F0-6 1차 결과 (2026-10-29, 선정 522 타깃, **의존 폐포 미로드 = 하한**)

| 입력 변형 | analyze 예외 없음 | 추출 함수 총수 |
|---|---|---|
| raw | 226 (43.3%) | 185 |
| 주석만 비움 | 329 (63.0%) | 652 |
| 정규화 | 198 (37.9%) | 878 |
| 정규화 + 주석 비움 | 216 (41.4%) | **982** |

**`analyze_source` 가 예외를 안 던지는 것은 커버리지 지표로 쓸 수 없다.** raw 는 226개가 "성공"
하는데 함수는 185개뿐(파일당 0.8)이고 그중 170개가 진단을 동반한다 — 조용한 오분석이 성공으로
집계된다. **지표는 "추출 함수 수"** 를 쓴다(raw 185 → norm+nocmt 982, 5.3배).

정규화+주석비움 기준 상세:

```
함수를 하나라도 추출한 파일: 190 / 522 (36.4%)
추출 함수 총수: 982   (추출된 파일만 보면 5.2 fns/file)
성공했지만 함수 0개: 26
```

**지배적 실패 원인은 의존성 부재다** — 상속 부모·`using` 라이브러리를 못 찾는다
(`'Ownable is Context'` 9, `'ERC20 is Context'` 6, `'ERC165 is IERC165'` 6,
`'AccessControl is Context'` 5, `using SafeCast` 2 …). 즉 이 측정은 **E1c 이전의 하한**이고,
E1c(레지스트리 빌드) 후 재측정해야 한다.

의존성과 무관한 나머지 원인(작다): `NotImplementedError / Unhandled expression context` 5,
`UnsupportedShadowing` 6(문서화된 한계), `ValueError / Contract context not found for line N` 다수.

### F1 — RQ1 (의미론이 실제 코드에서 잘 정의되는가)

착지점 커버리지 + 누락 원인 분포(루프 본문/더미/접힌 join/sink) · breakpoint 가능 줄 비율 + 거부 사유 ·
Step Into 가능 vs opaque 사유 분포 + **A-D8 로 도달 불가한 비율** · frontier 크기 분포 ·
불변식 위반 0건 확인 · ⊥ 노드·줄 비율 · **루프 탈출 경로 분포**(normal·break·return·revert, infeasible 비율 — K3 의 효용)

### F2 — RQ2 (재사용과 응답성)

operation 별 응답 시간(엔진 호출 0 주장의 실측) · `evaluate` 시간 vs 상태공간 크기 ·
`update` 수술 종류별 분해 · 업데이트 3분류 재계산 경계 + 커서·스택 보존 검증 · (선택) Forge 축소 비교

### F3 — RQ3 (soundness·정밀도)

γ-포함 검사 · 도달성 판정 검사(거짓 unreachable / 정밀도 손실) ·
상태공간 확대 효과 · loop delta soundness

### F4 — 이월된 개별 측정

한 줄 다중 호출 **21.7%**(완료) · merge-source 검증 · micro·macro 재가중

---

## G. 논문·투고 실무

| | 항목 | 소유 |
|---|---|---|
| G1 | `paper/SEMANTICS_AUDIT.md` 에 A 의 결정 기록 | 2026-10-29 사용자 허가로 **구현 세션이 작성** |
| G2 | `round9_operations.md` → 논문 §4 이식 | 논문 세션 |
| **G3** | **투고 전 저장소 비공개 전환** (`github.com/iwwyou/IQDebug` — 이중익명 de-anonymization). 아티팩트 준비 시점에 처리 | 사용자 |
| G4 | 익명 아티팩트 링크 (`Data Availability` 섹션 필수, 쪽수 불산입) | 논문 세션 |
| G5 | 18쪽 제약 / `\showtodofalse`·`\nocite{*}` 제거 / PDF 메타데이터 | 논문 세션 |
| G6 | 선행연구 대조 (Onward! 2024 Abstract Debuggers, GobPie, Frama-C Eva) | 사용자 |

---

## 순서

```
Phase 0  ✅ A 결정 묶음 + G1 기록(SEMANTICS_AUDIT §5) + Δ(n) 구현
Phase 1  ┌─ B1 탐색 모듈 추출 + B3 가드        ← 측정 축 병목
         └─ E1 deps 빌드                        ← 데이터 축 병목   (병렬)
Phase 2  F0 파일럿                              ← 적용 범위가 여기서 결정됨
Phase 3  D1 입력 생성기 + C1~C8 계측            ← F0 결과로 범위 조정
Phase 4  D2 원장 + D3 oracle → F1 (RQ1)
Phase 5  C4 기반 → F2 (RQ2)
Phase 6  D4 Forge 하네스 + D5 열거기 → F3 (RQ3) ← 가장 크고 독립적
Phase 7  B2·B4~B9 잔여 + G 투고 실무
```

**근거**

1. **Phase 0 이 맨 앞** — A-D3 하나가 논문 §4, `StateLocals`, `Frame` 에 동시에 걸린다. 나중에 정하면
   B1 추출본을 다시 쓴다.
2. **B1 이 Phase 1** — 지금 Continue·프레임 로직이 React hook 안에 있어 **대량 실험이 불가능**하다.
   RQ1·RQ2 가 둘 다 이걸 요구한다. 부산물로 B3(없는 가드)이 생기고, "논문에 적은 규칙 = 그 모듈" 이 된다.
3. **E1 을 B1 과 병렬** — 서로 의존하지 않고, deps 없이는 F0 자체를 못 돌린다.
4. **F0 를 하네스보다 먼저** — F0-1·F0-6 이 나쁘면 Phase 3~6 의 범위가 전부 달라진다. 하네스를 먼저
   만들고 나서 "적용 가능 함수가 40%였다" 를 알게 되는 것이 최악이다.
5. **D1 을 Phase 3** — F0 는 20~30 타깃이라 손으로 입력할 수 있지만 F1·F2 는 불가능하다. F0 의 슬롯
   분포를 보고 설계하는 것이 맞다.
6. **RQ3 를 마지막** — RQ1·RQ2 와 의존이 없고 가장 큰 미지다. 2.5개월 일정에서 줄여야 할 가능성이
   가장 높은 항목이라, 판단을 늦게 할 수 있도록 뒤에 둔다.

## 일정 리스크

| | 리스크 | 완충 |
|---|---|---|
| 1 | **F0-1 결과** — frontier 불변식 위반률이 높으면 적용 범위가 줄고 RQ1 이 "되는 것만 골랐다" 가 된다 | 위반 사유를 분포로 보고 + scope/threats 명시. 특정 구문(assembly 등)에 몰리면 명시적 거부로 돌린다(`try new` 와 같은 처리, §9.1) |
| 2 | **D1 입력 생성기** — 범위 미정이면서 F1·F2 의 전제 | 타입별 기본 구간 + 고정 시드로 **가장 단순하게** 먼저 돌리고 정교화는 선택 |
| 3 | **RQ3 하네스** — 외부 도구 의존 + 상태 주입이 컨트랙트마다 다름. 2.5개월에서 가장 위험 | 표본 N개 축소 tier(손 주입)로 후퇴 |
| 4 | **G3 저장소 공개** | 아티팩트 준비 시점에 처리 (투고 직전이 아니라) |
