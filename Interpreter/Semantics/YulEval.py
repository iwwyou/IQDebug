# Interpreter/Semantics/YulEval.py
"""Yul(inline assembly) 전용 평가 (2026-10-23, Yul P0-a).

Yul 은 타입 없는 **256-bit EVM word** 언어다. 예전엔 Yul 연산을 Solidity 연산(checked 산술)으로 낮춰 평가해
`div(a,0)` · `sub(0,1)` 이 ⊥(경로 소멸), `slt` 가 부호 없는 비교, `shl(s, v)` 인자 순서가 뒤바뀌었다.
여기서는 builtin 마다 EVM 의미를 따른다:
  · 산술은 mod 2^256 (wrap), `div/mod/sdiv/smod` 의 0 나누기 = 0, 비교·iszero 는 0/1 word
  · 부호 있는 연산(sdiv/smod/slt/sgt/sar/signextend)은 2의 보수 해석
  · 미지원 반환 builtin = word ⊤, 부작용은 효과 표(`_EFFECTS`)대로 havoc
값 도메인은 `UnsignedIntegerInterval(·,·,256)` 하나다. 구간 연산이 정확히 표현되지 않으면 ⊤ (sound).

Solidity 변수와의 경계:
  · Yul 이 Solidity 변수를 읽을 때(`word_of`): 256-bit 정수는 그대로(int256 은 2의 보수),
    **256 비트보다 좁은 타입은 ⊤** — Solidity 는 inline assembly 에서 좁은 타입의 상위 비트를 정리해 두지 않는다
    (Solidity docs, "Inline Assembly › Conventions": cannot make any assumptions about bits not part of the encoding).
  · Yul 이 Solidity 변수에 쓸 때(`cleanup`): Solidity 쪽 값 = 대상 타입의 cleanup(하위 N 비트 / 부호 확장 / 0·1 정규화).
"""
from __future__ import annotations

from Domain.Interval import UnsignedIntegerInterval, IntegerInterval, BoolInterval
from Domain.AddressSet import AddressSet
from Domain.BytesSet import BytesSet
from Domain.IR import Expression
from Domain.Variable import Variables, StructVariable, ArrayVariable, MappingVariable, EnumVariable

W = 256
MOD = 1 << W
MAX = MOD - 1
HALF = 1 << (W - 1)


def word(lo, hi=None) -> UnsignedIntegerInterval:
    return UnsignedIntegerInterval(lo, lo if hi is None else hi, W)


def top() -> UnsignedIntegerInterval:
    return UnsignedIntegerInterval.top(W)


def _wrap(lo, hi) -> UnsignedIntegerInterval:
    """정수 구간 [lo, hi] 를 mod 2^256 로. 한 주기 안에 있으면 정확, 걸치면 ⊤."""
    if hi - lo >= MOD:
        return top()
    k = lo // MOD
    if hi // MOD == k:
        return word(lo - k * MOD, hi - k * MOD)
    return top()


def _signed(w: UnsignedIntegerInterval):
    """word 구간의 2의 보수 해석 → (lo, hi) 또는 None(부호 경계를 걸침)."""
    if w.max_value < HALF:
        return w.min_value, w.max_value
    if w.min_value >= HALF:
        return w.min_value - MOD, w.max_value - MOD
    return None


def _single(w):
    return w.min_value if w.min_value == w.max_value else None


def _cmp(always, never) -> UnsignedIntegerInterval:
    if always:
        return word(1)
    if never:
        return word(0)
    return word(0, 1)


# ── builtin 분류 ─────────────────────────────────────────────────────────────
#   ENV: Solidity 전역으로 값이 있는 것 (없으면 ⊤)
_ENV_GLOBALS = {
    "callvalue": "msg.value", "timestamp": "block.timestamp", "number": "block.number",
    "chainid": "block.chainid", "basefee": "block.basefee", "gasprice": "tx.gasprice",
    "gaslimit": "block.gaslimit", "difficulty": "block.difficulty", "prevrandao": "block.prevrandao",
}
#   값만 돌려주고 상태 효과가 없는 것 → ⊤ word (메모리·storage·환경 읽기 포함 — 읽기는 상태를 바꾸지 않는다)
_VALUE_TOP = {
    "gas", "caller", "origin", "address", "coinbase", "balance", "selfbalance", "extcodesize", "extcodehash",
    "returndatasize", "calldatasize", "codesize", "msize", "blockhash", "blobhash", "blobbasefee",
    "mload", "sload", "tload", "keccak256", "calldataload", "pc", "datasize", "dataoffset",
}
#   메모리에 쓰는 것: (목적지 인자 위치, 크기 인자 위치 또는 고정 크기)
_MEM_WRITES = {
    "mstore": (0, 32), "mstore8": (0, 1), "calldatacopy": (0, "a2"), "codecopy": (0, "a2"),
    "returndatacopy": (0, "a2"), "extcodecopy": (1, "a3"), "mcopy": (0, "a2"), "datacopy": (0, "a2"),
}
#   외부 호출: (출력 메모리 위치 인자, 출력 크기 인자, 호출자 storage 를 바꿀 수 있는가)
_CALLS = {
    "call": (5, 6, False), "staticcall": (4, 5, False), "delegatecall": (4, 5, True), "callcode": (5, 6, True),
}
_NO_EFFECT = {"log0", "log1", "log2", "log3", "log4", "pop"}
#   Solidity 의 메모리 객체가 놓이지 않는 영역(scratch 0x00-0x3f + free memory pointer 0x40-0x5f).
#   여기에만 쓰면 Solidity 변수는 바뀌지 않는다. (zero slot 0x60 은 빈 배열 등이 가리키므로 포함하지 않는다)
_RESERVED_END = 0x60


class YulEvaluator:
    def __init__(self, ev):
        self.ev = ev            # Evaluation

    # ── Solidity 값 ↔ word ───────────────────────────────────────────────────
    def word_of(self, expr, variables) -> UnsignedIntegerInterval:
        """Yul 식 하나의 word 값."""
        ctx = getattr(expr, "context", None)
        if ctx == "YulBuiltinContext":
            r = self.builtin(expr, variables)
            if r is None:
                raise ValueError(f"Yul builtin '{expr.identifier}' has no return value but is used as a value")
            return r
        if ctx == "LiteralExpContext":
            v = self.ev.evaluate_expression(expr, variables, None, None)
            if isinstance(v, UnsignedIntegerInterval):
                return word(v.min_value, v.max_value)
            raise ValueError(f"Yul literal {expr.literal!r} is not a number word (string literal 미지원)")
        if ctx == "IdentifierExpContext":
            var = variables.get(expr.identifier)
            if var is None:
                raise ValueError(f"Yul identifier '{expr.identifier}' is not declared")
            return self._var_word(var)
        raise ValueError(f"Yul expression context '{ctx}' not supported")

    def _var_word(self, var) -> UnsignedIntegerInterval:
        """Solidity 변수의 Yul 관점 word. 256 비트 정수만 값을 그대로 쓰고, 좁은 타입·주소·bytes·bool·참조 타입은 ⊤."""
        if isinstance(var, (StructVariable, ArrayVariable, MappingVariable, EnumVariable)):
            return top()            # 참조 타입 = 메모리/storage 위치(포인터) — 위치를 모델링하지 않는다
        v = getattr(var, "value", None)
        if isinstance(v, UnsignedIntegerInterval) and v.type_length == W:
            return word(v.min_value, v.max_value) if not v.is_bottom() else v
        if isinstance(v, IntegerInterval) and v.type_length == W and not v.is_bottom():
            if v.min_value >= 0:
                return word(v.min_value, v.max_value)
            if v.max_value < 0:
                return word(v.min_value + MOD, v.max_value + MOD)
            return top()
        return top()

    def cleanup(self, w: UnsignedIntegerInterval, var):
        """Yul 이 Solidity 변수 var 에 word w 를 대입했을 때 Solidity 쪽 값."""
        if w.is_bottom():
            raise ValueError("Yul assignment of ⊥ word")
        if isinstance(var, (StructVariable, ArrayVariable, MappingVariable)):
            raise ValueError(f"Yul assignment to reference-type variable '{var.identifier}' "
                             f"(메모리/storage 포인터 재지정) is not supported yet")
        if isinstance(var, EnumVariable):
            raise ValueError(f"Yul assignment to enum variable '{var.identifier}' is not supported "
                             f"(enum 값 도메인에 ⊤ 표현이 없다)")
        t = getattr(var, "typeInfo", None)
        cat = getattr(t, "typeCategory", None)
        et = getattr(t, "elementaryTypeName", None) or ""
        s = _single(w)
        if cat == "interface" or et.startswith("address"):
            a = AddressSet(ids={0}) if (s is not None and s % (1 << 160) == 0) else AddressSet.top()
            if cat == "interface":
                a._cast_interface = getattr(t, "interfaceName", None)
            return a
        if et == "bool":
            if w.max_value == 0:
                return BoolInterval(0, 0)
            if w.min_value > 0:
                return BoolInterval(1, 1)
            return BoolInterval.top()
        if et.startswith("uint"):
            n = t.intTypeLength or 256
            if w.max_value < (1 << n):
                return UnsignedIntegerInterval(w.min_value, w.max_value, n)
            if s is not None:
                x = s % (1 << n)
                return UnsignedIntegerInterval(x, x, n)
            return UnsignedIntegerInterval.top(n)
        if et.startswith("int"):
            n = t.intTypeLength or 256
            half = 1 << (n - 1)
            if s is not None:
                x = s % (1 << n)
                x = x - (1 << n) if x >= half else x
                return IntegerInterval(x, x, n)
            if w.max_value < half:
                return IntegerInterval(w.min_value, w.max_value, n)
            if w.min_value >= MOD - half:
                return IntegerInterval(w.min_value - MOD, w.max_value - MOD, n)
            return IntegerInterval.top(n)
        if et.startswith("bytes") and et != "bytes":
            return BytesSet.top(int(et[5:]))
        if et in ("bytes", "string"):
            return f"symbol_yul_{var.identifier}"      # 동적 bytes/string 내용은 모델링하지 않는다(심볼)
        raise ValueError(f"Yul assignment to variable '{var.identifier}' of type {et or cat} is not supported")

    # ── builtin ──────────────────────────────────────────────────────────────
    def builtin(self, expr, variables):
        name = expr.identifier
        args = expr.arguments or []
        if name in ("return", "stop", "selfdestruct", "tstore", "revert", "invalid"):
            raise ValueError(f"Yul '{name}' inside an expression is not supported")
        if name in _ENV_GLOBALS:
            gv = variables.get(_ENV_GLOBALS[name])
            return self._var_word(gv) if gv is not None else top()
        a = [self.word_of(x, variables) for x in args]          # 인자는 왼쪽부터 평가 (부작용 포함)
        f = getattr(self, f"_op_{name}", None)
        if f is not None:
            return f(*a)
        if name in _VALUE_TOP:
            return top()
        if name in _MEM_WRITES:
            dst_i, size = _MEM_WRITES[name]
            n = size if isinstance(size, int) else a[int(size[1:])]
            self._havoc_memory(a[dst_i], n, variables)
            return None
        if name in _CALLS:
            out_i, outsize_i, writes_storage = _CALLS[name]
            self._havoc_memory(a[out_i], a[outsize_i], variables)
            if writes_storage:
                self._havoc_storage(variables)
            return word(0, 1)
        if name in ("create", "create2"):
            return top()
        if name == "sstore":
            self._havoc_storage(variables)
            return None
        if name in _NO_EFFECT:
            return None
        raise ValueError(f"Yul function '{name}' is not supported "
                         f"(builtin 효과 표에 없음 — 사용자 정의 Yul 함수 호출은 블록 단위 해석 단계에서 지원)")

    # 산술 (mod 2^256)
    def _op_add(self, x, y):
        return _wrap(x.min_value + y.min_value, x.max_value + y.max_value)

    def _op_sub(self, x, y):
        return _wrap(x.min_value - y.max_value, x.max_value - y.min_value)

    def _op_mul(self, x, y):
        return _wrap(x.min_value * y.min_value, x.max_value * y.max_value)

    def _op_div(self, x, y):
        parts = []
        if y.min_value == 0:
            parts.append(word(0))
        lo_d = max(y.min_value, 1)
        if y.max_value >= 1:
            parts.append(word(x.min_value // y.max_value, x.max_value // lo_d))
        return self._join(parts)

    def _op_mod(self, x, y):
        parts = []
        if y.min_value == 0:
            parts.append(word(0))
        if y.max_value >= 1:
            lo_d = max(y.min_value, 1)
            if x.max_value < lo_d:
                parts.append(word(x.min_value, x.max_value))
            else:
                parts.append(word(0, min(x.max_value, y.max_value - 1)))
        return self._join(parts)

    def _op_exp(self, b, e):
        if _single(b) is not None and _single(e) is not None:
            return word(pow(b.min_value, e.min_value, MOD))
        if b.max_value.bit_length() * e.max_value <= W:
            corners = [b.min_value ** e.min_value, b.min_value ** e.max_value,
                       b.max_value ** e.min_value, b.max_value ** e.max_value]
            return word(min(corners), max(corners))
        return top()

    def _op_addmod(self, x, y, n):
        return self._modn(x, y, n, lambda p, q: p + q)

    def _op_mulmod(self, x, y, n):
        return self._modn(x, y, n, lambda p, q: p * q)

    def _modn(self, x, y, n, f):
        if all(_single(v) is not None for v in (x, y, n)):
            return word(0 if n.min_value == 0 else f(x.min_value, y.min_value) % n.min_value)
        lo, hi = f(x.min_value, y.min_value), f(x.max_value, y.max_value)   # (무한 정밀도 — EVM addmod/mulmod 의미)
        if n.min_value > 0 and hi < n.min_value:          # 나머지 연산이 아무것도 바꾸지 않는다
            return word(lo, hi)
        parts = []
        if n.min_value == 0:
            parts.append(word(0))
        if n.max_value >= 1:
            parts.append(word(0, n.max_value - 1))
        return self._join(parts)

    # 부호 있는 연산 (2의 보수)
    def _op_sdiv(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            a, b = (sx - MOD if sx >= HALF else sx), (sy - MOD if sy >= HALF else sy)
            if b == 0:
                return word(0)
            q = abs(a) // abs(b)
            q = -q if (a < 0) != (b < 0) else q
            return word(q % MOD)
        return top()

    def _op_smod(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            a, b = (sx - MOD if sx >= HALF else sx), (sy - MOD if sy >= HALF else sy)
            if b == 0:
                return word(0)
            r = abs(a) % abs(b)
            return word((-r if a < 0 else r) % MOD)
        return top()

    def _op_slt(self, x, y):
        a, b = _signed(x), _signed(y)
        if a is None or b is None:
            return word(0, 1)
        return _cmp(a[1] < b[0], a[0] >= b[1])

    def _op_sgt(self, x, y):
        return self._op_slt(y, x)

    def _op_sar(self, s, v):
        ss, sv = _single(s), _signed(v)
        if ss is not None and sv is not None and (sv[0] == sv[1]):
            return word((sv[0] >> min(ss, 300)) % MOD)
        if sv is not None and sv[0] >= 0:
            return self._op_shr(s, v)
        return top()

    def _op_signextend(self, b, x):
        sb, sx = _single(b), _single(x)
        if sb is not None and sx is not None:
            if sb >= 31:
                return word(sx)
            bits = 8 * (sb + 1)
            low = sx % (1 << bits)
            return word((low - (1 << bits)) % MOD if low >= (1 << (bits - 1)) else low)
        return top()

    # 비교 (unsigned)
    def _op_lt(self, x, y):
        return _cmp(x.max_value < y.min_value, x.min_value >= y.max_value)

    def _op_gt(self, x, y):
        return self._op_lt(y, x)

    def _op_eq(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            return word(1 if sx == sy else 0)
        disjoint = x.max_value < y.min_value or y.max_value < x.min_value
        return word(0) if disjoint else word(0, 1)

    def _op_iszero(self, x):
        return _cmp(x.max_value == 0, x.min_value > 0)

    # 비트 연산
    def _op_not(self, x):
        return word(MAX - x.max_value, MAX - x.min_value)

    def _op_and(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            return word(sx & sy)
        return word(0, min(x.max_value, y.max_value))

    def _op_or(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            return word(sx | sy)
        n = max(x.max_value.bit_length(), y.max_value.bit_length())
        return word(max(x.min_value, y.min_value), (1 << n) - 1)

    def _op_xor(self, x, y):
        sx, sy = _single(x), _single(y)
        if sx is not None and sy is not None:
            return word(sx ^ sy)
        n = max(x.max_value.bit_length(), y.max_value.bit_length())
        return word(0, (1 << n) - 1)

    def _op_shl(self, s, v):          # shl(s, v) = v << s  (인자 순서 주의: 시프트 양이 먼저)
        ss = _single(s)
        if ss is None:
            return top() if v.max_value > 0 else word(0)
        if ss >= W:
            return word(0)
        return _wrap(v.min_value << ss, v.max_value << ss)

    def _op_shr(self, s, v):          # shr(s, v) = v >> s
        if s.min_value >= W:
            return word(0)
        return word(v.min_value >> min(s.max_value, W), v.max_value >> s.min_value)

    def _op_byte(self, n, x):
        sn, sx = _single(n), _single(x)
        if sn is not None and sx is not None:
            return word(0 if sn >= 32 else (sx >> (8 * (31 - sn))) & 0xFF)
        return word(0, 255)

    @staticmethod
    def _join(parts):
        out = parts[0]
        for p in parts[1:]:
            out = out.join(p)
        return out

    # ── 효과 ─────────────────────────────────────────────────────────────────
    def _havoc_memory(self, dst, size, variables):
        """메모리 쓰기 — 목적지·크기가 상수이고 Solidity 객체가 없는 영역([0, 0x60))이면 효과 없음.
        그 밖에는 메모리 객체 전체를 ⊤ (주소를 모델링하지 않으므로 어느 객체와 겹칠지 모른다).
        메모리 객체 = 이 프레임의 상태변수가 아닌 참조 타입 변수(struct/array). 동적 bytes/string 은 원래 심볼이다."""
        sd = _single(dst)
        n = size if isinstance(size, int) else _single(size)
        if sd is not None and n is not None and sd + n <= _RESERVED_END:
            return
        owner = self.ev._executing_contract_cfg()
        for name, v in list(variables.items()):
            if getattr(v, "scope", None) == "state" or "." in name:
                continue
            if isinstance(v, (StructVariable, ArrayVariable)):
                self.ev.engine._havoc(v, owner)

    def _havoc_storage(self, variables):
        """storage 쓰기(슬롯 미상) — 변경 가능한 상태변수 전체 ⊤ (constant·immutable 은 storage 가 아니다)."""
        owner = self.ev._executing_contract_cfg()
        for name, v in list(variables.items()):
            if "." in name or getattr(v, "scope", None) != "state":
                continue
            if v.isConstant:          # constant·immutable 둘 다 process_constant_variable 경로 → isConstant
                continue
            self.ev.engine._havoc(v, owner)
