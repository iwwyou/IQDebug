# Foundry v1.9.0 — 논문 반영용 자료

**기준**: Foundry **v1.9.0** 단독. 2026-11-02T21:28:02Z 릴리스,
commit **`6056c5845d34205a83cf7108042adb137325c9df`**. 번들 SolDB = **0.4.5**.

> **v1.8.x 기준 측정치는 전부 폐기했다** (사용자 지시, 2026-11-03). 그 버전의 내장 디버거는
> 존재하지 않는다. `README.md`·`RELATED_TOOLS.md` 에서도 같이 제거해야 한다 (§8).
> 폐기 대상: 총 스텝 4,601 / 18 프레임 / 타깃 1,014 / fuzz 5프레임·2,480스텝 /
> `--dump` JSON 스키마 / TUI 키맵 / "스텝 레코드에 소스 줄 없음".

## 1차 출처 (접근일 2026-11-03)

| 출처 | 쓸 내용 |
|---|---|
| [릴리스 노트 v1.9.0](https://github.com/foundry-rs/foundry/releases/tag/v1.9.0) (body 12,018자) | 변경 전체, Action required |
| **[PR #17429 `feat(debugger): replace built-in dbg with soldb`](https://github.com/foundry-rs/foundry/pull/17429)** | **가장 좋은 출처.** merged 2026-10-29T16:20:08Z, 42 files, +3,183 −8,415. 벤치마크 표 + 방법론 |
| [릴리스 노트 v1.8.7](https://github.com/foundry-rs/foundry/releases/tag/v1.8.7) | DTL·캐시 서술 (1.9.0 에도 유효) |
| [walnuthq/soldb](https://github.com/walnuthq/soldb) + [docs/commands.md](https://github.com/walnuthq/soldb/blob/main/docs/commands.md) | SolDB 명령 표면·한계 |
| [foundry_debugger::soldb rustdoc](https://foundry-rs.github.io/foundry/foundry_debugger/soldb/index.html) | 통합 모듈 `[미확인]` |
| 로컬 `forge --help` 실측 | `--symbolic` 외 (§4) |

**등급**: `[1차]` 릴리스/PR/공식문서 명시 · `[실측]` 직접 실행 확인 · `[해석]` 우리 분석 · `[미확인]`

---

## §1 디버거 = SolDB. 정확한 사실관계

### 1.1 PR #17429 원문 — 이것만 인용하면 된다

> **Why.** Before: Foundry kept its own **opcode-level debugger** and its UI code. Debugging a large
> test cloned the whole trace and held every step's full stack and memory, so a 1.3M-step Aave test
> needed about 9.5 GB.
> After: Foundry uses SolDB, a **source-level debugger** that Walnut maintains, and keeps only the
> code that records steps and hands over artifacts.

> **Change.** Replaces the built-in debugger with SolDB in `forge test --debug`, `forge script
> --debug`, `cast run --debug`, and `cast call --trace --debug`, and removes the legacy UI. A
> **`DebugRecorder` inspector writes each step straight into SolDB's columnar step table** during
> execution, with memory as per-frame writes, so there is no second copy of the trace. A session
> covers **deployment, setup, and execution**, with **`vm.breakpoint` stops**, return data, and
> transient storage. **It reads legacy source maps, so ETHDebug is not required.** Debug runs build
> info files **without the solc AST**.

> **Frames and programs carry the hash of the code they run**, so code replaced with `vm.etch` keeps
> its own source map, and the **`--dump` bundle keeps one entry per program.** Uses SolDB 0.4.5.

> **Downstream (breaking).** `--debug-layout` is removed, `--dump` writes a **SolDB bundle directory**
> instead of the old JSON file, and the `foundry-debugger` API is now `DebuggerBuilder::run`.
> SolDB profiles **cannot split one root call from the others yet** (walnuthq/soldb#197).

### 1.2 성능 — PR 의 벤치마크 표 `[1차]`

Aave v4, `forge test --debug` 각 suite 1 테스트, profiling 빌드, 3회 중위값.
컬럼 = **instructions retired · peak RSS · wall time**. `Plain` = 같은 테스트의 `forge test`.

| Suite | Steps | 구 디버거 | SolDB | Plain | Memory | Speed |
|---|---:|---:|---:|---:|---:|---:|
| AaveV4BatchDeploymentTest | 193,418 | 14.9G · 1.45 GB · 1.01s | 14.6G · 0.39 GB · 0.60s | 11.3G | −73% | 1.7× |
| AaveV4PayloadTest | 307,539 | 16.7G · 1.31 GB · 1.54s | 16.7G · 0.46 GB · 0.78s | 11.5G | −65% | 2.0× |
| HubRestoreTest | 579,004 | 20.1G · 2.51 GB · 1.66s | 18.1G · 0.55 GB · 0.78s | 11.5G | −78% | 2.1× |
| TreasurySpokeTest | 717,934 | 22.6G · 3.61 GB · 1.95s | 19.6G · 0.61 GB · 0.85s | 11.6G | −83% | 2.3× |
| SpokeLiquidationCallDustTest | 1,014,531 | 28.3G · 5.02 GB · 2.47s | 23.6G · 0.74 GB · 1.05s | 11.7G | −85% | 2.4× |
| SpokeLiquidationCallScenariosTest | 1,283,170 | 41.7G · 9.47 GB · 4.49s | 23.8G · 0.85 GB · 1.04s | 11.8G | −91% | 4.3× |

⚠ **방법론 주의 (PR 원문)**: *"The old debugger stops after building its data when stdin is not a
terminal, so **its numbers are a lower bound**; SolDB builds the full session."*
→ 구 디버거와의 배율(1.7–4.3×, −65~−91%)은 **구 디버거에 유리하게 측정된 것**이다.

⚠ **릴리스 노트와 PR 의 수치가 다르다.** 릴리스 노트는 *"1.46M-step ... 27.6 GiB → 0.92 GiB,
6.58 s → 1.13 s, 5.8× sooner"*. PR 표의 최대는 1,283,170 steps / 9.47 GB. 서로 다른 측정으로
보인다. **논문에는 방법론이 붙은 PR 표를 쓴다.** 27.6 GiB 는 인용하지 않는다.

### 1.3 🟢 Motivation 에 쓸 수 있는 올바른 형태

**쓰면 안 되는 문장**: "리플레이 디버거는 27.6 GiB 를 쓴다" — 최적화 전 구현의 수치이고, 측정이
구 디버거에 유리하게 돼 있다.

**쓸 수 있는 문장** `[해석 + 1차 수치]`:

> 최적화된 현행 구현에서도 소스 수준 리플레이 디버깅의 peak RSS 는 트레이스 길이에 따라
> **0.39 GB(193K steps) → 0.85 GB(1.28M steps)** 로 증가한다. 디버그 세션 비용은 **스텝 수의
> 함수**이고(≈0.4–0.7 KB/step), 같은 테스트의 비-디버그 실행(`Plain`)보다 항상 크다.
> 반면 추상해석 기반 상태는 CFG 크기에 묶이며 실행 길이와 무관하다.

**추가로 쓸 수 있는 것**: 11.3–11.8G 의 `Plain` instructions 대비 디버그 실행은 14.6–23.8G —
즉 **관측을 위해 실행 비용이 1.3–2.0배**가 된다. (IQDebug 는 navigation 에 엔진 호출 0.)

### 1.4 구 디버거를 어떻게 서술할 것인가 — 정확히

내 이전 서술과 공유해 주신 자료 ②가 둘 다 약간 부정확하다. 사실관계:

- 1.8.x 내장 디버거에도 **Source 패널과 Variables 패널이 있었다** (공식 문서: Variables =
  params, return values, locals, storage reads+writes). "소스 수준 관찰이 불가능했다"는 **거짓**.
- 다만 **스텝 단위가 opcode** 였다. Foundry 자신이 PR 에서 구 디버거를 **"opcode-level debugger"**,
  SolDB 를 **"source-level debugger"** 라고 부른다 → **이 표현은 1차 출처로 인용 가능**.
- 내가 기록했던 "스텝 레코드에 소스 줄 필드가 없다"는 **`--dump` JSON 한정** 사실이었고,
  TUI 가 소스를 못 보여준다는 뜻이 아니었다. **오해를 부르므로 폐기.**

→ **정확한 1줄**: 1.9.0 에서 Foundry 의 디버거는 opcode 단위 스테핑에서 **소스 줄 단위 스테핑 +
역방향 스테핑**으로 전환됐다 (PR #17429 의 자체 표현 인용).

---

## §2 SolDB 의 능력과 한계 — 여기가 가장 중요하다

### 2.1 명령 표면 `[1차: soldb docs/commands.md]`

완전한 GDB 급이다. **디버거 기능 수로 경쟁하는 서술은 불가능하다.**

| 분류 | 명령 |
|---|---|
| 전진 | `next`(n) 줄 단위 step-over · `step`(s) 줄 단위 step-in · `nexti`/`stepi` 명령어 1개 · `finish` 프레임 반환까지 · `continue` · **`goto <step>` 트레이스 스텝 점프** |
| **역방향** | `reverse-next` · `reverse-step` · `reverse-nexti`(`back`) · `reverse-finish` · `reverse-continue` |
| breakpoint | `break <file>:<line>` · `break <function>` · `break <state variable>` · `break <pc>` · `break storage <slot>` · `break revert` · `break call [<addr>]` · `break op <OPCODE>` · **`break <target> if <cond>`** (비교 + `&&`/`\|\|`, 산술 없음) · `clear` · `delete` · `info breakpoints` |
| 관찰 | `backtrace`(bt) · `list`(l) · `stack` · `memory` · `storage` · `transient` · `calldata` · `returndata` |
| 변수 | **`vars`**(locals, 라이브 지역변수 + 상태변수) · **`print <var>`** — `balances[0x…]`, `item.tags[1]` 같은 경로 접근 |
| 모드 | `mode source` / `mode asm` · `tui` · `info resources`(ETHDebug 필요) |

**비대화형**: `-x '<cmd>'`(반복 가능, 순서대로 실행) · `--batch`(`-x` 끝나면 종료) ·
`--json`(**한 줄에 JSON 객체 1개, `kind` 태그** — `stop`, `variables`, `backtrace` 등) ·
`--tui` · `--ethdebug-dir <addr>:<name>:<dir>` · `--source-path <dir>`.
`soldb>` 프롬프트는 stdin 이 터미널일 때만 나온다.

### 2.2 🟢 변수 관찰의 실제 한계 — 가장 유용한 발견 `[1차]`

`ETHDebug vs Legacy source maps` 절의 내용이 결정적이다:

| 항목 | 사실 |
|---|---|
| 줄·함수·프레임·소스 텍스트 | ETHDebug **와** legacy `combined.json` 소스맵 **둘 다 지원** |
| 디버그 메타데이터 **필수** 기능 | 소스 수준 스테핑, 줄·함수 breakpoint, `list`, **`vars`**, **`print`**. 없으면 나머지는 raw 트레이스 위에서만 작동 |
| **지역변수** | ETHDebug variable info 로 읽는다 — **"The page says no compiler release emits this yet."** 따라서 현실에서는 solc **legacy 파이프라인의 소스맵 + 스택 레이아웃으로 추론**하고, **`vars` 가 그렇다는 경고를 출력**한다 |
| **via-IR 코드** | **지역변수 사용 불가.** `vars`/`print` 가 이유를 설명하고 거부 |
| 상태변수 | `solc --storage-layout` 의 레이아웃으로 읽는다 — **legacy 소스맵에서도 동작** |
| transient 상태변수 | `solc --transient-storage-layout` |

**논문용 서술** `[1차 + 해석]`:

> 2026-11 기준 어떤 solc 릴리스도 ETHDebug 변수 정보를 생성하지 않는다. 따라서 현행 소스 수준
> 디버거의 **지역변수는 legacy 스택 레이아웃으로부터 추론된 값**이며, 도구가 그 사실을 경고로
> 알린다. **via-IR 로 컴파일된 코드에서는 지역변수를 전혀 볼 수 없다.** 상태변수는 storage
> 레이아웃으로 신뢰성 있게 읽힌다.

이건 "소스맵이 부정확하다"보다 훨씬 날카롭다 — **① 지역변수는 best-effort, ② 배포 설정(via-IR)에서는
지역변수가 사라진다.** IQDebug 는 CFG 노드에 묶인 추상 환경을 들고 있어 둘 다 해당하지 않는다.

⚠ `[미확인]` **Forge 통합 경로**(ETHDebug 없이 legacy 소스맵)에서 `vars`/`print` 가 실제로
작동하는지는 반드시 실측해야 한다. PR 은 "ETHDebug is not required" 라고만 하고, 변수까지
포함하는지는 말하지 않는다. **§7 의 M5.**

### 2.3 🔴 Standalone SolDB — 준비비용 논증의 실질적 반례 `[1차]`

공유 자료 양쪽이 지적한 것이 맞고, **제 문서에 없던 가장 중요한 항목**이다.

| 명령 | 입력 |
|---|---|
| `soldb run <bytecode> [<sig> [args...]]` | **바이트코드 + 시그니처 + 인자.** 노드 불필요, **테스트 컨트랙트 불필요** |
| `soldb simulate <addr> <sig> [args] --from <sender>` | 주소·시그니처·인자·발신자 |
| `soldb trace <tx_hash>` | tx 해시 |
| (`soldb compile`, `soldb replay` 언급됨, `profile` 은 commands.md 에 미기재) | |

자동화 예 (문서화된 형태):

```
soldb run out/Counter.bin "increment(uint256)" 4 \
  --ethdebug-dir <ADDRESS>:Counter:./out \
  -x 'break Counter.sol:20' -x continue -x vars --batch --json
```

**함의 — 논증을 수정해야 한다:**

- **쓸 수 없게 된 문장**: "Solidity 함수를 디버깅하려면 테스트 컨트랙트를 작성해야 한다."
  `soldb run` 은 컴파일된 바이트코드에 직접 함수를 호출한다.
- **여전히 필요한 것**: ① **컴파일 성공** (바이트코드 + 디버그 메타데이터), ② **모든 인자의 구체값**,
  ③ `internal`/`private` 함수는 **ABI 에 없으므로 여전히 호출 불가** → 하네스 필요,
  ④ 상태변수를 심으려면 별도 수단(여기엔 `vm.store` 가 없다) `[미확인]`.
- ③④가 우리 `Vault.sol` 예제가 정확히 겨냥한 지점이다. **`_afterFee` 는 `internal` 이므로
  `soldb run` 으로도 못 부른다.** → 준비비용 논증은 **"테스트 작성 노동"에서 "접근 가능성 +
  구체값 확정"으로 무게중심을 옮기면 그대로 살아난다.**
- `soldb` 는 **Foundry 와 함께 설치되지 않는다** `[미확인→M2]`. 엔진이 라이브러리로 링크된 것과
  CLI 가 PATH 에 생기는 것은 다르다.

### 2.4 fuzz — 메커니즘을 정확히 `[1차]`

공유 자료의 지적이 맞다. **"256회 중 1회만 기록한다"가 아니라 "표시할 사례를 다시 실행해 기록한다"** 다.

```
fuzz campaign (256 runs, DebugRecorder 없음)
      ↓  디버깅할 사례 선택 (반례 또는 대표 실행)
   선택된 입력으로 재실행  ← 이때만 DebugRecorder 부착
      ↓
   그 한 실행의 트레이스만 bundle 에 기록
```

PR 은 fuzz 실행 횟수가 8회여도 1,024회여도 **디버거로 내보내는 스텝 수가 같은** 사례를 제시한다.
invariant 테스트에도 같은 최적화.

**논문용 서술 (조심스럽게)**:

> fuzz 는 입력 공간에서 N 개의 구체 실행을 수행하지만, 디버거에 올라오는 것은 **선택된 한 실행**이다
> (N 과 무관). 즉 탐색은 N 점, **관찰은 1 점**이다.

**과장 금지** `[해석]`: ① fuzz 가 반례를 찾으면 그 반례는 재현·디버깅 가능하다. ② 거꾸로 IQDebug 의
요약 상태는 **iteration·path 별 값을 구분해 주지 않는다** — 합쳐진 over-approximation 이다.
**두 도구는 "관찰 가능한 정보의 종류"가 다르고, 한쪽이 다른 쪽을 포함하지 않는다.** 평가에서
coverage(폭)와 precision(정밀도)을 **둘 다** 재야 공정하다.

### 2.5 multi-contract / `vm.etch` `[1차]`

> Frames and programs carry the **hash of the code they run**, so code replaced with `vm.etch` keeps
> its own source map, and the `--dump` bundle keeps **one entry per program**.

→ 같은 주소에 여러 코드 버전이 있어도 소스 매핑이 구분된다. SolDB 0.4.5 의 walnuthq/soldb#217 이
"한 주소에 여러 program 이 있는 bundle"을 읽게 한 변경.
**알려진 한계**: *"SolDB profiles cannot split one root call from the others yet"* (soldb#197).
cross-contract 실험을 할 때 IQDebug 의 callee CFG · MAX_CALL_DEPTH=16 모델과 대비할 지점.

---

## §3 `--dump` = bundle 디렉터리

`[1차]` JSON 파일 → **디렉터리**. 공유 자료가 보고한 구성:

```
debug_bundle/
├── trace.json
├── contracts.json
└── ...                 ← 전체 구성은 실측 필요 (M3)
```

재사용: `soldb profile --trace-file <dir>/trace.json --contracts <dir>/contracts.json`
(단 `soldb` CLI 별도 설치 필요)

**함의**: 이전 `debug.json` 파서는 **폐기**. 하지만 `trace.json` + `contracts.json` 이라는 **명시적
기계 판독 경로**가 생겼으므로 Python 하네스에는 오히려 유리하다. `--json`(kind 태그된 줄 단위 JSON)
까지 있으면 **operation 단위 계측**이 가능해진다 — 이게 평가 설계의 핵심 열쇠다.

---

## §4 🔴 `forge test --symbolic` — 두 조사 모두 놓친 항목

**공유해 주신 자료 ①②에 전혀 없다.** 내가 `forge test --help` 에서 실측한 것이고,
**Related Work 최대 리스크**다. (1.9.0 에서 재확인 필요 → M6)

30개 이상의 플래그. 요지:

```
--symbolic                    check*/prove*/invariant*/statefulFuzz* 테스트를 심볼릭으로
--symbolic-loop / -depth / -width      ← Halmos-compatible 로 명시된 별칭
--symbolic-solver-portfolio <...>      여러 SMT 솔버 병렬 경쟁
--symbolic-storage-layout <solidity|generic|zero_init>   심볼릭 storage 모델링
--emit-regression             심볼릭 반례 → Solidity 회귀 테스트 자동 생성
--replay-symbolic-artifact    지속 반례 artifact 리플레이
--symbolic-use-fuzz-frontiers / --symbolic-frontier-{limit,ids,pcs,selectors}
--symbolic-max-paths / -max-depth / -timeout / -dump-smt / -call-targets
```

**위험**: ① "심볼릭 실행은 별도 학술 도구(Halmos/Kontrol)" 서술 불가 — 플래그 수준에서 흡수됐다.
② `--symbolic-storage-layout` 이 상태 주입 부담을 덜 수 있다. ③ `--emit-regression` 이 테스트를
생성한다. ④ **용어 충돌**: 저들의 "frontier" = fuzz 가 못 뒤집은 분기점, 우리 `(focus, frontier)` =
착지 가능 노드 집합 → 논문 각주로 구분.

**방어선** `[해석]`: `--symbolic` 은 **검증기**다. 속성을 `prove_*` 로 먼저 정식화해야 답을 주고,
루프는 `--symbolic-loop` 로 bounded(unroll)이며, 산출물은 **반례 또는 증명**이다. IQDebug 는
**디버거**다 — 질문 없이 둘러보고, 루프를 fixpoint 로 접고, 모든 프로그램 점의 추상 상태를 준다.
**결정적으로 심볼릭 상태 위에 커서를 놓는 수단이 없다** `[미확인→M7]`.

---

## §5 증분 컴파일 — 디버그 빌드가 별개라는 점 `[1차]`

공유 자료 ①②가 지적한 것이 맞고 **제 문서에 없던 항목**이다.

> (PR #17429) Debug runs **build info files without the solc AST**.

> (1.9.0) **Dynamic test linking:** compiler failures retry natively and unchanged inputs skip the
> retry. ... sources are reused on expansion, and deleting a file that no preprocessing decision
> depends on no longer recompiles scripts.

SolDB 는 소스를 **build-info 에서** 읽는다. 일반 `forge build` 의 캐시가 디버깅에 필요한 build-info
를 충분히 갖지 않을 수 있어, **디버그 실행이 일부 항목을 무효화하고 재컴파일할 수 있다.**

→ **측정 시나리오를 3개로 분리해야 한다** (공유 자료의 제안을 채택):

| 시나리오 | 명령 흐름 | 재는 것 |
|---|---|---|
| **Cold debug** | `forge clean` → `forge test --debug` | 전체 컴파일 + 세션 구성 |
| **Warm debug** | `forge test --debug` → 동일 재실행 | 캐시 적중 시 비용 |
| **Debug after plain build** | `forge build` → `forge test --debug` | **디버그용 재컴파일이 실제로 일어나는가** |
| (+) **1줄 편집 후** | 소스 1줄 수정 → `forge test --debug` | 증분 재측정 비용 |

`No files changed, compilation skipped` 같은 로그를 **그대로 보존**한다.

기타 (1.8.7, 1.9.0 에 유효): DTL 기본 활성 / 캐시된 remapping 이 프로젝트 루트 상대 경로로 저장되어
디렉터리 이동 시 캐시 유지 / 좁은 빌드 후 넓은 빌드가 전체 재컴파일을 유발하지 않음.

**IQDebug 대비 (서술 주의)**: forge 의 compilation cache 와 IQDebug 의 incremental CFG update 는
**재사용하는 산출물의 종류가 다르다**(바이트코드 artifact vs CFG + 추상 상태). 같은 종류의 최적화로
설명하면 안 된다. 대비는 **입도**(파일 vs 라인)와 **실패 모드**(전체 재컴파일 폴백 vs
`IncrementalSurgeryError`, 폴백 없음)로 한다.

---

## §6 재현성 — Evaluation 에 반드시 명시 `[1차]`

| 사실 |
|---|
| `"bytecode metadata no longer depends on the checkout path and **can differ from 1.8 builds**"` (#17507) |
| 가스 스냅샷 재생성 필요 — DTL 이 `new` 를 `vm.deployCode` 로 바꾸고 생성 가스를 과금 (#17732, #18034). **프레임 구조·가스가 1.8 과 다를 수 있다** |
| `vm.getRecordedLogs` 가 revert/halt 한 호출의 로그를 더 이상 반환하지 않음 (#17866) |
| 커버리지가 **정확히 일치하는 artifact 에만** 귀속 → `vm.etch` 코드의 커버리지가 떨어질 수 있음 (#17520) |
| Anvil **즉시 채굴** — 동시 제출 tx 가 다른 블록에 들어갈 수 있다. `--transaction-coalescing-window 5` 로 1.8 동작 복원 (#17578, #17419) |
| 가스 리포트에서 flamegraph·evm-profile 이 refund 를 더하지 않음 (#17728, #17545) |
| Solar **0.3.0** (코드 생성 진입), **Solidity 0.8.38** |

모든 baseline 수치에 **`Foundry v1.9.0 (6056c58) · SolDB 0.4.5 · solc <ver> · 측정일`** 을 붙인다.
Anvil 을 하네스 노드로 쓰면 `--transaction-coalescing-window` 를 명시한다.

---

## §7 재측정 계획 — 1.9.0 기준 처음부터

`[실측]` 등급을 다시 채워야 하는 항목. 전부 비대화형으로 가능하도록 설계.

| ID | 과제 | 왜 필요한가 |
|---|---|---|
| **M1** | `forge --version` / `--help` / `forge test --help` / `cast --help` 를 파일로 보존 | 버전 고정 + `--debug-layout` 제거·`properties`/`exploit` 등장 확인 |
| **M2** | `soldb` CLI 가 PATH 에 설치되는가 (`bin/` 내용 확인) | §2.3 — standalone 경로의 가용성 |
| **M3** | `forge test --match-test 'test_withdraw\(\)' --debug --dump debug_bundle` → **디렉터리 전체 구조 + `trace.json`/`contracts.json` 스키마** | 하네스 재작성의 전제 |
| **M4** | bundle 에서 **스텝 수·프레임 수·프로그램 수**를 추출. `_afterFee` 루프 3회전이 어떻게 나타나는가 | 입도 비교의 수치 |
| **M5** | ★ Forge 통합 경로(ETHDebug 없음)에서 **`vars`/`print` 가 작동하는가**, 경고가 뜨는가. `--via-ir` 로 다시 | §2.2 — 가장 중요한 논거 |
| **M6** | `forge test --symbolic` 이 1.9.0 에도 있는가 + `prove_` 테스트 작성해 실행 (솔버 요구사항) | §4 — 최대 리스크 |
| **M7** | `--symbolic` + `--debug` 조합 가능 여부 | 가능하면 차별점 축소 |
| **M8** | fuzz `--debug --dump` → 기록되는 실행이 **runs 수와 무관**한지 (`--fuzz-runs 8` vs `1024`) | §2.4 검증 |
| **M9** | Cold / Warm / debug-after-build / 1줄 편집 후 (§5 표). `No files changed` 로그 보존 | 증분성 비교 |
| **M10** | `soldb run`/`simulate` 로 `_afterFee`(internal)를 호출할 수 **없음**을 확인 | §2.3 ③ 논거 확정 |
| **M11** | `vm.breakpoint` 가 SolDB 세션에서 작동하는가 (PR 이 지원한다고 명시) | breakpoint 비교 |
| **M12** | `forge test -vvvvv` 가 실제로 주는 정보 범위 | baseline 을 과소평가하지 않기 위해 |
| **M13** | `forge lsp --stdio` capability (hover 에 값이 뜨는가) | §4 편집시점 경쟁 |

측정은 `subprocess.run` 으로 **returncode / stdout / stderr / wall time / 산출물 크기**를 기록한다.
TUI 자동화는 별도 계층으로 두고, **가능하면 standalone SolDB 의 `-x ... --batch --json`** 으로
operation 단위 지연을 재는 경로를 만든다 (트레이스 로드 시간과 navigation 지연을 분리).

---

## §8 README.md / RELATED_TOOLS.md 에서 제거·수정할 것

| 대상 | 조치 |
|---|---|
| `README.md` 의 Foundry 버전 표 (v1.8.6) | → v1.9.0 / commit 6056c58 / SolDB 0.4.5 |
| `README.md` 의 `--dump` JSON 구조 + 4,601/18/1,014 표 | **삭제** |
| `README.md` 의 forge 명령 목록 중 `--debug` 설명 | SolDB 기준으로 재작성 |
| `RELATED_TOOLS.md` "스텝 레코드에 소스 줄 없음" | **삭제** |
| `RELATED_TOOLS.md` 차별점 "source-map-free" | §2.2 기준으로 재서술 (지역변수 추론·via-IR 제약) |
| `RELATED_TOOLS.md` 커서 비교표의 forge 행 | "opcode 인덱스" → "소스 줄 단위 트레이스 인덱스 + 역방향" |
| `RELATED_TOOLS.md` fuzz 서술 | §2.4 의 "재실행 기록" 메커니즘으로 교체 |
| **신설 §1.6 SolDB** (Simbolik·EDB 와 동급) | standalone 포함 |
| **신설 §1.7 `forge test --symbolic`** | §4 |
| `RELATED_TOOLS.md` §5 (우리가 지는 축) | SolDB 의 역방향 스테핑·조건 breakpoint·`print` 경로 접근 추가 |

### 폐기하는 주장 (양쪽 자료와 일치)

| 주장 | 판정 |
|---|---|
| 기존 디버거는 source-level stepping 을 지원하지 않는다 | ❌ 사용 불가 |
| 기존 디버거는 backward debugging 을 지원하지 않는다 | ❌ 사용 불가 |
| Forge 디버깅에는 반드시 테스트 컨트랙트가 필요하다 | ❌ 일반화 불가 (`soldb run`) |
| forge 는 소스맵이 부정확해 변수를 못 본다 | ❌ → §2.2 의 정확한 형태로 교체 |
| Forge 는 **기록된 구체 실행**을 탐색한다 | ✅ 유지 |
| fuzz 는 여러 구체 입력을 실행하되 **관찰은 1 점** | ✅ 유지 (§2.4 단서 포함) |
| IQDebug 는 **입력 구간의 추상 상태**를 탐색한다 | ✅ **핵심 차별점** |
| 동일 소스·입력에서 navigation 이 **재해석 없이**(엔진 호출 0) 작동한다 | ✅ 우리 구현으로 검증할 기여 |
| 준비 비용 / 입력 변경 재분석 비용이 다르다 | ⚠ **실험으로 검증할 가설** (단정 금지) |

---

## §9 공유 자료 검토 결과

### 채택 — 내 문서에 없던 것
1. **ETHDebug 불필요** (Forge 통합은 legacy 소스맵 사용) — PR 원문으로 확인. 내 §1.5 전제가 틀렸다.
2. **bundle = `trace.json` + `contracts.json`**, `soldb profile` 재사용 — 하네스 설계의 핵심.
3. **standalone `soldb run`/`simulate`** 가 테스트 하네스 없이 함수를 디버깅한다 — 준비비용 논증의 실질적 반례.
4. **fuzz 는 "선택 사례 재실행 기록"** — 메커니즘 서술 교정.
5. **디버그 빌드가 재컴파일을 유발할 수 있다** → Cold/Warm/after-build 3분할 측정 설계.
6. **`vm.etch` / 코드해시별 program 분리** — multi-contract 실험 항목.
7. "coverage 와 precision 을 둘 다 재야 한다", "soundness ≠ formal proof" — 평가 서술의 절제.

### 교정 필요
1. 양쪽 자료가 이전 버전을 **1.8.4** 로 적었다. 실제로 쓴 것은 **1.8.6** (commit `6bad0a66`).
   어차피 폐기하지만 논문에 전파되면 안 된다.
2. 자료 ②의 "기존 디버거는 source-level stepping 미지원" → 1.8.x TUI 에도 Source·Variables
   패널이 있었다. **Foundry 자신의 표현인 "opcode-level"** 을 쓰는 게 정확하다 (§1.4).
3. 자료 ②의 "4,601 / 1,014 steps 는 환경이 같으면 크게 달라지지 않을 수 있다" → **위험.**
   DTL 이 `new` 를 `vm.deployCode` 로 바꾸므로 **프레임 구조와 가스가 달라질 수 있다** (§6).
   재측정 전에 유효하다고 가정하면 안 된다.
4. 릴리스 노트의 **27.6 GiB** 수치는 PR 벤치마크 표(최대 9.47 GB)와 일치하지 않는다. 방법론이
   붙은 PR 표를 쓰고, 27.6 GiB 는 인용하지 않는다 (§1.2).
5. 자료 ①의 설치 안내(Git Bash/WSL + `foundryup`)는 우리에게 불필요하다. `install.ps1` 이
   릴리스 zip 을 직접 받는다.
6. 자료 ①의 "`--dump` 는 program 별 항목을 기록할 수 있음" → "**can**"이 아니라 PR 원문은
   "**keeps one entry per program**" (단정형).

### 양쪽 자료가 놓친 것
1. **`forge test --symbolic`** (§4) — 가장 큰 Related Work 리스크.
2. **지역변수를 생성하는 solc 릴리스가 아직 없다** + **via-IR 에서 지역변수 불가** (§2.2) —
   "소스 수준 디버깅이 된다"는 서술의 실제 한계. 가장 쓸모 있는 논거.
3. PR 의 **방법론 단서** — 구 디버거 수치가 lower bound (§1.2).
4. `soldb` 조건부 breakpoint `break <target> if <cond>`, `goto <step>`, `break storage/revert/call/op`
   — §5(우리가 지는 축)에 추가해야 한다.
5. soldb#197 — profile 이 root call 을 분리하지 못하는 알려진 한계.
6. Solar 0.3.0 의 코드 생성 진입, Solidity 0.8.38.

---

## §10 1.9.0 실측 결과 (2026-11-03) — M1–M5 완료

환경: Windows 11, Foundry **v1.9.0 (6056c58)**, solc 0.8.37 (기본 설정 = legacy codegen, optimizer off),
`playground/forge` 의 `Vault.sol`/`Vault.t.sol`. 덤프는 스크래치패드에 생성(저장소 미반영).

### M1 버전·플래그 `[실측]`
`forge Version: 1.9.0 / Commit SHA: 6056c5845d34205a83cf7108042adb137325c9df / Build 2026-11-02T20:11:29Z`.
`--debug-layout` **없음**, `forge properties`·`forge exploit` **있음**, `forge test --symbolic` **1.9.0 에도 있음**.

### M2 `soldb` CLI `[실측]`
**설치되지 않는다.** `bin/` = anvil·cast·chisel·forge·solar 뿐. standalone 은 `cargo install soldb` 만
지원(릴리스 에셋 0개, crates.io `soldb 0.4.5`, README 에 Windows 언급 없음). 이 PC 는 Rust 미설치,
MSVC(VS 2022 Community VC tools)는 있음.

### M3 bundle 구조 `[실측]` — `forge test --match-test 'test_withdraw\(\)' --debug --dump <dir>`

```
<dir>/
├── trace.json        12.1 MB   ← 세션 전체
├── contracts.json     0.4 KB   ← [{address, name, debug_dir, code_hash}, ...]
├── 0x5615…72f/combined.json    ← VaultHarness 의 solc legacy combined-json (srcmap, srcmap-runtime, bin, bin-runtime, storage-layout)
├── 0x7fa9…496/combined.json    ← VaultTest
├── src/Vault.sol, test/Vault.t.sol, lib/forge-std/src/**   ← 소스 사본
```

`trace.json` 최상위: `tx_hash`(null) `from_addr` `to_addr` `value` `input_data` `gas_used` `output`
`success` `error` `debug_trace_available` `contract_address` **`backend: "foundry"`**
**`capabilities`**(14: opcode_steps·stack·memory·storage·storage_diff·call_trace·contract_creation·
transient_storage·return_data = true / logs·revert_data·gas_details·account_changes = false)
**`artifacts`**{`calls`[13], `creations`[2], logs, account_changes, gas, revert_data} **`steps`[4,776]**.

- `steps[i]` = `{pc, op, gas, gas_cost, depth, stack, memory, storage, error, snapshot{stack,memory,storage,storage_diff}}`
  → **소스 줄 필드 없음.** 소스 귀속은 소비자(SolDB)가 `combined.json` 의 srcmap 으로 계산한다.
- **프레임별 arena 가 아니라 평평한 리스트 + `depth`**. 호출 구조는 `artifacts.calls[].{entry_step, exit_step, parent_id, input, code_hash}`.
- 세션 범위 = **배포 + setUp + 테스트 본문** (PR 서술과 일치): creation[0] steps 0–59, `setUp()` 59–175, `test_withdraw()` 175–4,776.
- **주소**: VaultTest = `0x7fa9385b…`, VaultHarness = `0x5615deb7…`. ⚠ forge-std `Base.sol` 의 상수
  `DEFAULT_TEST_CONTRACT = 0x5615…` 와 다르다 (DTL 영향으로 추정 `[해석]`).

### M4 수치 `[실측]`

| 항목 | 값 |
|---|---|
| 세션 총 스텝 | **4,776** (depth1 2,921 / depth2 1,855) |
| `calls` 수 | 13 (setUp 1, test 1, 그 아래 11) |
| 타깃 `withdraw(100)` (`0x2e1a7d4d`, call #7) | **steps 1,905–2,919 = 1,014** |
| 타깃 비율 | **21.2%** — 세션의 78.8% 는 배포·setUp·하네스·getter·cheatcode |
| cheatcode `vm.store` ×2 (→ `0x7109…`) | **0 스텝** (entry = exit, 네이티브 처리) |
| `console.log` ×3 (→ `0x…636F6e736F6c652e6c6f67`) | 0 스텝 |
| getter (`shares` 211, `totalShares` 94) × 여러 번 | 하네스 측 관찰 비용 |
| `_afterFee` 루프 1 회전 | **210 스텝** (breakpoint hit 2064 → 2274 → 2484) |
| 타깃 1,014 의 독립 교차검증 | Hardhat 3 `debug_traceTransaction` structLogs = 1,014 (README 기록, Hardhat 측 실측이라 유효) |

### M5 변수 가시성 `[실측]` — **가장 중요한 결과**

**자동화 경로 발견**: `forge test --debug` 에 **stdin 으로 명령을 흘려 넣으면 내장 SolDB 가
비대화형으로 동작한다.** 외부 `soldb` 불필요. Python `subprocess.run(..., input=cmds)` 로 구동,
warm 기준 **wall 0.64s** (테스트 실행 + 세션 구성 + 명령 실행 + 종료).
⚠ PowerShell 의 `StandardInput` 은 UTF-8 **BOM** 을 붙여 첫 명령을 깨뜨린다(`Unknown command: ﻿break`).
Python 으로 바이트를 넘기면 문제없다. 출력은 사람이 읽는 텍스트(내장 경로에 `--json` 이 있는지 `[미확인]`).

`break src/Vault.sol:25` → `continue` × 3 → `reverse-continue` 결과:

```
Breakpoint #1 hit at step 2064, src/Vault.sol:25
src/Vault.sol:25 in _afterFee  (step 2064/4775, pc 564, PUSH2, gas 1056914229)
   25 |             r = r - (r * feeBps) / 10000;
warning: local variables are inferred from the legacy source map and the stack layout of solc's
         legacy code generator, not from compiler-reported variable locations
uint256 amt = 100 [stack+6]   uint256 ret0 = 0 [stack+7]   uint256 r = 100 [stack+8]   uint256 i = 0 [stack+9]
State:
mapping(address => uint256) shares = <mapping; index it with [key]> [slot 0x0]
uint256 totalShares = 1000 [slot 0x1]
uint256 feeBps = 30 [slot 0x2]                        ← private 상태변수도 storage layout 으로 읽힌다
#0  _afterFee at src/Vault.sol:25      ← internal 함수(JUMP)도 backtrace 프레임으로 복원
#1  withdraw at src/Vault.sol:17
#2  VaultHarness at test/Vault.t.sol:9
#3  test_withdraw at test/Vault.t.sol:36
#4  VaultTest at test/Vault.t.sol:15
(hit 2: step 2274, i = 1, r = 100)  (hit 3: step 2484, i = 2, r = 100)  reverse-continue → step 2484 복귀
```

**컴파일 설정별 비교** (같은 테스트, 같은 명령):

| 설정 | 세션 스텝 | 지역변수 (`vars`, `print r`) | 상태변수 | backtrace 인자 |
|---|---:|---|---|---|
| 기본 (legacy, opt off) | 4,776 | ✅ **추론** + 경고 | ✅ | — |
| legacy + `--optimize` | 2,509 | ✅ **추론** + 경고 | ✅ | — |
| **`--via-ir`** | 7,934 | ❌ *"locals are unavailable here: this contract was compiled through the via-IR pipeline, whose stack layout cannot be recovered without compiler-reported variable locations"* | ✅ | `_afterFee(amt = 100)` — *"frame arguments are read off the stack ... the calling convention was proved from this trace's calldata"* |
| **`--via-ir --optimize`** | 2,282 | ❌ 동일 | ✅ | ❌ 인자 없음 |

**논문용 결론** `[실측]`:
1. 현행 Foundry 디버거의 지역변수는 **추론값**이고 도구가 스스로 경고한다.
2. **via-IR 에서는 지역변수가 사라진다**(최적화 여부 무관). 최적화까지 켜면 프레임 인자도 사라진다.
   상태변수는 모든 설정에서 읽힌다.
3. **스텝 수는 프로그램의 성질이 아니라 컴파일 설정의 성질이다** — 같은 테스트가 2,282 ~ 7,934 (3.5배 차).
   → 평가에서 "스텝 수"를 지표로 쓰려면 컴파일 설정을 고정·명시해야 하고, 도구 간 비교 지표로는 부적합하다.
4. SolDB 는 internal 함수 프레임·조건 없는 breakpoint·역방향 continue·private 상태변수 읽기를 **모두 한다**.
   "기존 도구는 X 를 못 한다"류 기능 비교는 쓸 수 없다. 차이는 **구체 실행 1개 위의 트레이스 인덱스** vs
   **추상 상태 위의 NG 노드**라는 탐색 대상에 있다.
5. `r = 100` 이 세 회전 내내 같은 것(바닥 나눗셈으로 수수료 0)을 보려면 **세 번 멈춰 세 번 읽어야** 한다.
   IQDebug 는 같은 루프를 fixpoint 노드 하나로 보여준다 — 단 iteration 별 값은 구분하지 않는다(§2.4).

### §7 진행 상태
M1 ✅ · M2 ✅ · M3 ✅ · M4 ✅ · M5 ✅ · M6–M13 대기.
standalone `soldb` 가 꼭 필요한 남은 과제는 **M10**(`soldb run` 으로 internal 함수 호출 불가 확인)과
**구조화 출력(`--json`)** 뿐이다. operation 단위 자동 계측은 **내장 경로(stdin 구동)로 이미 가능**하다.

---

## §11 standalone SolDB 실측 (2026-11-03) — M2·M10 완료

### 설치 `[실측]`
- `rustup-init.exe -y --default-toolchain stable --profile minimal` → **rustc 1.99.1**, host `x86_64-pc-windows-msvc`
  (VS 2022 Community 의 MSVC 사용). 설치 위치 `%USERPROFILE%\.cargo`, `%USERPROFILE%\.rustup` (저장소 밖).
- `cargo install soldb --locked` → **soldb 0.4.5**, 빌드 2분 55초, `%USERPROFILE%\.cargo\bin\soldb.exe`.
  **README 에 Windows 언급이 없지만 빌드·실행 모두 정상.**
- 서브커맨드: `bridge compile debug-diff info list-contracts list-events trace replay run profile simulate`.
- solc: forge 가 받은 `%APPDATA%\svm\0.8.37\solc-0.8.37` (확장자 없음 → `.exe` 로 복사해 `-s` 에 지정).
  ⚠ PATH 의 `solc` 는 예전 solc-select 의 **0.8.4** — Vault(`^0.8.20`) 컴파일 불가.

### `soldb compile` 은 via-IR 이다 `[실측]` — 중요
`soldb compile src/Vault.sol -s solc-0.8.37.exe -o out` → `Vault.bin`, `Vault.abi`, `Vault_ethdebug.json`,
`Vault_ethdebug-runtime.json`(117KB), `Vault_storage.json`, `ethdebug_resources.json`.
이 산출물로 디버깅하면 **`vars` 가 "this contract was compiled through the via-IR pipeline" 이라며 지역변수를 거부**한다.
(solc 의 ETHDebug 출력은 via-IR 파이프라인에서만 나오고, 변수 위치 정보는 아직 어떤 solc 릴리스도 내보내지 않는다 — §2.2.)

→ **역설적 결과**: "가장 풍부한" ETHDebug 경로가 현재는 legacy 경로(Forge 통합, 지역변수 추론)보다
**지역변수를 덜 보여준다.** ETHDebug 가 주는 것은 소스 매핑·함수 프레임·타입 정보이고, 지역변수는 아직 아니다.
(`soldb compile --dual-compile` 로 legacy 산출물을 같이 내는 경로는 `[미확인]`.)

### M10 — 하네스 없이 원본 `Vault` 를 돌린다 `[실측]`

`soldb run <bytecode> <sig> <args> -e <addr>:Vault:<dir> -x ... --batch [--json]`, 배포 주소 =
`--from` 기본값 `0xf39F…2266` 의 nonce 0 CREATE 주소 `0x5FbD…0aa3`.

| 시도 | 결과 | 스텝 |
|---|---|---:|
| ① `_afterFee(uint256)` 10000 **직접 호출** | **디스패처에서 REVERT** — internal 함수는 ABI 에 없다 | 42 |
| ② `withdraw(100)`, 상태 없음 | `require(amt <= bal)` 에서 REVERT | 310 |
| ③ `withdraw(10000)` + `--storage <mapping slot>=…` (생성 코드 배포) | **여전히 REVERT** — soldb 가 읽은 슬롯은 정확히 `0x7230…a722`(우리가 `cast index` 로 계산한 값)인데 **값이 0x0**. 생성 코드 경로에서는 `--storage` 가 반영되지 않았다 (배포 시 초기화로 추정 `[해석]`) | 310 |
| ④ ③ + **`--runtime`** (런타임 코드를 직접 설치) + `--storage` 3개 (`shares[caller]`, slot 1, slot 2=`0x1e`) | ✅ `require` 통과, `_afterFee` 진입, line 25 에서 **3회 hit (step 327/570/813)**, RETURN. **단 지역변수 `r`·`i` 전부 불가** (via-IR) | 1,435 |

**④의 준비 과정을 정리하면** — 테스트 파일은 없지만 사람이 해야 하는 일:
1. 컴파일 (ETHDebug, solc 0.8.29+ 필요) + **런타임 바이트코드를 별도로 추출**
2. 발신자 주소를 알고 mapping 슬롯을 계산 (`cast index address 0xf39F… 0`)
3. `--storage` 로 슬롯마다 값 지정 — **`private feeBps` 는 생성자가 안 돌았으므로 slot 2 에 직접 30 을 넣어야 한다**
4. `--runtime` 을 써야 한다는 사실을 알아야 한다 (③처럼 조용히 실패함)
5. internal 함수는 **public 입구(`withdraw`)를 통해서만** 도달 — 입구의 전제조건(`require`)을 만족시켜야 함

**JSON 출력** `[실측]`: `--json` 은 한 줄에 객체 하나, `kind` ∈ {`loaded`, `stop`(reason: initial/breakpoint/end),
`breakpoint`(event: set), `message`(level: note/warning), `variables`(locals, unavailable, state)}.
`stop.location = {path, line, generated, text}`. **존재하지 않는 줄에 breakpoint 를 걸면**
`"no step of this trace reaches …:25, so this breakpoint cannot be hit"` 이라는 note 를 준다.
wall time: `run` 전체 ≈ **0.04s**.

### 논문용 결론 (§2.3 갱신)

| 주장 | 판정 |
|---|---|
| "Solidity 함수를 디버깅하려면 테스트 컨트랙트를 작성해야 한다" | ❌ `soldb run` 이 반례 |
| "**internal 함수는 하네스 없이 직접 호출할 수 없다**" | ✅ **실측 확인** (①: 42 스텝 REVERT) |
| "internal 함수에 도달하려면 public 입구의 전제조건을 만족시킬 상태를 구체값으로 만들어야 한다" | ✅ 실측 (②③④) |
| "상태 주입에는 슬롯 계산 지식이 필요하다" | ✅ — `--storage slot=value` 는 슬롯 번호를 요구, mapping 은 keccak |
| "ETHDebug 를 쓰면 지역변수가 보인다" | ❌ **현재는 반대** — ETHDebug 경로 = via-IR = 지역변수 없음 |
| "standalone SolDB 는 operation 단위 자동 계측이 가능하다" | ✅ `-x … --batch --json` |

---

## §12 추가 검증 (실측 2026-11-03, 정리 2026-11-05) — M6 차단, M8·M11·M12·M13

등급: `[실측]` = 직접 실행, `[소스]` = soldb 0.4.5 crate 소스 확인, `[도움말]` = `--help` 만 확인, `[미실측]` = 실행 안 함.

### 12.0 §7 측정 계획 진행 상태

| ID | 상태 | 비고 |
|---|---|---|
| M1–M5, M10 | ✅ 완료 | §10, §11 |
| **M6** `--symbolic` | ⛔ **차단** | z3 필요. 12.1 |
| **M7** `--symbolic` + `--debug` | ⛔ 미착수 | M6 에 종속 |
| **M8** fuzz 디버깅 | ✅ 완료 | 12.2. 단 `--dump` 없이 `--debug` 로 측정 |
| **M9** Cold/Warm 등 | ⏳ 미완 | 관찰 1건만 있음. 12.5 |
| **M11** `vm.breakpoint` | ✅ 완료 | 12.3 |
| **M12** `-vvvvv` | ✅ 완료 | 12.4 |
| **M13** `forge lsp` | 🔶 부분 | 진단만 확인, hover 값 미확인. 12.6 |

### 12.1 M6 `forge test --symbolic` — 아직 결과 없음 `[실측]`

- 실행 자체는 되지만 z3 가 PATH 에 없으면 모든 대상이 **실패로 보고된다**. skip 이 아니다:
  `[FAIL: incomplete symbolic execution (Error): solver error: failed to execute z3: program not found] … (paths: 0, queries: 0, …)`
- forge 배포본에 솔버가 포함되어 있지 않다. **외부 SMT 솔버가 별도 준비물**이다.
- `--help` 기준 대상은 `check*`/`prove*`/`invariant*`/`statefulFuzz*` 함수다. `test_*` 에 `--symbolic` 을 붙이면
  구체 실행으로 돌고 PASS 한다 (`test_afterFee_internal` 로 확인). `testFuzz_*` 도 구체 fuzz 로 돌았다.
- z3 5.3.3 을 `forge/z3/z3-5.3.3-x64-win/` 에 받아 두었으나 **실행은 하지 못했다** (자동 권한 분류기가 외부 바이너리 실행을 거부).
  `evaluation/.gitignore` 에 `baselines/forge/z3/` 추가도 같은 명령이라 **미적용**이다.
- 사용자가 실행할 명령:
  ```powershell
  $Z = "..\..\forge\z3\z3-5.3.3-x64-win\bin\z3.exe"
  & $F test --symbolic --match-contract VerifyTest --symbolic-solver (Resolve-Path $Z)
  ```
- 예상(미검증): `check_afterFee_le`·`prove_withdraw` 증명, `check_afterFee_eq` 반례. **결과가 나오기 전에는 논문에 쓰지 않는다.**
  `_afterFee` 는 곱셈·나눗셈이 있어 SMT 에 어려울 수 있고, 시간 초과도 그 자체로 정보다.

### 12.2 M8 fuzz 디버깅 — 녹화는 실행 1건 `[실측]`

| 테스트 | `--fuzz-runs 8` | `--fuzz-runs 1024` |
|---|---|---|
| `testFuzz_afterFee_eq` (실패) | 1,982 스텝, `amt`=537092772634104217 | 1,982 스텝, `amt`=51864680374247776 |
| `testFuzz_withdraw` (통과) | 2,655 스텝, `amt`=41 | 2,655 스텝, `amt`=296979225070875839145 |

- 스텝 수는 runs 와 무관하다. `--help` 설명과 일치: **실패하면 첫 실패 사례, 통과하면 마지막 사례**를 연다.
- 어떤 입력이 녹화될지 사용자가 고를 수 없다 (`amt=41` 처럼 우연히 결정).
- ⚠ 실패 테스트의 `runs: 0` 은 이전 실패 입력을 재실행했기 때문으로 보인다 (forge 의 실패 영속화). 재측정 시 캐시를 감안할 것.
- 위 스텝 수는 이 두 테스트에 한정. 경로가 입력에 따라 달라지는 테스트라면 사례마다 다를 수 있다.

### 12.3 M11 `vm.breakpoint` `[실측]`

`vm.breakpoint("a")` 는 세션 시작 시 breakpoint #1 로 자동 등록된다. `continue` 하면 해당 줄에서 멈춘다.
```
Breakpoint #1 hit at step 828, PC 3718 if step >= 175 && step < 2079 && depth == 1
test/Verify.t.sol:49 in test_breakpoint  (step 828/2078, pc 3718, STATICCALL, gas 1073713306)
```
cheatcode 호출이 trace 에서 `STATICCALL` 1개로 보인다. 소스를 고쳐야 하는 방식이므로 재컴파일이 따른다.

### 12.4 M12 trace 출력 `[실측]`

| 옵션 | 내용 |
|---|---|
| `-vvvv` | 호출 단위 trace. 기본적으로 internal 호출은 없음 |
| `-vvvvv` | 위 + setup trace + **storage 변경** (`@ 0x5ff1…bb03: 1000 → 900`), 줄 번호 backtrace. `test_withdraw` 출력 46줄 |
| `-vvvv --decode-internal` | **internal 호출까지 표시**: `Vault::_afterFee(uint256)(100)` |

baseline 을 과소평가하지 말 것: 디버거를 열지 않아도 호출 구조와 storage 변화는 trace 로 볼 수 있다. 단 지역변수·루프 회차 값은 나오지 않았다.

### 12.5 M9 관찰 (측정 아님) `[실측]`

`forge build` 직후 첫 `--debug` 에서 `Compiling 21 files` (1.06s) 가 나왔다. 이후 같은 파일에는 `No files changed`.
테스트 파일을 추가한 뒤에는 `build` 와 `--debug` 가 각각 `Compiling 2 files` 를 출력했다.
→ 디버그 빌드 캐시가 일반 빌드와 분리되어 있다는 §5 와 일치. **시간 수치는 M9 로 따로 측정할 것.**

### 12.6 M13 `forge lsp` `[실측]`

`scratchpad/lsp_probe.py` 로 JSON-RPC 를 직접 구동.
- `initialize` 응답에 capability 23개 (hover, completion, definition, references, rename, inlayHint, codeLens, callHierarchy, typeHierarchy, …).
- **저장하지 않은 편집**에 진단이 온다. `return r;` → `return r` 로 바꾸니 version 2 에 대해 `expected one of ... found }` 수신 (`solar` 파서).
- ⚠ hover 는 `null` 이었다. 보낸 위치가 공백이었고 버퍼도 문법 오류 상태라 **hover 가 안 된다는 뜻이 아니다.** "hover 에 값이 뜨는가"는 미확인.
- 확인한 것은 문법 진단까지다. 값 수준 분석을 제공하는지는 미확인.

### 12.7 디버깅 입구 비교

| 입구 | 테스트 컨트랙트 | 상태 주입 | internal 직접 호출 | 등급 |
|---|---|---|---|---|
| `forge test --debug` | 필요 (`setUp`, 상태 주입, 하네스) | `vm.store` + 슬롯 계산 | 하네스 상속으로만 | `[실측]` |
| `forge script --debug` | Script 컨트랙트 필요 | `vm.store` | 하네스로만 | `[도움말]` |
| `cast call --trace --debug` | 불필요 | `--override-state-diff addr:slot:value` | ABI 함수만 | `[도움말]` RPC 노드와 배포 필요 |
| `cast run <tx> --debug` | 불필요 | 체인 상태 그대로 | ABI 경유만 | `[도움말]` 채굴된 tx 필요 |
| `soldb run` | 불필요 | `--storage slot=value` + `--runtime` | ❌ 42 스텝 REVERT | `[실측]` §11 |

**공통으로 남는 비용**: ① 접근 경로 ② 슬롯 수준 상태 지정 ③ 구체값 1개 확정. 논문에는 이 세 가지로 쓴다.
"테스트 컨트랙트가 필요하다"는 `soldb run`·`cast call` 이 반례다.

`stdstore` 는 슬롯 탐색을 자동화하지만 getter 호출을 근거로 하므로 `private` 변수에는 쓸 수 없다 `[미실측, 라이브러리 사용법 기준]`.

공정성: IQDebug 도 `@StateVar`/`@Param` 입력이 필요하다. 차별점은 준비 0 이 아니라
**변수 이름 수준 지정(슬롯은 엔진이 도출), internal 직접 대상화, 구간 입력**이다. 평가에서는 같은 시나리오의 필요 입력을 표로 비교한다.

### 12.8 실행 방법 정리 `[실측]`

```powershell
chcp 65001
cd evaluation\baselines\playground\forge
$F = "..\..\forge\bin\forge.exe"
& $F build
& $F test --match-contract "^VaultTest$"
& $F test --match-test "test_withdraw\(\)" --match-contract "^VaultTest$" -vvvv --decode-internal
& $F test --match-test "test_withdraw\(\)" --match-contract "^VaultTest$" --debug
cmd /c "..\..\forge\bin\forge.exe test --match-test ""test_withdraw\(\)"" --match-contract ""^VaultTest$"" --debug < ..\dbg_cmds.txt 2>&1"
& $F test --match-test testFuzz_withdraw --fuzz-runs 1024 --debug
& $F test --match-test test_breakpoint --debug
& $F test --match-test test_breakpoint --debug --dump .\bundle_x
```

함정:
- 문자열 변수 실행은 `& $F`.
- `--match-test` 는 시그니처 대상 정규식. `^test_withdraw$` 는 매치 0건, `test_withdraw` 는 2건. `"test_withdraw\(\)"` 를 쓴다.
- `--debug` 는 정확히 1개 테스트가 매치되어야 한다. `Verify.t.sol` 추가 후에는 `--match-contract` 도 필요할 수 있다.
- PowerShell 파이프는 BOM 이 붙어 첫 명령이 깨진다. `cmd /c` + `<` 리다이렉트, 명령 파일은 BOM 없는 UTF-8.
- `Verify.t.sol` 의 `testFuzz_afterFee_eq` 는 **의도적 실패** 테스트라 필터 없는 `forge test` 는 FAILED 로 끝난다.

SolDB `break` 대상 `[실측]`: `<pc>`, `<file>:<line>`, `line <line>`, `<function>`, `<Contract>.<f>`, `<state variable>`(쓰기), `storage <slot>`, `revert`, `call [<address>]`, `op <OPCODE>`. 조건은 지역변수·경로·상태변수·인자·enum 과 `pc`,`gas`,`depth`,`op`,`step` 을 `== != < <= > >=`, `&&`, `||` 로 비교.

### 12.9 SolDB TUI `[소스: soldb-tui-0.4.5/src/lib.rs]`

`--debug` 의 기본은 `(soldb)` 프롬프트이고 TUI 는 `tui` 명령으로 들어간다. **키보드 전용**이다 (이벤트 루프가 `Event::Key` 만 처리, 마우스 캡처 없음).

| 키 | 명령 | 키 | 명령 |
|---|---|---|---|
| `n`/`N` | next / reverse-next | `b` | 현재 줄 breakpoint |
| `s`/`S` | step / reverse-step | `m` | source ↔ asm |
| `c`/`C` | continue / reverse-continue | `:` | 명령 입력 |
| `f`/`F` | finish / reverse-finish | `Tab`/`Shift+Tab` | 패널 포커스 |
| `i`/`I` | nexti / reverse-nexti | `j`/`k`, `↑`/`↓`, `PgUp`/`PgDn` | 패널 스크롤 |
| `g` | 현재 줄로 복귀 | `?` | 도움말 |
| `q`, `Ctrl-X A` | 프롬프트로 | `Q`, `Ctrl-C` | 종료 |

패널: Source, Variables, Stack, Memory, Backtrace, Opcodes, Console. UI 평가는 논문 논거로 쓰지 않는다.

### 12.10 Background 용 — cheatcode 메커니즘 `[실측]`

- 테스트 파일과 `setUp` 은 **일반 Solidity** 이고 개발자가 직접 쓴다. `setUp`, `test_*` 는 forge 의 이름 규칙이다.
- cheatcode 는 `vm.xxx(...)` 호출 자체다. `forge-std/src/Base.sol:40` `Vm internal constant vm = Vm(VM_ADDRESS)`,
  주소 `0x7109709ECfa91a80626fF3989D68f67F5b1DD12D`. 코드 없는 주소로의 호출을 forge 의 EVM 이 가로채 네이티브로 처리한다.
- 실측: `vm.store` 내부 스텝 0개, `vm.breakpoint` 는 `STATICCALL` 1개.
- `bound`, `assertEq` 는 cheatcode 가 아니라 forge-std 라이브러리. `console.log` 는 별도 특수 주소 호출이다.

### 12.11 산출물 위치

| 파일 | 위치 | 주의 |
|---|---|---|
| 검증 테스트 | `playground/forge/test/Verify.t.sol` | 신규 |
| 명령 파일 | `playground/dbg_cmds.txt` | 신규 |
| z3 | `forge/z3/` | untracked, `.gitignore` 미적용 |
| solc 복사본, `Vault.runtime.bin`, `soldb_out/`, `bundle_unit/`, `m4*.py`, `m5*.py` | 이전 스크래치패드 | ⚠ 임시 폴더. 재현에 필요하면 옮길 것 |
| `lsp_probe.py`, `bundle_bp/` | 현재 스크래치패드 | ⚠ 임시 폴더 |

---

## §13 정정 — standalone SolDB 의 legacy 경로 (실측 2026-11-06)

**정정 대상**: "테스트 불필요와 지역변수 가시성을 동시에 만족하는 경로가 없다"는 채팅 서술은 **틀렸다**. §11 은 `soldb compile`(via-IR + ETHDebug) 경로만 시험했다.

`soldb run` 은 `-e addr:Name:dir` 의 dir 에 있는 legacy `combined.json` 도 읽는다 (`[소스]` `soldb-0.4.5/src/main.rs:2618`).

```powershell
solc --combined-json abi,bin,bin-runtime,srcmap,srcmap-runtime,storage-layout src/Vault.sol > <dir>\combined.json
# combined.json 의 bin-runtime 을 <dir>\Vault.runtime.bin 으로 추출
soldb run <dir>\Vault.runtime.bin "withdraw(uint256)" 10000 --runtime -e "0x5FbDB2315678afecb367f032d93F642f64180aa3:Vault:<dir>" --source-path . --storage "<slot>=0x4e20" --storage "0x1=0x4e20" --storage "0x2=0x1e" -x "break src/Vault.sol:25" -x continue -x vars --batch
```

결과 `[실측]`: 원본 `Vault`, 테스트·하네스 없음, **1,014 스텝**, `_afterFee` 진입, `amt=10000`, `r=10000→9970`, `i=0→1` 표시 (추론 경고 포함), backtrace 에 internal 프레임 포함.

| 경로 | 테스트 파일 | 지역변수 | 상태 입력 |
|---|---|---|---|
| `forge test --debug` | 필요 | 추론값 | cheatcode |
| `soldb run` + ETHDebug (`soldb compile`) | 불필요 | **없음** | slot=value |
| `soldb run` + legacy `combined.json` | 불필요 | **추론값** | slot=value |

추가 관찰:
- `--storage` 는 배포 **전에** 체인에 설정된다 (`[소스]` `main.rs:1303–1320`, 그 뒤 `chain.deploy`). 생성 코드로 실행하면 값이 호출에 반영되지 않았다 (§11 ③). 따라서 **생성자 상태와 사용자 지정 상태를 함께 쓰려면 `--runtime` + 생성자 상태 수동 입력**이 필요하다 (Vault 기준).
- standalone 은 아직 접근하지 않은 상태변수를 `<unknown: slot 0x1 has not been read or written yet>` 로 표시한다. `--storage` 로 넣은 값도 읽히기 전에는 표시되지 않았다.
- `--tui` 플래그는 도움말에서 확인, 실행은 미확인.

표기 정정: solc source map 은 **instruction index** 기준이다. `README.md:104`, `RELATED_TOOLS.md:21` 의 `pc → 소스` 서술은 정리 시 고칠 것.
