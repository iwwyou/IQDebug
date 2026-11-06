# Baseline 실험 계획 (forge / Foundry 직접 호출 / Hardhat 3)

작성 2026-11-06. 범위: **비교 도구 쪽 측정만.** IQDebug 측정과 논문 논리·RQ 번호는 다른 세션 소관이다.
여기서는 축을 `준비`, `재획득`, `범위`, `관찰` 로만 부른다.
짝 문서: `RELATED_TOOLS.md`, `FOUNDRY_RELEASE_NOTES.md`, `../DAppSCAN/DATASET_RECORD.md`, `../ROADMAP.md`(D4·D5 가 이 문서의 구현 항목).

---

## 1. 전제 확인: 테스트 없는 입구 실측 (2026-11-06, Foundry 1.9.0, Vault)

| 입구 | 테스트·하네스 | 상태 주입 | 생성자 상태 | 소스·지역변수 | 결과 |
|---|---|---|---|---|---|
| `forge script src/Vault.sol:Vault --sig "withdraw(uint256)" 100 --debug` | 불필요 | **수단 없음** | 자동 | ✅ | 잔고 0 으로 16행 REVERT (248 스텝) |
| 위 + `--fork-url <anvil>` | 불필요 | 불가 (스크립트가 새로 배포) | 자동 | ✅ | 같은 REVERT |
| `cast call … --trace --debug --override-state-diff …` | 불필요 | ✅ | 자동 | ❌ **instruction 단위만** | 1,014 스텝 RETURN |
| 위 + **`--with-local-artifacts`** | 불필요 | ✅ | 자동 | ✅ | **1,014 스텝, `amt`·`r`·`i` 표시** |
| `anvil_setStorageAt` 후 `cast call … --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 |
| `cast send` 후 `cast run <tx> --debug --with-local-artifacts` | 불필요 | ✅ | 자동 | ✅ | 같음 (트랜잭션 실제 전송) |
| standalone `soldb run` + legacy `combined.json` | 불필요 | `--storage` | **수동** (`--runtime` 필요) | ✅ | 1,014 스텝 |

**결론: Foundry 안에서 테스트·하네스 없이 입력과 상태를 주는 경로가 있다.** 필요한 것은 anvil 실행, 배포, 그리고 슬롯 지식이다.
standalone SolDB 는 필수가 아니다.

공통 한계: internal 함수 직접 호출은 어느 입구로도 불가 (`forge script` 도 `not implemented in your script`).
`cast` 에는 `--dump` 가 없어서 스텝 전수 추출은 불가하다. 대신 stdin 명령으로 디버거를 구동한다.

---

## 2. 비교 대상 워크플로

| ID | 정체 | 구성 | 역할 |
|---|---|---|---|
| **W1** | forge 테스트 | 생성한 `.t.sol` (+ 필요 시 하네스) + `forge test --debug` | 실무 표준 |
| **W2** | Foundry 직접 호출 | `anvil` + `forge create` + `cast call --trace --debug --with-local-artifacts --override-state-diff` | 테스트 없는 최선 경로 |
| **W3** | Hardhat 3 관행 | 스크립트 + `hardhat_setStorageAt` + `console.log` 삽입 + `debug_traceTransaction` | 디버거 없는 관측 관행 |
| W4 (보조) | standalone SolDB | `soldb run --runtime --storage` + legacy `combined.json` | 노드 없는 경로. **준비 축에만** 한 행 추가. `--json` 계측 도구 겸용 |

Hardhat 의 Solidity 테스트 경로는 W1 과 준비가 거의 같아 제외한다. Remix 는 정성 표로만 다룬다.
W1·W2·W4 는 SolDB 탐색 계층을 공유하므로 **탐색 지표는 W1 에서 한 번만** 측정한다.

**최선-경로 원칙**: baseline 은 항상 합리적으로 가능한 가장 빠른 자동화로 측정한다 (캐시 유지, env 파라미터, 스냅샷 재사용 등).
느린 변형은 부가 행으로만 둔다. baseline 에 유리한 조건에서도 이기는 결과만 주장한다.

---

## 3. 공통 입력: 케이스 명세

모든 도구(IQDebug 포함)가 같은 파일을 읽는다. **다른 세션과의 계약은 이 스키마다.**

```json
{
  "case_id": "...", "file": "...", "contract": "Vault", "function": "withdraw(uint256)",
  "tier": "T1", "solc": "0.8.37", "evm_version": "default", "optimizer": false, "via_ir": false,
  "ctor_args": [], "sender": "0xf39F...", "value": 0, "timestamp": null,
  "params": {"amt": 100},
  "state": [{"path": "shares[sender]", "value": 1000}, {"path": "totalShares", "value": 1000}],
  "externals": [],
  "observe": {"exit": ["ret0", "shares[sender]", "totalShares"], "points": [{"line": 25, "vars": ["r", "i"]}]},
  "range": {"amt": [0, 1000]}
}
```

- `state` 는 **변수 경로**로 적는다. baseline 생성기가 storage layout 으로 슬롯을 계산한다. 그 변환 횟수가 곧 측정값이다.
- 구체값 `params`/`state` 는 정상 종료에 도달하는 값이어야 한다. 값 탐색은 §6.3.
- `observe.exit` 는 학위논문 `O_exit` 와 같다 (정상 종료 시 반환값 + 변경된 상태).

---

## 4. 측정 지표

### 4.1 준비 (첫 관찰까지 무엇이 필요한가)

| 지표 | 정의 | 수집 |
|---|---|---|
| `D_value` | 사용자가 확정해야 하는 구체값 개수 | 케이스 명세 |
| `D_slot` | 슬롯 도출 수. 단순·mapping·중첩·packed·동적을 분리 집계 | 생성기 로그 |
| `A_env` | 환경 작업 수: 노드 기동, 배포, 생성자 인자, 라이브러리 링크, mock 컨트랙트, 프록시 | 생성기 로그 |
| `A_access` | 접근 경로 산출물: 하네스 계약, 노출 함수, public 입구 경유 여부 | 생성기 로그 |
| `E_src` | 대상 소스 수정 줄 수 (`console.log` 등). 관찰 변수 수에 비례 | 생성기 로그 |
| `L_add` | **자동 생성물** 줄 수. 정규화(`forge fmt`) 후, import·공백 제외. **하한값** | 산출물 |
| `F_cli` | 명령 인자 개수·슬롯 인자 개수 (W2·W4 용) | 명령 |

`L_add` 는 단독으로 쓰지 않는다. W2·W4 가 0 이 되기 때문이다. 표에는 `D_*`, `A_*`, `E_src` 를 함께 보고한다.

**도달 결과** (케이스별 필수 기록):

| 코드 | 의미 |
|---|---|
| `OK_EXIT` | 타깃 진입 + 정상 종료 |
| `OK_ENTRY` | 타깃 진입 + 요청 지점 미도달 또는 revert |
| `F_COMPILE` | 컴파일 실패 |
| `F_DEPLOY` | 배포 실패 (abstract, 생성자 인자, 의존성) |
| `F_ACCESS` | 접근 불가 (private, storage 참조 파라미터 등) |
| `F_STATE` | 상태 표현 불가 |
| `F_PRECOND` | 입구 전제조건을 만족시키는 값을 못 찾음 |
| `F_TIMEOUT` | 시간 제한 초과 |

### 4.2 시간

모든 값은 구간별로 쪼개 기록한다: `compile` / `node` / `deploy` / `state` / `execute` / `trace_load` / `first_obs`.

| ID | 시나리오 | 측정 |
|---|---|---|
| `T_cold` | 캐시 없음 | 컴파일 포함 첫 관찰까지 |
| `T_warm` | 캐시·노드 유지 | 재실행 첫 관찰까지 |
| `T_dbg1` | `build` 직후 첫 `--debug` | 디버그 빌드 분리 비용 |

일회성 환경 준비(forge-std, npm install)는 제외하고 별도 표기한다.

### 4.3 재획득

**입력 변경** (같은 소스, 입력 1개 변경 후 같은 관찰):

| 변형 | W1 | W2 | W3 |
|---|---|---|---|
| 기본 | 테스트 리터럴 수정 + 재컴파일 | `cast call` 인자만 변경 | 스크립트 인자 변경 + 재실행 |
| 최선 | `vm.envUint` 파라미터화 → 재컴파일 없음 | 같음 | 상주 노드 + `evm_snapshot`/`evm_revert` |

**소스 편집**: 값 / 연산자 / 구조 편집 3분류 (학위논문 프로토콜). 시나리오당 타깃별 고정 변이 1개.

| | W1 | W2 | W3 |
|---|---|---|---|
| 필요 단계 | 재컴파일 + 재실행 | 재컴파일 + 재배포 + 재주입 + 호출 | 재컴파일 + 재배포 + 재주입 + 호출 |
| 최선 | 증분 컴파일 | `--override-code` 로 재배포 생략 **(미시험)** | 상주 노드 + `hardhat_setCode` **(미시험)** |

기록: 재컴파일 파일 수, 슬롯 재계산 필요 여부 (상태변수 추가 시), 디버거 위치 보존 여부 (SolDB 는 세션 종료이므로 breakpoint 재설정 명령 수).

### 4.4 범위

입력 영역 크기 `N ∈ {1, 10, 100, 1000, 10000}` (구간 폭 × 슬롯 조합).

| 측정 | 방법 |
|---|---|
| `T_enum(N)` | 영역 전수 실행 시간. **외삽값은 실측과 구분 표시** |
| 프로세스 재기동 없는 최선 경로 | W1: 입력 배열을 도는 단일 테스트. W2: 상주 anvil + 일괄 호출 또는 Python RPC. W3: 상주 노드 + `eth_call` |
| 기록량 | 전수 실행에서 디버거로 볼 수 있는 실행 수 (`--debug` 는 1건) |
| `fuzz` 대비 | `--fuzz-runs {8, 256, 1024}` 의 분기 결과 커버리지 vs 전수 |
| 산출 | **구체 정답(ground truth)**: 입력별 반환값·변경 상태·방문 분기, 줄별 환경 |

이 산출물이 IQDebug 의 포함 검사 입력이다 (§7). 단일 입력 지연은 baseline 이 이길 수 있으므로 N=1 을 반드시 포함한다.

### 4.5 관찰 능력

| 지표 | 정의 |
|---|---|
| `V_local` | 관찰 지점의 스코프 내 지역변수 중 값이 표시된 비율 |
| `V_state` | 상태변수 중 값이 표시된 비율 (`<unknown>` 은 미표시) |
| `V_acc` | 표시된 **추론** 지역변수가 실제 값과 일치하는 비율. 정답은 `console.log` 삽입본 |
| `V_frame` | backtrace 에 internal 프레임이 복원된 비율 |
| 호출 내부 | `-vvvv --decode-internal`, Hardhat `-vvvv` 가 보여 주는 범위 |

컴파일 설정별(기본 / optimize / via-IR / via-IR+optimize) `V_local` 은 표본 일부에서 측정한다.
SolDB 의 지역변수는 추론값이므로 `V_acc` 없이 "보인다"고 단정하지 않는다.

### 4.6 탐색 (W1 만)

| 지표 | 정의 |
|---|---|
| `Ops_goal` | 타깃 진입 후 지정 줄의 값을 볼 때까지 **사용자 operation 수** (`break`+`continue`+`vars` 기준) |
| `Ops_step` | `next` 만 썼을 때 operation 수 (보조) |
| `Steps_target` / `Steps_session` | 타깃 구간 / 세션 전체 스텝. **서로 다른 값으로 보고, 조작 수로 해석 금지** |
| `L_op` | operation 응답 시간 (W4 `--json` 사용) |
| `Mem` | 세션 peak RSS, 번들 크기 |
| 지원 표 | 역방향, 조건부 breakpoint, 쓰기 감시 등 operation 지원 여부 |

지원하지 않는 operation 은 수치를 부여하지 않는다 (0 또는 ∞ 금지). W3 은 `해당 없음` 으로 둔다.

---

## 5. 관찰값 추출 방법

| | 지역변수 / 중간 지점 | 정상 종료 시 값 |
|---|---|---|
| W1 | 명령 파일 `break <line>` → `continue` → `vars` | 번들 `trace.json` 의 타깃 `exit_step` → `goto` → `vars`, `print <path>` |
| W2 | 같은 명령을 stdin 으로 | 같음. 단 `--dump` 가 없어 `finish` 또는 반환 지점 breakpoint 사용 |
| W3 | 소스 `console.log` | 호출 후 getter, `eth_getStorageAt`, `eth_call` |
| W4 | `-x … --batch --json` | 같음 |

디버거 출력 파서는 공용 모듈 하나로 둔다. PowerShell 파이프는 BOM 문제가 있으므로 Python `subprocess` 에 bytes 로 넘긴다.

---

## 6. 대상 선정과 준비 자동화

### 6.1 단계

| 단계 | 대상 | 통과 기준 |
|---|---|---|
| S0 | `Vault` 전 워크플로 E2E | 관찰값 4개 워크플로 일치 |
| S1 | 손으로 고른 8개 (leaf / 호출 / 루프 / modifier / 상속 / library / 외부 호출 / 생성자 인자) | 지표 수집 완성 |
| S2 | 계층 파일럿 20개 | **자동 준비 성공률 추정 → 범위 결정** |
| S3 | 자동 준비 성공 타깃 전부 | 성공률 보고 |

S2 성공률이 낮으면 S3 은 계층 표본으로 내린다. 층: T1/T2, 호출 유무, 루프 유무, 외부 호출 유무.
**루프 타깃을 제외하지 않는다.**

### 6.2 컴파일 선행 조사

569 파일의 컴파일 성공은 미검증이다 (`DATASET_RECORD` §10). 측정 전에 다음을 수행한다.

- 스냅샷별 격리 디렉터리 + 의존 폐포 + 허용 최고 solc. `evaluation/solc` 의 바이너리는 0.8.0–0.8.13 일부와 0.8.38 뿐이다 (0.8.37 은 없음. 필요 시 svm 또는 fetch_solc.py 로 보충).
- 원본 소스를 수정하지 않는다. 정규화 입력(`normalize_source.py`)은 쓰지 않는다.
- Hardhat 은 프로젝트 config 를 케이스별로 생성한다. WASM solc 사용 여부를 기록한다.
- **solc 버전은 케이스별로 같고, 도구 간 동일**하게 고정한다.

### 6.3 정상 종료에 닿는 구체값 찾기 (가장 큰 미지)

무작위 값은 대부분 `require` 에서 멈춘다. 순서:

1. 케이스 명세 값 (다른 세션의 입력 생성기 D1 과 공유 가능)
2. 조건식에서 추출한 상수·경계값
3. `forge` fuzz 로 revert 없는 입력 탐색 (시드 고정, 예산 고정)
4. 실패 시 `F_PRECOND` 로 기록하고 제외하지 않는다

탐색 비용은 준비 시간에 넣지 않고 별도 보고한다. 사람이 값을 안다고 가정하는 편이 baseline 에 유리하기 때문이다.

### 6.4 외부 의존

- 외부 호출 타깃은 실질 타깃의 약 24.4% (추정).
- W1: `vm.mockCall` 또는 mock 컨트랙트. W2: 대상 주소에 `--override-code`, mock 배포. W3: mock 배포.
- mock 은 호출당 `A_env` 로 센다. 값은 케이스 `externals` 에서 가져온다.
- 대형 프로젝트처럼 mock 이 많으면 `F_STATE` 로 처리하고 사유를 남긴다.

### 6.5 접근 규칙

| 타깃 | W1 | W2 / W4 | W3 |
|---|---|---|---|
| public/external | 직접 | 직접 | 직접 |
| internal | 상속 하네스 | public 입구가 있으면 경유, 없으면 `F_ACCESS` | 상속 하네스 |
| private | **소스 수정 필요** → `E_src` 집계 또는 public 경유 | public 경유 | 동일 |
| library internal | 호출 래퍼 | 경유만 | 호출 래퍼 |
| abstract | 구체화 하네스 | `F_DEPLOY` | 구체화 하네스 |
| storage 참조 파라미터 | 전용 하네스 | 경유만 | 전용 하네스 |

public 입구 경유는 호출 경로를 찾아야 하므로 입구의 전제조건까지 만족시켜야 한다. 이 비용도 `D_value` 에 들어간다.

---

## 7. 다른 세션에 넘기는 산출물

| 파일 | 내용 |
|---|---|
| `harness/cases/*.json` | §3 케이스 명세 |
| `harness/results/<workflow>.jsonl` | 케이스·반복별 지표 |
| `harness/truth/<case>.jsonl` | 입력별 구체 정답 (반환값, 변경 상태, 방문 분기, 줄별 환경) |

IQDebug 쪽은 같은 케이스에 대해 §4.1 의 대응 지표(채운 슬롯 수 등), 첫 관찰·재획득 시간, 분기 판정을 내면 된다.
포함 검사와 거짓 feasible 집계는 양쪽 데이터를 합쳐 계산한다.

---

## 8. 측정 절차

- **고정**: forge 1.9.0, soldb 0.4.5, Hardhat 3.21.1 또는 3.21.2 (결정 필요), forge-std v1.16.2, Node v22.21.1, OS·CPU·RAM 기록.
- **반복**: 케이스당 10회. 첫 회는 warm-up 으로 분리. cold 는 매회 캐시·산출물 삭제.
- **격리**: 다른 세션의 벤치와 동시에 돌리지 않는다. Defender 실시간 검사 영향 확인. anvil 포트 고정, 매 케이스 새 노드.
- **제한**: 케이스 타임아웃 120초 (S1 에서 재조정).
- **통계**: 중앙값 + IQR. 쌍체 비교는 Wilcoxon signed-rank, 효과크기 Cliff's δ. 프로젝트 쏠림이 크므로 micro 와 프로젝트별 macro 를 함께 보고. 실패 케이스를 시간 통계에서 뺄 때는 공통 성공 집합을 명시.
- **기록**: 원시 stdout/stderr, 명령, 버전, 커밋을 전부 보존한다.

---

## 9. 비교 방법

| 비교 | 방식 | 주의 |
|---|---|---|
| 준비 | 같은 케이스의 지표 쌍 비교 | 단일 합산 점수를 만들지 않는다 |
| 첫 관찰 시간 | 공통 성공 집합 위 쌍체 | N=1 을 숨기지 않는다 |
| 입력 변경 | 기본 / 최선 변형 모두 | 최선 변형 기준으로 주장 |
| 소스 편집 | 편집 3분류별 | 수술 미지원 편집은 IQDebug 측 실패로 집계 |
| 범위 | N 곡선 + 정밀도 | 구체값 N 개와 구간 1개는 **같은 산출물이 아니다** |
| 관찰 | 비율 표 | 값의 종류가 다름을 명시 (구체 vs 추상) |

---

## 10. 위협 요소

- **생성기 하한**: 사람이 쓴 테스트보다 작다. baseline 에 유리한 편향이므로 그대로 둔다.
- **기존 테스트**: 데이터셋에 테스트가 있는 스냅샷이 있다 (`*.t.sol` 50개, `test/` 하위 파일 1,534개, 실행 가능 여부 미확인).
  "기존 테스트가 타깃에 닿으면 준비 비용이 작다"는 반론이 가능하다. 재사용 가능한 타깃 비율을 **민감도 분석**으로 표본 측정한다.
- **숙련도**: 사람 시간은 측정하지 않는다. user study 없음을 명시한다.
- **도구 변화**: 전부 active development. 버전·조회일 고정.
- **Windows**: 프로세스 기동 비용이 다르다. 구간별 시간으로 분리 보고한다.
- **추론 지역변수**: SolDB 값은 추정이다. `V_acc` 로 검증한다.
- **표본 대표성**: 감사된 공개 소스 집합으로 한정한다. 프로젝트 쏠림 존재.
- **Hardhat 대표성**: `console.log` 경로가 대표인지. 플러그인 디버거는 범위 밖으로 명시.

---

## 11. 구현 순서 (`harness/` 신규)

| 순서 | 항목 | 산출 |
|---|---|---|
| 1 | 케이스 스키마, 공용 디버거 구동기·파서 | §3, §5 |
| 2 | Vault S0: W1–W4 E2E | 관찰값 일치 확인 |
| 3 | 컴파일 선행 조사 | 성공률, `F_COMPILE` 사유 |
| 4 | layout → 슬롯 변환기 | `D_slot` |
| 5 | 생성기: W1 테스트·하네스, W2 명령열, W3 스크립트 | §4.1 |
| 6 | S1 → S2 파일럿 | **범위 결정** |
| 7 | 재획득·범위 측정기, 구체 정답 덤프 | §4.3, §4.4, §7 |
| 8 | S3 전체 실행 + 집계 | 결과표 |

### 시작 전에 미리 검증할 항목

- `cast call --override-code` 로 재배포 없이 편집 코드 실행
- `hardhat_setCode` 편집 경로
- `cast call` 다중 호출 시퀀스 필요 케이스 (`approve` 후 `transferFrom` 처럼 선행 호출이 상태를 만드는 경우. 현재 설계는 상태를 직접 주입)
- 멀티컨트랙트에서 `--with-local-artifacts` 가 모든 주소의 소스를 잇는지
- `cast` stdin 구동의 시간 측정 신뢰도
- Hardhat 3 이 데이터셋의 구버전 solc 를 정상 처리하는지
