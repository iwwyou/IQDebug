# 평가 데이터셋 선정 — DAppSCAN 원천에서 최종 집합까지 (2026-11-19 동결)

> 논문의 *Dataset / Benchmark Construction / Threats to Validity* 를 쓰기 위한 **단일 기록**이다.
> 원천 데이터부터 최종 평가 대상까지의 모든 단계를 순서대로, 각 단계의 규칙 · 근거 · 수치와 함께 적고, 끝에 재현 방법을 둔다.
> 코드 대조용 세부(정규식, 함수 이름, 검토 이력)는 `PIPELINE_RECORD.md`, 이전 판의 이력은 `../DAppSCAN/DATASET_RECORD.md` 에 있다.
> 숫자는 모두 아래 두 스크립트의 출력에서 나온다. 손으로 판정한 단계는 없다.
>
> - 파일 선정: `evaluation/DAppSCAN/select_targets.py` → `evaluation/DAppSCAN/targets_v5.json`
> - 평가 대상 판정: `evaluation/Dataset/select_functions.py` → `evaluation/Dataset/target_functions.json` (정본)

---

## 0. 결과 요약

1. DAppSCAN 의 Solidity 파일 **21,457** 개에서 출발해, 소스 적격성 검사로 **569** 개 타깃 파일을 고른다(1 단계).
2. 569 개를 중복 제거 · 컴파일 · 함수 판정에 통과시켜 **평가 대상 파일 415 · 선정 함수 2,653** 개를 얻는다(2 단계).
3. 선정 함수는 T1(일반 계약의 public/external 함수) **1,608**, T2(internal · private · library 함수) **1,045** 다.
4. 415 파일은 **19** 개 감사 회사의 **81** 개 감사 폴더, **87** 개 저장소 스냅샷에서 나왔다.
5. 모든 선정 규칙은 소스와 컴파일러 출력만 본다. **IQDebug 를 한 번도 실행하지 않고** 정했으며, 엔진의 성공 · 실패가 표본 구성에 되먹임되지 않는다.

---

## 1. 원칙

1. **데이터셋은 버그 벤치마크가 아니라 실제 DApp 함수의 모집단이다.** IQDebug 의 기여는 추상 상태 위 디버깅 operation 의 의미론이므로, 취약점 라벨 · SWC 분류 · 버그 oracle 을 쓰지 않는다.
2. **세 단계를 분리한다.** (A) 소스 적격성(버전 · 의존 폐포 · 파일 유형), (B) 사전 정의한 함수 선정 기준(타입 AST), (C) 실행 결과.
   A · B 는 엔진 실행 전에 고정하고, C 의 실패는 보고만 하며 A · B 로 되돌려 표본을 지우지 않는다. 지원 범위 안의 엔진 실패는 버그로 보고 고친다.
3. **도구 중립.** 포함 여부는 IQDebug 가 아니라 공식 solc 컴파일 성공 여부로 정한다. 그래서 baseline(forge · Hardhat)도 같은 집합을 그대로 쓸 수 있다.
4. **보완 규칙을 두지 않는다.** 컴파일이 실패한 파일을 다른 버전이나 optimizer 설정으로 살리지 않는다. 결과를 보고 유리한 설정을 고르는 일을 막기 위해서다.

---

## 2. 원천

1. **DAppSCAN**(`InPlusLab/DAppSCAN`, arXiv 2305.08456). 감사 보고서마다 감사 대상 코드의 사본이 `DAppSCAN-source/contracts/<감사회사>-<프로젝트>/<저장소 스냅샷>/…` 구조로 들어 있다.
   커밋 `66a56619c44770e05c2db600fa6468115ff0dcd5`(2025-03-25)를 쓴다.
2. 받는 범위는 `DAppSCAN-source/contracts` 와 `DAppSCAN-source/SWCsource` 의 sparse checkout 이다(약 102 MB). 바이트코드(3.1 GB)와 보고서 PDF 는 쓰지 않는다.
3. 수집한 `.sol` 은 **21,457** 개이고, `git ls-files` 추적 목록과 정확히 같다(누락 0, 추가 0). 감사 폴더는 608 개다.
4. 고른 이유: 프로젝트 디렉터리 구조가 보존돼 import 폐포를 복원할 수 있고, 실제 감사 대상 코드라 산술 · 내부 호출 · 라이브러리 사용이 풍부하다.
5. 빌드 메타데이터는 거의 없다. 빌드 설정 파일이 있는 디렉터리가 트리 전체에 4 곳뿐이라 remapping 과 외부 패키지를 복원할 수 없다(→ 1 단계 ⑧, 위협 3).

---

## 3. 1 단계 — 소스 적격성 (`select_targets.py`, 21,457 → 569)

컴파일러를 쓰지 않는 렉서 · 정규식 단계다. 주석과 문자열을 가린 사본에서 판정해 주석 속 `pragma`·`import` 에 속지 않는다.
파일마다 아래 순서로 검사하고 처음 걸린 사유 하나를 기록한다.

1. **스냅샷 경계.** 감사 폴더 하나에 저장소나 커밋이 여러 개 들어 있으므로, 파일마다 소속 스냅샷을 정한다.
   빌드 마커(`package.json`, `foundry.toml`, `hardhat.config.*`, `truffle-config.js`, `remappings.txt`, `.git` 등)가 있는 가장 가까운 상위 디렉터리를 쓰고,
   없으면 "감사 폴더/그 아래 첫 디렉터리" 를 쓴다. import 는 스냅샷 경계를 넘어 해석하지 않는다.
2. **pragma 해석.** 파일 안 모든 `pragma solidity` 를 semver 제약(`^ ~ >= <= > < =`, `||`)으로 풀어 solc 출시 목록(0.4.0–0.8.38, 기준일 2026-10-28)과의 **교집합**을 허용 버전으로 둔다.
   해석할 수 없는 토큰, 빈 OR 항, 따옴표 버전은 거부한다(fail-closed).
   - pragma 구문 이상 **7**, pragma 없음 **138** 이 빠진다.
3. **Solidity 0.8 전용.** 허용 버전이 **전부 0.8.0 이상**인 파일만 남긴다. 0.8 부터 정수 산술이 기본 checked 라 엔진이 모델링하는 의미가 균일해진다.
   - **17,097** 이 빠진다. 이 중 1,675 개는 0.8.x 도 허용하지만 그 아래 버전도 허용하는 범위(`>=0.4.24` 847, `>=0.6.11` 251, `>=0.5.0` 197 …)다.
     이 규칙은 보수적인 선택이며, 모집단은 "pragma 가 0.8.x 만 허용하는 파일" 로 서술한다.
   - 여기까지 남은 파일은 **4,215** 다.
4. **파일 유형 제외(경로 태그).** 감사 대상 프로토콜 코드가 아닌 파일을 뺀다. 태그가 여럿이면 함께 기록한다.
   - 테스트 · mock(`test`·`mock` 디렉터리, `.t.sol`, `Test*`/`Mock*`) **775**
   - 외부 의존성 사본(`node_modules`, `@scope`, 알려진 패키지 이름, 스냅샷 직하 `lib`/`vendor`/`deps` 등) **470**
   - 병합 소스 **119**: `// File:` 마커 2 개 이상, 경로 · 파일명의 flatten 표기, 또는 import 0 · 최상위 선언 3 개 이상 · OpenZeppelin 고유 revert 문구 2 개 이상(라이브러리 본문 복제의 근거)
   - 형식 검증(`certora`, `spec`, `echidna` 등) **26**, migration **19**
   - 합계 **1,331** 이 빠지고 **2,884** 가 남는다.
   - 제외하지 않고 태그만 다는 유형: `suspected_flattened`(import 0 · 최상위 선언 3 개 이상인데 병합 근거 없음), `canonical_names`, `project_lib_dir`.
5. **본문 있는 함수.** 본문 있는 함수가 하나도 없는 파일(인터페이스, 선언만 있는 파일, constructor·receive·fallback 만 있는 파일 50 포함) **1,125** 를 뺀다. → **후보 1,759**.
6. **import 해석.** 같은 스냅샷 안에서 상대 경로 → remapping → 스냅샷 루트 기준 → 같은 디렉터리 순으로 찾는다. 파일 이름만으로 추측하지 않는다.
7. **폐포 검사.** 후보마다 전이적 import 폐포를 만들고 다음을 요구한다.
   - 미해결 import 0. 위반 **989** — 가장 큰 탈락이다. 외부 패키지(`@openzeppelin/…` 등)를 복원하지 않기 때문이다(위협 3).
   - 폐포의 모든 파일이 유효한 0.8 전용 pragma. 위반 **25**.
   - 폐포 전체에 공통으로 허용되는 solc 버전이 있음. 위반 0.
   - → **옵션 2 = 745**.
8. **범용 프레임워크 저장소 제외.** 감사 폴더 안에 OpenZeppelin 라이브러리 저장소 자체가 통째로 들어 있던 스냅샷 2 개를 뺀다
   (`Chainsulting-Global Rockstar/openzeppelin-contracts-upgradeable-master` 104 파일, `Chainsulting-SWAPP Protocol-project2/openzeppelin-contracts-4.2.0` 72 파일).
   감사 대상 프로토콜이 아니라 범용 프레임워크라서다. 규칙이 아니라 이름 목록(manifest)으로 지정한다.
   - → **옵션 2′ = 569**(102 스냅샷, 95 감사 폴더). **이것이 2 단계의 입력이다.**

1 단계 수치 흐름: 21,457 → (pragma 145 · 0.8 미만 허용 17,097) 4,215 → (유형 태그 1,331) 2,884 → (본문 함수 0 1,125) 1,759 → (미해결 import 989 · 폐포 0.8 미만 25) 745 → (프레임워크 저장소 176) **569**.

---

## 4. 2 단계 — 평가 대상 파일 · 함수 (`select_functions.py`, 569 → 415 파일 · 2,653 함수)

solc 의 **타입 있는 AST** 로 판정한다. 파일 단계(F1–F5)와 함수 단계로 나뉜다.

### 4.1 파일 단계

1. **F1 폐포.** 569 개 모두 폐포를 다시 구한다. 1 단계와 다른 구현(감사 폴더 안 해석)이지만 569 전부에서 두 결과가 같음을 확인했다. 실패 0.
2. **F2 중복 제거.** 서로 다른 감사 폴더에 같은 코드가 반복되는 경우(같은 라이브러리 사본, 같은 프로젝트의 재감사)를 하나로 줄인다.
   - 키 = (타깃 파일 내용, 폐포의 다른 파일들의 "타깃 기준 상대경로 + 내용" 집합).
   - 내용 = 주석을 지운 뒤의 토큰열(문자열 리터럴은 안의 공백까지 보존)의 sha256. 그래서 공백 · 줄바꿈 · 주석만 다른 파일은 같고, 코드나 문자열이 한 글자라도 다르면 다르다.
   - 같은 키면 경로 정렬 순 첫 파일을 남긴다.
   - **62** 개(40 그룹)가 빠지고 **507** 이 남는다. 62 개 중 30 은 원문까지 같고, 29 는 공백을 접으면 같으며, 3 은 주석과 파라미터 목록 줄바꿈만 다르다(토큰열 직접 대조로 확인).
3. **F3 컴파일.** 폐포 전체를 공식 solc 로 컴파일한다.
   - 버전 = 폐포 모든 pragma 의 교집합 중 설치된 최대 버전. 설정 = optimizer off, `metadata.bytecodeHash = none`.
   - 컴파일하는 것은 **정규화 소스**다(아래 4). 원본도 같은 설정으로 컴파일해 둘의 바이트코드를 비교한다.
   - 실패 **5** 개를 뺀다. 5 개 모두 원본도 같은 설정에서 실패하므로 정규화 탓이 아니다.
     QuillAudits FutiraCoin `token_audit.sol`(`ERC20` 정의 누락), Chainsecurity Compound `DAIInterestRateModelV3.sol`(0.8.38 의 override 명시 TypeError),
     SlowMist eden `Vault.sol` 과 QuillAudits Keep3r `StakingRewardsV3.sol`·`StakingRewardsV3-1.sol`(optimizer off 에서 stack too deep).
   - → **502 컴파일**. 사용된 solc 는 0.8.38(대부분)부터 0.8.0 까지 13 개 버전이다.
4. **정규화와 그 검증.** IQDebug 는 한 줄에 한 문장 단위로 소스를 소비하므로, 중괄호 없는 제어문을 감싸고 한 줄에 붙은 블록 · 문장을 나누는 기계적 정규화를 한다.
   세 도구 모두 이 정규화 소스를 쓴다. 정규화가 프로그램을 바꾸지 않았다는 근거로, **컴파일된 502 개 전부 원본과 정규화본의 creation · deployed 바이트코드가 모든 컨트랙트에서 같다.**
   원본 줄과 정규화 줄의 대응(`line_map`)은 폐포 파일 940 개 전부에서 단조 · 완전함을 검사했다(위반 0).
5. **F4 후보 함수.** 타깃 파일의 `FunctionDefinition` 중 일반 함수이고 본문이 있는 것. constructor · receive · fallback · modifier 는 넣지 않는다.
   502 파일의 후보는 **4,360** 개(T1 2,502 · T2 1,858)다. 후보 0 인 파일은 0 개.
6. **F5 선정.** 아래 4.2 의 규칙으로 선정된 함수가 하나도 없는 파일 **87** 개를 뺀다. `Context.sol`, `Ownable.sol`, `ERC165.sol`, `Pausable.sol` 처럼 기준 미달 함수만 있는 파일이다.
   이 파일들도 다른 타깃의 폐포 구성원으로는 그대로 남아, 호출되면 실행된다.
   - → **평가 대상 파일 415.**

### 4.2 함수 단계

1. **계층.**
   - **T1** = 일반 계약(`contractKind == contract`)의 `public`/`external` 함수. ABI 로 보이는 함수라는 뜻이며, 모두 트랜잭션 진입점이라고 단정하지 않는다.
   - **T2** = 그 밖의 본문 있는 함수(internal · private, library 함수). library 수학 함수처럼 그 자체의 기능을 따로 파악해야 하는 경우가 많으므로 **T2 도 단독 디버깅 대상**으로 둔다.
   - 두 계층을 모두 엔진 실행 전에 AST 로 고정한다. "T1 실행에서 닿은 T2" 처럼 실행 결과로 정하면 엔진 실패가 표본을 바꾸기 때문이다.
2. **구조 계층.** 함수 본문에 다음 중 하나가 있으면 선정한다.
   - 비-builtin 호출: 내부 · 상속 · library · `this` · 외부 · 저수준 · `new` · 함수 포인터 호출, 어셈블리 `call`/`delegatecall`(Step Into 의 대상이거나 호출 효과가 있는 지점).
   - 분기: `if`, 삼항, `try`, Yul `if`/`switch`.
   - 루프: `for`, `while`, `do-while`, Yul `for`.
   - builtin(`require`, `keccak256`, `abi.*`, 형 변환, struct 생성자, `emit`, 배열 `push` 등)은 호출로 치지 않는다. 타입 AST 로 판정하므로 형 변환과 이벤트를 호출로 잘못 세지 않는다.
   - 판정은 **타깃 함수 자신의 본문**만 본다. modifier 본문의 제어흐름과 `require` 는 넣지 않는다. 선정 기준과 실제 분석 범위는 다르다 — 분석할 때는 modifier 와 callee 도 모두 실행한다.
3. **연산 계층.** 구조가 없는(직선형) 함수는 다음 중 하나를 만족할 때만 선정한다. 분기 · 호출이 없어도 입력 구간이 여러 연산을 지나는 계산은 추상 상태로 관찰할 가치가 있기 때문이다.
   - (A) 셀 문장이 3 개 이상이고(`emit` · placeholder 제외, 선언 · `require` · `return` · 대입 포함) **입력 의존** 산술 연산이 하나 이상 있다.
   - (B) 한 문장에 산술 연산자(`+ - * / % ** << >>`, 복합 대입, `++ --`, 단항 `-`)가 2 개 이상 있거나, 두 피연산자가 모두 입력 의존인 비선형 연산(`* / % ** << >>` 와 그 복합 대입)이 있다.
   - **입력** = 파라미터, constant 가 아닌 상태변수(immutable 포함), `msg`/`block`/`tx` 멤버, `address(this).balance`. 이들에서 값을 받은 지역변수도 입력 의존이다.
     직선형이므로 문장 순서대로 전파하고, 입력과 무관한 값을 단순 대입하면 의존이 사라진다.
   - **컴파일 시점 상수 부분식은 산술로 세지 않는다.** 리터럴 · constant 변수 · `type(T).min/max` 만으로 된 식(`-2**127`, `10**18`)은 엔진에서도 단일 값이라 구간 연산을 거치지 않는다.
     예: SafeCast `toInt128` 의 `require(value >= -2**127 && value < 2**127)` 는 비교 하나로 본다.
4. **T2 얇은 래퍼 제외.** T2 이면서 분기 · 루프 · 어셈블리 · 산술이 없고, 셀 문장이 2 개 이하이며, 호출이 **전부 본문 있는 내부 · library · 상속 함수로의 하강**인 함수는 단독 대상에서 뺀다.
   예: `mulWadDown(x, y) { return mulDivDown(x, y, WAD); }`. 계산은 callee 에 있고 callee 는 따로 선정된다. 이 함수들은 호출자의 실행 안에서 callee 로 관찰된다.
   외부 · 저수준 호출이 있는 함수(`token.call(...)` + `require`)는 그 호출 자체가 내용이므로 래퍼로 보지 않는다. T1 에는 적용하지 않는다.
5. **제외 사유.** 선정되지 않은 함수는 "사전 복잡도 기준 미달" 로 기록한다. 디버깅할 가치가 없다는 뜻이 아니며, 엔진은 이 함수들도 정상 분석한다.
   사유는 view/pure → modifier 있음 → 기타 순으로 처음 걸리는 것 하나다(T2 래퍼는 별도 사유).

### 4.3 함수 수치

1. 후보 **4,360** → 선정 **2,653**, 제외 1,707.
2. T1: 후보 2,502, 선정 **1,608**(구조 1,587 · 연산 21), 제외 894(view/pure 476 · modifier 있음 304 · 기타 114).
3. T2: 후보 1,858, 선정 **1,045**(구조 880 · 연산 165), 제외 813(view/pure 320 · modifier 있음 3 · 기타 175 · 얇은 래퍼 315).
4. 연산 계층 186 개를 규칙별로 보면 (A)만 93, (B)만 67, 둘 다 26 이다.

---

## 5. 최종 집합의 구성

1. **출처.** 415 파일, 87 스냅샷, 81 감사 폴더, 19 감사 회사. 회사별 감사 폴더 수는 QuillAudits 17, Hacken 14, PeckShield 11, Trail of Bits 7, BlockSec 5, Inspex 5, Chainsecurity 4 순이다.
2. **편중.** 선정 함수 기준 상위 1 · 5 · 10 스냅샷이 각각 14.3% · 49.5% · 62.8% 를 차지한다(최대: MahaDAO). 그래서 함수 단위(micro)와 스냅샷 단위(macro) 결과를 함께 보고한다.
3. **파일 구성.** T1 만 선정된 파일 132, T1 · T2 모두 138, T2 만 145.
4. **컴파일러.** 415 파일의 solc 는 0.8.38 이 268 개이고, 나머지는 pragma 가 고정한 0.8.0–0.8.13 이다.
5. **폐포 크기.** 중앙값 3 파일, 최대 39 파일.
6. **함수 형태.**
   - 종류: T1 external 908 · public 700, T2 계약 internal 519 · private 77, library internal 372 · private 46 · external 18 · public 13. free function 은 0 이다.
   - mutability: nonpayable 1,639, view 607, pure 354, payable 53.
   - 파라미터 수: 0 개 326, 1 개 797, 2 개 816, 3 개 404, 4 개 이상 310.
   - abstract 계약 소속 299, virtual 332.
7. **실행 맥락 태그**(선정에는 쓰지 않고 보고에 쓴다).
   - 효과가 모델링되지 않는 외부 호출(CG3)을 포함: 782. `delegatecall` 포함: 47.
   - storage 참조 파라미터(호출 맥락의 storage 를 받아야 하는 함수): 117.
   - 폐포 안에 정적 내부 호출자가 없음(`is_root`): 1,892. T2 중 폐포 안 호출자가 있어 callee 로도 관찰되는 것: 581.

---

## 6. 민감도 분석용 변형 (주 집합은 위 415 · 2,653)

1. **`suspected_flattened` 제외.** 569 에서 이 태그 18 개를 뺀 551 을 출발점으로 한 결과. 규칙상 태그만 다는 유형이라 주 집합에는 넣는다.
2. **(A) 의 문장 계수.** 연산 계층 수가 선언 · `require` 를 모두 셀 때 186, `require` 를 빼면 133, 선언을 빼면 151, 둘 다 빼면 110 이다. 주 규칙은 첫째다.
3. **`is_root` 부분집합.** root 로 한정한 결과를 따로 낸다.
4. 원시 특징(문장 종류별 수, 산술 수, 입력 의존 산술 수, 상수 산술 수, 분기 · 루프 · 호출 수)을 함수마다 저장하므로 다시 컴파일하지 않고 재집계할 수 있다.

---

## 7. 위협과 한계

1. **스냅샷 경계는 대부분 추정이다.** 전체 파일의 99% 가 "감사 폴더 아래 첫 디렉터리" 규칙이다. 569 에서는 503 개가 커밋 해시 이름 디렉터리, 36 개가 그 하위, 30 개가 낮은 신뢰도 경계다.
   낮은 신뢰도 30 개(17 폴더)는 폴더마다 `.sol` 1–13 개이고 같은 파일 이름이 겹치지 않아 여러 버전이 섞인 흔적이 없다. 569 의 폐포가 스냅샷 밖으로 나가거나 커밋 디렉터리 둘을 가로지르는 경우는 0 이다.
2. **모집단은 0.8 전용 pragma 파일이다.** 0.8 을 허용하지만 그 아래도 허용하는 파일(파일 단위 1,675)은 들어오지 않는다.
3. **외부 패키지를 복원하지 않는다.** 미해결 import 989 가 가장 큰 탈락이며, 표본은 "외부 패키지 없이 자급하는 파일" 쪽으로 편향된다. 빌드 설정이 없어 패키지 버전을 정할 근거가 없기 때문이다.
4. **고정 컴파일 설정.** 컴파일 실패 5 개는 "pragma 허용 최대 설치 버전 · optimizer off" 설정에서의 실패다. 다른 허용 버전이나 설정에서는 컴파일될 수 있다.
5. **선정 기준은 사전 정의한 복잡도 기준이다.** 제외된 함수(예: `require(balance >= amount); balance -= amount;`)도 디버깅 가치가 있을 수 있다. 결과는 "기준을 만족하는 함수 집합" 에 대한 것으로 한정해 서술한다.
6. **T2 단독 실행의 입력 맥락.** T2 를 단독으로 실행하면 호출자가 보장하던 전제가 없으므로, 실제로는 도달하지 않는 상태까지 덮을 수 있다. T1 · T2 결과를 분모별로 나눠 보고한다.
7. **정규화 소스를 쓴다.** AST 위치(`definition_id` 의 바이트 범위)는 정규화본 기준이다. 원본 줄과의 대응은 `line_map` 으로 한다.
8. **대표성.** 이 집합은 Ethereum 생태계의 통계적 표본이 아니라 "감사 이력이 있는 공개 DApp 중 사전 정의한 조건을 만족하는 소스 집합" 이다.

---

## 8. 재현 방법

### 8.1 환경

1. Windows(solc 는 windows-amd64 공식 바이너리), Python **3.10.11**. 저장소 루트에서 `.venv` 를 만들고 `requirements.txt` 를 설치한다.
2. 아래 명령은 저장소 루트(`IQDebug/`)에서 실행한다. 콘솔이 cp949 이면 `PYTHONIOENCODING=utf-8` 을 준다.

### 8.2 원천 받기

```
git clone --filter=blob:none --sparse https://github.com/InPlusLab/DAppSCAN.git evaluation/DAppSCAN/repo
git -C evaluation/DAppSCAN/repo sparse-checkout set DAppSCAN-source/contracts DAppSCAN-source/SWCsource
git -C evaluation/DAppSCAN/repo checkout 66a56619c44770e05c2db600fa6468115ff0dcd5
```

### 8.3 solc 설치 — 정확히 이 13 개 버전

```
.venv/Scripts/python.exe -I evaluation/solc/fetch_solc.py 0.8.0 0.8.2 0.8.3 0.8.4 0.8.5 0.8.6 0.8.7 0.8.9 0.8.10 0.8.11 0.8.12 0.8.13 0.8.38
```

공식 목록(`binaries.soliditylang.org/windows-amd64/list.json`)의 sha256 으로 검증한다.
**설치된 버전 집합이 결과에 영향을 준다.** F3 는 "허용 버전 중 설치된 최대" 를 쓰므로, 다른 버전을 더 설치하면 일부 파일의 컴파일 버전이 바뀔 수 있다.
`evaluation/solc/` 에 위 13 개만 둔다(`compile_survey.py` 가 `solc-X.Y.Z.exe` 이름으로 찾는다).

### 8.4 실행

```
# 1 단계 — 파일 선정 (약 7 분). 추적 목록과 다르면 중단한다.
git -C evaluation/DAppSCAN/repo ls-files -z > tracked.z
.venv/Scripts/python.exe -I evaluation/DAppSCAN/select_targets.py evaluation/DAppSCAN/repo/DAppSCAN-source/contracts evaluation/DAppSCAN/targets_v5.json --tracked tracked.z

# 2 단계 — 평가 대상 파일 · 함수 (약 2 분)
.venv/Scripts/python.exe evaluation/Dataset/select_functions.py evaluation/Dataset/target_functions.json --jobs 6
```

`--jobs` 는 병렬도일 뿐 결과에 영향이 없다(F1 · F2 는 순차, F3 이후는 타깃마다 독립).

```
# 3 단계 — 세 도구 공통 입력: 정규화 소스 트리 (약 1 분)
.venv/Scripts/python.exe evaluation/Dataset/export_norm_sources.py --jobs 6
# 4 단계 — IQDebug 엔진용 의존성 빌드 (약 3 분)
.venv/Scripts/python.exe evaluation/Dataset/build_deps.py --jobs 6
```

### 8.7 공통 입력 트리 `evaluation/Dataset/norm_sources/`

IQDebug · forge · Hardhat 이 **같은 파일**을 쓰도록 만든 정규화 소스 트리다(생성물, git 제외).

1. 타깃 하나 = 독립 프로젝트 디렉터리 `norm_sources/<타깃 id>/`(id 는 `deps/` 와 같다). 폐포는 F1 과 같은 해석기로 구한다.
2. 파일 경로 = solc 에 넘긴 소스 이름에서 폐포 공통 디렉터리를 뗀 것이다. 상대 import 는 상대 위치가 그대로라 풀린다.
   비상대 import 가 있는 3 타깃(`@openzeppelin/…` 이 스냅샷 안 사본을 가리킴)은 그 이름 그대로 프로젝트 루트 아래에 두고 `remappings.txt` 를 둔다.
3. `line_maps.json` 은 파일마다 정규화 줄 → 원본 줄 대응이다.
4. `manifest.json` 은 타깃마다 디렉터리 id, solc 버전, 진입 파일, 파일 → 원천 경로, 파일별 sha256 을 담는다. 컴파일 설정은 optimizer off · `bytecodeHash = none` 이다.
5. **검증(2026-11-19).** 415 타깃 전부 트리에서 다시 읽은 파일로 컴파일되고, 원본 컴파일과 creation · deployed 바이트코드가 같다(외부 library 링크 자리표시자만 같은 표시로 바꿔 비교).
   solc 버전도 415 전부 `target_functions.json` 기록과 같다. IQDebug 용 `deps/<id>/sources/` 와는 415 전부 **바이트 단위로 같다**(파일 이름만 단위 이름으로 바뀜).
6. 이 검증은 solc standard-JSON 으로 한 것이다. forge · Hardhat 의 프로젝트 설정(소스 디렉터리, remapping)으로 실제 컴파일되는지는 baseline 쪽에서 확인한다.

### 8.5 기대 결과와 확인

1. `targets_v5.json`: `selected_option2` 745, `selected_option2prime` 569, `selected_option2prime_minus_suspected` 551. 2026-11-17 재실행에서 세 목록과 21,457 파일 각각의 탈락 단계가 이전 결과와 같았다.
2. `target_functions.json` 의 `summary`:
   `file_stage` = selected 415 · F5_no_selected_function 87 · F2_duplicate 62 · F3_compile_fail 5, `functions_selected` 2,653,
   `by_tier_layer` = T1/structural 1,587 · T1/operation 21 · T2/structural 880 · T2/operation 165, `bytecode_same` = True 502.
3. 파일에는 생성 시각(`summary.generated`)과 단계별 소요 시간(`sec_*`)이 들어 있어 바이트 단위로는 같지 않다. 이 둘을 빼고 비교한다. 2026-11-19 에 동결본과 재실행 결과가 이 기준으로 완전히 같았다.
4. 동결 시점 sha256(참고): `target_functions.json` 64d7338c3468f2d0b2819c029b0d945549e05086e9463a08bb0a7d0d3d4be92e,
   `targets_v5.json` 2af6c011aa9b6a2a07549c4d61b33ca9008d73cf0a8bf1278c685033f482b78e,
   `select_targets.py` d4a3ddc9280b2c26ed8a5b14715773876e156117e6a720ca8110b5a5878bf479.
   `select_functions.py` 는 동결 후 이 문서와 함께 커밋되는 판이 정본이다.

### 8.6 산출물 읽는 법

1. `target_functions.json` 의 `files[]` 는 569 개 전부다. 각 항목의 `stage` 가 그 파일이 어디서 빠졌는지(`F2_duplicate` 면 `duplicate_of`), 통과했으면 `selected` 다.
2. 각 파일의 `functions[]` 는 F4 후보 전부이고, 함수마다 `selected`, `layer`(structural/operation), `exclusion_reason`, `tier`, 원시 `features`, 태그(`cg3_effect`, `has_delegatecall`, `is_root`, `callers_in_closure`, `requires_storage_binding`, `params`), `definition_id`(스냅샷|파일|계약|정규화본 AST 바이트 범위)가 있다.
3. 평가 대상 = `stage == "selected"` 인 파일의 `selected == true` 인 함수다.
