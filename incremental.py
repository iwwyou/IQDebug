"""incremental — record-space diff 레이어 (B1, §5.6 1단계).

프론트는 풀소스를 보내고(와이어 단순), 서버는 prev/new 레코드를 diff 해 *변경분만* 영속 세션의
`update_code` 로 적용한다. Monaco 라인 좌표가 아니라 **slice 정규화된 레코드 시퀀스**에서 diff 하므로
좌표매핑·closeBefore·`}`스트립 재구현이 필요 없다(slice 가 단일 진실원).

이 모듈은 **순수 함수**다 — 엔진을 import 하지 않으며, 레코드(slice_solidity 출력) 리스트만 받아
`EditOp` 스트림을 낸다. 엔진 적용(=`update_code` 호출)은 호출자(EngineSession, §5.6 5단계)가 한다.

좌표 모델: slice_solidity 는 `cur_line=1` 부터 정규화 라인을 매기고, main.py 드라이버는 record 의
startLine/endLine 을 그대로 update_code 에 넘긴다 → **엔진 라인 공간 == slice 정규화 공간**.
따라서 prev 를 적용한 세션의 라인은 prev 레코드 라인과 일치하고, 아래 `cur` 커서(엔진 현재 좌표)는
edit script 를 좌→우로 처리하며 각 op 의 라인을 산출한다(update_code 가 insert/delete 시 뒤 라인을
자동 시프트하므로 equal 구간은 자연히 new 공간으로 재정렬된다).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass


def syntax_valid(source: str) -> tuple[bool, list[dict]]:
    """전체 소스를 ANTLR `sourceUnit`(EOF) 로 파스해 syntax 오류를 수집한다 (§5.6 4단계).

    B1 적용 게이트: 새 풀소스가 invalid(예: `if` 지웠는데 `else` 만 남음, do-while 의 `do` 만
    지워 `}` 잔존, brace 불일치)면 edit 을 거부하고 이전 valid CFG 를 유지한다 → 잘못된 부분삭제가
    CFG 수술에 도달조차 못 하게 막아 손상을 원천 차단(논문 Alg. Code Deletion 1단계 SyntaxValid).

    반환: (valid, diagnostics) — diagnostics = [{line, column, message, severity}].
    """
    from antlr4 import CommonTokenStream
    from antlr4.error.ErrorListener import ErrorListener

    from Parser.SolidityParser import SolidityParser

    diags: list[dict] = []

    class _Collect(ErrorListener):
        def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
            diags.append({"line": line, "column": column,
                          "message": msg, "severity": "error"})

    listener = _Collect()
    from Utils.Helper import ParserHelpers
    lexer = ParserHelpers.new_lexer(source, intent=False)      # (2026-11-13) 코드 렉싱 — intent 단어는 Identifier
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)
    parser = SolidityParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    parser.sourceUnit()  # EOF 포함 → 전체 입력 소비 강제
    return (len(diags) == 0, diags)


def kind_of(code: str) -> str:
    """레코드 code 로 삭제/수정의 construct 종류 판별(DynamicCFGBuilder.delete_cfg_range kind).
    호출자가 delete/modify 를 적절한 surgical 경로로 라우팅하는 데 쓴다."""
    t = code.strip()
    if t.startswith("else if") or t.startswith("} else if"):
        return "else if"
    if t.startswith("else") or t.startswith("} else"):
        return "else"
    if t.startswith("return"):
        return "return"
    if t.startswith("revert"):
        return "revert"
    if t.startswith("break"):
        return "break"
    if t.startswith("continue"):
        return "continue"
    if t.startswith("catch") or t.startswith("} catch"):
        return "catch"
    return "auto"  # simple/if/while/for = generic SESE


@dataclass
class EditOp:
    """update_code(start, end, code, event, close_before) 에 1:1 대응하는 편집 연산."""
    event: str          # "add" | "modify" | "delete"
    start: int          # 엔진 현재 좌표 startLine
    end: int            # 엔진 현재 좌표 endLine (delete 는 제거 범위; add/modify 는 삽입 범위)
    code: str           # delete 는 삭제 대상 레코드 code(kind 판별용) ; add/modify 는 새 레코드 code
    close_before: bool  # slice 가 `} else/while/catch` 에 붙인 플래그
    kind: str = "auto"  # delete/modify 라우팅용 construct 종류 (kind_of)


class BlockExtentError(ValueError):
    """블록 헤더 레코드의 닫는 `}` 줄을 레코드에서 찾지 못했다 — 호출자는 증분 수술을 거부한다."""


def _is_header(rec: dict) -> bool:
    """블록 헤더 레코드 = slice 가 `헤더 ⏎ }` 2줄로 낸 것 (endLine 은 가짜 `}` 줄이다)."""
    return rec["code"].endswith("\n}")


def _block_body(records: list[dict], h: int) -> range:
    """records[h](블록 헤더)가 여는 블록 **안**의 레코드 인덱스 범위 (2026-11-13).

    닫는 `}` 는 레코드가 아니다. slice 는 단독 `}` 줄만 건너뛰고(빈 줄·주석 줄도 레코드로 낸다) 줄 번호는
    소스와 1:1 이므로, **어떤 레코드도 덮지 않는 줄 = 단독 `}`** 다. 헤더 레코드는 [startLine, endLine-1] 을,
    나머지는 [startLine, endLine] 을 덮는다. `} else {` 처럼 closeBefore 인 레코드는 그 줄에서 블록 하나를
    닫는다(if 의 본문은 else 줄 앞에서 끝난다).
    """
    hdr = records[h]
    starts = {}
    covered: set[int] = set()
    for r in records:
        starts.setdefault(r["startLine"], r)
        last = r["endLine"] - 1 if _is_header(r) else r["endLine"]
        covered.update(range(r["startLine"], max(r["startLine"], last) + 1))
    limit = max(r["endLine"] for r in records) + 1000
    depth, line = 1, hdr["endLine"]
    while line <= limit:
        r = starts.get(line)
        if r is not None and r is not hdr:
            if r.get("closeBefore"):
                depth -= 1
                if depth == 0:
                    break
            if _is_header(r):
                depth += 1
        elif line not in covered:
            depth -= 1
            if depth == 0:
                break
        line += 1
    else:
        raise BlockExtentError(f"line {hdr['startLine']}: 블록 `{hdr['code'].splitlines()[0]}` 의 닫는 `}}` 를 찾지 못함")
    close = line
    k = h + 1
    while k < len(records) and records[k]["startLine"] < close:
        k += 1
    return range(h + 1, k)


def _span(rec: dict) -> int:
    """레코드가 차지하는 라인 수 = endLine - startLine + 1 (>=1)."""
    sp = rec["endLine"] - rec["startLine"] + 1
    return sp if sp >= 1 else 1


def diff_records(prev: list[dict], new: list[dict]) -> list[EditOp]:
    """prev → new 로 가기 위한 순서 있는 EditOp 스트림.

    * 매칭 기준 = 레코드 `code` 텍스트(slice 정규화 결과). 라인번호는 시프트되므로 diff 키로 쓰지 않는다.
    * difflib 의 opcode(equal/insert/delete/replace)를 엔진 좌표 op 로 번역한다.
    * replace 가 1:1(레코드 한 개↔한 개)·동일 span 이면 `modify`(인플레이스), 그 외는 delete+add.
    * 좌→우 순차 적용 전제(각 op 후 엔진이 뒤 라인을 시프트). 반환 순서대로 update_code 호출하면 된다.
    * (2026-11-13) **감싸기·벗기기**: 블록 헤더가 추가·삭제됐는데 그 블록 안 레코드가 텍스트로 "equal" 이면,
      그 레코드는 같은 자리에 남은 것이 아니라 블록 안↔밖으로 **옮겨진** 것이다. equal 로 두면 헤더 삭제가 블록
      전체(본문 포함)를 지우거나 헤더 추가가 기존 문장을 블록 밖에 남긴다(조용한 오분석). 그런 레코드는
      delete(이전 자리) + add(새 자리)로 낸다. 그러면 모든 op 가 연속 범위 하나만 건드린다:
        감싸기 `x;` → `if (c) { x; }`  = delete `x;` · add `if (c) {` · add `x;`
        벗기기 `if (c) { x; }` → `x;`  = delete `x;` · delete `if (c) {` · add `x;`
      블록 범위를 못 찾으면 BlockExtentError (호출자가 거부).
    """
    sm = difflib.SequenceMatcher(
        a=[r["code"] for r in prev],
        b=[r["code"] for r in new],
        autojunk=False,
    )
    opcodes = sm.get_opcodes()

    # 블록 헤더의 추가·삭제로 블록 안↔밖을 옮긴 equal 레코드 (prev 인덱스)
    eq = {}                                   # prev i -> new j (equal)
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            eq.update({i1 + k: j1 + k for k in range(i2 - i1)})
    eq_inv = {j: i for i, j in eq.items()}
    moved: set[int] = set()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        is_modify = (tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1 and _span(prev[i1]) == _span(new[j1]))
        if is_modify:
            continue                          # 헤더 조건만 바뀜 — 본문은 그대로 그 블록 안
        for i in range(i1, i2):               # 삭제된 헤더: 이전 소스에서 그 블록 안의 equal
            if _is_header(prev[i]):
                moved.update(b for b in _block_body(prev, i) if b in eq)
        for j in range(j1, j2):               # 추가된 헤더: 새 소스에서 그 블록 안의 equal
            if _is_header(new[j]):
                moved.update(eq_inv[b] for b in _block_body(new, j) if b in eq_inv)

    ops: list[EditOp] = []

    def _del(r: dict) -> EditOp:
        return EditOp("delete", r["startLine"], r["endLine"], r["code"],
                      bool(r.get("closeBefore", False)), kind_of(r["code"]))

    def _add(r: dict) -> EditOp:
        return EditOp("add", r["startLine"], r["endLine"], r["code"],
                      bool(r.get("closeBefore", False)), kind_of(r["code"]))

    # 좌표 = 레코드의 실제 startLine/endLine (delete=prev 공간, add=new 공간). 호출자가
    #   modify(무이동) → delete(아래부터) → add(위부터) 순으로 적용해 좌표 정합 유지.
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for i in range(i1, i2):
                if i in moved:
                    ops.append(_del(prev[i]))
                    ops.append(_add(new[eq[i]]))
            continue
        elif tag == "delete":
            ops.extend(_del(prev[i]) for i in range(i1, i2))
        elif tag == "insert":
            ops.extend(_add(new[j]) for j in range(j1, j2))
        elif tag == "replace":
            if (i2 - i1) == 1 and (j2 - j1) == 1 and _span(prev[i1]) == _span(new[j1]):
                # 단일 레코드 1:1 교체, 같은 라인 span → 인플레이스 modify (논문: 조건식/할당값 변경)
                rec = new[j1]
                ops.append(EditOp("modify", rec["startLine"], rec["endLine"], rec["code"],
                                  bool(rec.get("closeBefore", False)), kind_of(rec["code"])))
            else:
                # n:m 교체 → delete + add 로 분해 (해당 구간 재구성)
                ops.extend(_del(prev[i]) for i in range(i1, i2))
                ops.extend(_add(new[j]) for j in range(j1, j2))
        else:
            raise ValueError(f"unknown opcode tag {tag!r}")

    return ops
