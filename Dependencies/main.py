#!/usr/bin/env python3
"""
소스 슬라이서(`slice_solidity`)와 프로젝트 의존성 로더(`load_project_deps`).

(2026-10-23) 의존성 **빌드**는 `Dependencies/registry.py` 로 옮겼다 — 선언별 조각 + 위상정렬(`_topo_order`) +
정규식 pkl 선별 로드를 버리고, 파일 단위 레지스트리(고정점 분석, 이름 참조 직렬화)로 바꿨다.
"""

import re
import sys
import pathlib
from typing import List, Dict

# 상위 디렉토리를 sys.path에 추가
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))



# ── soltotestjson 패턴 (soltotestjson.py와 동일) ──
_only_ws   = re.compile(r"^\s*$")
_open_blk  = re.compile(r"\{\s*$")
_empty_blk = re.compile(r"\{\s*\}\s*$")
_one_liner = re.compile(r";\s*$")
_only_clo  = re.compile(r"^\s*}\s*$")
_comment   = re.compile(r"^\s*//")


def _paren_depth(text: str) -> int:
    """문자열 리터럴을 건너뛰며 `(` `)` 깊이를 센다 (텍스트 끝 시점)."""
    d = 0
    i = 0
    n = len(text)
    q = None
    while i < n:
        c = text[i]
        if q:                      # 문자열 리터럴 안
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c == '"' or c == "'":
            q = c
        elif c == "(":
            d += 1
        elif c == ")":
            d -= 1
        i += 1
    return d


def _brace_opens_block(text: str) -> bool:
    """`{` 로 끝나는 텍스트에서 그 `{` 가 **블록**을 여는가(True), 아니면
    **식(expression) 중괄호**인가(False).

    ★ F30. `_open_blk` 는 "`{` 로 끝나면 블록 헤더" 로만 판정했다. 그런데 Solidity 의
      named struct literal 은 `(` **안에** `{` 가 온다:
      ```solidity
      res = FloatStruct({
          numerator : a.numerator * b.denominator,
          denominator : a.denominator * b.denominator
      });
      ```
      이걸 블록 헤더로 보면 슬라이서가 **가짜 `}`** 를 붙여 문장을 두 동강 낸다
      (실측: `'res = FloatStruct({\\n}'` + `'numerator : ..., ... });'`).
      합쳐서 한 줄로 주면 파서는 정상 처리하므로 순수 슬라이서 결함이다.

      판정: 그 `{` 시점에 **괄호가 열려 있으면** 식 중괄호다.

      전수 검증(Dependencies + RQ1 + Dataset): `{` 로 끝나는 줄 46,214개 중 괄호가
      열린 채인 것 189개 — 186개가 전부 struct 리터럴(`X = Y({`, `Y({`, `Y(Z({` …),
      나머지 3개는 **소스 자체의 문법 오류**(`if (_output == address(_token) {` —
      닫는 괄호 누락). 오탐 0.

    ⚠ `_empty_blk`(`{ }` 로 끝나는 줄)는 같은 조건에 걸리는 줄이 **0건**이라 그대로 둔다.
    """
    return _paren_depth(text) <= 0


_BODYLESS_CTRL = re.compile(r"^(?:if|for|while|else)\b")

# 문자열 리터럴 / 라인 주석을 지운 "코드 부분" — 구문 검사에서 오탐을 막는다
_STR_LIT = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _code_only(text: str) -> str:
    t = _STR_LIT.sub('""', text)
    i = t.find("//")
    return t if i < 0 else t[:i]


#  지원하지 않는 구문 → 사유. 입력 전제 위반(`_one_statement_violation`)과 **다른 범주**다:
#  전제 위반은 "같은 뜻을 줄만 나눠 쓰면 분석된다" 지만, 이쪽은 분석기가 그 구문 자체를 다루지
#  않는다. 조용한 오분석을 막기 위해 명시적으로 거부한다(프로젝트 원칙).
_UNSUPPORTED = (
    # (2026-10-29) `try new C(...)` = 컨트랙트 생성 try. CFG 빌더의 try 체인은 "try 표현식 =
    #   외부 함수 호출" 만 다룬다. 데이터셋 전체 0건이라(선정 551 파일 0, DAppSCAN-source 전체 0,
    #   줄바꿈 포함 검색 확인) 지원 범위 밖으로 확정했다 — evaluation/reviews/round9_operations.md §9.
    (re.compile(r"\btry\s+new\b"), "`try new C(...)`(컨트랙트 생성 try) 는 지원하지 않는다"),
)


def _unsupported_construct(txt: str) -> str | None:
    code = _code_only(txt)
    for rx, why in _UNSUPPORTED:
        if rx.search(code):
            return why
    return None


def _one_statement_violation(txt: str, close_before: bool) -> str | None:
    """입력 전제(**입력 1개 = statement 1개**) 위반이면 사유, 아니면 None. (Phase 4b, 2026-10-22 결정)

    위반 형태:
      · 한 줄 블록 `if (x) { return; }` / `modifier m() { _; }` / `unchecked { x; }` — 블록 헤더와 본문이 한 입력
      · 한 줄 다중 문장 `a = 1; b = 2;`
      · 중괄호 없는 제어문 `if (x) return;` / `for (...) s += 1;` / `else x = 1;`
    올바른 입력은 `if (x) {` ⏎ `}` 와 `return;` 두 입력이다. 예전엔 이런 줄이 어느 패턴에도 맞지 않아
    **다음 줄들과 조용히 합쳐져** 엉뚱한 문장이 됐다(조용한 오분석).
    판정은 문자열 리터럴·라인 주석을 건너뛰고 **괄호 밖** `;` 만 센다(`for (a; b; c) {` 의 `;` 는 문장 구분이 아님).

    (2026-10-22) `;` 가 없는 위반도 잡는다 — 예전엔 아래 둘이 그대로 통과해 다음 줄과 합쳐지거나(한 줄 enum) ANTLR 이
    오류를 출력만 하고 본문 없는 함수를 만들었다(조용한 오분석):
      · 닫는 `}` 뒤에 다른 문장 `}    function f() {` (`} else` / `} catch` / do-while 꼬리는 호출 전에 벗겨진다)
      · 괄호 밖 `{` 뒤에 내용이 오는 한 줄 블록 `enum E { A, B }` (빈 블록 `{}` 은 허용 — 전용 처리 경로가 있다)
    """
    if txt.startswith("}") and txt[1:].split("//", 1)[0].strip():
        return "닫는 `}` 뒤에 다른 문장"
    code, q, i, depth, semis = [], None, 0, 0, []
    while i < len(txt):
        c = txt[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif txt.startswith("//", i):
            break
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth == 0:
            semis.append(i)
        elif c == "{" and depth == 0:
            # 호출 옵션 `x.call{value: v}(...)` / `new C{salt: s}()` 의 중괄호는 블록이 아니다 — 짝 `}` 뒤가 `(` 면 건너뛴다
            j, d = i, 0
            while j < len(txt):
                if txt[j] == "{":
                    d += 1
                elif txt[j] == "}":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            if j < len(txt) and txt[j + 1:].lstrip().startswith("("):
                i = j + 1
                continue
            rest = txt[i + 1:].split("//", 1)[0].strip()
            if rest and rest != "}":
                return "블록 헤더와 본문이 한 줄(한 줄 블록)"
        i += 1
    if not semis:
        return None
    if len(semis) > 1 or txt[semis[0] + 1:].split("//", 1)[0].strip():
        return "한 줄에 문장이 둘 이상(한 줄 블록 또는 다중 문장)"
    if _BODYLESS_CTRL.match(txt) and not (close_before and txt.startswith("while")):   # `} while (c);` = do-while 꼬리
        return "중괄호 없는 제어문 본문"
    return None


def slice_solidity(source: str) -> List[Dict]:
    """Solidity 소스를 라인 단위 JSON records로 변환 (soltotestjson.py와 동일)"""
    lines = source.splitlines()
    inputs = []
    cur_line = 1
    i = 0
    in_assembly = 0  # assembly brace depth 추적
    in_enum = False    # enum body 추적

    while i < len(lines):
        raw = lines[i]
        txt = raw.strip()

        # 0-E) enum 내부: 멤버를 } 까지 모아서 한 record로 전달
        if in_enum:
            enum_start = cur_line
            enum_end = cur_line
            collected = []
            while i < len(lines):
                raw2 = lines[i]; txt2 = raw2.strip()
                if _only_ws.match(raw2):
                    cur_line += 1; i += 1; continue
                if '}' in txt2:
                    before_brace = txt2.split('}')[0].strip()
                    if before_brace:
                        collected.extend(m.strip() for m in before_brace.split(',') if m.strip())
                        enum_end = cur_line
                    cur_line += 1; i += 1; break
                collected.extend(m.strip() for m in txt2.split(',') if m.strip())
                enum_end = cur_line
                cur_line += 1; i += 1
            in_enum = False
            if collected:
                inputs.append({"code": ", ".join(collected), "startLine": enum_start, "endLine": enum_start, "event": "add"})
            continue

        # 0) assembly 내부: 줄 단위로 처리 (Yul은 ; 없으므로)
        if in_assembly > 0:
            if _only_ws.match(raw):
                inputs.append({"code": "\n", "startLine": cur_line, "endLine": cur_line, "event": "add"})
                cur_line += 1; i += 1; continue
            if _only_clo.match(txt):
                in_assembly -= 1
                cur_line += 1; i += 1; continue
            # ★ F32. `{` 로 끝나는 **Yul 블록 헤더**(`default {`, `for {`, `if cond {`)도
            #   assembly 바깥과 동일하게 **2줄 레코드**(헤더 + 가짜 `}`)로 낸다.
            #
            #   가짜 `}` 의 역할은 분석기 라인 공간에서 **닫는 브레이스가 놓일 줄을 미리
            #   확보**하는 것이다. 예전엔 assembly 안의 모든 줄을 단일 줄 레코드로 냈고,
            #   그래서 여러 줄에 걸친 Yul 블록의 닫는 `}` 자리가 확보되지 않아
            #   함수 EXIT 노드가 **삽입 지점보다 앞**에 남았다. 그러면
            #   `DynamicCFGBuilder` 가 "내 줄 다음의 첫 CFG 노드" 를 못 찾아
            #   assembly **바깥**의 뒤따르는 문장들이 터진다:
            #       `No CFG node found after line N (searched up to line M)`
            #   (실측: PRBMathSD59x18 L249-251, PRBMathUD60x18 L192-194 — 각 3건)
            #
            #   최소 재현: `default { r := 2 }` 한 줄이면 정상, 아래처럼 쪼개지면 실패했다.
            #       default {
            #           r := 2
            #       }
            #
            #   ※ `case 1 { r := 1 }` 처럼 한 줄에서 여닫는 블록은 브레이스가 상쇄되므로
            #     예전에도 문제가 없었다(단일 줄 레코드 그대로 유지).
            #   ※ Yul `switch`/`case` 의 **분기 join 의미론**은 이것과 별개로 여전히
            #     미구현이다 — `EnhancedYulVisitor.visitYulSwitchStatement` 가 `return None`
            #     이고, 줄 단위 레코드라 case 들이 **순차 적용(마지막이 이김)** 되어
            #     건전하지 않다. 블록 전체를 한 트리로 넘기는 방향(=(B))이 필요하다.
            # (2026-10-23, Yul P0-a) 괄호가 닫히지 않은 줄 = 여러 줄에 걸친 Yul 식(`result := add(` ⏎ `mul(` …).
            #   Yul 에는 `;` 가 없어 줄마다 잘랐는데, 그러면 각 줄이 단독으로 파싱되지 않는다. 괄호가 닫힐 때까지 이어
            #   **레코드 하나 = 문장 하나** 전제를 지킨다(Solidity 의 여러 줄 문장 5) 과 같은 처리).
            if txt.count("(") > txt.count(")"):
                start_line = cur_line
                acc = [txt]
                depth = txt.count("(") - txt.count(")")
                cur_line += 1; i += 1
                while i < len(lines) and depth > 0:
                    nxt = lines[i].strip()
                    cur_line += 1; i += 1
                    if not nxt:
                        continue
                    acc.append(nxt)
                    depth += nxt.count("(") - nxt.count(")")
                merged = " ".join(acc)
                if _open_blk.search(merged):          # `if and(` ⏎ `…) {` — 여러 줄 헤더
                    in_assembly += 1
                    inputs.append({"code": f"{merged}\n}}", "startLine": start_line,
                                   "endLine": cur_line, "event": "add"})
                else:
                    in_assembly += merged.count('{') - merged.count('}')
                    inputs.append({"code": merged, "startLine": start_line,
                                   "endLine": cur_line - 1, "event": "add"})
                continue
            if _open_blk.search(txt):
                in_assembly += 1
                inputs.append({"code": f"{txt}\n}}", "startLine": cur_line,
                               "endLine": cur_line + 1, "event": "add"})
                cur_line += 1; i += 1; continue
            # assembly 내 nested { }
            in_assembly += txt.count('{') - txt.count('}')
            if in_assembly < 0:
                in_assembly = 0
            inputs.append({"code": txt, "startLine": cur_line, "endLine": cur_line, "event": "add"})
            cur_line += 1; i += 1; continue

        # 1) 빈 줄
        if _only_ws.match(raw):
            inputs.append({"code": "\n", "startLine": cur_line, "endLine": cur_line, "event": "add"})
            cur_line += 1; i += 1; continue

        # 2) 단독 '}'
        if _only_clo.match(txt):
            cur_line += 1; i += 1; continue

        # 2.3) '} else/catch/while ...' 패턴
        close_before = False
        if txt.startswith('}') and ('else' in txt or 'catch' in txt or 'while' in txt):
            txt = txt[1:].strip()
            close_before = True

        # 2.4) 입력 전제: 입력 1개 = statement 1개 (주석 줄은 제외)
        if not _comment.match(raw):
            why = _one_statement_violation(txt, close_before)
            if why:
                # 사용자(입력 쪽) 소스 오류 — 엔진 세션은 이 소스를 반영하지 않고 진단으로 돌려준다(SourceError ⊂ ValueError)
                from Utils.Errors import SourceError
                raise SourceError(f"line {cur_line}: 입력 전제 위반 — {why}: `{txt[:80]}` "
                                  f"(블록 헤더와 각 문장을 별도 줄로)", line=cur_line)

            # 2.4b) 지원하지 않는 구문 — 전제 위반과 달리 줄을 나눠도 분석되지 않는다.
            why = _unsupported_construct(txt)
            if why:
                from Utils.Errors import SourceError
                raise SourceError(f"line {cur_line}: 지원하지 않는 구문 — {why}: `{txt[:80]}`",
                                  line=cur_line)

        # 2.5) 주석 라인
        if _comment.match(raw):
            rec = {"code": txt, "startLine": cur_line, "endLine": cur_line, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)
            cur_line += 1; i += 1; continue

        # 3-pre) '{}' empty block
        if _empty_blk.search(txt):
            rec = {"code": txt, "startLine": cur_line, "endLine": cur_line, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)
            cur_line += 1; i += 1; continue

        # 3) '{' 로 끝나는 헤더 줄 (★ 식 중괄호는 제외 — _brace_opens_block 주석 참고)
        if _open_blk.search(txt) and _brace_opens_block(txt):
            block_code = f"{txt}\n}}"
            rec = {"code": block_code, "startLine": cur_line, "endLine": cur_line + 1, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)
            # assembly { 진입 시 in_assembly 활성화
            if txt.startswith('assembly'):
                in_assembly = 1
            elif txt.startswith('enum'):
                in_enum = True
            cur_line += 1; i += 1; continue

        # 4) 세미콜론으로 끝나는 한 줄 문장
        #   (2026-10-23) 괄호가 닫힌 경우만 — `for (uint256 i = 0;` ⏎ `i < n;` ⏎ `i++) {` 처럼 괄호 안의 `;` 로 끝나는 줄을
        #   문장 끝으로 보면 for 헤더가 입력 3개로 쪼개졌다(w3 CrossMarginLiquidation·w34 DrawCalculator). 아래 5) 로 잇는다.
        if _one_liner.search(txt) and _paren_depth(txt) == 0:
            rec = {"code": txt, "startLine": cur_line, "endLine": cur_line, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)
            cur_line += 1; i += 1; continue

        # 5) 다중 라인 문장
        start_line = cur_line
        accumulated = [txt]
        cur_line += 1; i += 1
        found_block_header = False

        while i < len(lines):
            next_raw = lines[i]
            next_txt = next_raw.strip()

            if _only_ws.match(next_raw):
                cur_line += 1; i += 1; continue

            accumulated.append(next_txt)
            cur_line += 1; i += 1

            if _empty_blk.search(next_txt):
                break
            # ★ 누적 텍스트 기준으로 판정한다 — `foo(` ⏎ `Bar({` 처럼 중첩된 경우
            #   그 줄만 보면 깊이를 알 수 없다.
            if _open_blk.search(next_txt) and _brace_opens_block(" ".join(accumulated)):
                found_block_header = True
                break
            if _one_liner.search(next_txt) and _paren_depth(" ".join(accumulated)) == 0:
                break

        if found_block_header:
            merged_code = " ".join(accumulated)
            block_code = f"{merged_code}\n}}"
            rec = {"code": block_code, "startLine": start_line, "endLine": cur_line, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)
            if merged_code.strip().startswith('assembly'):
                in_assembly = 1
        else:
            merged_code = " ".join(accumulated)
            rec = {"code": merged_code, "startLine": start_line, "endLine": cur_line - 1, "event": "add"}
            if close_before: rec["closeBefore"] = True
            inputs.append(rec)

    return inputs


# ── 프로젝트 단위 의존성 로더 (CLI main.py · 서버 engine_api 공용, 2026-10-22) ──────────────
def load_project_deps(sa, ca, deps_root, exclude=(), verbose: bool = True) -> dict:
    """프로젝트 레지스트리(deps/<p>/builds/<CURRENT>)를 sa/ca 에 로드한다.

    exclude: 분석 대상 소스가 스스로 선언하는 이름. 그 이름들을 선언한 단위가 **타깃 단위**이고, 타깃 파일이 그 단위를
    통째로 대신한다 — 타깃 단위의 의존 폐포만 로드한다(선언 집합이 다르면 오류). 자세한 규칙은 `registry.select_units`.
    (2026-10-23) 예전엔 objectfile/*.pkl 을 이름 접두로 골라 읽고, type alias·file-level enum·interface 이름을
    소스 정규식으로 따로 모았다 — 이제 모두 단위 pkl 에 들어 있다."""
    from Dependencies.registry import load
    return load(sa, ca, deps_root, exclude, verbose)
