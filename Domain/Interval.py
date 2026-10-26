INFINITY = float('inf')
NEG_INFINITY = float('-inf')

class Interval:
    """
    모든 Interval의 기본 클래스.
    min_value, max_value가 모두 None이면 bottom(빈 집합)을 의미한다.
    """
    def __init__(self, min_value=None, max_value=None, type_length=None):
        self.min_value = min_value
        self.max_value = max_value
        self.type_length = type_length

    def is_bottom(self):
        return self.min_value is None and self.max_value is None

    def is_top(self):
        """
        자식 클래스에서 override해서 사용.
        기본 Interval은 top/bottom 개념이 모호하므로 False로 둠.
        """
        return False

    @staticmethod
    def _bottom_propagate(a, b, fn):
        """
        a 또는 b 가 ⊥(bottom) 이면 같은 타입의 bottom 을 반환.
        아니면 fn(a, b) 실행 결과 반환.
        """
        if a.is_bottom() or b.is_bottom():
            # a, b 두 클래스는 같다고 가정
            return a.__class__(None, None, a.type_length)
        return fn(a, b)

    def make_bottom(self):
        """bottom(빈 집합)을 만들어 반환"""
        return type(self)(None, None)


    def encompass(self, intended_interval):
        """
        실제 interval이 intended_interval(개발자 의도)에 포함되는지 여부.
        """
        if self.is_bottom() or intended_interval.is_bottom():
            # bottom인 경우 비교가 모호하므로 우선 False 처리
            return False
        return (self.min_value >= intended_interval.min_value
                and self.max_value <= intended_interval.max_value)

    def equals(self, other):
        return (self.min_value == other.min_value
                and self.max_value == other.max_value)

    def copy(self):
        new_obj = type(self)(self.min_value, self.max_value)
        new_obj.type_length = self.type_length
        return new_obj

    def __repr__(self):
        if self.is_bottom():
            return f"{type(self).__name__}(BOTTOM)"
        return f"{type(self).__name__}([{self.min_value}, {self.max_value}])"


# ----------------------------------------------------------------------------
# IntegerInterval (signed)
# ----------------------------------------------------------------------------

def _fit_signed(lo: int, hi: int, w: int):
    """signed 산술 결과를 intN 표현 범위로 정규화. 벗어나면 TOP.

    ★ 왜 필요한가: 예전엔 `IntegerInterval(lo, hi, w)` 가 `w` 를 받기만 하고
      **경계를 전혀 강제하지 않았다.** 그래서 int256*int256 결과가 512비트 구간이
      되고, fixpoint 루프의 `x = x*x` 에서 비트수가 매 반복 2배로 자랐다
      (실측: 8회에 65,281비트 → 이후 곱셈이 끝나지 않음).
      연산 자체는 O(1) 4코너인데 **피연산자가 도메인을 탈출**해 멈춘 것이다.

    ★ 왜 포화(clamp)가 아니라 TOP 인가: `unchecked` 블록에서 Solidity 는 **wrap** 한다.
      큰 값이 작은 값이 될 수 있으므로 경계로 잘라 붙이면 그 작은 값을 놓쳐 **불건전**하다.
      (checked 문맥에선 revert 라 '범위 교집합' 이 더 정밀하지만, 두 문맥을 구분하려면
       unchecked_block 플래그를 봐야 한다 — 별건.)
    """
    b_lo, b_hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    if lo < b_lo or hi > b_hi:
        return b_lo, b_hi
    return lo, hi


def _fit_unsigned(lo: int, hi: int, w: int):
    """unsigned 산술 결과를 uintN 표현 범위로 정규화. 벗어나면 TOP. (_fit_signed 주석 참조)"""
    b_hi = (1 << w) - 1
    if lo < 0 or hi > b_hi:
        return 0, b_hi
    return lo, hi


def _pow_fits(base: int, exp: int, w: int) -> bool:
    """pow(base, exp) 를 **실제로 계산해도 안전한가**를 비트수로 미리 판정.

    `pow(2, 2**255)` 같은 걸 계산하려 들면 그대로 멈춘다. 결과 비트수 ≈ exp·bitlen(base)
    이므로 곱만 해보고 판단한다(거듭제곱을 수행하지 않는다).
    """
    if exp < 0:
        return False
    if base in (0, 1, -1):
        return True
    return exp * abs(base).bit_length() <= w + 1


class IntegerInterval(Interval):
    def __init__(self, min_value=None, max_value=None, type_length=256):
        super().__init__(min_value, max_value, type_length)

    # ---------- Top / Bottom ----------
    def is_top(self):
        # ★ `top()` / `widen()` 과 같은 기준인 **타입 경계**로 판정한다(⑯).
        #   예전엔 `±INFINITY` 를 봤는데, `top()` 은 타입 경계를 돌려주므로
        #   `IntegerInterval.top(256).is_top()` 이 **False** 였다.
        #   (호출자가 없어 증상은 없었지만, 쓰기 시작하면 바로 틀리는 자리였다.
        #    UnsignedIntegerInterval.is_top 은 처음부터 타입 경계를 본다.)
        return (self.min_value == -2 ** (self.type_length - 1)
                and self.max_value == 2 ** (self.type_length - 1) - 1)

    @staticmethod
    def _bottom_propagate(a, b, fn):
        if a.is_bottom() or b.is_bottom():
            return a.__class__(None, None, a.type_length)
        return fn(a, b)

    @staticmethod
    def top(type_len: int = 256):
        """
        signed int의 top = [-2^(bits-1), 2^(bits-1) - 1]
        내부적으로는 '이론적 -∞, +∞' 대신 실제 비트 범위를 사용합니다.
        """
        min_value = -2 ** (type_len - 1)
        max_value = 2 ** (type_len - 1) - 1
        return IntegerInterval(min_value, max_value, type_len)

    def theoretical_top(self):
        """
        실제로는 위 top()을 사용하는 게 맞지만,
        만약 '완전히 무한대' 개념을 쓰고 싶다면 이 메서드를 호출할 수 있음.
        """
        return IntegerInterval(NEG_INFINITY, INFINITY, self.type_length)

    @staticmethod
    def bottom(type_len: int = 256) -> "IntegerInterval":
        """빈 집합 (⊥)"""
        return IntegerInterval(None, None, type_len)


    def initialize_range(self, type_name: str) -> None:
        """
        type_name : 'int', 'int8', 'int256' …
        지정된 비트 수에 맞춰 min/max 를 설정한다.
        """
        if not type_name.startswith("int"):
            raise ValueError(f"Unsupported signed integer type: {type_name}")

        # 방법 ① 컴프리헨션 ─ 타입-체커 경고 無
        digits = ''.join(c for c in type_name if c.isdigit())

        # ▸ 방법 ② 정규식 (주석 처리; 취향에 따라 사용 가능)
        # m = re.search(r'\d+', type_name)
        # digits = m.group(0) if m else ""

        bits = int(digits) if digits else 256  # 'int' → 256
        self.type_length = bits
        self.min_value = -2 ** (bits - 1)
        self.max_value = 2 ** (bits - 1) - 1

    # ---------- Lattice 연산 ----------
    def join(self, other):
        """
        Join(합집합) : [min(self, other), max(self, other)]
        """
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self

        if self.type_length != other.type_length:
            # 여기서는 간단히 에러. 필요시 더 유연하게 처리 가능.
            raise ValueError("Cannot join intervals of different type lengths.")

        new_min = min(self.min_value, other.min_value)
        new_max = max(self.max_value, other.max_value)
        return IntegerInterval(new_min, new_max, self.type_length)

    def meet(self, other):
        """
        Meet(교집합) : [max(min), min(max)]
        """
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        if self.type_length != other.type_length:
            raise ValueError("Cannot meet intervals of different type lengths.")

        new_min = max(self.min_value, other.min_value)
        new_max = min(self.max_value, other.max_value)
        if new_min > new_max:
            return self.bottom(self.type_length)
        return IntegerInterval(new_min, new_max, self.type_length)

    def widen(self, current_interval):
        """
        widen: 값이 자라는 쪽 경계를 **타입 경계**까지 한 번에 밀어 올린다.

        ★ 예전에는 `NEG_INFINITY`/`INFINITY` 를 넣었다. 그런데 `top()` 은 이미
          `[-2^(bits-1), 2^(bits-1)-1]` 을 쓰고 주석에도 "이론적 ±∞ 대신 실제 비트 범위"
          라고 적혀 있어 **widen 만 예외**였다. 결과적으로 같은 변수가 경로에 따라
          `[0,inf]`(widen 경유)와 `[0,2^256-1]`(산술 경유)로 **다르게 직렬화**됐고,
          `Refine` 의 `!= INFINITY` 가드가 `inf` 를 보면 **정제를 건너뛰어** 타입으로
          알 수 있는 상한까지 버렸다. 타입 있는 구간은 `inf` 를 갖지 않는다(⑯).
        """
        # (1) 오른쪽이 bottom 이면 self 그대로
        if current_interval is None or current_interval.is_bottom():
            return self.copy()

        if self.is_bottom():
            return current_interval

        type_min = -2 ** (self.type_length - 1)
        type_max = 2 ** (self.type_length - 1) - 1
        new_min = (type_min
                   if self.min_value > current_interval.min_value
                   else self.min_value)
        new_max = (type_max
                   if self.max_value < current_interval.max_value
                   else self.max_value)
        return IntegerInterval(new_min, new_max, self.type_length)

    def narrow(self, new_interval):
        """
        narrow: 만약 현재가 -∞ 이었다면 new_interval의 min_value로 좁히고,
        +∞였으면 new_interval의 max_value로 좁힘
        """
        if self.is_bottom():
            return new_interval

        cur_min = self.min_value if self.min_value != NEG_INFINITY else new_interval.min_value
        cur_max = self.max_value if self.max_value != INFINITY else new_interval.max_value
        if cur_min is None or cur_max is None:
            return self.bottom(self.type_length)
        return IntegerInterval(cur_min, min(cur_max, new_interval.max_value), self.type_length)

    # ---------- 논리/비교 (Interval 축소) ----------
    def less_than(self, other):
        # a < b  => a.max < b.min
        # 추상 해석에서는 a <= b.min - 1로 제한
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        new_max = min(self.max_value, other.min_value - 1)
        if new_max < self.min_value:
            return self.bottom(self.type_length)
        return IntegerInterval(self.min_value, new_max, self.type_length)

    def greater_than(self, other):
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        new_min = max(self.min_value, other.max_value + 1)
        if new_min > self.max_value:
            return self.bottom(self.type_length)
        return IntegerInterval(new_min, self.max_value, self.type_length)

    def less_than_or_equal(self, other):
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        new_max = min(self.max_value, other.max_value)
        if new_max < self.min_value:
            return self.bottom(self.type_length)
        return IntegerInterval(self.min_value, new_max, self.type_length)

    def greater_than_or_equal(self, other):
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        new_min = max(self.min_value, other.min_value)
        if new_min > self.max_value:
            return self.bottom(self.type_length)
        return IntegerInterval(new_min, self.max_value, self.type_length)

    # ---------- 산술 연산 ----------
    def add(self, other):
        def _impl(x, y):
            lo, hi = _fit_signed(x.min_value + y.min_value,
                                 x.max_value + y.max_value, self.type_length)
            return IntegerInterval(lo, hi, self.type_length)
        return Interval._bottom_propagate(self, other, _impl)

    def subtract(self, other):
        def _impl(x, y):
            lo, hi = _fit_signed(x.min_value - y.max_value,
                                 x.max_value - y.min_value, self.type_length)
            return IntegerInterval(lo, hi, self.type_length)
        return Interval._bottom_propagate(self, other, _impl)

    def multiply(self, other):
        def _mul(x, y):
            candidates = [
                x.min_value * y.min_value,
                x.min_value * y.max_value,
                x.max_value * y.min_value,
                x.max_value * y.max_value,
            ]
            lo, hi = _fit_signed(min(candidates), max(candidates), self.type_length)
            return IntegerInterval(lo, hi, self.type_length)
        return Interval._bottom_propagate(self, other, _mul)

    def divide(self, other: "IntegerInterval") -> "IntegerInterval":
        """
        self / other
        · 분모가 정확히 [0,0]이면 항상 revert → BOTTOM
        · 분모가 0을 포함하면 0 제외 후 두 구간으로 나눠 계산, 보수적 합산
        · 아니면 4 개의 끝점 조합을 // 로 계산해 [min, max] 보수적 추정
        """
        def _impl(x: "IntegerInterval", y: "IntegerInterval") -> "IntegerInterval":
            # 정확히 [0,0]이면 항상 revert → BOTTOM
            if y.min_value == 0 and y.max_value == 0:
                return x.make_bottom()

            def _sdiv(a: int, b: int) -> int:
                return a // b

            def _div_range(x_interval, y_min, y_max):
                vals = [
                    _sdiv(x_interval.min_value, y_min),
                    _sdiv(x_interval.min_value, y_max),
                    _sdiv(x_interval.max_value, y_min),
                    _sdiv(x_interval.max_value, y_max),
                ]
                return min(vals), max(vals)

            if y.min_value <= 0 <= y.max_value:
                # 0을 포함 → 음수부 [-min, -1]과 양수부 [1, max]로 분리, 보수적 합산
                all_mins, all_maxs = [], []
                if y.min_value < 0:
                    lo, hi = _div_range(x, y.min_value, -1)
                    all_mins.append(lo); all_maxs.append(hi)
                if y.max_value > 0:
                    lo, hi = _div_range(x, 1, y.max_value)
                    all_mins.append(lo); all_maxs.append(hi)
                if not all_mins:
                    return x.make_bottom()
                return IntegerInterval(min(all_mins), max(all_maxs), x.type_length)

            vals = [
                _sdiv(x.min_value, y.min_value),
                _sdiv(x.min_value, y.max_value),
                _sdiv(x.max_value, y.min_value),
                _sdiv(x.max_value, y.max_value),
            ]
            return IntegerInterval(min(vals), max(vals), x.type_length)

        return Interval._bottom_propagate(self, other, _impl)
    # ---------- 산술 연산 ----------
    def exponentiate(self, other: "IntegerInterval") -> "IntegerInterval":
        """
        a ** b  (b ≥ 0 가정).  음수 지수·bottom 전파·오버플로 클램프 포함.
        """
        def _clamp(v: int, bits: int) -> int:
            lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
            return max(lo, min(hi, v))

        def _impl(x: "IntegerInterval", y: "IntegerInterval") -> "IntegerInterval":
            # 음수 지수 → bottom
            if y.min_value < 0:
                return x.make_bottom()

            w = self.type_length
            base_cand = [x.min_value, x.max_value]
            exp_cand  = [y.min_value, y.max_value]
            # ★ pow 를 실제로 계산하기 전에 비트수로 판정한다 — pow(2, 2**255) 는 멈춘다.
            if not all(_pow_fits(a, b, w) for a in base_cand for b in exp_cand):
                return IntegerInterval.top(w)
            vals = [pow(a, b) for a in base_cand for b in exp_cand]
            lo, hi = _fit_signed(min(vals), max(vals), w)
            return IntegerInterval(lo, hi, w)

        return Interval._bottom_propagate(self, other, _impl)


    def modulo(self, other: "IntegerInterval") -> "IntegerInterval":
        """
        self % other  – 0 포함 / bottom 전파, 보수적 범위 추정.
        """
        def _impl(x: "IntegerInterval", y: "IntegerInterval") -> "IntegerInterval":
            # 분모가 0 범위 포함 → bottom
            if y.min_value <= 0 <= y.max_value:
                return x.make_bottom()
            max_div = max(abs(y.min_value), abs(y.max_value))
            return IntegerInterval(-max_div + 1, max_div - 1, x.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    # ---------- 쉬프트/비트 연산 ----------
    def shift(self, shift_iv, op: str) -> "IntegerInterval":
        """<<, >>, >>> 의 추상 의미론.

        `x << s ≡ x·2^s` / `x >> s ≡ floor(x/2^s)`.
        부호에 따라 단조성이 뒤집히므로 **4코너 min/max** 로 구한다 —
        예전의 "lo 는 s.min, hi 는 s.max" 같은 고정 코너는 음수에서 틀린다
        (w=4/5 전수 검증: signed 49~53%, unsigned 41~44% 조합이 불건전했다.
         예: `[-8,-8] << [0,3]` 참값 (-64,-8) → 구현 (-8,-64) 로 **역전**).

        오버플로는 **포화가 아니라 TOP**. Solidity 시프트는 revert 없이 wrap 하므로
        clamp 는 불건전하다 — uint256 에서 `(2^255) << 1` 은 2^256 이 아니라 **0** 이다.

        거대 정수를 만들지 않는다(임의정밀도 폭주 방지):
          '<<' 는 s.max ≥ w 면 계산 없이 TOP, '>>' 는 s 를 w 로 캡한다
          (그 이상 시프트는 결과가 동일 — 0 또는 -1).

        shift_iv 는 min/max 만 읽으므로 **타입이 달라도 된다**
        (Solidity 에서 시프트 양은 unsigned, 좌변은 signed 여도 유효하다).
        """
        if self.is_bottom() or shift_iv.is_bottom():
            return self.make_bottom()
        if shift_iv.min_value < 0:          # 음수 시프트 양 = 미정의
            return self.make_bottom()

        w = self.type_length
        xlo, xhi = self.min_value, self.max_value
        slo, shi = shift_iv.min_value, shift_iv.max_value

        if op == '<<':
            if shi >= w:
                return type(self).top(w)
            c = (xlo << slo, xlo << shi, xhi << slo, xhi << shi)
            lo, hi = min(c), max(c)
            if lo < -(1 << (w - 1)) or hi > (1 << (w - 1)) - 1:
                return type(self).top(w)
            return type(self)(lo, hi, w)

        if op in ('>>', '>>>'):
            slo_e, shi_e = min(slo, w), min(shi, w)
            c = (xlo >> slo_e, xlo >> shi_e, xhi >> slo_e, xhi >> shi_e)
            return type(self)(min(c), max(c), w)

        raise ValueError(f"Unsupported shift op {op}")

    def bitwise(self, op: str, other: "IntegerInterval") -> "IntegerInterval":
        """
        &, |, ^ – 매우 보수적: 동일 폭 signed top 으로 환원.
        """
        def _impl(x: "IntegerInterval", y: "IntegerInterval") -> "IntegerInterval":
            w = x.type_length
            # (2026-10-23) 단일값끼리는 정확히 — 파이썬 정수의 비트 연산은 무한 2의 보수라 같은 폭 안에서 결과가 맞다
            if x.min_value == x.max_value and y.min_value == y.max_value:
                a, b = x.min_value, y.min_value
                v = a & b if op == '&' else (a | b if op == '|' else a ^ b)
                return IntegerInterval(v, v, w)
            lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
            return IntegerInterval(lo, hi, w)

        if op not in {'&', '|', '^'}:
            raise ValueError(f"bad bit-op {op}")
        return Interval._bottom_propagate(self, other, _impl)

    # ---------- 단항 연산 ----------
    def negate(self) -> "IntegerInterval":
        """
        단항 ‘-’  (bottom 전파)
        """
        if self.is_bottom():
            return self
        return IntegerInterval(-self.max_value, -self.min_value, self.type_length)

    # Interval.py  ─ IntegerInterval 안쪽, 앞/뒤 어디든 메서드 하나만 삽입
    # ---------- 단항 연산 ----------
    def bitwise_not(self) -> "IntegerInterval":
        """
        단항  '~'  (two’s-complement 가정).
        구간 [min,max]  →  [~max, ~min]  으로 보수-변환 후
        비트폭 한계에 맞춰 클램프한다.
        """
        if self.is_bottom():
            return self
        lo_bound = -(1 << (self.type_length - 1))
        hi_bound = (1 << (self.type_length - 1)) - 1
        new_min = max(lo_bound, ~self.max_value)
        new_max = min(hi_bound, ~self.min_value)
        return IntegerInterval(new_min, new_max, self.type_length)

    def prefix_increment(self):
        if self.is_bottom():
            return self
        return IntegerInterval(self.min_value + 1, self.max_value + 1, self.type_length)

    def prefix_decrement(self):
        if self.is_bottom():
            return self
        return IntegerInterval(self.min_value - 1, self.max_value - 1, self.type_length)

    def postfix_increment(self):
        if self.is_bottom():
            return self
        old = IntegerInterval(self.min_value, self.max_value, self.type_length)
        self.min_value += 1
        self.max_value += 1
        return old

    def postfix_decrement(self):
        if self.is_bottom():
            return self
        old = IntegerInterval(self.min_value, self.max_value, self.type_length)
        self.min_value -= 1
        self.max_value -= 1
        return old

    # ---------- Guardian 연산 (percent_of, ceil, floor) ----------
    def percent_of(self, pct: int) -> "IntegerInterval":
        """
        self의 pct% 를 계산.
        보수적 추정: lo는 floor, hi는 ceil로 범위를 넓게 유지.
        signed의 경우 음수도 고려.
        """
        if self.is_bottom():
            return self

        candidates = [
            (self.min_value * pct) // 100,
            (self.min_value * pct + 99) // 100,
            (self.max_value * pct) // 100,
            (self.max_value * pct + 99) // 100,
        ]
        return IntegerInterval(min(candidates), max(candidates), self.type_length)

    def ceil_to_unit(self, unit: int) -> "IntegerInterval":
        """
        self를 unit 단위로 올림.
        """
        if self.is_bottom():
            return self
        if unit <= 0:
            return self

        def _ceil(v):
            if v >= 0:
                return ((v + unit - 1) // unit) * unit
            else:
                return (v // unit) * unit

        lo = _ceil(self.min_value)
        hi = _ceil(self.max_value)
        return IntegerInterval(lo, hi, self.type_length)

    def floor_to_unit(self, unit: int) -> "IntegerInterval":
        """
        self를 unit 단위로 내림.
        """
        if self.is_bottom():
            return self
        if unit <= 0:
            return self

        def _floor(v):
            if v >= 0:
                return (v // unit) * unit
            else:
                return ((v - unit + 1) // unit) * unit

        lo = _floor(self.min_value)
        hi = _floor(self.max_value)
        return IntegerInterval(lo, hi, self.type_length)


# ----------------------------------------------------------------------------
# UnsignedIntegerInterval
# ----------------------------------------------------------------------------

class UnsignedIntegerInterval(Interval):
    def __init__(self, min_value=None, max_value=None, type_length=256):
        super().__init__(min_value, max_value, type_length)

    # ---------- Top / Bottom ----------
    def is_top(self):
        """
        top = [0, 2^bits - 1]
        """
        return (self.min_value == 0 and
                self.max_value == 2 ** self.type_length - 1)

    @staticmethod
    def top(type_len: int = 256):
        min_val = 0
        max_val = 2 ** type_len - 1
        return UnsignedIntegerInterval(min_val, max_val, type_len)

    def theoretical_top(self):
        """
        이론적 top(무한대)을 쓰고 싶다면 여기서 [0, +∞] 처리를 할 수도 있음.
        """
        return UnsignedIntegerInterval(0, INFINITY, self.type_length)

    @staticmethod
    def bottom(type_len: int = 256) -> "UnsignedIntegerInterval":
        return UnsignedIntegerInterval(None, None, type_len)

    # ---------- 범위 초기화 ----------
    def initialize_range(self, type_name: str) -> None:
        """
        type_name : 'uint', 'uint8', 'uint256' …
        지정된 비트 수(bit-width)에 맞춰 min/max 를 설정한다.
        """
        if not type_name.startswith("uint"):
            raise ValueError(f"Unsupported unsigned integer type: {type_name}")

        # ────────────────────────────────────────────────
        # ① 컴프리헨션 ― IDE 경고 無
        digits = ''.join(c for c in type_name if c.isdigit())

        # ▸ ② 정규식 사용 예시  (원한다면)
        # import re
        # m = re.search(r'\d+', type_name)
        # digits = m.group(0) if m else ""
        # ────────────────────────────────────────────────

        bits = int(digits) if digits else 256  # 'uint' ⇒ 기본 256-bit
        self.type_length = bits
        self.min_value = 0
        self.max_value = (1 << bits) - 1  # 2**bits − 1

    def negate(self) -> "UnsignedIntegerInterval":
        """
        uintN 범위에서의 단항 `-` (2^N 모듈러 보수).
        * [a,a]  단일값이면              → [-a mod 2^N , -a mod 2^N]
        * 구간이 여러 값을 포함하면       → 정보 손실을 피하기 어려우므로 TOP([0,max])
        """
        # ⊥ 그대로
        if self.is_bottom():
            return self

        bits = self.type_length
        modulus = 1 << bits  # 2^N

        # ① 싱글톤 [v,v] 만 정확히 처리
        if self.min_value == self.max_value:
            v = self.min_value % modulus
            neg_v = (-v) % modulus  # = 2^N - v, 단 v==0 이면 0
            return UnsignedIntegerInterval(neg_v, neg_v, bits)

        # ② 구간이 넓으면 정확한 역상(보수)이 '랩(wrap-around) 구간'이 되므로
        #    분석 단순화를 위해 TOP 으로 보수화
        return UnsignedIntegerInterval(0, modulus - 1, bits)

    # ---------- Lattice 연산 ----------
    def join(self, other):
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self

        if self.type_length != other.type_length:
            raise ValueError("Cannot join intervals of different type lengths")

        new_min = min(self.min_value, other.min_value)
        new_max = max(self.max_value, other.max_value)
        return UnsignedIntegerInterval(new_min, new_max, self.type_length)

    def meet(self, other):
        if self.is_bottom() or other.is_bottom():
            return self.bottom(self.type_length)

        if self.type_length != other.type_length:
            raise ValueError("Cannot meet intervals of different type lengths")

        new_min = max(self.min_value, other.min_value)
        new_max = min(self.max_value, other.max_value)
        if new_min > new_max:
            return self.bottom(self.type_length)
        return UnsignedIntegerInterval(new_min, new_max, self.type_length)

    def widen(self, current_interval):
        """
        min 은 0, max 는 **타입 최댓값**(2^bits-1)으로 확장.

        ★ 예전에는 `INFINITY` 를 넣었다. 그런데 바로 위 `is_top()` 은
          `max == 2^bits - 1` 을 검사하므로 **widen 된 값이 자기 `is_top()` 에서 False**
          였고, 산술(`add` → `_fit_unsigned`)은 타입 최댓값으로 맞추므로 같은 변수가
          경로에 따라 `[0,inf]` 와 `[0,2^256-1]` 로 **다르게 직렬화**됐다.
          `Refine` 의 `!= INFINITY` 가드가 `inf` 를 보면 정제를 건너뛰는 손실도 있었다.
          타입 있는 구간은 `inf` 를 갖지 않는다(⑯).
        """
        # (1) 오른쪽이 bottom 이면 self 그대로
        if current_interval is None or current_interval.is_bottom():
            return self.copy()

        if self.is_bottom():
            return current_interval

        new_min = 0 if self.min_value > current_interval.min_value else self.min_value
        type_max = 2 ** self.type_length - 1
        new_max = type_max if self.max_value < current_interval.max_value else self.max_value
        return UnsignedIntegerInterval(new_min, new_max, self.type_length)

    def narrow(self, new_interval):
        if self.is_bottom():
            return new_interval

        cur_min = self.min_value
        cur_max = self.max_value
        if cur_max == INFINITY:
            cur_max = new_interval.max_value

        # 간단 처리
        return UnsignedIntegerInterval(max(cur_min, new_interval.min_value),
                                       min(cur_max, new_interval.max_value),
                                       self.type_length)

    # ---------- 산술 연산 (실제 빼기) ----------
    def subtract(self, other: "UnsignedIntegerInterval", wrapping: bool = False) -> "UnsignedIntegerInterval":
        """checked(기본): 음수가 되는 경로는 revert → 0 미만을 잘라내고, 전부 음수면 ⊥.
        wrapping=True (`unchecked` 블록, Phase 2): 음수는 2^w 를 더해 감긴다 —
          전부 음수면 정확히 [lo+2^w, hi+2^w], 0 을 가로지르면 두 조각의 합집합이라 구간으론 ⊤."""
        def _impl(a, b):
            min_diff = a.min_value - b.max_value
            max_diff = a.max_value - b.min_value

            if wrapping:
                if min_diff >= 0:
                    return UnsignedIntegerInterval(min_diff, max_diff, a.type_length)
                mod = 1 << a.type_length
                if max_diff < 0:
                    return UnsignedIntegerInterval(min_diff + mod, max_diff + mod, a.type_length)
                return UnsignedIntegerInterval(0, mod - 1, a.type_length)

            # 전부 음수가 될 가능성이 있으면 실행 불가 → ⊥
            if max_diff < 0:
                return a.make_bottom()

            cmin = max(min_diff, 0)
            cmax = max(max_diff, 0)
            return UnsignedIntegerInterval(cmin, cmax, a.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    def add(self, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        def _impl(a, b):
            # 포화(clamp) 대신 TOP — unchecked 에선 wrap 하므로 경계로 잘라 붙이면
            # 실제로 나올 수 있는 '작은 값' 을 놓친다(_fit_unsigned 주석 참조).
            lo, hi = _fit_unsigned(a.min_value + b.min_value,
                                   a.max_value + b.max_value, a.type_length)
            return UnsignedIntegerInterval(lo, hi, a.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    def multiply(self, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        def _impl(a, b):
            vals = [
                a.min_value * b.min_value,
                a.min_value * b.max_value,
                a.max_value * b.min_value,
                a.max_value * b.max_value,
            ]
            # 예전엔 max 만 type_max 로 포화시키고 **min 은 그대로 뒀다** →
            # a.min*b.min 이 범위를 넘으면 [거대값, type_max] 라는 역전 구간이 나오고,
            # 그 값이 다음 연산의 피연산자가 되어 비트수가 계속 자랐다.
            lo, hi = _fit_unsigned(min(vals), max(vals), a.type_length)
            return UnsignedIntegerInterval(lo, hi, a.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    def divide(self, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        def _impl(a, b):
            # 분모가 정확히 [0,0]이면 항상 revert → BOTTOM
            if b.min_value == 0 and b.max_value == 0:
                return a.make_bottom()
            # 분모가 0을 포함하면 0 제외 (Solidity div-by-zero = revert)
            den_min = max(b.min_value, 1)
            den_max = b.max_value

            nums = [a.min_value, a.max_value]
            dens = [den_min, den_max]
            cand = [n // d for n in nums for d in dens]
            return UnsignedIntegerInterval(min(cand), max(cand), a.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    def exponentiate(self, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        def _impl(a, b):
            if b.min_value < 0:          # 음수 지수는 허용 안 함
                return a.make_bottom()

            w = a.type_length
            bases, exps = (a.min_value, a.max_value), (b.min_value, b.max_value)
            # ★ pow 를 계산하기 전에 비트수로 판정 — pow(2, 2**255) 는 멈춘다.
            if not all(_pow_fits(base, exp, w) for base in bases for exp in exps):
                return UnsignedIntegerInterval.top(w)
            vals = [pow(base, exp) for base in bases for exp in exps]
            lo, hi = _fit_unsigned(min(vals), max(vals), w)
            return UnsignedIntegerInterval(lo, hi, w)

        return Interval._bottom_propagate(self, other, _impl)

    def modulo(self, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        def _impl(a, b):
            if b.min_value <= 0 <= b.max_value:
                return a.make_bottom()

            # divisor 가 단일 값(d)일 때 최적화
            if b.min_value == b.max_value:
                d = b.min_value
                if a.max_value < d:
                    return UnsignedIntegerInterval(a.min_value, a.max_value, a.type_length)
                return UnsignedIntegerInterval(0, d - 1, a.type_length)

            # 범위가 넓은 제수 → 0..(max_d-1)
            return UnsignedIntegerInterval(0, b.max_value - 1, a.type_length)

        return Interval._bottom_propagate(self, other, _impl)

    # ------------------------------------------------------------------
    # Shift / Bitwise
    # ------------------------------------------------------------------
    def shift(self, shift_iv, op: str) -> "UnsignedIntegerInterval":
        """<<, >>, >>> 의 추상 의미론.

        `x << s ≡ x·2^s` / `x >> s ≡ floor(x/2^s)`.
        부호에 따라 단조성이 뒤집히므로 **4코너 min/max** 로 구한다 —
        예전의 "lo 는 s.min, hi 는 s.max" 같은 고정 코너는 음수에서 틀린다
        (w=4/5 전수 검증: signed 49~53%, unsigned 41~44% 조합이 불건전했다.
         예: `[-8,-8] << [0,3]` 참값 (-64,-8) → 구현 (-8,-64) 로 **역전**).

        오버플로는 **포화가 아니라 TOP**. Solidity 시프트는 revert 없이 wrap 하므로
        clamp 는 불건전하다 — uint256 에서 `(2^255) << 1` 은 2^256 이 아니라 **0** 이다.

        거대 정수를 만들지 않는다(임의정밀도 폭주 방지):
          '<<' 는 s.max ≥ w 면 계산 없이 TOP, '>>' 는 s 를 w 로 캡한다
          (그 이상 시프트는 결과가 동일 — 0 또는 -1).

        shift_iv 는 min/max 만 읽으므로 **타입이 달라도 된다**
        (Solidity 에서 시프트 양은 unsigned, 좌변은 signed 여도 유효하다).
        """
        if self.is_bottom() or shift_iv.is_bottom():
            return self.make_bottom()
        if shift_iv.min_value < 0:          # 음수 시프트 양 = 미정의
            return self.make_bottom()

        w = self.type_length
        xlo, xhi = self.min_value, self.max_value
        slo, shi = shift_iv.min_value, shift_iv.max_value

        if op == '<<':
            if shi >= w:
                return type(self).top(w)
            c = (xlo << slo, xlo << shi, xhi << slo, xhi << shi)
            lo, hi = min(c), max(c)
            if lo < 0 or hi > (1 << w) - 1:
                return type(self).top(w)
            return type(self)(lo, hi, w)

        if op in ('>>', '>>>'):
            slo_e, shi_e = min(slo, w), min(shi, w)
            c = (xlo >> slo_e, xlo >> shi_e, xhi >> slo_e, xhi >> shi_e)
            return type(self)(min(c), max(c), w)

        raise ValueError(f"Unsupported shift op {op}")

    def left_shift(self, s: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        type_max = (1 << self.type_length) - 1
        smin = min(self.min_value << s.min_value, type_max)
        smax = min(self.max_value << s.max_value, type_max)
        return UnsignedIntegerInterval(smin, smax, self.type_length)

    def right_shift(self, s: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        smin = self.min_value >> s.max_value
        smax = self.max_value >> s.min_value
        return UnsignedIntegerInterval(smin, smax, self.type_length)

    def bitwise(self, op: str, other: "UnsignedIntegerInterval") -> "UnsignedIntegerInterval":
        """(2026-10-23) 예전 `|`·`^` 상한 = max(hi) 는 **unsound** 였다(1 | 2 = 3 > 2, 1 ^ 2 = 3).
        단일값끼리도 계산하지 않아 `1 ^ 1` 이 [0,1] 이었다(16_H_04 PRBMathSD59x18.mul 부호 복원 `sx ^ sy`).
          · 둘 다 단일값 → 정확
          · `&` → [0, min hi]            (각 비트가 양쪽 모두 1 이어야 함)
          · `|` → [max lo, 2^k − 1]      (k = max hi 의 비트 길이; 결과 ≥ 각 피연산자)
          · `^` → [0, 2^k − 1]"""
        if op not in {'&', '|', '^'}:
            raise ValueError(f"bad bit-op {op}")

        def _impl(a, b):
            w = a.type_length
            if a.min_value == a.max_value and b.min_value == b.max_value:
                x, y = a.min_value, b.min_value
                v = x & y if op == '&' else (x | y if op == '|' else x ^ y)
                return UnsignedIntegerInterval(v, v, w)
            if op == '&':
                return UnsignedIntegerInterval(0, min(a.max_value, b.max_value), w)
            cap = min((1 << max(a.max_value, b.max_value).bit_length()) - 1, (1 << w) - 1)
            lo = max(a.min_value, b.min_value) if op == '|' else 0
            return UnsignedIntegerInterval(lo, cap, w)

        return Interval._bottom_propagate(self, other, _impl)

    # Interval.py  ─ UnsignedIntegerInterval 내부
    # ---------- 단항 연산 ----------
    def bitwise_not(self) -> "UnsignedIntegerInterval":
        """
        uintN 의  '~'  는  (2^N-1) − x  와 동일.
        구간 [min,max]  →  [mask-max , mask-min]
        """
        if self.is_bottom():
            return self
        mask = (1 << self.type_length) - 1
        return UnsignedIntegerInterval(
            mask - self.max_value,
            mask - self.min_value,
            self.type_length
        )

    # ---------- Guardian 연산 (percent_of, ceil, floor) ----------
    def percent_of(self, pct: int) -> "UnsignedIntegerInterval":
        """
        self의 pct% 를 계산.
        보수적 추정: lo는 floor, hi는 ceil로 범위를 넓게 유지.
        """
        if self.is_bottom():
            return self
        lo = (self.min_value * pct) // 100
        hi = (self.max_value * pct + 99) // 100
        return UnsignedIntegerInterval(lo, hi, self.type_length)

    def ceil_to_unit(self, unit: int) -> "UnsignedIntegerInterval":
        """
        self를 unit 단위로 올림.
        보수적 추정: lo는 그대로 ceil, hi도 ceil.
        """
        if self.is_bottom():
            return self
        if unit <= 0:
            return self

        def _ceil(v):
            return ((v + unit - 1) // unit) * unit

        lo = _ceil(self.min_value)
        hi = _ceil(self.max_value)
        return UnsignedIntegerInterval(lo, hi, self.type_length)

    def floor_to_unit(self, unit: int) -> "UnsignedIntegerInterval":
        """
        self를 unit 단위로 내림.
        보수적 추정: lo는 floor, hi도 floor.
        """
        if self.is_bottom():
            return self
        if unit <= 0:
            return self

        def _floor(v):
            return (v // unit) * unit

        lo = _floor(self.min_value)
        hi = _floor(self.max_value)
        return UnsignedIntegerInterval(lo, hi, self.type_length)

    # ------------------------------------------------------------------------


class BoolInterval(Interval):
    """
    BoolInterval:
      - top = [0,1]
      - bottom = [None, None]
      - always True = [1,1]
      - always False = [0,0]
    """
    def __init__(self, min_value=None, max_value=None):
        super().__init__(min_value, max_value, None)

    def is_top(self):
        return self.min_value == 0 and self.max_value == 1

    @staticmethod
    def top():
        return BoolInterval(0, 1)

    @staticmethod
    def bottom() -> "BoolInterval":
        return BoolInterval(None, None)

    def calculate_interval(self, right_interval, operator):
        if self.is_bottom() or right_interval.is_bottom():
            return self.bottom()

        if operator == '&&':
            return self.logical_and(right_interval)
        elif operator == '||':
            return self.logical_or(right_interval)
        elif operator == '!':
            return self.logical_not()
        elif operator in ['==', '!=', '<', '>', '<=', '>=']:
            # 비교 결과는 불리언
            return BoolInterval(0,1)
        else:
            raise ValueError(f"Unsupported operator for bool: {operator}")

    def join(self, other):
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self

        new_min = min(self.min_value, other.min_value)
        new_max = max(self.max_value, other.max_value)
        return BoolInterval(new_min, new_max)

    def meet(self, other):
        if self.is_bottom() or other.is_bottom():
            return self.bottom()

        new_min = max(self.min_value, other.min_value)
        new_max = min(self.max_value, other.max_value)
        if new_min > new_max:
            return self.bottom()
        return BoolInterval(new_min, new_max)

    def widen(self, current_interval=None):
        # bool 격자는 높이가 유한(⊥ < [0,0],[1,1] < [0,1])이라 widening = join 으로 충분하다.
        # ★ (Phase 2, D2) 예전엔 무조건 ⊤ 를 돌려 ⊥(도달 불가) 끼리의 widening 도 ⊤ 가 됐다.
        if current_interval is None:
            return self.copy()
        return self.join(current_interval)

    def narrow(self, new_interval):
        if self.is_bottom():
            return new_interval

        cur_min = self.min_value
        cur_max = self.max_value
        new_min = max(cur_min, new_interval.min_value)
        new_max = min(cur_max, new_interval.max_value)
        if new_min > new_max:
            return self.bottom()
        return BoolInterval(new_min, new_max)

    def logical_op(self, other: "BoolInterval", op: str):
        """
        self  ∈  [s_min, s_max],   other ∈ [o_min, o_max]
        op == '&&'  →  AND
        op == '||'  →  OR
        반환값도 BoolInterval (0 == false, 1 == true)
        """
        if not isinstance(other, BoolInterval):
            raise TypeError("logical_op expects BoolInterval")

        if op not in ("&&", "||"):
            raise ValueError(f"Unsupported boolean operator '{op}'")

        # ---- AND -----------------------------------------------------
        if op == "&&":
            # 확실히 TRUE?   두 피연산자가 모두 확실히 1
            if self.min_value == 1 and other.min_value == 1:
                return BoolInterval(1, 1)

            # 확실히 FALSE?  둘 중 하나라도 확실히 0
            if self.max_value == 0 or other.max_value == 0:
                return BoolInterval(0, 0)

            # 그 외는 불확실([0,1])
            return BoolInterval(0, 1)

        # ---- OR  -----------------------------------------------------
        if op == "||":
            # 확실히 TRUE?   둘 중 하나라도 확실히 1
            if self.min_value == 1 or other.min_value == 1:
                return BoolInterval(1, 1)

            # 확실히 FALSE?  두 피연산자 모두 확실히 0
            if self.max_value == 0 and other.max_value == 0:
                return BoolInterval(0, 0)

        return BoolInterval(0, 1)

    def less_than(self):
        # Boolean에서 < 연산, 크게 의미 X. 여기서는 그대로 자신 반환 혹은 top
        return self

    # 그 외 greater_than, etc. 마찬가지로 특수처리

    def logical_and(self, other):
        """
        - 둘 다 [1,1]이면 결과 [1,1]
        - 하나라도 [0,0]이면 [0,0]
        - 그 외 [0,1]
        """
        if (self.min_value == 1 and self.max_value == 1 and
            other.min_value == 1 and other.max_value == 1):
            return BoolInterval(1,1)
        if (self.max_value == 0) or (other.max_value == 0):
            return BoolInterval(0,0)
        return BoolInterval(0,1)

    def logical_or(self, other):
        """
        - 하나라도 [1,1]이면 [1,1]
        - 둘 다 [0,0]이면 [0,0]
        - 그 외 [0,1]
        """
        if (self.min_value == 1 and self.max_value == 1) or \
           (other.min_value == 1 and other.max_value == 1):
            return BoolInterval(1,1)
        if self.max_value == 0 and other.max_value == 0:
            return BoolInterval(0,0)
        return BoolInterval(0,1)

    def logical_not(self):
        if self.is_bottom():
            return self
        # always True -> always False
        if self.min_value == 1 and self.max_value == 1:
            return BoolInterval(0,0)
        # always False -> always True
        if self.min_value == 0 and self.max_value == 0:
            return BoolInterval(1,1)
        # 그 외 => [0,1]
        return BoolInterval(0,1)

    # Interval.py  ─ BoolInterval 내부
    def bitwise_not(self) -> "BoolInterval":
        # bool 에서는 ~ 연산을 논리 not 과 동일하게 다룬다.
        return self.logical_not()

    def __repr__(self):
        if self.is_bottom():
            return "BoolInterval(BOTTOM)"
        return f"BoolInterval([{self.min_value}, {self.max_value}])"
