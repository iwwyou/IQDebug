"""DAppSCAN 타깃 함수 후보 선정 (v3, 2026-10-28).

이력
  v1 `scan_projects.py` — 1차 외부 검토 대상. 보존용으로 남겨 두고 수정하지 않는다.
  v2 (이 파일의 이전 판) — 1차 검토 반영.
  v3 (현재)              — 2차 검토 반영 + 각 지적의 **실측 발생 규모** 확인 후 수정.

2차 검토 지적과 실측 결과(전수 21,457 파일, `audit_v2.py` 로 측정)
  (a) import 정규식의 `^` 앵커 → 한 줄에 여러 문장이면 import 누락 ····· **실측 0건**
  (b) 문자열 리터럴 속 `pragma` / `function` 오인 ························ **실측 0건**
  (c) semver 파서 fail-open(`^0.8.2 foo`) ································ **실측 0건**
  (d) 폐포 전체의 공통 컴파일러 버전 미검사 ····························· **실측 0건**
  (e) flattened 판정이 주석 제거본에서 `// File:` 을 찾아 **항상 0** ····· **실측: 후보 100개 과잉 배제**
  (f) `lib`/`libs` 를 외부 의존성으로 간주 ································ **실측 79개(그중 78개가 contracts//src/ 하위)**
  (g) 스냅샷 경계가 실제 저장소 루트와 다른 경우 ························· 확인(예: PeckShield-AaveV3/code)
  (a)~(d) 는 발생 0건이지만 **전부 닫았다**. 비용이 없고, "빈틈이 있었지만 우연히 안 걸렸다" 보다
  "닫았고 영향 0건이었다" 가 방어 가능한 서술이다. (e)(f) 만 숫자를 바꾼다.

v3 로직 요약
  1. 탐색: 확장 경로(`\\?\`) + `onerror` 즉시 실패 + 이름 정렬 + git 추적 목록 대조.
  2. 어휘: `bare`(주석만 공백화, 문자열 유지) / `mask`(주석 + **문자열 내용**까지 공백화). 길이 보존.
     pragma·함수는 `mask` 에서, import 키워드는 `mask` 에서 찾고 경로는 `bare` 에서 읽는다(앵커 없음).
  3. pragma: semver 제약 → 허용 버전 집합. 파일 내 모든 pragma 의 **교집합**.
     구문 이상(인식 못한 토큰 잔류, 따옴표 섞인 버전 `pragma solidity "0.8.13";`)은 **거부**(fail-closed).
  4. 경계: 격리 경계는 `<감사>/<스냅샷>` 2단(마커가 있으면 그 조상). 추정 저장소 루트·판정 근거·
     검토 필요 여부를 **별도 필드**로 기록하고, `Audit_and_Repository_link.xlsx` 와 대조 통계를 낸다.
  5. import 해석: 상대경로 → remappings → 스냅샷 루트 기준 → 같은 디렉터리 → **유일** 접미사.
     전부 같은 스냅샷 안이어야 하고, 모호하면 거부. basename fallback 없음.
  6. 폐포: 미해결 0 + 모든 파일이 pragma 보유 + 전부 ≥0.8 + **공통 컴파일러 버전 존재**.
  7. 태그: external_dependency(node_modules·`@scope`·알려진 패키지·**스냅샷 루트 직하**의 lib/vendor/deps),
     test_or_mock(단어 경계), verification, migration, flattened(**원문** `// File:` 마커 또는 flatten 경로명),
     suspected_flattened(import 0 + 최상위 선언 3개↑ — **제외하지 않고 태그만**), project_lib_dir(참고용).
  8. 제외 manifest: 범용 재사용 프레임워크 저장소(OpenZeppelin 2개)를 스냅샷 단위로 명시.
     옵션 2(전체)와 옵션 2′(manifest 제외) 숫자를 한 번에 낸다.
  9. 산출물: 모든 파일의 **최초 탈락 단계와 사유**, 미해결 import 분류와 영향 후보 수.

사용:
  python -I select_targets.py <contracts_root> <out_json> [--tracked <git-ls-files -z 파일>]
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

# ─────────────────────────── 어휘 분석 ───────────────────────────
_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def _blank(t):
    return re.sub(r"[^\n]", " ", t)


def lex(raw: str):
    """(bare, mask). 둘 다 원문과 길이·개행 위치가 같다.
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


PRAGMA = re.compile(r"pragma\s+solidity\s+([^;]*);", re.I)
IMPORT_KW = re.compile(r"\bimport\b")
IMPORT_PATH = re.compile(r"""import\s+(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""")
FUNC = re.compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\(")
TOPDECL = re.compile(r"(?m)^\s*(?:abstract\s+)?(contract|library|interface)\s+\w+")
FILE_MARK = re.compile(r"(?m)^\s*//\s*File:")

# ─────────────────────────── pragma = semver 제약 ───────────────────────────
SOLC_VERSIONS = ([(0, 4, i) for i in range(0, 27)] + [(0, 5, i) for i in range(0, 18)]
                 + [(0, 6, i) for i in range(0, 13)] + [(0, 7, i) for i in range(0, 7)]
                 + [(0, 8, i) for i in range(0, 41)])   # 0.8.40 까지 여유 (데이터셋 최대는 0.8.30 미만)
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


def _allowed_by_disjunct(part: str):
    cmps = [(m.group(1), int(m.group(2)), int(m.group(3)),
             None if m.group(4) is None else int(m.group(4)))
            for m in _CMP.finditer(part)]
    if not cmps:
        return set()
    return {v for v in SOLC_VERSIONS if all(_sat(v, *c) for c in cmps)}


_spec_cache = {}


def allowed_versions(spec: str):
    spec = spec.strip()
    if spec not in _spec_cache:
        out = set()
        for part in spec.split("||"):
            out |= _allowed_by_disjunct(part)
        _spec_cache[spec] = out
    return _spec_cache[spec]


def spec_syntax_ok(spec: str) -> bool:
    """fail-closed: 비교자·버전으로 설명되지 않는 토큰이 남으면 거부. (2차 검토 (c))"""
    if not spec.strip():
        return False
    for part in spec.split("||"):
        if re.search(r"\S", _CMP.sub("", part)):
            return False
    return True


def file_allowed(specs):
    """파일 내 모든 pragma 의 허용 집합 교집합. pragma 가 없으면 None."""
    if not specs:
        return None
    acc = None
    for s in specs:
        a = allowed_versions(s)
        acc = a if acc is None else (acc & a)
    return acc


# ─────────────────────────── import / 함수 ───────────────────────────
def imports_of(bare: str, mask: str) -> list:
    """`import` 키워드는 문자열·주석 밖(mask)에서 찾고, 경로는 원문(bare)에서 읽는다.
    줄 앵커가 없으므로 `pragma ...; import "./X.sol";` 같은 한 줄 배치도 잡는다. (2차 검토 (a))"""
    out = []
    for m in IMPORT_KW.finditer(mask):
        mm = IMPORT_PATH.match(bare, m.start())
        if mm:
            out.append(mm.group(1))
    return out


def func_count(mask: str) -> int:
    n = 0
    for m in FUNC.finditer(mask):
        tail = mask[m.end():m.end() + 400]
        b, s = tail.find("{"), tail.find(";")
        if b != -1 and (s == -1 or b < s):
            n += 1
    return n


# ─────────────────────────── 태그 ───────────────────────────
EXT_PKGS = {"openzeppelin", "openzeppelin-contracts", "openzeppelin-contracts-upgradeable",
            "uniswap", "uniswap-v2-core", "uniswap-v3-core", "chainlink", "aave",
            "solmate", "forge-std", "ds-test", "hardhat", "truffle", "prb-math"}
# 스냅샷 루트 **직하**에 있을 때만 의존성 설치 디렉터리로 본다 (2차 검토 (f))
DEP_ROOT_DIRS = {"node_modules", "lib", "libs", "vendor", "deps", "dependencies",
                 "submodules", "third_party", "third-party", "external"}
TEST_DIRS = {"test", "tests", "testing", "mock", "mocks", "test_helpers", "test-utilities",
             "testutils", "test-utils"}
VERIF_DIRS = {"certora", "spec", "specs", "echidna", "scribble", "halmos"}
# 대소문자를 구분한다 — `re.I` 를 주면 [A-Z] 가 소문자도 받아 `Testament.sol` 까지 잡힌다.
#   잡을 것: Test.sol, TestVault.sol, Test_Vault.sol, MockToken.sol, mock_token.sol
#   놓아줄 것: Testament.sol, Mocker.sol
TEST_NAME = re.compile(r"^(?:Test|Mock)(?=[A-Z_0-9]|\.sol$)|^(?:test|mock)(?=[_\-0-9]|\.sol$)")
MIG_NAME = re.compile(r"^migrations?\.sol$|^migration", re.I)
FLAT_NAME = re.compile(r"(?:^|[_\-.])flat(?:ten(?:ed)?)?(?:$|[_\-.])", re.I)


def path_tags(rel: str, snap: str) -> set:
    inside = rel[len(snap) + 1:] if snap and rel.startswith(snap + "/") else rel
    raw_parts = inside.split("/")
    parts = [p.lower() for p in raw_parts]
    dirs, name = parts[:-1], parts[-1]
    orig_name = raw_parts[-1]          # 대소문자 보존 — TEST_NAME 판정에 필요
    tags = set()
    if "node_modules" in dirs or any(d.startswith("@") for d in dirs) \
            or any(d in EXT_PKGS for d in dirs) \
            or (dirs and dirs[0] in DEP_ROOT_DIRS):
        tags.add("external_dependency")
    elif any(d in ("lib", "libs") for d in dirs):
        tags.add("project_lib_dir")          # 프로젝트 자작 라이브러리 — 제외하지 않는다
    if any(d in TEST_DIRS for d in dirs) or name.endswith(".t.sol") or TEST_NAME.match(orig_name):
        tags.add("test_or_mock")
    if any(d in VERIF_DIRS for d in dirs):
        tags.add("verification")
    if MIG_NAME.match(name) or "migrations" in dirs:
        tags.add("migration")
    if FLAT_NAME.search(name) or any(FLAT_NAME.search(d) or d in ("flat", "flatten", "flattened")
                                     for d in dirs):
        tags.add("flattened")
    return tags


# ─────────────────────────── 파일시스템 ───────────────────────────
def lp(p: str) -> str:
    r"""Windows MAX_PATH 회피: `\\?\` 확장 경로."""
    ap = os.path.abspath(p).replace("/", os.sep)
    pre = "\\\\?\\"
    return pre + ap if os.name == "nt" and not ap.startswith(pre) else ap


SNAPSHOT_MARKERS = {"package.json", "foundry.toml", "hardhat.config.js", "hardhat.config.ts",
                    "truffle-config.js", "truffle.js", "remappings.txt", "brownie-config.yaml",
                    ".git", "yarn.lock", "package-lock.json"}
SHA_DIR = re.compile(r"[-_][0-9a-f]{7,40}$")


def _walk_error(exc):
    raise RuntimeError(f"디렉터리 탐색 실패: {getattr(exc, 'filename', exc)}") from exc


def collect(root: str):
    """(.sol 상대경로, 마커 보유 디렉터리, 디렉터리→하위디렉터리 맵)."""
    sols, marker_dirs = [], set()
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
        if (SNAPSHOT_MARKERS & set(names)) or ".git" in dirs:
            marker_dirs.add(rel_dir)
    return sols, marker_dirs, children


def snapshot_boundary(rel: str, marker_dirs: set):
    """격리 경계. 마커를 가진 가장 얕은 조상(깊이≥2), 없으면 2단 폴백."""
    parts = rel.split("/")[:-1]
    for i in range(2, len(parts) + 1):
        cand = "/".join(parts[:i])
        if cand in marker_dirs:
            return cand, "marker"
    if len(parts) >= 2:
        return "/".join(parts[:2]), "depth2_fallback"
    return (parts[0] if parts else ""), "depth1_fallback"


def repo_root_guess(boundary: str, children: dict):
    """경계 아래의 실제 저장소 루트 추정 (2차 검토 (g)). 격리 경계는 바꾸지 않는다."""
    base = boundary.split("/")[-1]
    if SHA_DIR.search(base):
        return boundary, "boundary_is_sha_named", False
    kids = sorted(children.get(boundary, ()))
    sha_kids = [k for k in kids if SHA_DIR.search(k)]
    if len(sha_kids) == 1:
        return f"{boundary}/{sha_kids[0]}", "sha_named_child", False
    if len(sha_kids) > 1:
        return boundary, "multiple_sha_named_children", True
    return boundary, "boundary", False


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
    """openpyxl 없이 xlsx(zip+XML)에서 첫 시트의 행을 읽는다. 실패하면 None."""
    try:
        with zipfile.ZipFile(path) as z:
            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                for si in re.findall(r"<si>(.*?)</si>", sx, re.S):
                    shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
            sheet = next((n for n in z.namelist() if re.match(r"xl/worksheets/sheet1\.xml$", n)),
                         None)
            if sheet is None:
                return None
            sx = z.read(sheet).decode("utf-8", "replace")
            rows = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sx, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", row, re.S):
                    tm = re.search(r't="(\w+)"', attrs)
                    typ = tm.group(1) if tm else ""
                    v = re.search(r"<v>(.*?)</v>", body, re.S)
                    if typ == "s" and v:
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
# 2차 검토 수용: "라이브러리"라는 선언 유형이 아니라 **범용 재사용 프레임워크 저장소**만 제외한다.
# Aave V3·Compound·Frax 등 실제 프로토콜 구현은 포함한다(수치 연산·내부 호출이 풍부해 RQ1 에 중요).
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
    if "/" in spec:
        return "direct_or_package_path"
    return "bare_source_unit"


# ─────────────────────────── 본체 ───────────────────────────
def main():
    root, out = sys.argv[1], sys.argv[2]
    tracked = None
    if "--tracked" in sys.argv:
        tf = sys.argv[sys.argv.index("--tracked") + 1]
        data = io.open(tf, encoding="utf-8", errors="replace").read()
        tracked = {p.split("DAppSCAN-source/contracts/", 1)[-1]
                   for p in data.split("\x00") if p.strip().endswith(".sol")}
    xlsx = sys.argv[sys.argv.index("--xlsx") + 1] if "--xlsx" in sys.argv else None

    sols, marker_dirs, children = collect(root)
    print("수집한 .sol:", len(sols))
    if tracked is not None:
        miss, extra = tracked - set(sols), set(sols) - tracked
        print(f"git 추적 대조: 추적 {len(tracked)} | 누락 {len(miss)} | 추가 {len(extra)}")
        if miss or extra:
            raise SystemExit("수집 목록이 git 추적 목록과 불일치")

    files, by_snap, idx = {}, defaultdict(list), defaultdict(list)
    basis_cnt, tag_cnt, anomaly = Counter(), Counter(), Counter()
    for rel in sols:
        try:
            raw = io.open(lp(os.path.join(root, rel)), encoding="utf-8", errors="strict").read()
            enc_err = False
        except UnicodeDecodeError:
            raw = io.open(lp(os.path.join(root, rel)), encoding="utf-8", errors="replace").read()
            enc_err = True
            anomaly["encoding_error"] += 1
        bare, mask = lex(raw)
        snap, basis = snapshot_boundary(rel, marker_dirs)
        basis_cnt[basis] += 1
        specs = PRAGMA.findall(mask)
        bad_syntax = [s for s in specs if not spec_syntax_ok(s)]
        quoted = [s for s in PRAGMA.findall(bare) if '"' in s or "'" in s]
        if bad_syntax:
            anomaly["malformed_pragma"] += 1
        if quoted:
            anomaly["quoted_pragma"] += 1
        tags = path_tags(rel, snap)
        imports = imports_of(bare, mask)
        if len(FILE_MARK.findall(raw)) >= 2:          # 원문에서 판정 (2차 검토 (e))
            tags.add("flattened")
        if not imports and len(TOPDECL.findall(mask)) >= 3:
            tags.add("suspected_flattened")           # 약한 신호 — 제외하지 않는다
        for t in tags:
            tag_cnt[t] += 1
        files[rel] = {
            "snap": snap, "basis": basis, "tags": tags, "imports": imports,
            "allowed": None if (bad_syntax or quoted) else file_allowed(specs),
            "pragma_bad": bool(bad_syntax or quoted),
            "specs": specs, "funcs": func_count(mask), "enc_err": enc_err,
            "hash": hashlib.sha256(re.sub(r"\s+", " ", mask).strip().encode("utf-8", "replace")).hexdigest(),
        }
        by_snap[snap].append(rel)
        parts = rel.split("/")
        for i in range(len(parts)):
            idx["/".join(parts[i:])].append(rel)
    print("경계 판정 근거:", dict(basis_cnt), "| 스냅샷:", len(by_snap))
    print("태그 분포:", dict(tag_cnt))
    print("이상치:", dict(anomaly))

    # 저장소 루트 추정 (보고용)
    repo_meta, rr_cnt = {}, Counter()
    for snap in by_snap:
        rr, method, review = repo_root_guess(snap, children)
        repo_meta[snap] = {"repository_root": rr, "boundary_method": method,
                           "needs_review": review}
        rr_cnt[method] += 1
    print("저장소 루트 추정:", dict(rr_cnt),
          "| 검토 필요:", sum(1 for v in repo_meta.values() if v["needs_review"]))

    # xlsx 대조 (보고용 — 필터로 쓰지 않는다)
    if xlsx:
        rows = read_xlsx_rows(xlsx)
        if rows is None:
            print("xlsx 파싱 실패 — 대조 생략")
        else:
            listed = {str(r[0]).strip() for r in rows[1:] if r and str(r[0]).strip()}
            audits = {s.split("/")[0] for s in by_snap}
            print(f"xlsx 행 {len(rows) - 1} | 목록에 있는 감사 디렉터리 {len(listed & audits)}"
                  f" / 우리 {len(audits)}")

    # ── import 해석 ────────────────────────────────────────────────
    remaps = {snap: load_remappings(root, snap) for snap in by_snap}
    how, unres_kind, unres_spec = Counter(), Counter(), Counter()

    def resolve(src, spec):
        snap = files[src]["snap"]
        base = os.path.dirname(src)
        if spec.startswith("."):
            t = os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
            if t in files:
                if files[t]["snap"] == snap:
                    how["relative"] += 1
                    return t
                how["relative_escaped_boundary"] += 1
            else:
                how["relative_missing"] += 1
            return None
        for prefix, target in remaps.get(snap, ()):
            if spec.startswith(prefix):
                t = os.path.normpath(os.path.join(snap, target + spec[len(prefix):])).replace("\\", "/")
                if t in files and files[t]["snap"] == snap:
                    how["remapping"] += 1
                    return t
        for cand in (os.path.join(snap, spec.lstrip("/")), os.path.join(base, spec.lstrip("/"))):
            t = os.path.normpath(cand).replace("\\", "/")
            if t in files and files[t]["snap"] == snap:
                how["snapshot_relative" if cand.startswith(snap) else "same_dir"] += 1
                return t
        hits = [h for h in idx.get(spec.lstrip("/"), ()) if files[h]["snap"] == snap]
        if len(hits) == 1:
            how["unique_suffix"] += 1
            return hits[0]
        if len(hits) > 1:
            how["ambiguous_rejected"] += 1
            return None
        how["unresolved"] += 1
        return None

    edges, miss, miss_specs = {}, Counter(), defaultdict(list)
    for rel, rec in files.items():
        o = []
        for spec in rec["imports"]:
            r = resolve(rel, spec)
            if r:
                o.append(r)
            else:
                miss[rel] += 1
                miss_specs[rel].append(spec)
                unres_kind[unresolved_kind(spec)] += 1
                unres_spec[spec] += 1
        edges[rel] = o
    print("import 해석:", dict(how))
    print("미해결 분류:", dict(unres_kind), "| 총 미해결", sum(unres_kind.values()))

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

    # ── 선정 funnel (모든 파일의 최초 탈락 단계를 기록) ─────────────
    EXCLUDE_TAGS = {"external_dependency", "test_or_mock", "verification", "migration",
                    "flattened"}
    stage, rej = {}, Counter()
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
        elif rec["funcs"] == 0:
            stage[rel] = "no_body_function"
        else:
            cand.append(rel)
            continue
        rej[stage[rel].split(":")[0] if stage[rel].startswith("tag:") else stage[rel]] += 1
    rej_tag = Counter()
    for rel, s in stage.items():
        if s.startswith("tag:"):
            for t in s[4:].split(","):
                rej_tag[t] += 1

    clean, closure_rej, blocking = [], Counter(), Counter()
    for rel in cand:
        seen, m, specs = closure(rel)
        if m > 0:
            stage[rel] = "closure_unresolved_import"
            closure_rej["unresolved_import"] += 1
            for s in set(specs):
                blocking[s] += 1
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

    excluded = [r for r in clean if files[r]["snap"] in EXCLUDED_SNAPSHOTS]
    clean_prime = [r for r in clean if files[r]["snap"] not in EXCLUDED_SNAPSHOTS]

    def stats(sel):
        per = Counter(files[r]["snap"] for r in sel)
        groups = defaultdict(list)
        for r in sel:
            groups[files[r]["hash"]].append(r)
        dup = {h: v for h, v in groups.items() if len(v) > 1}
        sizes = Counter(len(closure(r)[0]) for r in sel)
        tot = sum(per.values()) or 1
        top = per.most_common(10)
        return {
            "files": len(sel),
            "functions_approx": sum(files[r]["funcs"] for r in sel),
            "snapshots": len(per),
            "audit_projects": len({r.split("/")[0] for r in sel}),
            "duplicate_groups": len(dup),
            "files_in_duplicate_groups": sum(len(v) for v in dup.values()),
            "closure_size": {"1": sizes.get(1, 0),
                             "2-4": sum(v for k, v in sizes.items() if 2 <= k <= 4),
                             "5+": sum(v for k, v in sizes.items() if k >= 5)},
            "top10_share_pct": round(100 * sum(n for _, n in top) / tot, 1),
            "top1_share_pct": round(100 * top[0][1] / tot, 1) if top else 0,
            "top_snapshots": top,
            "suspected_flattened": sum(1 for r in sel if "suspected_flattened" in files[r]["tags"]),
            "project_lib_dir": sum(1 for r in sel if "project_lib_dir" in files[r]["tags"]),
        }

    summary = {
        "collected": len(files),
        "boundary_basis": dict(basis_cnt),
        "repo_root_methods": dict(rr_cnt),
        "tag_counts_all_files": dict(tag_cnt),
        "anomalies": dict(anomaly),
        "import_resolution": dict(how),
        "unresolved_kinds": dict(unres_kind),
        "rejected_before_closure": dict(rej),
        "rejected_by_tag": dict(rej_tag),
        "candidates_before_closure": len(cand),
        "closure_rejected": dict(closure_rej),
        "option2_all": stats(clean),
        "option2prime_excl_framework": stats(clean_prime),
        "excluded_by_manifest": {s: sum(1 for r in excluded if files[r]["snap"] == s)
                                 for s in EXCLUDED_SNAPSHOTS},
        "top_blocking_unresolved_imports": blocking.most_common(15),
    }
    print("\n" + json.dumps({k: v for k, v in summary.items()
                             if k not in ("option2_all", "option2prime_excl_framework")},
                            indent=2, ensure_ascii=False))
    print("\n옵션 2 (전체):", json.dumps(summary["option2_all"], indent=2, ensure_ascii=False))
    print("\n옵션 2′ (프레임워크 제외):",
          json.dumps(summary["option2prime_excl_framework"], indent=2, ensure_ascii=False))

    json.dump({
        "summary": summary,
        "selected_option2": sorted(clean),
        "selected_option2prime": sorted(clean_prime),
        "excluded_snapshots": EXCLUDED_SNAPSHOTS,
        "per_file_stage": stage,
        "selected_meta": {r: {"snap": files[r]["snap"],
                              "repository_root": repo_meta[files[r]["snap"]]["repository_root"],
                              "boundary_method": repo_meta[files[r]["snap"]]["boundary_method"],
                              "funcs": files[r]["funcs"], "closure": len(closure(r)[0]),
                              "specs": files[r]["specs"], "hash": files[r]["hash"],
                              "tags": sorted(files[r]["tags"])} for r in sorted(clean)},
    }, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
