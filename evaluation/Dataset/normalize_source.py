# -*- coding: utf-8 -*-
"""
데이터셋 소스 정규화 (2026-11-06, 사용자 승인) — 엔진의 입력 전제에 맞추되 의미는 바꾸지 않는다.

  1. 주석을 공백으로 비운다 (줄 번호 유지).
  2. 중괄호 없는 제어문 본문을 `{ }` 로 감싼다. `if (x) return;` → `if (x) {` ⏎ `return;` ⏎ `}`

줄 단위가 아니라 **토큰 + 문장 경계 추적**으로 동작한다. 그래서 다음을 구조적으로 구분한다.
  - 문자열·주석 안의 키워드와 중괄호
  - `else if` 체인 (`else` 를 감싸지 않는다)
  - do-while 꼬리 `} while (c);` (빈 본문 while 이 아니다)
  - dangling else (`else` 는 가장 가까운 `if` 에 붙는다)
  - assembly 블록 안의 Yul `if`/`for` (건드리지 않는다)

삽입으로 줄이 늘어나므로 `line_map`(정규화 줄 → 원본 줄)을 함께 돌려준다.

사용:
  .venv/Scripts/python.exe evaluation/Dataset/normalize_source.py in.sol -o out.sol
"""
import argparse
import json
import os
import re
from dataclasses import dataclass, field

_TOK = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d[\w.]*|\S")
_CLOSE = {"(": ")", "[": "]", "{": "}"}


def read_source(path: str) -> str:
    """Windows 260자 경로 제한을 피해 읽는다 (DAppSCAN 타깃 29개가 이 제한에 걸린다)."""
    p = os.path.abspath(path)
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        return f.read().lstrip("﻿")


def blank_comments(src: str) -> tuple[str, str]:
    """(주석만 비운 소스, 주석·문자열 내용을 비운 소스). 둘 다 길이와 줄 구조가 입력과 같다."""
    code, mask = list(src), list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                code[i] = mask[i] = " "
                i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            while i < j:
                if src[i] != "\n":
                    code[i] = mask[i] = " "
                i += 1
        elif c in "\"'":
            i += 1
            while i < n and src[i] != c and src[i] != "\n":
                if src[i] == "\\" and i + 1 < n:
                    mask[i] = " "
                    i += 1
                mask[i] = " "
                i += 1
            i += 1
        else:
            i += 1
    return "".join(code), "".join(mask)


class _Tokens:
    def __init__(self, masked: str):
        self.items = [(m.group(), m.start(), m.end()) for m in _TOK.finditer(masked)]
        self.n = len(self.items)

    def text(self, i: int) -> str:
        return self.items[i][0] if 0 <= i < self.n else ""

    def match(self, i: int) -> int:
        """여는 괄호 토큰 i 의 짝 인덱스."""
        opener, closer, depth = self.text(i), _CLOSE[self.text(i)], 0
        while i < self.n:
            t = self.text(i)
            if t == opener:
                depth += 1
            elif t == closer:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        raise ValueError("괄호 짝 없음")

    def _scan_to(self, i: int, tok: str) -> int:
        while self.text(i) != tok:
            i += 1
            if i >= self.n:
                raise ValueError(f"`{tok}` 없음")
        return i

    def statement_end(self, i: int) -> int:
        """토큰 i 에서 시작하는 문장의 끝 다음 인덱스."""
        t = self.text(i)
        if t == "{":
            return self.match(i) + 1
        if t == "unchecked" and self.text(i + 1) == "{":
            return self.match(i + 1) + 1
        if t == "if":
            j = self.statement_end(self.match(i + 1) + 1)
            return self.statement_end(j + 1) if self.text(j) == "else" else j
        if t in ("for", "while"):
            return self.statement_end(self.match(i + 1) + 1)
        if t == "do":
            j = self.statement_end(i + 1)
            if self.text(j) != "while":
                raise ValueError("do 뒤에 while 없음")
            return self.match(j + 1) + 2
        if t == "assembly":
            return self.match(self._scan_to(i, "{")) + 1
        if t == "try":
            return self._try_end(i)
        j = i
        while True:
            if j >= self.n:
                raise ValueError("문장 끝 없음")
            x = self.text(j)
            if x == ";":
                return j + 1
            if x in _CLOSE:
                j = self.match(j) + 1
            elif x == "}":
                raise ValueError("문장 끝 없음")
            else:
                j += 1

    def _try_end(self, i: int) -> int:
        j = i + 1
        while True:                                  # 호출 옵션 `{value: v}(...)` 은 뒤에 `(` 가 온다
            x = self.text(j)
            if x in ("(", "["):
                j = self.match(j) + 1
            elif x == "{":
                k = self.match(j)
                j = k + 1
                if self.text(j) != "(":
                    break
            else:
                j += 1
                if j >= self.n:
                    raise ValueError("try 본문 없음")
        while self.text(j) == "catch":
            j = self.match(self._scan_to(j, "{")) + 1
        return j


@dataclass
class _Body:
    kw: str
    header_end: int       # 헤더 끝 문자 오프셋
    start: int            # 본문 시작 문자 오프셋
    end: int              # 본문 끝 문자 오프셋
    next_start: int       # 뒤따르는 else/while 의 시작 오프셋, 없으면 -1


@dataclass
class Normalized:
    text: str
    line_map: dict = field(default_factory=dict)   # {정규화 줄: 원본 줄}
    braces_inserted: int = 0
    chains_joined: int = 0
    lines_split: int = 0


def find_braceless(masked: str) -> tuple[list[_Body], list[tuple[int, int]]]:
    """(중괄호 없는 본문들, `else` ⏎ `if` 처럼 줄바꿈으로 벌어진 체인의 간격들)."""
    tk = _Tokens(masked)
    bodies, gaps = [], []
    i = 0
    while i < tk.n:
        t = tk.text(i)
        if t == "assembly":
            j = i
            while j < tk.n and tk.text(j) not in ("{", ";"):
                j += 1
            if tk.text(j) == "{":
                i = tk.match(j) + 1
                continue
        if t not in ("if", "for", "while", "else", "do") or tk.text(i - 1) == ".":
            i += 1
            continue
        if t in ("if", "for", "while"):
            if tk.text(i + 1) != "(":
                i += 1
                continue
            head = tk.match(i + 1)
            body = head + 1
            if t == "while" and tk.text(body) == ";":     # do-while 꼬리
                i += 1
                continue
        else:
            head, body = i, i + 1
            if t == "else" and tk.text(body) == "if":     # else-if 체인
                if "\n" in masked[tk.items[head][2]:tk.items[body][1]]:
                    gaps.append((tk.items[head][2], tk.items[body][1]))
                i += 1
                continue
        if tk.text(body) != "{":
            end = tk.statement_end(body)
            nx = tk.text(end)
            follows = (t == "if" and nx == "else") or (t == "do" and nx == "while")
            bodies.append(_Body(t, tk.items[head][2], tk.items[body][1], tk.items[end - 1][2],
                                tk.items[end][1] if follows else -1))
        i += 1
    return bodies, gaps


def _indent(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    return text[start:start + len(text[start:]) - len(text[start:].lstrip(" \t"))]


_SPLIT_OK_AFTER_CLOSE = ("else", "catch", "while")    # `} else` / `} catch` / do-while 꼬리 `} while (c);`


def _is_word(t: str) -> bool:
    return bool(t) and (t[0].isalnum() or t[0] in "_$")


def _yul_single_statement(tk: "_Tokens", a: int, b: int) -> bool:
    """토큰 [a, b) 가 Yul 문장 **하나 이하**인가. 보수적으로 판정한다 — 확신이 없으면 False.
    Yul 에는 `;` 가 없으므로 괄호 깊이 0 에서 "앞 문장이 끝났는데 이름이 또 나오는" 지점을 문장 경계로 센다.
    `for {..} cond {..} {..}` · `switch` 처럼 경계로 오인될 수 있는 형태는 여럿으로 세어 펼치지 않는다."""
    depth, prev = 0, ""
    for k in range(a, b):
        t = tk.text(k)
        if t in "([{":
            depth += 1
        elif t in ")]}":
            depth -= 1
        elif depth == 0 and _is_word(t) and prev and (prev in (")", "}") or (_is_word(prev) and prev != "let")):
            return False
        if depth == 0 or t in ")]}":
            prev = t
    return True


def find_one_line_blocks(masked: str) -> list[tuple[int, int, str]]:
    """한 줄에 붙은 블록·문장을 펼칠 자리 [(시작, 끝, "\\n"+들여쓰기)]. [시작, 끝) 은 두 토큰 사이 공백이다.

    슬라이서의 입력 전제(입력 1개 = interactive 규칙 하나)를 기준으로 한다. 블록 헤더 `… {` 는 줄 끝에서 끝나고,
    본문 문장은 각자 한 줄이며, 닫는 `}` 는 줄 처음에 오고 뒤에는 `else`/`catch`/do-while 꼬리만 온다.
      - `{` 뒤에 같은 줄 내용 → `{` 뒤에서 줄을 나눈다. 빈 블록 `{}` 뒤에 내용이 오면 `{` 와 `}` 사이를 나눈다.
      - `}` 앞에 같은 줄 내용 → `}` 앞에서 나눈다. `}` 뒤에 허용되지 않는 내용 → `}` 뒤에서 나눈다.
      - 블록 안 `;` 뒤에 같은 줄 문장 → `;` 뒤에서 나눈다.
    건드리지 않는 중괄호: 괄호 `( [` 안(명명 인자 `f({a: 1})`), 호출 옵션 `{value: v}`, `import {…}`·`using {…}`,
    assembly 안쪽의 Yul 블록. assembly 의 바깥 `{ }` 는 같은 줄 내용이 Yul 문장 하나일 때만 나눈다."""
    tk = _Tokens(masked)
    pos = lambda k: tk.items[k][1]                               # noqa: E731
    end = lambda k: tk.items[k][2]                               # noqa: E731
    same_line = lambda a, b: "\n" not in masked[a:b]             # noqa: E731

    out = []
    forced = {}           # {토큰 인덱스: 그 토큰 앞에서 줄을 나눴을 때의 들여쓰기}
    stack = []            # (종류, 여는 토큰 인덱스, 닫는 토큰 인덱스, 그 줄의 들여쓰기)
    paren, line_ind = 0, ""

    def split_before(k, ind):
        out.append((end(k - 1), pos(k), "\n" + ind))
        forced[k] = ind

    for i in range(tk.n):
        t = tk.text(i)
        if i in forced:
            line_ind = forced[i]
        elif i == 0 or not same_line(end(i - 1), pos(i)):
            line_ind = _indent(masked, pos(i))
        if t in ("(", "["):
            paren += 1
            continue
        if t in (")", "]"):
            paren -= 1
            continue
        top = stack[-1][0] if stack else None
        if t == "{":
            j = tk.match(i)
            if paren > 0 or top in ("asm", "inner"):
                kind = "inner"
            elif tk.text(i - 1) in ("import", "using") or (
                    _is_word(tk.text(i + 1)) and tk.text(i + 2) == ":" and tk.text(i + 3) != "="):
                kind = "inner"                                  # 호출 옵션 / import·using 목록
            else:
                k = i - 1
                while k >= 0 and tk.text(k) not in (";", "{", "}"):
                    k -= 1
                kind = "asm" if tk.text(k + 1) == "assembly" else "block"
            stack.append((kind, i, j, line_ind))
            if kind == "inner" or j + 1 > tk.n:
                continue
            if j == i + 1:                                      # 빈 블록 `{}`
                if same_line(end(i), pos(j)) and j + 1 < tk.n and same_line(end(j), pos(j + 1)) \
                        and tk.text(j + 1) not in (";", ")"):
                    split_before(j, line_ind)
                continue
            if same_line(end(i), pos(i + 1)):
                if kind == "asm":
                    k = i + 1
                    while k < j and same_line(end(i), pos(k)):
                        k += 1
                    if not _yul_single_statement(tk, i + 1, k):
                        continue
                split_before(i + 1, line_ind + "    ")
            continue
        if t == "}":
            kind, o, _, ind = stack.pop() if stack else ("block", -1, i, "")
            if kind == "inner":
                continue
            if o != i - 1 and i not in forced and same_line(end(i - 1), pos(i)):
                ok = True
                if kind == "asm":
                    k = i - 1
                    while k > o and same_line(pos(k), pos(i)):
                        k -= 1
                    ok = _yul_single_statement(tk, k + 1, i)
                if ok:
                    split_before(i, ind)
                    line_ind = ind
            if i + 1 < tk.n and same_line(end(i), pos(i + 1)) and tk.text(i + 1) not in _SPLIT_OK_AFTER_CLOSE + (";",):
                split_before(i + 1, ind)
            continue
        if t == ";" and paren == 0 and top in (None, "block"):
            if i + 1 < tk.n and same_line(end(i), pos(i + 1)) and tk.text(i + 1) != "}":
                split_before(i + 1, stack[-1][3] + "    " if stack else "")
    return out


def _apply(code: str, edits: list) -> tuple[str, dict]:
    """edits = [(시작, 정렬키, 끝, 삽입 텍스트)] 를 적용한다. (결과, {결과 줄: 입력 줄})"""
    out, line_map, cur_line, src_line, pos = [], {}, 1, 1, 0

    def copy(upto):
        nonlocal pos, cur_line, src_line
        for ch in code[pos:upto]:
            if ch == "\n":
                line_map.setdefault(cur_line, src_line)
                cur_line += 1
                src_line += 1
            elif not ch.isspace():
                line_map.setdefault(cur_line, src_line)
            out.append(ch)
        pos = max(pos, upto)

    for start, _, stop, text in sorted(edits, key=lambda e: (e[0], e[1])):
        copy(start)
        src_line += code.count("\n", pos, stop)
        pos = max(pos, stop)
        for ch in text:
            if ch == "\n":
                line_map.setdefault(cur_line, src_line)
                cur_line += 1
            out.append(ch)
    copy(len(code))
    line_map.setdefault(cur_line, src_line)
    return "\n".join(ln.rstrip() for ln in "".join(out).split("\n")), line_map


def normalize(src: str) -> Normalized:
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    code, masked = blank_comments(src)
    bodies, gaps = find_braceless(masked)

    edits = []            # (시작, 정렬키, 끝, 삽입 텍스트)
    stack = []            # 바깥 본문의 (끝, 들여쓰기, 헤더 줄 시작)
    for b in sorted(bodies, key=lambda x: x.header_end):
        while stack and stack[-1][0] <= b.header_end:
            stack.pop()
        line_start = code.rfind("\n", 0, b.header_end) + 1
        ind = _indent(code, b.header_end)
        if stack and stack[-1][2] == line_start:      # 바깥 헤더와 같은 줄이면 한 단계 더 들인다
            ind = stack[-1][1] + "    "
        edits.append((b.header_end, 0, b.start, " {\n" + ind + "    "))
        if b.next_start >= 0:
            edits.append((b.end, -b.start, b.next_start, "\n" + ind + "} "))
        else:
            edits.append((b.end, -b.start, b.end, "\n" + ind + "}"))
        stack.append((b.end, ind, line_start))
    for a, z in gaps:
        edits.append((a, 0, z, " "))
    text1, map1 = _apply(code, edits)

    # 3. (2026-11-13) 한 줄 블록·한 줄 다중 문장을 펼친다 — 1·2 의 결과 위에서 따로 돈다(삽입끼리 섞이지 않게).
    splits = find_one_line_blocks(blank_comments(text1)[1])
    text2, map2 = _apply(text1, [(a, 0, z, s) for a, z, s in splits])
    line_map = {k: map1.get(v, v) for k, v in map2.items()}
    return Normalized(text2, line_map, len(bodies), len(gaps), len(splits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    r = normalize(read_source(a.source))
    with open(a.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(r.text)
    with open(a.output + ".linemap.json", "w", encoding="utf-8") as f:
        json.dump(r.line_map, f)
    print(f"{a.source}: 중괄호 {r.braces_inserted}건 삽입, 체인 {r.chains_joined}건 결합")


if __name__ == "__main__":
    main()
