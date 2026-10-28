# 외부 검토 이력 (external review log)

이 디렉터리는 선정 로직·실험 설계를 다른 LLM 에 검토 요청할 때 보낸 **프롬프트**를 모아 둔다.
검토 결과는 대화에서만 받았으므로, 그 **내용과 반영 여부**는 `../DATASET_RECORD.md` 에 정리돼 있다.

## 라운드별 요약

| 라운드 | 주제 | 보낸 프롬프트 | 핵심 지적 → 반영 |
|---|---|---|---|
| 1 | 데이터셋 선정 로직 v1 | **덮어써져 남아 있지 않음**(아래 참고) | 스냅샷 경계, basename fallback, semver 오해석, `os.walk` 누락 가능성, 함수 수 근사, 전처리 영향 |
| 2 | 선정 로직 v2 | 동일 | 한 줄 import 누락, 폐포 공통 컴파일러, flattened 마커 무효, 문자열 속 pragma — **전수 측정 후 전부 반영** |
| 3 | 선정 로직 v3 | 동일 | `suspected_flattened` 가 함수의 46.6% 차지, 감사 스크립트 재현 불가, 중복 해시 결함, Yul 함수 계수 |
| 4 | 선정 로직 v4 | 동일 | 계약 **이름**만으로 병합 판정(반례 재현), 빈 OR 항 pragma, constructor/receive/fallback 집계 의미 |
| 5 | 선정 로직 v5 + 타깃 정책 초안 | `dataset_selection/REVIEW_PROMPT_round5.md` | OZ 지문을 원문에서 탐색(주석 오탐), **T2 를 실행 결과로 고르면 selection bias**, T1 명칭·library/abstract 구분, storage 참조 파라미터 태깅 |
| 6 | **타깃 계층 구현 + 입력 정책** | `target_and_input/REVIEW_PROMPT_round6.md` | modifier 해석 미비(C10, 미해결 296건), 실질 타깃이 modifier 함수 제외(C11), overload 식별(C9), @IReturn 호출지점(C12), 빈 경계(C8), `type_repr` 열화 |

### 라운드 6 처리 결과 (2026-10-28)

수치는 대부분 정확히 재현됐다. 반영·반박을 나누면:

| 지적 | 판정 | 조치 |
|---|---|---|
| C10 modifier 해석 | **수용** | `ast_manifest.py` 가 `modifier_records`(역할·해석 상태·placeholder 수·앞뒤 문장 수) 기록. base-constructor specifier 44건 분리로 수치 교정(899→856 함수). **본문 분할 modifier 623건**이 새로 드러남 |
| §④ `type_repr` 열화 | **수용** | 중첩 타입 보존 + 파라미터 이름·반환 파라미터 기록 |
| C11 modifier 함수를 타깃에 | **반박** | 166개가 전부 접근 통제 + 145개가 단일 대입 setter → 제외 **사유로 명시**(`access_control_guarded_setter`). 타깃은 1,941 유지 |
| C9 overload | **부분 반박 + 수용** | 백엔드는 이미 overload 를 구분한다(`name → {sig → fcfg}`). 배선 누락이었고 `param_types` 를 연결 |
| C8 빈 경계 | **반박** | `_debug_value_text` 에 이미 구현(미입력 = seed 없음, 한쪽 빈칸 = 타입 min/max) |
| C12 @IReturn 호출지점 | **반박** | 인자 드롭은 설계 의도. 비관계적 interval 에서 공유 = 독립과 동치. 정밀도 한계로만 기록 |
| 정수 파라미터 1,087 | **정정** | 1,061(스칼라). 26 차이는 정수 배열 — 생성 문법이 다르므로 분모 분리 |

상세는 `../DATASET_RECORD.md` §5.2·§8·§9(8~13).

> ⚠ 1~4 라운드 프롬프트 원문은 같은 파일(`REVIEW_PROMPT.md`)을 매 라운드 덮어쓰며 작성해 남아 있지
> 않다. 라운드마다 무엇을 물었고 어떤 지적을 받아 무엇을 바꿨는지는 `../DATASET_RECORD.md`
> §6(개선 이력·실측된 결함 규모)과 §9(정정 목록)에 기록돼 있고, 라운드 5 프롬프트 §3·§4 에도
> 1~4 라운드의 지적과 조치가 표로 들어 있다. 라운드 6 이후로는 이 디렉터리에 **라운드별로 보존**한다.

## 주제별 디렉터리

- `dataset_selection/` — 어떤 소스 파일을 평가 모집단에 넣을지(라운드 1~5).
  현재 결론: 옵션 2′ = 569파일 / 102스냅샷 / 95감사 프로젝트.
- `target_and_input/` — 그 파일들 안에서 **어떤 함수를 타깃으로** 삼고 **어떤 입력**을 줄지(라운드 6~).
