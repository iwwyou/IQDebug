"""DAppSCAN 타깃 함수 후보 선정 (v4, 2026-10-28).

이력: v1 `scan_projects.py`(= `historical/select_targets_v1.py`) → v2(내장 사본은 `audit_v2.py`)
      → v3 `historical/select_targets_v3.py` → **v4 (이 파일)**.
      앞으로 각 판은 `historical/` 에 보존한다(3차 검토 ①-2 재현성 지적).

3차 검토 반영 (지적 → 조치)
  1. `suspected_flattened` 100개가 **추정 함수의 46.6%** 를 차지 → 약한 신호만으로 제외/포함을
     결정하지 않고, **근거 기반으로 재분류**한다. 조사 결과 100개 중 **84개는 표준 라이브러리 계약
     (Context/Ownable/IERC20/SafeMath/…)을 본문째 내장**하고 있었다(함수 3,426개) → `merged_source`
     로 **제외**. 근거가 없는 16개(함수 368개)는 정상 단일 파일 설계로 보고 **유지**하되 태그를 남긴다.
  2. `audit_v2.py` 가 v3 교체 후 실행 불가 → v2 규칙을 그 스크립트에 **내장**해 독립 실행 가능하게 했다.
  3. 중복 해시가 `mask`(문자열 내용 공백화) 기반이라 문자열만 다른 파일을 같은 해시로 묶었다
     → **원문 해시(raw_hash)** 와 **주석만 제거하고 문자열은 보존한 정규화 해시(normalized_hash)** 로 교체.
  4. `func_count` 가 **Yul `function`** 까지 셌고, 선언이 400자를 넘으면 함수를 놓쳤다
     → assembly 블록을 공백화한 뒤 세고, 전방 탐색 길이 제한을 없앴다.
  5. `SOLC_VERSIONS` 에 미출시 버전(0.8.39/0.8.40)이 포함 → **0.8.38 상한 + 기준일 명시**.
     (확인: 0.8.35 2026-04-29, 0.8.36 2026-07-09, 0.8.37 2026-09; 0.8.38 은 2차 출처만 확인됨.
      데이터셋 최대 pragma 는 0.8.30 미만이라 선정 결과에는 영향이 없다.)
  6. `top_blocking_unresolved_imports` 는 복원 효과가 아니라 **영향 후보 수**이므로 이름을
     `affected_candidate_count` 로 바꾸고 합산 불가임을 명시.
  7. `unique_suffix` fallback(전 코퍼스 3건)은 solc 의 import 의미론과 어긋나므로 **제거**.
  8. 경계·메타데이터: 마커 종류별 집계, xlsx 미등재 감사 디렉터리 목록,
     `boundary_method` 별 신뢰도, 선정 파일의 **import 해석 근거**를 manifest 에 기록.

2차 검토 반영분(유지): 확장 경로 탐색 + `onerror` + git 대조 / 주석·문자열 마스킹 /
  semver 제약 교집합 + fail-closed / 스냅샷 경계 격리 / 폐포 공통 컴파일러 검사 /
  태그는 경로 **성분** 기준 / 범용 프레임워크 저장소(OpenZeppelin 2개) 제외 manifest.

사용:
  python -I select_targets.py <contracts_root> <out_json> [--tracked <git-ls-files -z>]
                              [--xlsx <Audit_and_Repository_link.xlsx>]
"""
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict

# Windows 기본 콘솔 코드페이지(cp949)에서 '—' 같은 문자가 출력 중 예외를 내 스크립트를 죽였다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────── 어휘 분석 ───────────────────────────
_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def _blank(t):
    return re.sub(r"[^\n]", " ", t)


def lex(raw: str):
    """(bare, mask) — 원문과 길이·개행 위치 동일.
    bare = 주석만 공백화(문자열 유지) / mask = 주석 + 문자열 내용까지 공백화."""
    bare, mask, pos = [], [], 0
    for m in _TOKEN.finditer(raw):
        chunk = raw[pos:m.start()]
        bare.append(chunk)
        mask.append(chunk)
        t = m.group()
        if t.startswith(("//", "/*")):
            bare.append(_blank(t))
            mask.append(_blank(t))
        else:
            bare.append(t)
            mask.append(_blank(t))
        pos = m.end()
    bare.append(raw[pos:])
    mask.append(raw[pos:])
    return "".join(bare), "".join(mask)


def content_hashes(raw: str):
    """(raw_hash, normalized_hash). 정규화는 **주석만** 제거하고 문자열 리터럴은 원문 그대로 둔다.
    문자열 밖의 공백만 접는다 — 3차 검토 ①-3·②-5."""
    raw_hash = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    # 세그먼트로 쪼갠다: 코드 / 문자열 리터럴 / 주석(→ 공백 하나)
    segs, pos = [], 0
    for m in _TOKEN.finditer(raw):
        segs.append(("code", raw[pos:m.start()]))
        t = m.group()
        segs.append(("code", " ") if t.startswith(("//", "/*")) else ("str", t))
        pos = m.end()
    segs.append(("code", raw[pos:]))
    # 인접한 코드 세그먼트를 먼저 합친 뒤 공백을 접는다 — 경계에서 공백이 두 개 남지 않게.
    merged, buf = [], []
    for kind, text in segs:
        if kind == "code":
            buf.append(text)
        else:
            if buf:
                merged.append(re.sub(r"\s+", " ", "".join(buf)))
                buf = []
            merged.append(text)
    if buf:
        merged.append(re.sub(r"\s+", " ", "".join(buf)))
    norm = "".join(merged).strip()
    return raw_hash, hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()


PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]*);", re.I)
IMPORT_KW = re.compile(r"\bimport\b")
IMPORT_PATH = re.compile(r"""import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""")
FUNC = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(")
TOPDECL = re.compile(r"(?m)^\s*(?:abstract\s+)?(?:contract|library|interface)\s+(\w+)")
FILE_MARK = re.compile(r"(?m)^\s*//\s*File:")
ASM = re.compile(r"\bassembly\b[^{]*\{")

# ─────────────────────────── pragma = semver 제약 ───────────────────────────
# 기준일 2026-10-28. 출시된 릴리스만 넣는다(미출시 버전을 넣으면 "허용 집합이 비지 않았다" 는
# 판정이 거짓이 된다 — 3차 검토 ①-5). 최종적으로는 공식 릴리스 manifest 에서 생성해야 한다.
SOLC_RELEASES_ASOF = "2026-10-28"
SOLC_VERSIONS = ([(0, 4, i) for i in range(0, 27)] + [(0, 5, i) for i in range(0, 18)]
                 + [(0, 6, i) for i in range(0, 13)] + [(0, 7, i) for i in range(0, 7)]
                 + [(0, 8, i) for i in range(0, 39)])   # 0.8.0 … 0.8.38
_CMP = re.compile(r"(\^|~|>=|<=|>|<|=)?\s*(\d+)\.(\d+)(?:\.(\d+))?")


def _sat(v, op, major, minor, patch):
    target = (major, minor, patch if patch is not None else 0)
    if op in ("^", "~"):
        return v >= target and (v[0], v[1]) == (major, minor)
    if op == ">=":
        return v >= target
    if op == ">":
        return v > target
    if op == "<=":
        return v <= (major, minor, patch) if patch is not None else v[:2] <= (major, minor)
    if op == "<":
        return v < target if patch is not None else v[:2] < (major, minor)
    return v == target if patch is not None else v[:2] == (major, minor)


_spec_cache = {}


def allowed_versions(spec: str):
    spec = spec.strip()
    if spec not in _spec_cache:
        out = set()
        for part in spec.split("||"):
            cmps = [(m.group(1), int(m.group(2)), int(m.group(3)),
                     None if m.group(4) is None else int(m.group(4)))
                    for m in _CMP.finditer(part)]
            if cmps:
                out |= {v for v in SOLC_VERSIONS if all(_sat(v, *c) for c in cmps)}
        _spec_cache[spec] = out
    return _spec_cache[spec]


def spec_syntax_ok(spec: str) -> bool:
    """fail-closed. 4차 검토 ①-2: `0.8.20 ||` 처럼 **빈 OR 항**이 있어도 통과했었다.
    이제 각 항이 ① 비어 있지 않고 ② 비교자/버전을 실제로 포함하며 ③ 설명되지 않는 토큰이
    남지 않을 때만 통과한다."""
    parts = spec.split("||")
    if any(not p.strip() for p in parts):
        return False
    return all(_CMP.search(p) and not re.search(r"\S", _CMP.sub("", p)) for p in parts)


def file_allowed(specs):
    if not specs:
        return None
    acc = None
    for s in specs:
        a = allowed_versions(s)
        acc = a if acc is None else (acc & a)
    return acc


# ─────────────────────────── import / 함수 ───────────────────────────
def imports_of(bare: str, mask: str) -> list:
    out = []
    for m in IMPORT_KW.finditer(mask):
        mm = IMPORT_PATH.match(bare, m.start())
        if mm:
            out.append(mm.group(1))
    return out


def strip_assembly(mask: str) -> str:
    """assembly 블록 본문을 공백화 — Yul `function` 을 Solidity 함수로 세지 않기 위해."""
    out = list(mask)
    for m in ASM.finditer(mask):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(mask)):
            if mask[j] == "{":
                depth += 1
            elif mask[j] == "}":
                depth -= 1
                if depth == 0:
                    for k in range(i + 1, j):
                        if out[k] != "\n":
                            out[k] = " "
                    break
    return "".join(out)


CTOR = re.compile(r"\bconstructor\s*\(")
RECV = re.compile(r"\breceive\s*\(\s*\)")
FALL = re.compile(r"\bfallback\s*\(")


def _body_nonempty(txt: str, start: int) -> bool:
    """start 이후 첫 `{`..`}` 블록에 코드가 있는지(공백·세미콜론만이면 비었다고 본다)."""
    i = txt.find("{", start)
    if i == -1:
        return False
    depth = 0
    for j in range(i, len(txt)):
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
            depth -= 1
            if depth == 0:
                return bool(re.sub(r"[\s;]", "", txt[i + 1:j]))
    return False


def special_fns(mask_noasm: str) -> dict:
    """constructor / receive / fallback 집계.

    ★ 타깃 단위는 **named·implemented 함수**다(사용자 결정). 생성자는 엔진의 디버깅 입력 모델이
      대체하고(상태변수가 전부 입력 슬롯), receive/fallback 은 이름·파라미터가 없어 입력 모델이
      바인딩할 것이 없다. 다만 **파일을 데이터셋에서 빼지는 않는다** — 타깃만 되지 않을 뿐이고
      폐포 구성원으로는 그대로 쓰인다. 여기서는 존재 여부만 기록한다(4차 검토 ①-3).
    """
    out = {}
    for nm, rx in (("constructor", CTOR), ("receive", RECV), ("fallback", FALL)):
        hits = list(rx.finditer(mask_noasm))
        out[nm] = len(hits)
        out[nm + "_nonempty"] = sum(1 for m in hits if _body_nonempty(mask_noasm, m.end()))
    return out


def func_count(mask_noasm: str) -> int:
    """본문 있는 Solidity 함수 수. 전방 탐색에 길이 제한을 두지 않는다(3차 검토 ①-4 반례 B)."""
    n = 0
    for m in FUNC.finditer(mask_noasm):
        rest = mask_noasm[m.end():]
        b, s = rest.find("{"), rest.find(";")
        if b != -1 and (s == -1 or b < s):
            n += 1
    return n


# ─────────────────────────── 태그 ───────────────────────────
EXT_PKGS = {"openzeppelin", "openzeppelin-contracts", "openzeppelin-contracts-upgradeable",
            "uniswap", "uniswap-v2-core", "uniswap-v3-core", "chainlink", "aave",
            "solmate", "forge-std", "ds-test", "hardhat", "truffle", "prb-math"}
DEP_ROOT_DIRS = {"node_modules", "lib", "libs", "vendor", "deps", "dependencies",
                 "submodules", "third_party", "third-party", "external"}
TEST_DIRS = {"test", "tests", "testing", "mock", "mocks", "test_helpers", "test-utilities",
             "testutils", "test-utils"}
VERIF_DIRS = {"certora", "spec", "specs", "echidna", "scribble", "halmos"}
TEST_NAME = re.compile(r"^(?:Test|Mock)(?=[A-Z_0-9]|\.sol$)|^(?:test|mock)(?=[_\-0-9]|\.sol$)")
MIG_NAME = re.compile(r"^migrations?\.sol$|^migration", re.I)
FLAT_NAME = re.compile(r"(?:^|[_\-.])flat(?:ten(?:ed)?)?(?:$|[_\-.])", re.I)
# ★ 병합/vendoring 의 **본문 지문** — 표준 라이브러리 구현에만 나오는 revert 문구.
#   이름이 아니라 본문이 복사됐다는 증거라서, "이름만 같은 자작 계약" 을 걸러내지 않는다.
OZ_FINGERPRINTS = [
    "Ownable: caller is not the owner", "Ownable: new owner is the zero address",
    "ERC20: transfer amount exceeds balance", "ERC20: transfer from the zero address",
    "ERC20: transfer to the zero address", "ERC20: approve from the zero address",
    "ERC20: decreased allowance below zero", "ERC20: burn amount exceeds balance",
    "ERC20: mint to the zero address", "SafeMath: addition overflow",
    "SafeMath: subtraction overflow", "SafeMath: multiplication overflow",
    "SafeMath: division by zero", "SafeMath: modulo by zero",
    "Address: insufficient balance for call", "Address: low-level call failed",
    "Address: call to non-contract", "SafeERC20: low-level call failed",
    "SafeERC20: ERC20 operation did not succeed", "ReentrancyGuard: reentrant call",
    "Initializable: contract is already initialized", "Pausable: paused",
    "Pausable: not paused", "AccessControl: account ",
    "ERC721: transfer caller is not owner nor approved", "ERC721: approve to caller",
    "EnumerableSet: index out of bounds", "Strings: hex length insufficient",
    "SafeCast: value doesn't fit in ", "Counters: decrement overflow",
]
# 이름 기반(약한) 신호 — 제외 근거로 쓰지 않고 태그로만 기록한다.
CANONICAL_DECLS = {"Context", "Ownable", "IERC20", "SafeMath", "Address", "ERC20",
                   "ReentrancyGuard", "SafeERC20", "IERC20Metadata", "Pausable",
                   "AccessControl", "ERC20Burnable", "EnumerableSet", "Strings",
                   "IUniswapV2Router02", "IUniswapV2Factory", "IUniswapV2Pair", "SafeCast",
                   "Initializable", "OwnableUpgradeable", "IERC721", "ERC721"}


def path_tags(rel: str, snap: str) -> set:
    inside = rel[len(snap) + 1:] if snap and rel.startswith(snap + "/") else rel
    raw_parts = inside.split("/")
    parts = [p.lower() for p in raw_parts]
    dirs, name = parts[:-1], parts[-1]
    orig_name = raw_parts[-1]
    tags = set()
    if "node_modules" in dirs or any(d.startswith("@") for d in dirs) \
            or any(d in EXT_PKGS for d in dirs) or (dirs and dirs[0] in DEP_ROOT_DIRS):
        tags.add("external_dependency")
    elif any(d in ("lib", "libs") for d in dirs):
        tags.add("project_lib_dir")
    if any(d in TEST_DIRS for d in dirs) or name.endswith(".t.sol") or TEST_NAME.match(orig_name):
        tags.add("test_or_mock")
    if any(d in VERIF_DIRS for d in dirs):
        tags.add("verification")
    if MIG_NAME.match(name) or "migrations" in dirs:
        tags.add("migration")
    if FLAT_NAME.search(name) or any(FLAT_NAME.search(d) or d in ("flat", "flatten", "flattened")
                                     for d in dirs):
        tags.add("merged_source")       # 경로·파일명 근거
    return tags


def merge_signals(raw: str, bare: str, mask: str, imports: list):
    """(근거 있는 병합 소스인가, 약한 의심인가, 근거 라벨들).

    ★ 4차 검토 ①-1: v4 는 **계약 이름**이 표준 라이브러리와 겹치면 병합으로 봤는데, 이름만 같고
      구현이 다른 정상 소스도 걸린다(리뷰어가 반례 재현). 그래서 근거를 **본문 지문**으로 바꿨다 —
      OpenZeppelin 고유 revert 문구가 2개 이상 들어 있으면 그 라이브러리 **본문이 복사돼 있다**는
      뜻이다. 실측: v4 가 병합으로 제외한 123개 중 지문 ≥2 가 112개(91%), 마커 18, 경로명 20,
      **근거 전무 7개** → 그 7개는 이제 제외하지 않고 `canonical_names` 태그만 붙는다.
    """
    decls = TOPDECL.findall(mask)
    canon = sorted(set(decls) & CANONICAL_DECLS)
    # ★ 5차 검토 ①-1: 지문을 **원문**에서 찾으면 주석에 그 문구만 적힌 파일도 병합으로 걸린다.
    #   지문은 `require(..., "Ownable: caller is not the owner")` 처럼 **코드의 문자열 리터럴**이므로
    #   주석만 제거하고 문자열은 보존한 `bare` 에서 찾는다(mask 는 문자열까지 지워 쓸 수 없다).
    fp = [s for s in OZ_FINGERPRINTS if s in bare]
    why = []
    if len(FILE_MARK.findall(raw)) >= 2:   # `// File:` 은 주석이라 원문에서 찾는 것이 맞다
        why.append("file_marker")
    if not imports and len(decls) >= 3 and len(fp) >= 2:
        why.append("vendored_library_body:" + ",".join(fp[:3]))
    weak_tags = []
    if not imports and len(decls) >= 3:
        weak_tags.append("multiple_declarations_no_imports")
        if len(canon) >= 2:
            weak_tags.append("canonical_names:" + ",".join(canon[:4]))
    return bool(why), weak_tags, why


# ─────────────────────────── 파일시스템 ───────────────────────────
def lp(p: str) -> str:
    r"""Windows MAX_PATH 회피: `\\?\` 확장 경로."""
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


SNAPSHOT_MARKERS = {"package.json", "foundry.toml", "hardhat.config.js", "hardhat.config.ts",
                    "truffle-config.js", "truffle.js", "remappings.txt", "brownie-config.yaml",
                    "yarn.lock", "package-lock.json"}
SHA_DIR = re.compile(r"[-_][0-9a-f]{7,40}$")


def _walk_error(exc):
    raise RuntimeError(f"디렉터리 탐색 실패: {getattr(exc, 'filename', exc)}") from exc


def collect(root: str):
    """(.sol 상대경로, 마커 디렉터리→마커 종류, 디렉터리→하위디렉터리)."""
    sols, markers = [], {}
    children = defaultdict(set)
    for dirpath, dirs, names in os.walk(lp(root), onerror=_walk_error, followlinks=False):
        dirs.sort()
        names.sort()
        rel_dir = os.path.relpath(dirpath, lp(root)).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for d in dirs:
            children[rel_dir].add(d)
        for nm in names:
            if nm.endswith(".sol"):
                sols.append(f"{rel_dir}/{nm}" if rel_dir else nm)
        hit = sorted(SNAPSHOT_MARKERS & set(names)) + ([".git"] if ".git" in dirs else [])
        if hit:
            markers[rel_dir] = hit
    return sols, markers, children


def snapshot_boundary(rel: str, markers: dict):
    parts = rel.split("/")[:-1]
    for i in range(2, len(parts) + 1):
        cand = "/".join(parts[:i])
        if cand in markers:
            return cand, "marker"
    if len(parts) >= 2:
        return "/".join(parts[:2]), "depth2_fallback"
    return (parts[0] if parts else ""), "depth1_fallback"


def repo_root_guess(boundary: str, children: dict):
    base = boundary.split("/")[-1]
    if SHA_DIR.search(base):
        return boundary, "boundary_is_sha_named", "medium", False
    kids = sorted(children.get(boundary, ()))
    sha_kids = [k for k in kids if SHA_DIR.search(k)]
    if len(sha_kids) == 1:
        return f"{boundary}/{sha_kids[0]}", "sha_named_child", "medium", False
    if len(sha_kids) > 1:
        return boundary, "multiple_sha_named_children", "low", True
    return boundary, "boundary", "low", False


_REMAP_LINE = re.compile(r"^\s*([^\s=]+)\s*=\s*(\S+)\s*$")
_FOUNDRY_REMAP = re.compile(r"remappings\s*=\s*\[(.*?)\]", re.S)


def load_remappings(root: str, snap: str) -> list:
    out = []
    for nm in ("remappings.txt", "foundry.toml"):
        try:
            txt = io.open(lp(os.path.join(root, snap, nm)), encoding="utf-8",
                          errors="replace").read()
        except OSError:
            continue
        if nm == "remappings.txt":
            for line in txt.splitlines():
                m = _REMAP_LINE.match(line)
                if m:
                    out.append((m.group(1), m.group(2)))
        else:
            for blk in _FOUNDRY_REMAP.findall(txt):
                for item in re.findall(r"[\"']([^\"']+)[\"']", blk):
                    if "=" in item:
                        a, b = item.split("=", 1)
                        out.append((a.strip(), b.strip()))
    return out


# ─────────────────────── Audit_and_Repository_link.xlsx ───────────────────────
def read_xlsx_rows(path: str):
    try:
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                for si in re.findall(r"<si>(.*?)</si>", sx, re.S):
                    shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
            sheet = next((n for n in z.namelist()
                          if re.match(r"xl/worksheets/sheet1\.xml$", n)), None)
            if sheet is None:
                return None
            sx = z.read(sheet).decode("utf-8", "replace")
            rows = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", row, re.S):
                    tm = re.search(r't="(\w+)"', attrs)
                    v = re.search(r"<v>(.*?)</v>", body, re.S)
                    if tm and tm.group(1) == "s" and v:
                        i = int(v.group(1))
                        cells.append(shared[i] if i < len(shared) else "")
                    elif v:
                        cells.append(v.group(1))
                    else:
                        t = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                        cells.append(t.group(1) if t else "")
                rows.append(cells)
            return rows
    except Exception:
        return None


# ─────────────────────── 제외 manifest (범용 프레임워크) ───────────────────────
EXCLUDED_SNAPSHOTS = {
    "Chainsulting-Global Rockstar/openzeppelin-contracts-upgradeable-master":
        "OpenZeppelin Contracts (Upgradeable) 라이브러리 저장소 자체 — 범용 재사용 프레임워크",
    "Chainsulting-SWAPP Protocol-project2/openzeppelin-contracts-4.2.0":
        "OpenZeppelin Contracts 4.2.0 라이브러리 저장소 자체 — 범용 재사용 프레임워크",
}


def unresolved_kind(spec: str) -> str:
    if spec.startswith(("./", "../")):
        return "missing_relative"
    if spec.startswith("@"):
        return "scoped_package"
    if spec.startswith(("http://", "https://")):
        return "remote_import"
    return "direct_or_package_path" if "/" in spec else "bare_source_unit"


# ─────────────────────────── 본체 ───────────────────────────
def main():
    root, out = sys.argv[1], sys.argv[2]
    tracked = None
    if "--tracked" in sys.argv:
        data = io.open(sys.argv[sys.argv.index("--tracked") + 1], encoding="utf-8",
                       errors="replace").read()
        tracked = {p.split("DAppSCAN-source/contracts/", 1)[-1]
                   for p in data.split("\x00") if p.strip().endswith(".sol")}
    xlsx = sys.argv[sys.argv.index("--xlsx") + 1] if "--xlsx" in sys.argv else None

    sols, markers, children = collect(root)
    print("수집한 .sol:", len(sols))
    if tracked is not None:
        miss, extra = tracked - set(sols), set(sols) - tracked
        print(f"git 추적 대조: 추적 {len(tracked)} | 누락 {len(miss)} | 추가 {len(extra)}")
        if miss or extra:
            raise SystemExit("수집 목록이 git 추적 목록과 불일치")

    files, by_snap, idx = {}, defaultdict(list), defaultdict(list)
    basis_cnt, tag_cnt, anomaly, merge_why = Counter(), Counter(), Counter(), Counter()
    for rel in sols:
        try:
            raw = io.open(lp(os.path.join(root, rel)), encoding="utf-8", errors="strict").read()
            enc_err = False
        except UnicodeDecodeError:
            raw = io.open(lp(os.path.join(root, rel)), encoding="utf-8", errors="replace").read()
            enc_err = True
            anomaly["encoding_error"] += 1
        bare, mask = lex(raw)
        snap, basis = snapshot_boundary(rel, markers)
        basis_cnt[basis] += 1
        specs = PRAGMA.findall(mask)
        bad = [s for s in specs if not spec_syntax_ok(s)]
        quoted = [s for s in PRAGMA.findall(bare) if '"' in s or "'" in s]
        if bad:
            anomaly["malformed_pragma"] += 1
        if quoted:
            anomaly["quoted_pragma"] += 1
        imports = imports_of(bare, mask)
        tags = path_tags(rel, snap)
        merged, weak_why, why = merge_signals(raw, bare, mask, imports)
        if merged:
            tags.add("merged_source")
            for w in why:
                merge_why[w.split(":")[0]] += 1
        for w in weak_why:
            tags.add("suspected_flattened" if w.startswith("multiple_declarations")
                     else "canonical_names")
        for t in tags:
            tag_cnt[t] += 1
        mask_noasm = strip_assembly(mask)
        spec_fns = special_fns(mask_noasm)
        rh, nh = content_hashes(raw)
        files[rel] = {
            "snap": snap, "basis": basis, "tags": tags, "imports": imports,
            "allowed": None if (bad or quoted) else file_allowed(specs),
            "pragma_bad": bool(bad or quoted), "specs": specs,
            "funcs": func_count(mask_noasm), "special": spec_fns,
            "raw_hash": rh, "normalized_hash": nh, "enc_err": enc_err,
            "merge_why": why, "weak_why": weak_why,
        }
        by_snap[snap].append(rel)
        parts = rel.split("/")
        for i in range(len(parts)):
            idx["/".join(parts[i:])].append(rel)
    print("경계 판정:", dict(basis_cnt), "| 스냅샷:", len(by_snap))
    print("태그 분포:", dict(tag_cnt), "| 병합 근거:", dict(merge_why))
    print("이상치:", dict(anomaly))

    repo_meta = {}
    rr_cnt = Counter()
    for snap in by_snap:
        rr, method, conf, review = repo_root_guess(snap, children)
        repo_meta[snap] = {"repository_root": rr, "boundary_method": method,
                           "confidence": conf, "needs_review": review,
                           "markers": markers.get(snap, [])}
        rr_cnt[f"{method}({conf})"] += 1
    print("저장소 루트 추정:", dict(rr_cnt))
    print("마커 종류:", dict(Counter(m for v in markers.values() for m in v)))

    xlsx_missing = []
    if xlsx:
        rows = read_xlsx_rows(xlsx)
        if rows is None:
            print("xlsx 파싱 실패")
        else:
            listed = {str(r[0]).strip() for r in rows[1:] if r and str(r[0]).strip()}
            audits = {s.split("/")[0] for s in by_snap}
            xlsx_missing = sorted(audits - listed)
            print(f"xlsx 행 {len(rows) - 1} | 등재 {len(listed & audits)}/{len(audits)}"
                  f" | 미등재 {len(xlsx_missing)}")

    # ── import 해석 ────────────────────────────────────────────────
    remaps = {snap: load_remappings(root, snap) for snap in by_snap}
    how, unres_kind = Counter(), Counter()
    resolution = defaultdict(list)

    def resolve(src, spec):
        snap = files[src]["snap"]
        base = os.path.dirname(src)
        if spec.startswith("."):
            t = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
            if t in files:
                if files[t]["snap"] == snap:
                    how["relative"] += 1
                    return t, "relative", "exact"
                how["relative_escaped_boundary"] += 1
            else:
                how["relative_missing"] += 1
            return None, None, None
        for prefix, target in remaps.get(snap, ()):
            if spec.startswith(prefix):
                t = os.path.normpath(os.path.join(snap, target + spec[len(prefix):])).replace("\\", "/")
                if t in files and files[t]["snap"] == snap:
                    how["remapping"] += 1
                    return t, "remapping", "configured"
        t = os.path.normpath(os.path.join(snap, spec.lstrip("/"))).replace("\\", "/")
        if t in files and files[t]["snap"] == snap:
            how["snapshot_relative"] += 1
            return t, "snapshot_relative", "assumed_source_root"
        t = os.path.normpath(os.path.join(base, spec.lstrip("/"))).replace("\\", "/")
        if t in files and files[t]["snap"] == snap:
            how["same_dir"] += 1
            return t, "same_dir", "assumed_same_dir"
        # 3차 검토 ②-2: unique_suffix fallback 제거 (전 코퍼스 3건, solc 의미론과 불일치)
        how["unresolved"] += 1
        return None, None, None

    edges, miss, miss_specs = {}, Counter(), defaultdict(list)
    for rel, rec in files.items():
        o = []
        for spec in rec["imports"]:
            r, method, conf = resolve(rel, spec)
            if r:
                o.append(r)
                resolution[rel].append({"import": spec, "resolved": r, "method": method,
                                        "confidence": conf})
            else:
                miss[rel] += 1
                miss_specs[rel].append(spec)
                unres_kind[unresolved_kind(spec)] += 1
                resolution[rel].append({"import": spec, "resolved": None,
                                        "method": "unresolved",
                                        "confidence": unresolved_kind(spec)})
        edges[rel] = o
    print("import 해석:", dict(how))
    print("미해결 분류:", dict(unres_kind), "| 총", sum(unres_kind.values()))

    cache = {}

    def closure(rel):
        if rel in cache:
            return cache[rel]
        seen, m, specs = {rel}, miss[rel], list(miss_specs.get(rel, ()))
        work = list(edges.get(rel, ()))
        while work:
            cur = work.pop()
            if cur in seen:
                continue
            seen.add(cur)
            m += miss.get(cur, 0)
            specs.extend(miss_specs.get(cur, ()))
            work.extend(edges.get(cur, ()))
        cache[rel] = (seen, m, specs)
        return cache[rel]

    EXCLUDE_TAGS = {"external_dependency", "test_or_mock", "verification", "migration",
                    "merged_source"}
    stage, rej, rej_tag = {}, Counter(), Counter()
    cand = []
    for rel, rec in files.items():
        if rec["pragma_bad"]:
            stage[rel] = "pragma_syntax_anomaly"
        elif rec["allowed"] is None:
            stage[rel] = "no_real_pragma"
        elif not rec["allowed"]:
            stage[rel] = "contradictory_pragma"
        elif min(rec["allowed"]) < (0, 8, 0):
            stage[rel] = "pragma_allows_below_0.8"
        elif rec["tags"] & EXCLUDE_TAGS:
            stage[rel] = "tag:" + ",".join(sorted(rec["tags"] & EXCLUDE_TAGS))
            for t in rec["tags"] & EXCLUDE_TAGS:
                rej_tag[t] += 1
        elif rec["funcs"] == 0:
            # ★ 타깃 단위 = named·implemented 함수. 생성자/receive/fallback 만 있는 파일은
            #   "제외" 가 아니라 **제공할 타깃이 없는** 파일이다(폐포 구성원으로는 계속 쓰인다).
            sp = rec["special"]
            only_special = any(sp[k + "_nonempty"] for k in ("constructor", "receive", "fallback"))
            stage[rel] = ("no_eligible_target_function:special_only" if only_special
                          else "no_eligible_target_function")
        else:
            cand.append(rel)
            continue
        rej["tag" if stage[rel].startswith("tag:")
            else "no_eligible_target_function" if stage[rel].startswith("no_eligible")
            else stage[rel]] += 1

    special_only = sum(1 for s in stage.values()
                       if s == "no_eligible_target_function:special_only")

    clean, closure_rej, affected = [], Counter(), Counter()
    for rel in cand:
        seen, m, specs = closure(rel)
        if m > 0:
            stage[rel] = "closure_unresolved_import"
            closure_rej["unresolved_import"] += 1
            for s in set(specs):
                affected[s] += 1
            continue
        sets = [files[x]["allowed"] for x in seen]
        if any(files[x]["pragma_bad"] or s is None for x, s in zip(seen, sets)):
            stage[rel] = "closure_pragma_missing_or_bad"
            closure_rej["pragma_missing_or_bad"] += 1
            continue
        if any((not s) or min(s) < (0, 8, 0) for s in sets):
            stage[rel] = "closure_below_0.8"
            closure_rej["below_0.8"] += 1
            continue
        common = set.intersection(*(set(s) for s in sets))
        if not common:
            stage[rel] = "closure_no_common_compiler"
            closure_rej["no_common_compiler"] += 1
            continue
        if min(common) < (0, 8, 0):
            stage[rel] = "closure_common_below_0.8"
            closure_rej["common_below_0.8"] += 1
            continue
        stage[rel] = "selected"
        clean.append(rel)

    prime = [r for r in clean if files[r]["snap"] not in EXCLUDED_SNAPSHOTS]
    no_suspect = [r for r in prime if "suspected_flattened" not in files[r]["tags"]]

    def stats(sel, label):
        per = Counter(files[r]["snap"] for r in sel)
        fper = Counter()
        for r in sel:
            fper[files[r]["snap"]] += files[r]["funcs"]
        groups = defaultdict(list)
        for r in sel:
            groups[files[r]["normalized_hash"]].append(r)
        dup = {h: v for h, v in groups.items() if len(v) > 1}
        sizes = Counter(len(closure(r)[0]) for r in sel)
        tot, ftot = sum(per.values()) or 1, sum(fper.values()) or 1
        return {
            "label": label,
            "files": len(sel),
            "functions_approx": sum(files[r]["funcs"] for r in sel),
            "snapshots": len(per),
            "audit_projects": len({r.split("/")[0] for r in sel}),
            "duplicate_groups": len(dup),
            "files_in_duplicate_groups": sum(len(v) for v in dup.values()),
            "closure_size": {"1": sizes.get(1, 0),
                             "2-4": sum(v for k, v in sizes.items() if 2 <= k <= 4),
                             "5+": sum(v for k, v in sizes.items() if k >= 5)},
            "top10_file_share_pct": round(100 * sum(n for _, n in per.most_common(10)) / tot, 1),
            "top10_function_share_pct": round(100 * sum(n for _, n in fper.most_common(10)) / ftot, 1),
            "top_snapshots_by_files": per.most_common(10),
            "suspected_flattened_files": sum(1 for r in sel
                                             if "suspected_flattened" in files[r]["tags"]),
            "suspected_flattened_functions": sum(files[r]["funcs"] for r in sel
                                                 if "suspected_flattened" in files[r]["tags"]),
            "project_lib_dir_files": sum(1 for r in sel if "project_lib_dir" in files[r]["tags"]),
            "canonical_names_files": sum(1 for r in sel if "canonical_names" in files[r]["tags"]),
            # ★ 경계 추정 신뢰도와 **provenance(xlsx 등재 여부)는 별개 개념**이라 합치지 않는다
            #   (4차 검토 ②-3·②-4). 주 집합에서 빼지 않고 민감도 분석용으로만 보고한다.
            "low_boundary_confidence": {
                "files": sum(1 for r in sel
                             if repo_meta[files[r]["snap"]]["confidence"] == "low"),
                "functions": sum(files[r]["funcs"] for r in sel
                                 if repo_meta[files[r]["snap"]]["confidence"] == "low"),
                "snapshots": len({files[r]["snap"] for r in sel
                                  if repo_meta[files[r]["snap"]]["confidence"] == "low"}),
            },
            "unlisted_provenance": {
                "files": sum(1 for r in sel if r.split("/")[0] in set(xlsx_missing)),
                "functions": sum(files[r]["funcs"] for r in sel
                                 if r.split("/")[0] in set(xlsx_missing)),
                "audit_dirs": len({r.split("/")[0] for r in sel
                                   if r.split("/")[0] in set(xlsx_missing)}),
            },
            "special_fns": {
                k: sum(files[r]["special"][k] for r in sel)
                for k in ("constructor", "constructor_nonempty", "receive",
                          "receive_nonempty", "fallback", "fallback_nonempty")
            },
        }

    summary = {
        "solc_releases_asof": SOLC_RELEASES_ASOF,
        "collected": len(files),
        "boundary_basis": dict(basis_cnt),
        "repo_root_methods": dict(rr_cnt),
        "marker_kinds": dict(Counter(m for v in markers.values() for m in v)),
        "xlsx_unlisted_audit_dirs": xlsx_missing,
        "tag_counts_all_files": dict(tag_cnt),
        "merge_evidence_counts": dict(merge_why),
        "anomalies": dict(anomaly),
        "import_resolution": dict(how),
        "unresolved_kinds": dict(unres_kind),
        "rejected_before_closure": dict(rej),
        "rejected_by_tag": dict(rej_tag),
        # 타깃을 제공하지 않는 파일 중 "본문 있는 constructor/receive/fallback 만 가진" 것.
        # 데이터셋에서 빼는 게 아니라 **타깃 단위(named 함수)가 없을 뿐**이다.
        "no_target_but_has_special_fns": special_only,
        "candidates_before_closure": len(cand),
        "closure_rejected": dict(closure_rej),
        "option2_all": stats(clean, "option2 (전체)"),
        "option2prime": stats(prime, "option2′ (프레임워크 제외)"),
        "option2prime_minus_suspected": stats(no_suspect, "option2′ − suspected_flattened"),
        "excluded_by_manifest": {s: sum(1 for r in clean if files[r]["snap"] == s)
                                 for s in EXCLUDED_SNAPSHOTS},
        # ★ 이름 주의: 각 import 가 **영향을 준 후보 수**. 한 후보가 여러 미해결 import 로 탈락하면
        #   중복 집계되므로, 이 값을 "복원하면 회복되는 후보 수" 로 읽으면 안 된다(3차 검토 ②-3).
        "affected_candidate_count_by_unresolved_import": affected.most_common(15),
    }
    print("\n" + json.dumps({k: v for k, v in summary.items()
                             if not k.startswith("option2")}, indent=2, ensure_ascii=False))
    for k in ("option2_all", "option2prime", "option2prime_minus_suspected"):
        print(f"\n{k}:", json.dumps(summary[k], indent=2, ensure_ascii=False))

    json.dump({
        "summary": summary,
        "selected_option2": sorted(clean),
        "selected_option2prime": sorted(prime),
        "selected_option2prime_minus_suspected": sorted(no_suspect),
        "excluded_snapshots": EXCLUDED_SNAPSHOTS,
        "per_file_stage": stage,
        # ★ 4차 검토 ②-1: 왜 그렇게 분류됐는지 JSON 만으로 추적되게 한다.
        #   (근거를 가진 파일만 싣는다 — 전수 21,457개를 다 실으면 파일이 지나치게 커진다.)
        "per_file_evidence": {rel: {"tags": sorted(rec["tags"]),
                                    "merge_evidence": rec["merge_why"],
                                    "weak_evidence": rec["weak_why"],
                                    "special_fns": rec["special"],
                                    "stage": stage[rel]}
                              for rel, rec in files.items()
                              if rec["merge_why"] or rec["weak_why"]
                              or stage[rel].startswith(("tag:", "no_eligible", "pragma_syntax"))},
        "selected_meta": {r: {"snap": files[r]["snap"],
                              "repository_root": repo_meta[files[r]["snap"]]["repository_root"],
                              "boundary_method": repo_meta[files[r]["snap"]]["boundary_method"],
                              "boundary_confidence": repo_meta[files[r]["snap"]]["confidence"],
                              "funcs": files[r]["funcs"], "closure": len(closure(r)[0]),
                              "specs": files[r]["specs"], "raw_hash": files[r]["raw_hash"],
                              "normalized_hash": files[r]["normalized_hash"],
                              "tags": sorted(files[r]["tags"]),
                              "imports": resolution.get(r, [])} for r in sorted(clean)},
    }, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
