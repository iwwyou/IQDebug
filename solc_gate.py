"""solc_gate — 누적 전체 소스를 solc 로 검사한다(AST 까지 = 타입 검사 포함, 코드 생성 없음).

컴파일되지 않는 코드는 해석하지 않는다(2026-11-24 사용자 결정). `EngineSession.analyze_source`·`update` 의 앞단에서
호출하며, 이 모듈은 엔진을 import 하지 않는다.

- 컴파일러: `IQDEBUG_SOLC_DIR`(기본 `evaluation/solc`)의 `solc-X.Y.Z` 중 소스와 import 폐포의 pragma 를 모두 만족하는
  최신 버전. 없으면 설치하지 않고 진단으로 알린다.
- 의존성: 레지스트리의 `sources/<단위>.sol` 은 평평한 사본이라 import 경로가 맞지 않는다. manifest 의 `origin`(원본 경로)을
  소스 이름으로 써서 원래 배치를 복원한다. 절대 경로 import 는 원본 경로의 접미사로 유일하게 정해질 때만 remapping 한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import posixpath
import re
import subprocess
import tempfile

_TOKEN = re.compile(r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'""", re.S)
_PRAGMA = re.compile(r"\bpragma\s+solidity\s+([^;]*);")
_IMPORT = re.compile(r"""\bimport\s*(?:[^"';]*?\bfrom\s*)?["']([^"']+)["']""")
_CMP = re.compile(r"(\^|~|>=|<=|>|<|=)?\s*v?(\d+)\.(\d+)(?:\.(\d+))?")
TIMEOUT = 60


def _blank_comments(src: str) -> str:
    """주석을 같은 길이의 공백으로(줄바꿈 유지). 문자열은 그대로 둔다."""
    return _TOKEN.sub(lambda m: re.sub(r"[^\n]", " ", m.group()) if m.group()[0] == "/" else m.group(), src)


# ── 버전 선택 ────────────────────────────────────────────────────────────────
def _solc_dir() -> pathlib.Path:
    env = os.environ.get("IQDEBUG_SOLC_DIR")
    return pathlib.Path(env) if env else pathlib.Path(__file__).resolve().parent / "evaluation" / "solc"


def installed() -> dict:
    """{(major, minor, patch): 실행 파일 경로}"""
    out = {}
    d = _solc_dir()
    for f in (d.iterdir() if d.is_dir() else ()):
        m = re.fullmatch(r"solc-(\d+)\.(\d+)\.(\d+)(?:\.exe)?", f.name)
        if m:
            out[tuple(map(int, m.groups()))] = str(f)
    return out


def _one(v, op, a, b, c) -> bool:
    t, full = (a, b, c or 0), c is not None
    if op == "^":
        hi = (a + 1,) if a > 0 else (a, b + 1) if b > 0 or not full else (a, b, c + 1)
        return v >= t and v[:len(hi)] < hi
    if op == "~":
        return v >= t and v[:2] == (a, b)
    if op == ">=":
        return v >= t
    if op == ">":
        return v > t if full else v[:2] > (a, b)
    if op == "<=":
        return v <= t if full else v[:2] <= (a, b)
    if op == "<":
        return v < t
    return v == t if full else v[:2] == (a, b)


def _satisfies(v, spec: str) -> bool:
    for part in spec.split("||"):
        part = re.sub(r"(\d+(?:\.\d+)+)\s+-\s+(\d+(?:\.\d+)+)", r">=\1 <=\2", part)
        cmps = [(m.group(1), int(m.group(2)), int(m.group(3)), None if m.group(4) is None else int(m.group(4)))
                for m in _CMP.finditer(part)]
        if cmps and all(_one(v, *c) for c in cmps):
            return True
    return False


# ── 소스 묶음 ────────────────────────────────────────────────────────────────
class Project:
    """의존성 원본. files: 원본 경로 → 소스. target: 타깃 파일의 원본 경로(의존성 밖이면 None)."""

    def __init__(self, files: dict | None = None, target: str | None = None):
        self.files, self.target = files or {}, target

    @classmethod
    def load(cls, deps_root, exclude=()) -> "Project":
        from Dependencies.registry import current_build, select_units
        root = pathlib.Path(deps_root)
        _, manifest = current_build(root)
        _, tid = select_units(manifest, exclude)
        files, target = {}, None
        for uid, m in manifest["units"].items():
            name = posixpath.normpath((m.get("origin") or uid + ".sol").replace("\\", "/"))
            p = os.path.abspath(root / "sources" / (uid + ".sol"))
            if os.name == "nt" and not p.startswith("\\\\?\\"):
                p = "\\\\?\\" + p                        # 260자 경로 제한
            with open(p, encoding="utf-8") as f:
                files[name] = f.read()
            if uid == tid:
                target = name
        return cls(files, target)

    def bundle(self, source: str):
        """(타깃 이름, {이름: 소스} import 폐포, remappings). 못 찾은 import 는 넣지 않는다(solc 가 오류를 낸다)."""
        main = self.target or "source.sol"
        out, remap, todo = {main: source}, [], [main]
        while todo:
            cur = todo.pop()
            for p in _IMPORT.findall(_blank_comments(out[cur])):
                if p.startswith("."):
                    name = posixpath.normpath(posixpath.join(posixpath.dirname(cur), p))
                else:
                    name = p
                    if name not in self.files:
                        hits = [f for f in self.files if f.endswith("/" + p)]
                        if len(hits) != 1:
                            continue
                        name = hits[0]
                        if f"{p}={name}" not in remap:
                            remap.append(f"{p}={name}")
                if name not in out and name in self.files:
                    out[name] = self.files[name]
                    todo.append(name)
        return main, out, remap


# ── 검사 ────────────────────────────────────────────────────────────────────
def _line_of(source: str, byte_off: int) -> int:
    return source.encode("utf-8")[:byte_off].count(b"\n") + 1


def check(source: str, project: Project | None = None) -> list[dict]:
    """컴파일 오류 목록 [{line, message}] (빈 목록 = 통과, 경고는 무시). line = 타깃 소스의 줄, 다른 파일·위치 미상 = -1."""
    main, sources, remap = (project or Project()).bundle(source)
    specs = [s.strip() for t in sources.values() for s in _PRAGMA.findall(_blank_comments(t))]
    have = installed()
    ok = [v for v in have if all(_satisfies(v, s) for s in specs)]
    if not ok:
        m = _PRAGMA.search(_blank_comments(source))
        return [{"line": source[:m.start()].count("\n") + 1 if m else -1,
                 "message": f"pragma {sorted(set(specs))} 를 만족하는 solc 가 없다 "
                            f"({_solc_dir()}: {', '.join('.'.join(map(str, v)) for v in sorted(have)) or '없음'})"}]
    req = {"language": "Solidity", "sources": {n: {"content": t} for n, t in sources.items()},
           "settings": {"remappings": remap, "outputSelection": {"*": {"": ["ast"]}}}}
    # 작업 디렉터리를 빈 임시 폴더로 — solc 가 묶음에 없는 import 를 로컬 파일에서 읽어 결과가 달라지지 않게
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run([have[max(ok)], "--standard-json"], input=json.dumps(req).encode("utf-8"),
                           capture_output=True, timeout=TIMEOUT, cwd=cwd)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(f"solc 실행 실패({p.returncode}): {p.stderr.decode('utf-8', 'replace')[:300]}")
    out = []
    for e in json.loads(p.stdout.decode("utf-8", "replace")).get("errors", []):
        if e.get("severity") != "error":
            continue
        loc = e.get("sourceLocation") or {}
        here = loc.get("file") == main and loc.get("start", -1) >= 0
        out.append({"line": _line_of(source, loc["start"]) if here else -1,
                    "message": f"{e.get('type', 'Error')}: {e.get('message', '')}"
                               + ("" if here or not loc.get("file") else f" ({loc['file']})")})
    return out
