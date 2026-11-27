"""@IReturn 키 규칙 (2026-11-27 사용자 결정) — 슬롯 도출(engine_api)·입력 파싱(visitor)·실행 조회(Evaluation)가 같은 함수를 쓴다.

입력의 뜻 = "키에 맞는 외부 view 호출들의 반환값이 이 범위". 외부 코드가 없으므로 **인자는 키에 넣지 않는다**.

키 = <수신자>.<f>()<반환값 접근>
  * 수신자 원문에 메서드 호출이 없으면(식별자·멤버·index·그 캐스트) → **호출 지점 원문 그대로**
      `token.balanceOf()`, `s.token.f()`, `pools[pid].f()`, `IERC20(addr).f()`, `IERC20(s.addr).f()`
    (원문 = 호출 지점의 이름 — callee 안이면 callee 의 이름. 타깃 이름으로 옮기지 않는다)
  * 수신자에 메서드 호출이 섞여 있으면(`IFace(x.g()).f()`, `getPool().f()`, `pools[id()].f()`) → `IFace()`
      — 그 인터페이스의 f 호출 중 원문 키로 이름 붙지 않는 것 **전부**에 적용된다.
  * 반환값 접근: `.m`(struct 멤버) · `[k]`(튜플 원소) · `.g()`(이어지는 호출)
키 문자열에는 공백이 없다(입력 파서의 getText 와 같은 모양).
"""
from __future__ import annotations

import re

from Domain.IR import Expression

_ELEMENTARY = re.compile(r"address|payable|bool|string|bytes\d*|u?int\d*")


def _is_index(e):  return getattr(e, "context", None) == "IndexAccessContext"
def _is_member(e): return getattr(e, "context", None) == "MemberAccessContext"
def _is_call(e):   return getattr(e, "function", None) is not None
def _member_base(e): return getattr(e, "base", None)


def _is_type_name(name, type_names) -> bool:
    return isinstance(name, str) and (name in type_names or bool(_ELEMENTARY.fullmatch(name)))


def is_cast(e, type_names) -> bool:
    """`T(x)` — 형변환(TypeConversion, 또는 선언 전에 파싱돼 함수 호출 모양이 된 `T(x)`)."""
    if getattr(e, "context", None) == "TypeConversion":
        return True
    fn = getattr(e, "function", None)
    return (fn is not None and getattr(fn, "context", None) == "IdentifierExpContext"
            and _is_type_name(getattr(fn, "identifier", None), type_names))


def has_method_call(e, type_names) -> bool:
    """식 안에 형변환이 아닌 호출이 있는가."""
    if not isinstance(e, Expression):
        return False
    if _is_call(e) and not is_cast(e, type_names):
        return True
    for a in ("base", "index", "expression", "function", "left", "right", "operand"):
        c = getattr(e, a, None)
        if isinstance(c, Expression) and has_method_call(c, type_names):
            return True
    for c in (getattr(e, "arguments", None) or []):
        if has_method_call(c, type_names):
            return True
    return False


def label(e) -> str:
    """원문 키(공백 없음). 메서드 호출은 인자를 버리고 `f()`, 캐스트는 인자를 둔다 `T(x)`."""
    if not isinstance(e, Expression):
        return re.sub(r"\s+", "", str(e))
    if _is_index(e):
        return f"{label(e.base)}[{label(e.index)}]"
    if _is_member(e):
        return f"{label(_member_base(e))}.{e.member}"
    if getattr(e, "context", None) == "TypeConversion":
        return f"{e.typeName}({label(e.expression) if getattr(e, 'expression', None) is not None else ''})"
    if _is_call(e):
        fn = e.function
        if _is_member(fn):
            return f"{label(fn)}()"
        args = ",".join(label(a) for a in (getattr(e, "arguments", None) or []))
        return f"{label(fn)}({args})"
    if getattr(e, "identifier", None) is not None:
        return str(e.identifier)
    if getattr(e, "literal", None) is not None:
        return re.sub(r"\s+", "", str(e.literal))
    return re.sub(r"\s+", "", str(e))


def receiver_key(receiver, iface: str, type_names) -> str:
    """수신자 → 키의 수신자 부분."""
    if has_method_call(receiver, type_names):
        return f"{iface}()"
    return label(receiver)


def call_key(call_expr, iface: str, type_names) -> str:
    """외부 호출 `recv.f(...)` → `<수신자>.f()`."""
    fn = call_expr.function
    return f"{receiver_key(_member_base(fn), iface, type_names)}.{fn.member}()"


def split_key(key: str):
    """키 문자열 → (수신자, f, 반환값 접근 텍스트). 괄호 균형으로 수신자 끝의 `.f()` 를 찾는다. 못 읽으면 None."""
    k = re.sub(r"\s+", "", key or "")
    depth, i = 0, 0
    # 수신자 = 첫 `.이름(` 가 나오기 전까지(괄호·대괄호 밖에서)
    while i < len(k):
        c = k[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "." and depth == 0:
            m = re.match(r"\.([A-Za-z_$][\w$]*)\(\)", k[i:])
            if m:
                return k[:i], m.group(1), k[i + m.end():]
        i += 1
    return None
