# dataset_legacy — 동결 파이프라인에 쓰지 않는 옛 데이터셋 파일 (2026-11-19 이동)

데이터셋은 2026-11-19 에 `evaluation/Dataset/target_functions.json`(415 파일 · 2,653 함수)으로 동결했다.
정본 파이프라인과 재현 방법은 `evaluation/Dataset/DATASET_SELECTION.md` 에 있다.
여기 있는 파일은 그 재현에 필요 없어서 원래 경로 구조(`evaluation/...`) 그대로 옮겨 둔 것이다. 지우지 않고 이력으로 남긴다.

1. `evaluation/DAppSCAN/`
   - `scan_pragma.py`·`scan_projects.py`·`scan_features.py`, `dappscan_ge08.json`·`dappscan_projects.json`: 초기 조사 도구와 그 출력(버전 분포, 프로젝트 분류, 구성요소 빈도).
   - `targets_v2–v4.json`, `historical/select_targets_v1·v3·v4·v5.py`: 선정 스크립트의 이전 판과 그 출력. 현행은 `evaluation/DAppSCAN/select_targets.py` → `targets_v5.json`.
   - `audit_logic.py`, `audit_strict.*`, `audit_v2.*`: 2차 외부 검토 지적의 규모 측정.
   - `ast_manifest.*`, `target_analysis.*`: solc parse-only AST 기반의 옛 함수 집계(실질 타깃 1,941). 타입 AST 기반 `select_functions.py` 로 대체됐다.
   - `suspected_flattened_report.json`: `suspected_flattened` 18 개 조사.
2. `evaluation/Dataset/`
   - `targets_final.json`: 551 출발 · 중복 제거 486 목록. 폐기된 기준이다.
   - `compile_survey_486.json`: 486 전수 컴파일 검사 결과(2026-11-17). `compile_survey.py` 는 모듈로 계속 쓰지만 `main()` 은 이 486 목록을 읽던 판이다.
   - `compile_retry.py`: 컴파일 실패 타깃을 다른 버전·optimizer·viaIR 로 다시 시도한 진단 스크립트.

주의: `evaluation/Dataset/compile_survey.py` 의 `main()` 과 `build_deps.py` 는 아직 `targets_final.json` 을 읽는다. 둘 다 새 기준(`target_functions.json`)으로 바꿔야 다시 돌릴 수 있다(ROADMAP §0.B-3 의 `deps/` 재빌드 때).
