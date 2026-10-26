from Domain.Interval import *
from Domain.Type import SolType
from Domain.AddressSet import AddressSet
from Domain.BytesSet import BytesSet
import copy

class Variables:
    def __init__(self, identifier=None, value=None,
                 isConstant=False, scope=None, typeInfo=None):
        # 기본 속성
        self.identifier = identifier  # 변수명
        self.scope = scope  # 변수의 스코프 (local, state 등)
        self.isConstant = isConstant  # 상수 여부
        self.typeInfo = typeInfo # SolType

        # 값 정보
        self.value = value  # interval
        self.initial_value = copy.deepcopy(value)  # ← NEW

class StructDefinition:
    def __init__(self, struct_name):
        self.struct_name = struct_name
        # (2026-10-23) 멤버 이름 → SolType. 선언 순서(위치 인자 생성자 `S(a, b)`)는 dict 삽입 순서가 보존한다.
        #   예전 형식은 [{'member_name', 'member_type'}] list 였고, dict 로 읽는 쪽(정적 타입 계산)이 조용히 늘 실패했다.
        self.members: dict = {}

    def add_member(self, var_name, type_obj):
        if var_name in self.members:
            raise ValueError(f"struct '{self.struct_name}': duplicate member '{var_name}'")
        self.members[var_name] = type_obj

class EnumDefinition:
    def __init__(self, enum_name):
        self.enum_name = enum_name
        self.members = []  # 멤버들의 리스트

    def add_member(self, member_name):
        if member_name not in self.members:
            self.members.append(member_name)
        else:
            raise ValueError(f"Member '{member_name}' is already defined in enum '{self.enum_name}'.")

    def get_member(self, index):
        return self.members[index]

class GlobalVariable(Variables):
    def __init__(self, identifier=None, isConstant=False, scope=None, base=None, member=None, value=None
                 , typeInfo=None):
        super().__init__(identifier, value, isConstant, scope)
        self.base = base
        self.member = member
        self.value = value
        self.typeInfo = typeInfo

        self.default_value: Interval | str | None = None  # 런타임 기본값
        self.debug_override: Interval | str | None = None  # 마지막 @GlobalVar 값
        self.usage_sites: set[str] = set()   # func_name 만!

    # helper ― override 가 있으면 그것, 없으면 value
    @property
    def current(self):
        return self.debug_override if self.debug_override is not None else self.value

class ArrayVariable(Variables):
    """
    ▸ 정적/동적 배열 래퍼
    ▸ int·uint·bool 은 interval 로,
      address·string·bytes 등은 심볼/AddressManager 를 이용해 초기화

    (2026-10-23, 사용자 승인) **희소 원소별 표현** — A = (N, D, M, S):
      · N = 길이: 정적 = 선언 길이(`typeInfo.arrayLength`), 동적 = `length`(추상 길이, None = 선언에서 유도)
      · M = `elements: {index: 셀}` — 구체화된 원소만. 원소 수 ≠ 길이
      · D = `summary` — 구체화되지 않은 원소의 값(None = 선언 기본값 ⊤). 0 으로 초기화되는 배열은 0 셀
      · S = `sym_cells` — 미확정 인덱스 선택자 셀(`arr[i]`)
      Read(k) = M[k] 이 있으면 그것, 없으면 D 에서 만든 셀. CFG join = 키 합집합에서 Read₁(k) ⊔ Read₂(k), D₁ ⊔ D₂.
    예전엔 정적 배열을 선언 때 원소 단위로 전부 만들었다(`_init_recursive`) — `Observation[16777215]`(PoolTogether
    TWAB ring buffer, w34) 에서 메모리 8GB 초과. storage 는 쓰기 전엔 비용이 없어서 이런 선언이 흔하다.
    """

    def __init__(self, identifier=None, base_type=None,
                 array_length=None,
                 value=None, isConstant=False, scope=None,
                 is_dynamic=False,
                 struct_defs=None, enum_defs=None):
        super().__init__(identifier, value, isConstant, scope)

        self.typeInfo = SolType()
        self.typeInfo.typeCategory = "array"
        self.typeInfo.arrayBaseType = base_type
        self.typeInfo.arrayLength = array_length  # None → 동적
        self.typeInfo.isDynamicArray = is_dynamic
        self.elements: dict[int, Variables] = {}          # 구체화된 원소만 (index → 셀)
        self.struct_defs = struct_defs or {}
        self.enum_defs = enum_defs or {}
        _init_indexed_storage(self)
        self.sym_cells: dict[str, Variables] = {}   # 선택자 셀(arr[i]) — 메타는 self.sym
        # (2026-10-23) 추상 길이. None = 선언에서 유도(array_length) — 구체화한 원소 수와는 별개다.
        self.length = None

    def __setstate__(self, state):
        self.__dict__.update(state)
        _init_indexed_storage(self)
        self.__dict__.setdefault("sym_cells", {})
        self.__dict__.setdefault("length", None)
        if isinstance(self.__dict__.get("elements"), list):      # 예전 pkl (조밀한 list)
            self.elements = dict(enumerate(self.elements))

    def static_length(self):
        """정적 배열의 선언 길이(정적이 아니면 None)."""
        return None if self.typeInfo.isDynamicArray else self.typeInfo.arrayLength

    def in_static_range(self, idx: int) -> bool:
        n = self.static_length()
        return n is None or 0 <= idx < n

    def read_element(self, idx: int):
        """Read(k) — 구체화된 원소면 그것, 아니면 D 에서 만든 셀(저장하지 않는다)."""
        v = self.elements.get(idx)
        return v if v is not None else self.fresh_element(idx)

    # models.py ― ArrayVariable  내부
    def _create_default_value(self, eid: str):
        """
        base_type 에 맞춰 'TOP' 값을 만들어 준다.
        """
        bt = self.typeInfo.arrayBaseType  # SolType | str
        # ─ address ──────────────────────────────────
        if (isinstance(bt, SolType) and bt.elementaryTypeName == "address") \
                or bt == "address":
            return AddressSet.top()

        # ─ bool ─────────────────────────────────────
        if (isinstance(bt, SolType) and bt.elementaryTypeName == "bool") \
                or bt == "bool":
            return BoolInterval(0, 1)  # TOP of bool

        # ─ int / uint ──────────────────────────────
        if isinstance(bt, SolType) and bt.elementaryTypeName and bt.elementaryTypeName.startswith("int"):
            w = bt.intTypeLength or 256
            return IntegerInterval(-(2 ** (w - 1)), 2 ** (w - 1) - 1, w)
        if isinstance(bt, SolType) and bt.elementaryTypeName and bt.elementaryTypeName.startswith("uint"):
            w = bt.intTypeLength or 256
            return UnsignedIntegerInterval(0, 2 ** w - 1, w)

        # ─ 그 밖의 타입(bytes,string,구조체 등) ───
        return f"symbol_{eid}"  # 마지막 보루

    # -----------------------------------------------------------------
    def get_or_create_element(self, idx: int):
        """원소 idx 를 구체화해 돌려준다(그 칸만 — 0‥idx 를 채우지 않는다). 정적 배열은 선언 길이로 범위 검사.
        구체화는 길이를 바꾸지 않는다(`length` 참고)."""
        if idx < 0:
            raise IndexError("negative index")
        if not self.in_static_range(idx):
            raise IndexError(f"index {idx} out of range ({self.static_length()})")
        v = self.elements.get(idx)
        if v is None:
            v = self.elements[idx] = self.fresh_element(idx)
        return v

    def _create_new_array_element(self, idx: int):
        """
        배열 push·동적-초기화 시 1칸짜리 child 를 만든다.
        ▸ base_type 이
            · elementary / address / bool → Variables
            · struct                    → StructVariable
            · 배열                       → (중첩) ArrayVariable
            · mapping                   → MappingVariable
            · enum                      → EnumVariable
        """
        eid = f"{self.identifier}[{idx}]"
        btype = self.typeInfo.arrayBaseType  # SolType | str

        # ─ elementary / address / bool ───────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "elementary":
            # 주소형
            if btype.elementaryTypeName == "address":
                val = AddressSet.top()
                return Variables(eid, val, scope=self.scope, typeInfo=btype)
            # uint / int / bool → ⊤ interval
            if btype.elementaryTypeName.startswith("uint"):
                bits = int(btype.elementaryTypeName[4:] or 256)
                val = UnsignedIntegerInterval(0, 2 ** bits - 1, bits)
                return Variables(eid, val, scope=self.scope, typeInfo=btype)
            if btype.elementaryTypeName.startswith("int"):
                bits = int(btype.elementaryTypeName[3:] or 256)
                val = IntegerInterval(-(2 ** (bits - 1)), 2 ** (bits - 1) - 1, bits)
                return Variables(eid, val, scope=self.scope, typeInfo=btype)
            if btype.elementaryTypeName == "bool":
                return Variables(eid, BoolInterval(0, 1), scope=self.scope, typeInfo=btype)
            # bytes/string 등
            return Variables(eid, f"symbol_{eid}", scope=self.scope, typeInfo=btype)

        # ─ struct ───────────────────────────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "struct":
            sv = StructVariable(eid, btype.structTypeName, scope=self.scope)
            # (2026-10-23) 정의 조회 = 전체 이름, 없으면 마지막 이름(`ObservationLib.Observation` → `Observation`) —
            #   예전엔 전체 이름만 봐서 qualified struct 원소가 **조용히 멤버 없는 struct** 로 만들어졌다. 못 찾으면 오류.
            sdef = self.struct_defs.get(btype.structTypeName) \
                or self.struct_defs.get(btype.structTypeName.rsplit(".", 1)[-1])
            if sdef is None:
                raise ValueError(f"array element {eid}: struct '{btype.structTypeName}' 의 정의가 없다")
            sv.initialize_struct(sdef, struct_defs=self.struct_defs, enum_defs=self.enum_defs)
            return sv

        # ─ enum ─────────────────────────────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "enum":
            return EnumVariable(eid, btype.enumTypeName, scope=self.scope)

        # ─ mapping ──────────────────────────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "mapping":
            return MappingVariable(eid,
                                   btype.mappingKeyType,
                                   btype.mappingValueType,
                                   scope=self.scope,
                                   struct_defs=self.struct_defs,
                                   enum_defs=self.enum_defs)

        # ─ 중첩 배열 ────────────────────────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "array":
            return ArrayVariable(eid,
                                 btype.arrayBaseType,
                                 btype.arrayLength,
                                 scope=self.scope,
                                 is_dynamic=btype.isDynamicArray,
                                 struct_defs=self.struct_defs,
                                 enum_defs=self.enum_defs)

        # ─ interface ────────────────────────────────────────────────
        if isinstance(btype, SolType) and btype.typeCategory == "interface":
            ifc_name = getattr(btype, 'interfaceName', None)
            v = Variables(eid, AddressSet.top(), scope=self.scope, typeInfo=btype)
            if ifc_name:
                v.value._cast_interface = ifc_name
            return v

        # fallback
        raise ValueError(f"Unhandled array base-type for {eid!r}")

    def _create_element_virtual(self, idx: int):
        """
        배열에 실제로 추가하지 않고 가상으로 요소를 생성한다.
        범위 인덱스 join 시 사용.
        """
        return self.fresh_element(idx)

    def fresh_element(self, idx):
        """아직 구체화하지 않은 원소 하나. (2026-10-23) summary 가 있으면 그것에서 만든다 — 미확정 인덱스 쓰기가
        남긴 효과를 새 원소가 잃지 않게. summary 가 없으면 선언 기본값(⊤, D5)."""
        if self.summary is None:
            return self._create_new_array_element(idx)
        v = copy.deepcopy(self.summary)
        rename_tree(v, f"{self.identifier}[{idx}]")
        return v

    def fresh_default(self):
        """summary 가 없을 때의 기본값(선언 기본값)."""
        return self._create_new_array_element("*")


    # ────────────────────────── public API ──────────────────────────
    def initialize_elements(self, init_iv: Interval):
        """int / uint / bool 원소 배열 — 모든 원소가 init_iv (D = init_iv 셀). 원소를 만들지 않는다."""
        # 동적 배열이고 길이도 없으면(선언만) 초기화할 원소가 없다
        if self.typeInfo.isDynamicArray and self.typeInfo.arrayLength is None:
            return
        self.elements = {}
        self.summary = self._uniform_cell("*", self.typeInfo.arrayBaseType, init_iv)

    def initialize_not_abstracted_type(self):
        """address / bytes / string / struct 등 — 선언 기본값(⊤·심볼). D = None(기본값) 이면 충분하다."""
        if self.typeInfo.isDynamicArray and self.typeInfo.arrayLength is None:
            return
        self.elements = {}
        self.summary = None

    def _uniform_cell(self, idx, baseT, init_iv):
        """모든 원소가 같은 값일 때의 D 셀. 중첩 배열이면 그 배열도 같은 방식(희소)으로."""
        eid = f"{self.identifier}[{idx}]"
        if isinstance(baseT, SolType) and baseT.typeCategory == "array":
            sub = ArrayVariable(identifier=eid, base_type=baseT.arrayBaseType, array_length=baseT.arrayLength,
                                scope=self.scope, is_dynamic=baseT.isDynamicArray,
                                struct_defs=self.struct_defs, enum_defs=self.enum_defs)
            if self._is_abstractable(baseT.arrayBaseType):
                sub.initialize_elements(init_iv)
            return sub
        if self._is_abstractable(baseT):
            return Variables(eid, init_iv.copy() if hasattr(init_iv, "copy") else init_iv,
                             scope=self.scope, typeInfo=baseT)
        return self._create_new_array_element(idx)

    def initialize_default_by_base_type(self, is_return_param: bool = False):
        """base_type을 보고 numeric/bool이면 initialize_elements(TOP 또는 0),
        아니면 initialize_not_abstracted_type()으로 위임 — make_param_variable/
        top_from_soltype/MappingVariable._make_value 세 호출부가 공유하는
        단일 진실 공급원(예전엔 세 곳이 서로 다르게 복붙돼 있어서 두 곳이
        base type 체크 없이 무조건 initialize_not_abstracted_type()을 불러
        numeric 배열 원소가 raw 문자열로 초기화되는 버그가 있었음)."""
        bt = self.typeInfo.arrayBaseType
        et = bt.elementaryTypeName if isinstance(bt, SolType) else None

        if et and et.startswith("int"):
            bits = bt.intTypeLength or 256
            val = IntegerInterval(0, 0, bits) if is_return_param else IntegerInterval.top(bits)
            self.initialize_elements(val)
        elif et and et.startswith("uint"):
            bits = bt.intTypeLength or 256
            val = UnsignedIntegerInterval(0, 0, bits) if is_return_param else UnsignedIntegerInterval.top(bits)
            self.initialize_elements(val)
        elif et == "bool":
            val = BoolInterval(0, 0) if is_return_param else BoolInterval.top()
            self.initialize_elements(val)
        else:
            self.initialize_not_abstracted_type()

    @staticmethod
    def _is_abstractable(bt):
        """int / uint / bool 이면 True"""
        if isinstance(bt, SolType):
            et = bt.elementaryTypeName
        else:
            et = str(bt)
        # struct 등 non-elementary base type이면 elementaryTypeName이 None —
        # new <StructType>[](n) 같은 케이스에서 걸림(web3bugs_35_H_08/35_H_12).
        if et is None:
            return False
        return et.startswith("int") or et.startswith("uint") or et == "bool"

class MappingVariable(Variables):
    def __init__(
        self,
        identifier: str | None = None,
        key_type: SolType | None = None,
        value_type: SolType | None = None,
        *,
        scope: str | None = None,
        struct_defs: dict[str, StructDefinition] | None = None,
        enum_defs: dict[str, EnumDefinition] | None = None,          # ⭐️ enum 목록 전달
    ):
        super().__init__(identifier, value=None, isConstant=False, scope=scope)

        self.typeInfo = SolType()
        self.typeInfo.typeCategory     = "mapping"
        self.typeInfo.mappingKeyType   = key_type
        self.typeInfo.mappingValueType = value_type

        # 구체 키 셀과 선택자 셀(키 = 선택자 식 텍스트, 메타는 self.sym)이 함께 들어 있다
        self.mapping: dict[str, Variables] = {}
        self.struct_defs = struct_defs or {}
        self.enum_defs   = enum_defs   or {}
        _init_indexed_storage(self)

    def __setstate__(self, state):
        self.__dict__.update(state)
        _init_indexed_storage(self)

    # ────────────────────────────────────────────────
    # 값-생성 전용 private 헬퍼
    # ────────────────────────────────────────────────
    def _make_value(self, sub_id: str, sol_t: SolType) :
        """
        mappingValueType(SolType) 을 보고 알맞은 객체를 만들어 준다.
        숫자/불린 ⇒ ⊥ interval, 주소 ⇒ TOP interval, 복합 타입 ⇒ 재귀 생성
        """
        # 1) 배열 --------------------------------------------------------
        if sol_t.typeCategory == "array":
            arr = ArrayVariable(
                identifier   = sub_id,
                base_type    = sol_t.arrayBaseType,
                array_length = sol_t.arrayLength,
                is_dynamic   = sol_t.isDynamicArray,
                scope        = self.scope,
                struct_defs  = self.struct_defs,  # ★ struct 정의 전달
                enum_defs    = self.enum_defs,    # ★ enum 정의 전달
            )
            arr.initialize_default_by_base_type()   # 내부까지 재귀 초기화, base type 체크 포함
            return arr

        # 2) 매핑 --------------------------------------------------------
        if sol_t.typeCategory == "mapping":
            return MappingVariable(
                identifier=sub_id,
                key_type=sol_t.mappingKeyType,
                value_type=sol_t.mappingValueType,
                scope=self.scope,
                struct_defs=self.struct_defs,
                enum_defs=self.enum_defs,  # ⭐️ 재귀 전파
            )

        # 3) 구조체 ------------------------------------------------------
        if sol_t.typeCategory == "struct":
            sv = StructVariable(identifier=sub_id,
                                struct_type=sol_t.structTypeName,
                                scope=self.scope)

            if sol_t.structTypeName in self.struct_defs:  # ⭐️ 멤버 초기화
                sv.initialize_struct(self.struct_defs[sol_t.structTypeName],
                                     struct_defs=self.struct_defs, enum_defs=self.enum_defs)

            return sv

        if sol_t.typeCategory == "enum":
            ev = EnumVariable(identifier=sub_id,
                              enum_type=sol_t.enumTypeName,
                              scope=self.scope)

            # enum 정의가 있으면 멤버 테이블 세팅
            if sol_t.enumTypeName in self.enum_defs:
                defn = self.enum_defs[sol_t.enumTypeName]  # EnumDefinition
                ev.members = {m: i for i, m in enumerate(defn.members)}
                ev.valueIndex = 0
                ev.value = defn.members[0]            # ← 첫 멤버명 저장

            return ev

        # 4) interface -------------------------------------------------
        if sol_t.typeCategory == "interface":
            ifc_name = getattr(sol_t, 'interfaceName', None)
            v = Variables(identifier=sub_id, scope=self.scope)
            v.typeInfo = sol_t
            v.value = AddressSet.top()
            if ifc_name:
                v.value._cast_interface = ifc_name
            return v

        # 5) elementary -------------------------------------------------
        v = Variables(identifier=sub_id, scope=self.scope)
        v.typeInfo = sol_t
        et = sol_t.elementaryTypeName

        if et.startswith("int"):
            bits = sol_t.intTypeLength or 256
            v.value = IntegerInterval.top(bits)           # ⊤ interval
        elif et.startswith("uint"):
            bits = sol_t.intTypeLength or 256
            v.value = UnsignedIntegerInterval.top(bits)   # 0 ~ 2ᵇⁱᵗˢ-1
        elif et == "bool":
            v.value = BoolInterval.top()
        elif et == "address":
            v.value = AddressSet.top()   # TOP 주소
        elif et.startswith("bytes") and len(et) > 5:  # bytes32, bytes16 등
            byte_size = int(et[5:])  # "bytes32" -> 32
            v.value = BytesSet.top(byte_size)  # TOP bytes
        else:                               # string / bytes 등
            v.value = f"symbol_{sub_id}"
        return v

    # ────────────────────────────────────────────────
    # public API : get_or_create(key_val)  (기존 get_mapping 대체)
    # ────────────────────────────────────────────────
    def get_or_create(self, key_val) -> Variables:
        """
        키가 없으면 value_type 에 맞춰 **자동 생성** 후 반환
        """
        key_val = str(key_val)
        if key_val not in self.mapping:
            self.mapping[key_val] = self.fresh_entry(key_val)
        return self.mapping[key_val]

    def _create_value_virtual(self, key_val):
        """
        매핑에 실제로 추가하지 않고 가상으로 value를 생성한다.
        범위 키 join 시 사용.
        """
        return self.fresh_entry(key_val)

    def fresh_entry(self, key_val):
        """아직 구체화하지 않은 키의 값. (2026-10-23) summary 가 있으면 그것에서 만든다 — 미확정 키 쓰기가 남긴
        효과를 새 엔트리가 잃지 않게. summary 가 없으면 선언 기본값(⊤, D5)."""
        sub_id = f"{self.identifier}[{key_val}]"
        if self.summary is None:
            return self._make_value(sub_id, self.typeInfo.mappingValueType)
        v = copy.deepcopy(self.summary)
        rename_tree(v, sub_id)
        return v

    def fresh_default(self):
        """summary 가 없을 때의 기본값(선언 기본값)."""
        return self._make_value(f"{self.identifier}[*]", self.typeInfo.mappingValueType)

    def get_default_interval_for_type(self, sol_type):
        # 예시 구현: elementary int/uint/bool만 처리
        if sol_type.typeCategory == 'elementary':
            etype = sol_type.elementaryTypeName
            if etype.startswith("int"):
                return IntegerInterval(float('-inf'), float('inf'), sol_type.intTypeLength)
            elif etype.startswith("uint"):
                return UnsignedIntegerInterval(0, float('inf'), sol_type.intTypeLength)
            elif etype == "bool":
                return BoolInterval(False, True)
        # 기타 타입일 경우 None
        return None


# utils.py  (발췌) ─ StructVariable  전체

class StructVariable(Variables):
    """
    구조체 변수를 나타내는 래퍼.
    ─ members : { fieldName(str) : Variables / ArrayVariable / MappingVariable … }
    """

    def __init__(
        self,
        identifier: str | None = None,
        struct_type: str | None = None,
        value=None,
        isConstant: bool = False,
        scope: str | None = None,
    ):
        super().__init__(identifier, value, isConstant, scope)

        self.typeInfo = SolType()
        self.typeInfo.typeCategory   = "struct"
        self.typeInfo.structTypeName = struct_type       # 구조체 이름
        self.members: dict[str, Variables] = {}          # field → variable ­obj

    def copy_from(self, other: 'StructVariable'):
        for k, member in other.members.items():
            self.members[k] = member  # 얕은 참조·필요시 deepcopy

    # ────────────────────────────────────────────────────────────
    # 초기화
    # ------------------------------------------------------------------
    def initialize_struct(
            self,
            struct_def: StructDefinition,
            *,  # ⬅️  키워드-전용
            struct_defs: dict[str, StructDefinition] | None = None,
            enum_defs: dict[str, "EnumDefinition"] | None = None
    ):
        """
        struct_def.members : { 멤버 이름: SolType } (선언 순서)

        멤버 하나하나의 top-value 생성은 VariableEnv.top_from_soltype에 위임한다
        (array/mapping/struct/interface/enum/elementary 전 타입 지원 - 이 함수가
        독자적으로 타입 분기를 다시 구현하면, 한쪽만 갱신되고 다른 쪽은 안 되는
        문제가 생긴다: enum 지원이 top_from_soltype에는 있었지만 여기엔 없어서
        enum 타입 struct 멤버(e.g. `Side side;`)를 만나면
        `sol_t.elementaryTypeName`이 None인 채로 `.startswith()`가 호출되어
        크래시하는 버그가 실제로 있었다 - Perpetuals.Order.side 케이스).
        """
        # 순환 import 회피를 위해 지역 import (Utils.Helper가 Domain.Variable을
        # import하므로 모듈 최상단에서 import하면 순환 참조가 됨)
        from Utils.Helper import VariableEnv

        if struct_defs is None:
            struct_defs = {struct_def.struct_name: struct_def}  # fallback
        if enum_defs is None:
            enum_defs = {}

        for m_name, m_type in struct_def.members.items():
            var_id = f"{self.identifier}.{m_name}"

            member_var = VariableEnv.top_from_soltype(
                m_type, struct_defs=struct_defs, enum_defs=enum_defs, identifier=var_id
            )
            member_var.scope = self.scope  # top_from_soltype은 scope를 모르므로 여기서 채움
            self.members[m_name] = member_var

    # 디버깅용 표현
    def __repr__(self):
        mem_str = ", ".join(f"{k}:{v.value}" for k, v in self.members.items())
        return f"StructVariable({self.identifier}){{{mem_str}}}"

class EnumVariable(Variables):
    def __init__(self, identifier=None, enum_type=None, value=None, isConstant=False, scope=None):
        super().__init__(identifier, value, isConstant, scope)
        self.typeInfo = SolType()
        self.typeInfo.typeCategory = 'enum'
        self.typeInfo.enumTypeName = enum_type  # 열거형 이름
        self.members = {}  # 멤버 변수들: 멤버명 -> 정수 값 (열거형의 각 멤버는 정수 값에 매핑됨)
        self.value = None  # 현재 설정된 멤버의 이름
        self.valueIndex = None

    def set_member_value(self, member_name):
        """
        열거형 변수의 값을 특정 멤버로 설정합니다.
        :param member_name: 열거형 멤버 이름
        """
        if member_name in self.members:
            self.current_value = member_name
            self.value = self.members[member_name]  # 멤버의 정수 값을 변수의 값으로 설정
        else:
            raise ValueError(f"Member '{member_name}' not found in enum '{self.typeInfo.enumTypeName}'.")

    def get_member_value(self):
        """
        열거형 변수의 현재 값을 반환합니다.
        :return: 현재 설정된 멤버의 이름
        """
        return self.current_value


# ──────────────────────────────────────────────────────────────────────
# (2026-10-23) 인덱스 저장소(mapping / array) 공통 상태
#
#   · 구체 셀   : mapping 의 구체 키 엔트리, array 의 elements
#   · 선택자 셀 : 소스의 접근식 `m[a]` 가 **이 프레임에서** 고르는 위치 하나(키 = 선택자 식 텍스트).
#                 메타 sym[key] = {"scope": 함수, "deps": 선택자가 읽는 변수들, "vals": 만들 때의 키 추상값}.
#                 mapping 은 셀을 self.mapping 에, array 는 self.sym_cells 에 둔다.
#   · summary   : 아직 구체화하지 않은 모든 위치의 값 요약. None = 선언 기본값(⊤, D5).
#                 미확정 위치 쓰기는 summary 에도 weak update 되고, 새 셀은 summary 에서 만들어진다.
#   의미론(읽기·쓰기·alias·무효화)은 Interpreter/Semantics/Storage.py.
# ──────────────────────────────────────────────────────────────────────
def _init_indexed_storage(c):
    c.__dict__.setdefault("sym", {})
    c.__dict__.setdefault("summary", None)


def rename_tree(v, new_id):
    """복사해 온 값 트리의 식별자를 새 위치 이름으로 바꾼다 (summary → 구체 셀)."""
    v.identifier = new_id
    if isinstance(v, StructVariable):
        for name, m in v.members.items():
            rename_tree(m, f"{new_id}.{name}")
    elif isinstance(v, ArrayVariable):
        for i, e in v.elements.items():
            rename_tree(e, f"{new_id}[{i}]")
        for k, e in v.sym_cells.items():
            rename_tree(e, f"{new_id}[{k}]")
        if v.summary is not None:
            rename_tree(v.summary, f"{new_id}[*]")
    elif isinstance(v, MappingVariable):
        for k, e in v.mapping.items():
            rename_tree(e, f"{new_id}[{k}]")
        if v.summary is not None:
            rename_tree(v.summary, f"{new_id}[*]")


def array_length(arr: ArrayVariable):
    """배열의 추상 길이. (2026-10-23) 구체화한 원소 수(len(elements))와 분리했다 — `arr[5]` 를 읽었다고 길이가 6 이 되지 않는다.
    정적 배열 = 선언 길이 · 동적 배열 = 추적 중인 길이(arr.length: new T[](n) · 디버그 .length 입력 · push/pop 이 정한다),
    없으면 모름(⊤, D5). 동적 배열의 typeInfo.arrayLength 는 길이가 아니다(선언 `T[]` 은 0, new 는 구체화 개수)."""
    t = arr.typeInfo
    if not t.isDynamicArray and isinstance(t.arrayLength, int) and t.arrayLength >= 0:
        return UnsignedIntegerInterval(t.arrayLength, t.arrayLength, 256)
    if arr.length is not None:
        return arr.length.copy()
    return UnsignedIntegerInterval.top(256)
