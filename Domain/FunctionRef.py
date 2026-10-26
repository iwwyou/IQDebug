# Domain/FunctionRef.py
"""함수 타입 값(함수 포인터)의 추상값. (2026-10-23)

지금은 **⊤ 만** 표현한다 — 어떤 함수를 가리키는지 추적하지 않는다(사용자 결정: 우선 ⊤).
함수 타입 변수를 통한 호출은 대상을 모르는 호출로 요약된다(Evaluation.evaluate_function_pointer_call).
"""


class FunctionRef:
    __slots__ = ("bottom",)

    def __init__(self, bottom: bool = False):
        self.bottom = bottom

    @staticmethod
    def top() -> "FunctionRef":
        return FunctionRef(False)

    @staticmethod
    def bot() -> "FunctionRef":
        return FunctionRef(True)

    def is_bottom(self) -> bool:
        return self.bottom

    def join(self, other: "FunctionRef") -> "FunctionRef":
        if not isinstance(other, FunctionRef):
            raise TypeError(f"FunctionRef join with {type(other).__name__}")
        return FunctionRef(self.bottom and other.bottom)

    def widen(self, other: "FunctionRef") -> "FunctionRef":
        return self.join(other)

    def meet(self, other: "FunctionRef") -> "FunctionRef":
        if not isinstance(other, FunctionRef):
            raise TypeError(f"FunctionRef meet with {type(other).__name__}")
        return FunctionRef(self.bottom or other.bottom)

    def equals(self, other) -> bool:
        return isinstance(other, FunctionRef) and self.bottom == other.bottom

    def __eq__(self, other):
        return self.equals(other)

    def __hash__(self):
        return hash(("FunctionRef", self.bottom))

    def __str__(self):
        return "function(⊥)" if self.bottom else "function(⊤)"

    __repr__ = __str__
