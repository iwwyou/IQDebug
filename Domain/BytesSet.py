from Domain.Interval import BoolInterval

class BytesSet:
    """
    bytes32 (고정 크기 바이트 배열)을 set domain으로 추상화
    - 최대 K개의 구체적인 bytes 값을 추적
    - K 초과 시 Top으로 확장
    - bytes32는 내부적으로 256비트 정수로 표현
    """
    __slots__ = ("values", "is_top", "byte_size")
    K = 8  # cap

    def __init__(self, values=None, is_top=False, byte_size=32):
        """
        Args:
            values: bytes 값들의 집합 (정수로 표현)
            is_top: Top 여부
            byte_size: 바이트 크기 (bytes32 = 32, bytes16 = 16 등)
        """
        self.values = frozenset(values or [])
        self.is_top = is_top
        self.byte_size = byte_size

    @staticmethod
    def top(byte_size=32):
        return BytesSet(is_top=True, byte_size=byte_size)

    @staticmethod
    def bot(byte_size=32):
        return BytesSet(values=frozenset(), byte_size=byte_size)

    def is_bottom(self) -> bool:
        """(Phase 2) 빈 집합 = 도달 불가. 예전엔 이 메서드가 없어 Engine._is_bottom_var 가 bytesN ⊥ 를 못 봤다."""
        return not self.is_top and not self.values

    def leq(self, other: "BytesSet") -> bool:
        """Partial order: self ⊑ other"""
        if self.is_top:
            return other.is_top
        if other.is_top:
            return True
        return self.values.issubset(other.values)

    def join(self, other: "BytesSet") -> "BytesSet":
        """Least upper bound (union with cap)"""
        if self.is_top or other.is_top:
            return BytesSet.top(self.byte_size)
        u = self.values | other.values
        return BytesSet(u, byte_size=self.byte_size) if len(u) <= BytesSet.K else BytesSet.top(self.byte_size)

    def meet(self, other: "BytesSet") -> "BytesSet":
        """Greatest lower bound (intersection)"""
        if self.is_top:
            return other
        if other.is_top:
            return self
        return BytesSet(self.values & other.values, byte_size=self.byte_size)

    def narrow(self, other: "BytesSet") -> "BytesSet":
        """Narrowing operator - refine the approximation"""
        # Narrowing: TOP인 경우 other로 구체화, 아니면 교집합
        if self.is_top:
            return other
        if other.is_top:
            return self
        return BytesSet(self.values & other.values, byte_size=self.byte_size)

    def equals(self, other: "BytesSet") -> BoolInterval:
        """Abstract equality: self == other"""
        if self.is_top or other.is_top:
            return BoolInterval(0, 1)  # Unknown
        inter = self.values & other.values
        if not inter:
            return BoolInterval(0, 0)  # Definitely false
        if len(self.values) == 1 and self.values == other.values:
            return BoolInterval(1, 1)  # Definitely true
        return BoolInterval(0, 1)  # May be true or false

    def not_equals(self, other: "BytesSet") -> BoolInterval:
        """Abstract inequality: self != other"""
        eq = self.equals(other)
        # Negate: [0,0] -> [1,1], [1,1] -> [0,0], [0,1] -> [0,1]
        if eq.min_value == 0 and eq.max_value == 0:
            return BoolInterval(1, 1)
        if eq.min_value == 1 and eq.max_value == 1:
            return BoolInterval(0, 0)
        return BoolInterval(0, 1)

    def add_value(self, val: int) -> "BytesSet":
        """Add a single bytes value"""
        if self.is_top:
            return self
        new_values = self.values | {val}
        return BytesSet(new_values, byte_size=self.byte_size) if len(new_values) <= BytesSet.K else BytesSet.top(self.byte_size)

    def is_singleton(self) -> bool:
        """Check if this is a singleton set"""
        return not self.is_top and len(self.values) == 1

    def get_singleton_value(self) -> int | None:
        """Get the single value if singleton, else None"""
        return next(iter(self.values)) if self.is_singleton() else None

    def is_zero(self) -> bool:
        """Check if this is definitely bytes32(0)"""
        return self.is_singleton() and next(iter(self.values)) == 0

    def __str__(self):
        if self.is_top:
            return f"BytesSet(⊤, size={self.byte_size})"
        if not self.values:
            return f"BytesSet(⊥, size={self.byte_size})"
        # 값이 작으면 10진수, 크면 16진수로 표시
        vals_str = ', '.join(
            str(v) if v < 1000 else f"0x{v:0{self.byte_size*2}x}"
            for v in sorted(self.values)
        )
        return f"BytesSet({{{vals_str}}}, size={self.byte_size})"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if not isinstance(other, BytesSet):
            return False
        return self.is_top == other.is_top and self.values == other.values and self.byte_size == other.byte_size

    def __hash__(self):
        return hash((self.is_top, self.values, self.byte_size))


# ═══════════════════════════════════════════════════════════════════
#  BytesManager: 싱글톤 매니저로 symbolic bytes 값 관리 (필요시 확장)
# ═══════════════════════════════════════════════════════════════════

class BytesManager:
    """
    주석 기반 bytes 할당을 위한 심볼릭 값 매니저
    - symbolicBytes N → BytesSet({N})
    - 변수명 → 값 역추적 (디버깅 용도)
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._next_id: int = 1  # auto-increment ID
        self._id_to_names: dict[int, set[str]] = {}  # ID → 변수명들
        self._name_to_ids: dict[str, set[int]] = {}  # 변수명 → ID들

    def reset(self):
        """테스트케이스 간 리셋용"""
        self._next_id = 1
        self._id_to_names.clear()
        self._name_to_ids.clear()

    # ─────────────────────── ID 발급 ───────────────────────
    def fresh_id(self) -> int:
        """새 심볼릭 ID 발급"""
        nid = self._next_id
        self._next_id += 1
        return nid

    def fresh_bytes(self, byte_size=32) -> BytesSet:
        """새 singleton BytesSet 반환"""
        nid = self.fresh_id()
        return BytesSet(values={nid}, byte_size=byte_size)

    # ─────────────────────── 고정 값 등록 ─────────────────────
    def register_value(self, val: int, var_name: str | None = None):
        """
        symbolicBytes N 같은 고정 값 등록
        """
        if val not in self._id_to_names:
            self._id_to_names[val] = set()

        if var_name:
            self._id_to_names[val].add(var_name)
            self._name_to_ids.setdefault(var_name, set()).add(val)

    def make_symbolic_bytes(self, val: int, var_name: str | None = None, byte_size=32) -> BytesSet:
        """symbolicBytes N → BytesSet({N})"""
        self.register_value(val, var_name)
        return BytesSet(values={val}, byte_size=byte_size)

    # ─────────────────────── 변수 바인딩 ─────────────────────
    def bind_var(self, var_name: str, bytes_set: BytesSet):
        """변수명 → BytesSet 바인딩 (역추적용)"""
        if bytes_set.is_top or not bytes_set.values:
            return

        for val in bytes_set.values:
            self._id_to_names.setdefault(val, set()).add(var_name)
        self._name_to_ids.setdefault(var_name, set()).update(bytes_set.values)

    def get_aliases(self, val: int) -> set[str]:
        """값 → 변수명 조회 (디버깅용)"""
        return self._id_to_names.get(val, set())

    def get_values(self, var_name: str) -> set[int]:
        """변수명 → 값 조회"""
        return self._name_to_ids.get(var_name, set())


# ─────────────────────── 글로벌 인스턴스 ─────────────────────
bytes_manager = BytesManager()
