from Domain.Interval import BoolInterval

class AddressSet:
    """
    Address를 set domain으로 추상화
    - 최대 K개의 구체적인 address ID를 추적
    - K 초과 시 Top으로 확장
    """
    __slots__ = ("ids", "is_top", "_cast_interface")
    K = 8  # cap

    def __init__(self, ids=None, is_top=False):
        self.ids = frozenset(ids or [])
        self.is_top = is_top
        self._cast_interface = None  # interface type cast 시 interface 이름 태깅

    def is_bottom(self) -> bool:
        """Empty set (⊥) check"""
        return not self.is_top and len(self.ids) == 0

    def retagged(self, interface_name):
        """같은 주소 집합, 다른 정적 타입 태그의 **새 객체** (2026-10-22).
        타입 변환(`address(t)`, `IERC20(x)`)은 값의 타입만 바꾼다 — 원래 객체의 태그를 고치면 그 값을 가진 다른 변수까지
        타입이 바뀐다(예전 `IERC20(x)` 가 `x` 의 값에 태그를 덮어썼다)."""
        r = AddressSet(ids=self.ids, is_top=self.is_top)
        r._cast_interface = interface_name
        return r

    @staticmethod
    def top():
        return AddressSet(is_top=True)

    @staticmethod
    def bot():
        return AddressSet(ids=frozenset())

    def leq(self, other: "AddressSet") -> bool:
        """Partial order: self ⊑ other"""
        if self.is_top:
            return other.is_top
        if other.is_top:
            return True
        return self.ids.issubset(other.ids)

    def join(self, other: "AddressSet") -> "AddressSet":
        """Least upper bound (union with cap)"""
        if self.is_top or other.is_top:
            result = AddressSet.top()
        else:
            u = self.ids | other.ids
            result = AddressSet(u) if len(u) <= AddressSet.K else AddressSet.top()
        # _cast_interface 보존: 양쪽이 같으면 유지
        s_ci = getattr(self, '_cast_interface', None)
        o_ci = getattr(other, '_cast_interface', None)
        ci = s_ci or o_ci
        if s_ci and o_ci and s_ci != o_ci:
            ci = None
        result._cast_interface = ci
        return result

    def meet(self, other: "AddressSet") -> "AddressSet":
        """Greatest lower bound (intersection)"""
        if self.is_top:
            return other
        if other.is_top:
            return self
        result = AddressSet(self.ids & other.ids)
        result._cast_interface = getattr(self, '_cast_interface', None) or getattr(other, '_cast_interface', None)
        return result

    def narrow(self, other: "AddressSet") -> "AddressSet":
        """Narrowing operator - refine the approximation"""
        if self.is_top:
            return other
        if other.is_top:
            return self
        result = AddressSet(self.ids & other.ids)
        result._cast_interface = getattr(self, '_cast_interface', None) or getattr(other, '_cast_interface', None)
        return result

    def equals(self, other: "AddressSet") -> BoolInterval:
        """Abstract equality: self == other"""
        if self.is_top or other.is_top:
            return BoolInterval(0, 1)  # Unknown
        inter = self.ids & other.ids
        if not inter:
            return BoolInterval(0, 0)  # Definitely false
        if len(self.ids) == 1 and self.ids == other.ids:
            return BoolInterval(1, 1)  # Definitely true
        return BoolInterval(0, 1)  # May be true or false

    def not_equals(self, other: "AddressSet") -> BoolInterval:
        """Abstract inequality: self != other"""
        eq = self.equals(other)
        # Negate: [0,0] -> [1,1], [1,1] -> [0,0], [0,1] -> [0,1]
        if eq.min_value == 0 and eq.max_value == 0:
            return BoolInterval(1, 1)
        if eq.min_value == 1 and eq.max_value == 1:
            return BoolInterval(0, 0)
        return BoolInterval(0, 1)

    def add_id(self, addr_id: int) -> "AddressSet":
        """Add a single address ID"""
        if self.is_top:
            return self
        new_ids = self.ids | {addr_id}
        return AddressSet(new_ids) if len(new_ids) <= AddressSet.K else AddressSet.top()

    def is_singleton(self) -> bool:
        """Check if this is a singleton set"""
        return not self.is_top and len(self.ids) == 1

    def get_singleton_id(self) -> int | None:
        """Get the single ID if singleton, else None"""
        return next(iter(self.ids)) if self.is_singleton() else None

    def __str__(self):
        if self.is_top:
            return "AddressSet(⊤)"
        if not self.ids:
            return "AddressSet(⊥)"
        return f"AddressSet({{{', '.join(map(str, sorted(self.ids)))}}})"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if not isinstance(other, AddressSet):
            return False
        return self.is_top == other.is_top and self.ids == other.ids

    def __hash__(self):
        return hash((self.is_top, self.ids))


# ═══════════════════════════════════════════════════════════════════
#  AddressManager: 싱글톤 매니저로 symbolic ID 관리
# ═══════════════════════════════════════════════════════════════════

class AddressManager:
    """
    주석 기반 address 할당을 위한 심볼릭 ID 매니저
    - symbolicAddress N → AddressSet({N})
    - arrayAddress[1,2,3] → [AddressSet({1}), AddressSet({2}), AddressSet({3})]
    - 변수명 → ID 역추적 (디버깅 용도)
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
        self._name_to_ids: dict[str, set[int]] = {}  # 변수명 → ID들 (may-alias)

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

    def fresh_address(self) -> AddressSet:
        """새 singleton AddressSet 반환"""
        nid = self.fresh_id()
        return AddressSet(ids={nid})

    # ─────────────────────── 고정 ID 등록 ─────────────────────
    def register_id(self, addr_id: int, var_name: str | None = None):
        """
        symbolicAddress N 같은 고정 ID 등록
        """
        if addr_id not in self._id_to_names:
            self._id_to_names[addr_id] = set()

        if var_name:
            self._id_to_names[addr_id].add(var_name)
            self._name_to_ids.setdefault(var_name, set()).add(addr_id)

    def make_symbolic_address(self, addr_id: int, var_name: str | None = None) -> AddressSet:
        """symbolicAddress N → AddressSet({N})"""
        self.register_id(addr_id, var_name)
        return AddressSet(ids={addr_id})

    # ─────────────────────── 변수 바인딩 ─────────────────────
    def bind_var(self, var_name: str, addr_set: AddressSet):
        """변수명 → AddressSet 바인딩 (역추적용)"""
        if addr_set.is_top or not addr_set.ids:
            return

        for nid in addr_set.ids:
            self._id_to_names.setdefault(nid, set()).add(var_name)
        self._name_to_ids.setdefault(var_name, set()).update(addr_set.ids)

    def get_aliases(self, addr_id: int) -> set[str]:
        """ID → 변수명 조회 (디버깅용)"""
        return self._id_to_names.get(addr_id, set())

    def get_ids(self, var_name: str) -> set[int]:
        """변수명 → ID 조회 (may-alias)"""
        return self._name_to_ids.get(var_name, set())

    # ─────────────────────── 배열 address 파싱 ─────────────────
    @staticmethod
    def parse_array_address(arr_str: str) -> list[AddressSet]:
        """
        'arrayAddress[1,2,3]' → [AddressSet({1}), AddressSet({2}), AddressSet({3})]
        'arrayAddress[]' → []
        """
        import re
        m = re.match(r'arrayAddress\[(.*)\]', arr_str.strip())
        if not m:
            raise ValueError(f"Invalid arrayAddress format: {arr_str}")

        content = m.group(1).strip()
        if not content:
            return []

        ids = [int(x.strip()) for x in content.split(',')]
        return [AddressSet(ids={nid}) for nid in ids]


# ─────────────────────── 글로벌 인스턴스 ─────────────────────
address_manager = AddressManager()
