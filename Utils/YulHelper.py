# Utils/YulHelper.py
"""
Yul 파서 헬퍼. Yul.g4 기반 파서를 사용하여 Yul 코드를 파싱.
"""
from antlr4 import InputStream, CommonTokenStream
from Parser.YulLexer import YulLexer
from Parser.YulParser import YulParser


class YulParserHelpers:

    @staticmethod
    def generate_parse_tree(src: str):
        """Yul 소스 코드를 파싱하여 parse tree 반환"""
        input_stream = InputStream(src)
        lexer = YulLexer(input_stream)
        token_stream = CommonTokenStream(lexer)
        parser = YulParser(token_stream)
        parser.removeErrorListeners()  # Yul 파싱 에러 억제
        return parser.yulUnit()
