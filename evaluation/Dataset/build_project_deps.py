# -*- coding: utf-8 -*-
"""
프로젝트 단위 의존성 pkl 빌드 (2026-10-21, 사용자 승인 — 오류 1번 (b) + 상속 복원의 전제).

입력: projects/<p>/out/build-info/*.json 의 source_id_to_path = forge 가 실제로 컴파일한 소스 전체(npm 포함, 조부모까지).
처리:
  1. 각 소스를 IQDebug 가 파싱할 수 있게 전처리 — tools/contraction_target 의 비코드 제거·enum 한 줄·다줄 문장 합치기·
     문법 충돌 식별자 rename·한 줄 블록 전개·멤버 재배치. **본문 슬라이싱은 하지 않는다**(의존성은 전부 필요할 수 있다).
  2. (2026-10-23, 레지스트리) 전처리본을 **원본 파일 하나 = 단위 하나**로 deps/<p>/sources/<단위>.sol 에 쓰고
     (예전: 선언별 조각 + 파일 수준 정의 복사), `Dependencies/registry.py --root deps/<p>` 로 빌드한다
     → deps/<p>/builds/build_NNN/{manifest.json, units/*.pkl}, deps/<p>/CURRENT.
타깃 파일도 포함한다 — 같은 프로젝트의 다른 케이스가 그것을 의존할 수 있다(w35 Ticks). 케이스 실행 시 로더가
**타깃 단위를 타깃 파일로 대신**하고 그 의존 폐포만 올린다(`registry.select_units`).

출력: deps/<p>/..., deps/<p>/build.log, deps/build_report.json
사용: .venv/Scripts/python.exe evaluation/Dataset/build_project_deps.py [--only w62] [--prep-only]
"""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DS = pathlib.Path(__file__).resolve().parent
ROOT = DS.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from contraction_target import (strip_noncode, join_multiline_statements, rename_grammar_clashes,  # noqa: E402
                                _mask_strings, _match_brace, _members, _TYPE_DECL, _KEYWORDS)
from preprocess_contraction import (collapse_enum_members, remove_bare_scoping_blocks,  # noqa: E402
                                    expand_single_line_blocks, collapse_empty_lines)

PROJ = DS / "projects"
DEPS = DS / "deps"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
KIND_DIR = {"interface": "interfaces", "library": "libraries", "contract": "contracts"}


def project_sources(pdir: pathlib.Path) -> list[pathlib.Path]:
    seen, out = set(), []
    for bi in sorted((pdir / "out" / "build-info").glob("*.json")):
        d = json.loads(bi.read_text(encoding="utf-8"))
        for p in (d.get("source_id_to_path") or {}).values():
            path = pathlib.Path(p)
            path = (path if path.is_absolute() else pdir / path).resolve()
            if path not in seen:
                seen.add(path)
                out.append(path)
    if not out:
        raise FileNotFoundError(f"{pdir.name}: build-info 없음 — reassemble.py --build 먼저")
    return out


def prep(source: str) -> str:
    lines = strip_noncode(source)
    lines = collapse_enum_members(lines)
    lines = join_multiline_statements(lines)
    text, _ = rename_grammar_clashes("\n".join(lines))
    lines = remove_bare_scoping_blocks(text.splitlines())
    lines = expand_single_line_blocks(lines)
    return reorder_top_level(reorder_members("\n".join(collapse_empty_lines(lines)) + "\n"))


# ── 멤버 재배치 (2026-10-21 사용자 승인: "일단 재배치해서 수행") ─────────────────────────────────
# 분석기는 컨트랙트 본문을 위→아래 한 번에 소비하므로, Solidity 에선 합법인 전방 참조(뒤에 정의된 함수 호출·modifier·
# struct/enum)를 해석하지 못한다. 의존성은 사용자가 편집하는 코드가 아니므로 선언 순서를 바꿔 전방 참조를 없앤다:
#   using → type 별칭 → enum → struct(멤버 타입 기준 위상정렬) → error → event → 상태변수(원래 순서)
#   → modifier·constructor·함수·fallback/receive (내부 호출·modifier 사용 기준 위상정렬, 동률은 원래 순서)
# 자기 재귀(PRBMathSD59x18.exp2 등)는 자기 간선이라 무시한다(재배치로 풀 수 없음 — 엔진 쪽 문제로 별도).
# 서로 다른 노드 사이의 순환(상호 재귀)은 순서를 정할 수 없으므로 폴백 없이 예외로 보고한다.
_MEMBER_GROUPS = ("using", "type", "enum", "const", "struct", "error", "event", "state", "callable")
_DOTTED_CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")


def _member_kind(head: str):
    h = head.strip()
    for kw in ("using", "enum", "struct", "error", "event", "modifier"):
        m = re.match(rf"{kw}\b\s*([A-Za-z_]\w*)?", h)
        if m:
            return ("callable", m.group(1)) if kw == "modifier" else (kw, m.group(1))
    m = re.match(r"type\s+([A-Za-z_]\w*)\s+is\b", h)
    if m:
        return "type", m.group(1)
    m = re.match(r"function\s+([A-Za-z_]\w*)", h)
    if m:
        return "callable", m.group(1)
    m = re.match(r"(constructor|fallback|receive)\s*\(", h) or re.match(r"(function)\s*\(", h)
    if m:
        return "callable", f"<{m.group(1)}>"
    # (2026-10-23) constant 상태변수는 struct 앞 — struct 멤버의 배열 길이가 상수를 쓴다(w34 TwabLib `Observation[MAX_CARDINALITY]`)
    if re.search(r"\bconstant\b", h.split("=", 1)[0]):
        return "const", None
    return "state", None


def _paren_span(masked: str, i: int) -> int:
    """masked[i] == '(' 의 짝 ')' 인덱스."""
    depth = 0
    for k in range(i, len(masked)):
        depth += masked[k] in "(["
        depth -= masked[k] in ")]"
        if depth == 0:
            return k
    raise ValueError(f"unbalanced paren at {i}")


def _count_args(masked: str, i: int) -> int:
    """masked[i] == '(' 인 호출/파라미터 목록의 최상위 인자 수."""
    j = _paren_span(masked, i)
    inner = masked[i + 1:j]
    if not inner.strip():
        return 0
    depth, n = 0, 1
    for c in inner:
        depth += c in "([{"
        depth -= c in ")]}"
        n += c == "," and depth == 0
    return n


def _after_params(head: str) -> str:
    """함수/modifier 헤더에서 파라미터 목록 뒤(가시성·modifier·returns) 부분."""
    i = head.find("(")
    return head[_paren_span(head, i) + 1:] if i >= 0 else ""


def _callable_key(name: str, masked_member: str) -> str:
    head = masked_member.split("{", 1)[0]
    if re.match(r"\s*modifier\b", head):
        return f"{name}/m"
    i = head.find("(")
    return f"{name}/{_count_args(head, i) if i >= 0 else 0}"


def _topo_stable(names: list[str], edges: dict[str, set[str]], where: str) -> list[str]:
    """names(원래 순서) 를 '의존 대상이 먼저' 로 정렬. 동률은 원래 순서. 순환이면 예외."""
    order, done, visiting = [], set(), []

    def visit(n):
        if n in done:
            return
        if n in visiting:
            cyc = visiting[visiting.index(n):] + [n]
            raise ValueError(f"[reorder] {where}: 서로 다른 이름 사이 호출 순환 — 순서를 정할 수 없음: {' -> '.join(cyc)}")
        visiting.append(n)
        for d in sorted(edges.get(n, ()), key=names.index):
            visit(d)
        visiting.pop()
        done.add(n)
        order.append(n)

    for n in names:
        visit(n)
    return order


def reorder_members(text: str) -> str:
    masked = _mask_strings(text)
    decls = []
    for m in _TYPE_DECL.finditer(masked):
        if any(o < m.start() < c for _, o, c in decls):
            continue
        ob, semi = masked.find("{", m.end()), masked.find(";", m.end())
        if ob < 0 or (0 <= semi < ob):
            continue
        decls.append((m.group(2), ob, _match_brace(masked, ob)))
    out, prev = [], 0
    for cname, ob, cb in decls:
        mems = []                                            # (group, name, src_text, masked_text)
        for s, e in _members(masked, ob + 1, cb):
            g, n = _member_kind(masked[s:e].split("{", 1)[0])
            mems.append((g, n, text[s:e], masked[s:e]))
        by_group = {g: [x for x in mems if x[0] == g] for g in _MEMBER_GROUPS}

        # struct: 멤버 타입으로 다른 struct 를 쓰면 그것이 먼저
        snames = [x[1] for x in by_group["struct"]]
        sedges = {x[1]: {w for w in re.findall(r"\b([A-Za-z_]\w*)\b", x[3].split("{", 1)[1])
                         if w in snames and w != x[1]} for x in by_group["struct"]}
        sorder = _topo_stable(snames, sedges, f"{cname} struct")
        by_group["struct"].sort(key=lambda x: sorder.index(x[1]))

        # callable: 노드 = (이름, 인자 수) — overload 끼리의 호출(SafeMath.sub(a,b) → sub(a,b,msg))도 순서에 반영한다.
        #   간선 = 본문의 비한정 호출(인자 수가 맞는 overload; 맞는 것이 없으면 같은 이름 전부) + 헤더의 modifier 사용.
        #   같은 이름·같은 인자 수(타입만 다른 overload)는 한 노드로 묶여 원래 순서를 유지한다.
        calls = by_group["callable"]
        keys = [_callable_key(x[1], x[3]) for x in calls]
        cnodes = list(dict.fromkeys(keys))
        by_name: dict[str, list[str]] = {}
        for k in cnodes:
            by_name.setdefault(k.split("/")[0], []).append(k)
        cedges = {k: set() for k in cnodes}
        for k, (_, n, _, mt) in zip(keys, calls):
            head, _, body = mt.partition("{")
            for cm in _DOTTED_CALL.finditer(body):
                name = cm.group(1)
                if name in _KEYWORDS or name not in by_name:
                    continue
                exact = f"{name}/{_count_args(body, cm.end() - 1)}"
                cedges[k] |= {exact} if exact in cedges else set(by_name[name])
            for w in re.findall(r"\b([A-Za-z_]\w*)\b", _after_params(head)):
                if f"{w}/m" in cedges:
                    cedges[k].add(f"{w}/m")
            cedges[k].discard(k)
        corder = _topo_stable(cnodes, cedges, f"{cname} function")
        order_of = {id(x): corder.index(k) for k, x in zip(keys, calls)}
        calls.sort(key=lambda x: order_of[id(x)])

        body = "".join(f"\n    {x[2].strip()}\n" for g in _MEMBER_GROUPS for x in by_group[g])
        out.append(text[prev:ob + 1])
        out.append(body)
        prev = cb
    out.append(text[prev:])
    return "".join(out)


# ── 최상위 재배치 (2026-10-23 사용자 승인 F1 (a)) ─────────────────────────────────────────────────
# 파일 하나가 단위가 되면서(레지스트리) 같은 파일 안의 **뒤에 나오는 최상위 선언**을 앞에서 쓰는 경우가 드러났다
# (w42 IMochiVault: 파일 수준 struct 멤버가 뒤쪽 `enum Status` 사용, w44 EmergencyGovernable: 뒤쪽 interface 를 타입으로 사용).
# 분석기는 위→아래 한 번에 소비하므로 멤버 재배치와 같은 방식으로 최상위도 정렬한다:
#   pragma·import·using → type 별칭 → 상수 → enum → struct(멤버 타입 기준) → error·event → 자유 함수
#   → contract/library/interface (본문에 다른 최상위 선언 이름이 나오면 그것이 먼저 — SCC 단위, SCC 안은 원래 순서)
# 같은 파일 안 선언끼리의 순환(SCC 크기 > 1)은 순서로 풀 수 없다 — 원래 순서로 두고 레지스트리가 미해석으로 보고한다.
#   ※ 근본 해법은 분석기의 지연 해석(F1 (b)) — 그때 이 재배치와 멤버 재배치를 함께 없앤다.
_TOP_GROUPS = ("header", "type", "const", "enum", "struct", "misc", "function", "decl")


def _top_kind(head: str):
    h = head.strip()
    m = re.match(r"(?:abstract\s+)?(contract|library|interface)\s+([A-Za-z_]\w*)", h)
    if m:
        return "decl", m.group(2)
    for kw, g in (("pragma", "header"), ("import", "header"), ("using", "header"), ("enum", "enum"),
                  ("struct", "struct"), ("error", "misc"), ("event", "misc"), ("function", "function")):
        m = re.match(rf"{kw}\b\s*([A-Za-z_]\w*)?", h)
        if m:
            return g, m.group(1)
    m = re.match(r"type\s+([A-Za-z_]\w*)\s+is\b", h)
    if m:
        return "type", m.group(1)
    m = re.search(r"\bconstant\s+([A-Za-z_]\w*)\s*=", h)
    if m:
        return "const", m.group(1)
    raise ValueError(f"[reorder top] 알 수 없는 최상위 항목: {h[:80]!r}")


def _scc_order(names: list, edges: dict) -> list:
    """의존 대상이 먼저. SCC 안과 동률은 원래 순서."""
    sys.path.insert(0, str(ROOT))
    from Dependencies.registry import _sccs
    order = []
    for comp in _sccs(names, edges):                    # 역위상(의존 대상 SCC 먼저)
        order.extend(sorted(comp, key=names.index))
    return order


def reorder_top_level(text: str) -> str:
    masked = _mask_strings(text)
    items = []                                          # (group, name, src_text, masked_text)
    for s, e in _members(masked, 0, len(masked)):
        g, n = _top_kind(masked[s:e].split("{", 1)[0])
        items.append((g, n, text[s:e], masked[s:e]))
    by_group = {g: [x for x in items if x[0] == g] for g in _TOP_GROUPS}

    def ordered(group: str, body_of):
        names = [x[1] for x in by_group[group]]
        if len(set(names)) != len(names):
            raise ValueError(f"[reorder top] 같은 이름의 최상위 {group} 이 여럿: {names}")
        edges = {x[1]: {w for w in re.findall(r"\b([A-Za-z_]\w*)\b", body_of(x)) if w in names and w != x[1]}
                 for x in by_group[group]}
        order = _scc_order(names, edges)
        by_group[group].sort(key=lambda x: order.index(x[1]))

    ordered("struct", lambda x: x[3].split("{", 1)[1])
    ordered("decl", lambda x: x[3])
    return "\n\n".join(x[2].strip() for g in _TOP_GROUPS for x in by_group[g]) + "\n"


# 프로젝트별 소스 편집 (사용자 결정) — 정규식 → 대체. 전처리 뒤에 적용하고, 맞는 곳이 정확히 count 개가 아니면 오류.
#   · n_HIT (2026-10-23): 0.4 문법의 이름 없는 fallback `function () external payable { getTokens(); }` 제거 — 도구 지원 범위는
#     0.5 이상(0.6 이전 문법의 이 형태는 문법이 받지 않는다)이고, 케이스의 버그(getTokens) 와 무관하다. 타깃 contraction 에는 원래 없다.
#   · w31 (2026-10-25): `wantToCVX(uint256 want)` 파라미터 → `_want` — 상태변수 `want` 를 가린다(엔진 미지원 `UnsupportedShadowing`,
#     DeclID = future work). 타깃 31_H_01 contraction 도 같은 rename.
SOURCE_EDITS = {
    "n_HIT": [(r"\n[ \t]*function \(\) external payable \{\n[ \t]*getTokens\(\);\n[ \t]*\}\n", "\n", 1)],
    "w31": [(r"function wantToCVX\(uint256 want\)", "function wantToCVX(uint256 _want)", 1),
            (r"return want\.mul\(bCVXToCVX\)", "return _want.mul(bCVXToCVX)", 1)],
    # · w8/w110/w61 (2026-10-25): 초기화 함수 파라미터 `_name`/`_symbol`/`_owner` → `nameArg`/`symbolArg`/`ownerArg` — 부모(OZ ERC20Upgradeable·
    #   OwnableUpgradeable)의 **private** 상태변수와 이름이 같다. Solidity 이름 해석으로는 가림이 아니지만 엔진 환경은 이름 하나로
    #   storage 와 지역을 같이 담아 충돌한다(`UnsupportedShadowing`). 각 단위에서 그 함수 안에만 나온다(케이스 의존 폐포 밖).
    "w8": [(r"function __NFTXVault_init\(string memory _name, string memory _symbol,",
            "function __NFTXVault_init(string memory nameArg, string memory symbolArg,", 1),
           (r"__ERC20_init\(_name, _symbol\)", "__ERC20_init(nameArg, symbolArg)", 1)],
    "w110": [(r"string memory _name, string memory _symbol, uint256\[4\] memory _feeConfig\)",
              "string memory nameArg, string memory symbolArg, uint256[4] memory _feeConfig)", 1),
             (r"abi\.encodePacked\(_name\)", "abi.encodePacked(nameArg)", 1),
             (r"name = _name;", "name = nameArg;", 1),
             (r"abi\.encodePacked\(_symbol\)", "abi.encodePacked(symbolArg)", 1),
             (r"symbol = _symbol;", "symbol = symbolArg;", 1)],
    "w61": [(r"address _strategyRegistry, address _owner,", "address _strategyRegistry, address ownerArg,", 1),
            (r"OwnableUpgradeable\.transferOwnership\(_owner\)", "OwnableUpgradeable.transferOwnership(ownerArg)", 1),
            (r"function initialize\(address _owner, address payable _savingsAccount\)",
             "function initialize(address ownerArg, address payable _savingsAccount)", 1),
            (r"super\.transferOwnership\(_owner\)", "super.transferOwnership(ownerArg)", 1)],
}


def _apply_source_edits(pkey: str, uid: str, text: str) -> str:
    for pat, repl, count in SOURCE_EDITS.get(pkey, []):
        new, n = re.subn(pat, repl, text)
        if n == 0:
            continue                                   # 이 단위에는 해당 없음
        if n != count:
            raise ValueError(f"{pkey}/{uid}: 소스 편집 {pat!r} 이 {n} 곳에 맞음 (기대 {count})")
        text = new
    return text


def build(pkey: str, prep_only: bool) -> dict:
    sys.path.insert(0, str(ROOT))
    from Dependencies.registry import unit_id_for
    pdir = PROJ / pkey
    out = DEPS / pkey
    if out.exists():
        shutil.rmtree(out)
    (out / "sources").mkdir(parents=True)
    origins = {}
    for src in project_sources(pdir):
        uid = unit_id_for(src, [pdir, DS, ROOT])
        if uid in origins:
            raise ValueError(f"{pkey}: 단위 id 충돌 {uid}: {origins[uid]} / {src}")
        origins[uid] = str(src)
        text = _apply_source_edits(pkey, uid, prep(src.read_text(encoding="utf-8", errors="replace")))
        (out / "sources" / f"{uid}.sol").write_text(text, encoding="utf-8")
    (out / "sources" / "origins.json").write_text(json.dumps(origins, indent=1, ensure_ascii=False), encoding="utf-8")
    rep = {"units": len(origins)}
    if prep_only:
        return rep
    proc = subprocess.run([str(PY), str(ROOT / "Dependencies" / "registry.py"), "--root", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    log = proc.stdout + proc.stderr
    (out / "build.log").write_text(log, encoding="utf-8")
    rep["exit"] = proc.returncode
    if proc.returncode == 0:
        manifest = json.loads((out / "builds" / (out / "CURRENT").read_text().strip() / "manifest.json")
                              .read_text(encoding="utf-8"))
        st = {}
        for u in manifest["units"].values():
            st[u["status"]] = st.get(u["status"], 0) + 1
        rep.update({"build": manifest["build"], "rounds": manifest["rounds"], "status": st,
                    "collisions": manifest["collisions"],
                    "not_complete": {k: {"status": u["status"], "unresolved": sorted(u["unresolved"]),
                                         "errors": len(u["errors"])}
                                     for k, u in manifest["units"].items() if u["status"] != "Complete"}})
    else:
        rep["traceback_tail"] = log[log.rfind("Traceback"):][:800] if "Traceback" in log else log[-800:]
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--prep-only", action="store_true")
    ap.add_argument("--skip-done", action="store_true", help="보고서에 이미 exit 가 있는 프로젝트는 건너뜀(중단 후 재개)")
    a = ap.parse_args()
    DEPS.mkdir(exist_ok=True)
    rp = DEPS / "build_report.json"
    report = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
    for pdir in sorted(PROJ.iterdir()):
        if not pdir.is_dir() or (a.only and pdir.name != a.only):
            continue
        if a.skip_done and "exit" in report.get(pdir.name, {}):
            continue
        rep = build(pdir.name, a.prep_only)
        report[pdir.name] = rep
        rp.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")   # 프로젝트마다 저장
        print(f"{pdir.name:<18} units={rep['units']:3d} exit={rep.get('exit', '-')} {rep.get('build', '')} "
              f"rounds={rep.get('rounds', '-')} {rep.get('status', '')}"
              + (f" COLLISION {sorted(rep['collisions'])}" if rep.get("collisions") else ""), flush=True)
    rp.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
