"""분석 오류 분류 (2026-10-22, 사용자 결정 — 증분 편집 트랜잭션).

SourceError = **사용자 소스가 언어 규칙을 어긴다는 확실한 근거가 있는** 오류(예: 어디에도 선언되지 않은 타입).
  편집 중에는 흔히 생기므로 세션을 망가뜨리지 않는다 — 그 업데이트만 반영하지 않고 진단으로 돌려준다.
그 외 예외 = 엔진 내부 실패로 본다(애매하면 이쪽이 기본값 — 엔진 버그가 사용자 오류로 위장하면 발견되지 않는다).

ValueError 를 상속한다: 기존 호출부의 `except ValueError` 계약을 깨지 않기 위해서다.
"""


class SourceError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


class UnresolvedName(ValueError):
    """(2026-10-23, 레지스트리) 다른 선언(컨트랙트·라이브러리·인터페이스·타입)을 **찾지 못한** 오류. `name` = 찾은 이름.
    의존성 레지스트리가 "선언이 아직 없다(Pending)" 와 "분석 실패(Invalid)" 를 가르는 데 쓴다.
    편집 세션의 분류(SourceError 냐 아니냐)는 바꾸지 않는다 — 소스 오류인 자리는 `UndefinedType` 을 쓴다."""
    def __init__(self, name: str, message: str):
        super().__init__(message)
        self.name = name


class UnsupportedShadowing(ValueError):
    """(2026-10-25, 사용자 결정) 파라미터·named return 이 같은 이름의 상태변수(상수 포함)를 가린다 — 합법 Solidity(경고만)지만
    환경이 변수를 이름 하나로 구분해(`env['want']`) 한쪽이 다른 쪽을 덮어쓴다(타입이 다르면 오류, 같으면 조용한 오분석).
    선언 정체성(DeclID) 기반 환경 = future work. 그 전까지는 명시적 미지원."""
    def __init__(self, func: str, name: str, kind: str):
        super().__init__(f"미지원: 함수 '{func}' 의 {kind} '{name}' 이 같은 이름의 상태변수를 가린다 "
                         f"(이름 충돌 — 선언 정체성 기반 환경은 future work)")
        self.func = func
        self.name = name


class AnnotationSyntaxError(ValueError):
    """(2026-10-25) 디버그 입력(`@StateVar/@Param/@GlobalVar/@IReturn`)·intent(`@During/@Post`) 한 줄이 문법에 맞지 않는다.
    예전엔 ANTLR 오류를 출력만 하고 복구된(일부가 빠진) 트리로 진행해 visitor 가 None 으로 죽거나 다른 술어로 조용히 처리됐다."""
    def __init__(self, text: str, errors: list):
        super().__init__(f"문법 오류 `{text}`: " + "; ".join(errors))
        self.text = text
        self.errors = errors


class UndefinedType(SourceError, UnresolvedName):
    """어디에도 선언이 없는 타입 이름 — 소스 오류(SourceError)이면서 미해석 이름(UnresolvedName)."""
    def __init__(self, name: str, message: str, line: int | None = None):
        ValueError.__init__(self, message)     # 두 부모의 __init__ 은 super() 로 이어져 인자가 맞지 않는다 — 필드를 직접 둔다
        self.line = line
        self.name = name
