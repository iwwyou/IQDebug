class SolType:
    def __init__(self):
        self.typeCategory = None  # 'elementary', 'array', 'mapping', 'struct', 'function', 'enum', 'interface'

        # elementary 타입 정보
        self.elementaryTypeName = None  # 예: 'uint256', 'address'
        self.intTypeLength = None  # 정수 타입의 비트 길이 (예: 256)

        # 배열 타입 정보
        self.arrayBaseType = None  # Type 객체
        self.arrayLength = None  # 배열 길이
        self.isDynamicArray = False  # 동적 배열 여부

        # mapping 타입 정보
        self.mappingKeyType = None  # Type 객체
        self.mappingValueType = None  # Type 객체

        # 구조체 타입 정보
        self.structTypeName = None  # 구조체 이름 (문자열)
        self.enumTypeName = None
        # (2026-10-23) struct·enum 을 **선언한** 컨트랙트/라이브러리 이름 (파일 수준 = None).
        #   Solidity 의 struct 타입 = (선언 스코프, 이름). 이름만으로 실행 중인 컨트랙트에서 찾으면, 다른 컨트랙트가 선언한
        #   struct 를 반환하는 외부 호출(`vg.internalParams().protocolGovernance`)의 멤버 타입을 못 찾는다.
        self.structOwner = None
        self.enumOwner = None

        # user-defined value type alias (type UFixed18 is uint256;)
        self.aliasName = None  # resolve 전 원래 타입 이름 (library function lookup용)

        # interface 타입 정보
        self.interfaceName = None  # interface 타입일 때 원본 이름 (e.g., "IERC20", "ILiquidityBasedTWAP")