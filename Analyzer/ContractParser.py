# Analyzer/ContractParser.py

"""
컨트랙트 전체 소스 코드를 분석 가능한 청크로 분할하는 모듈
soltotestjson.py의 로직을 기반으로 ContractAnalyzer에 맞게 개선
"""

from __future__ import annotations
import re
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class CodeChunk:
    """코드 청크 정보"""
    code: str
    start_line: int
    end_line: int
    event: str = "add"
    chunk_type: Optional[str] = None  # contract, function, library 등
    context_info: Optional[Dict] = None

class ContractParser:
    """
    Solidity 컨트랙트 소스 코드를 분석 가능한 청크로 분할하는 파서
    """
    
    # 패턴 정의
    _ONLY_WS = re.compile(r"^\s*$")          # 공백/탭만
    _OPEN_BLK = re.compile(r"\{\s*$")        # { 로 끝남
    _ONE_LINER = re.compile(r";\s*$")        # ; 로 끝남
    _ONLY_CLO = re.compile(r"^\s*}\s*$")     # } 만 있음
    _COMMENT_LINE = re.compile(r"^\s*//")    # 주석 라인
    _MULTILINE_COMMENT_START = re.compile(r"/\*")
    _MULTILINE_COMMENT_END = re.compile(r"\*/")
    
    # 컨텍스트 패턴
    _CONTRACT_START = re.compile(r"^\s*(contract|interface|library)\s+(\w+)")
    _FUNCTION_START = re.compile(r"^\s*(function|constructor|modifier|fallback|receive)\s*")
    _USING_DIRECTIVE = re.compile(r"^\s*using\s+(\w+)\s+for\s+([^;]+);")
    _PRAGMA_DIRECTIVE = re.compile(r"^\s*pragma\s+")
    _IMPORT_DIRECTIVE = re.compile(r"^\s*import\s+")
    
    def __init__(self):
        self.current_context_stack: List[str] = []
        self.brace_level = 0
        self.in_multiline_comment = False
        
    def parse_contract(self, source: str) -> List[CodeChunk]:
        """
        컨트랙트 소스 코드를 분석 가능한 청크로 분할
        
        Args:
            source: Solidity 소스 코드
            
        Returns:
            CodeChunk 리스트
        """
        lines = source.splitlines()
        chunks = []
        
        self._reset_state()
        
        i = 0
        current_line = 1
        
        while i < len(lines):
            raw_line = lines[i]
            
            # 멀티라인 주석 처리
            if self._handle_multiline_comment(raw_line):
                chunks.append(CodeChunk(
                    code=raw_line,
                    start_line=current_line,
                    end_line=current_line,
                    event="add",
                    chunk_type="comment"
                ))
                i += 1
                current_line += 1
                continue
                
            stripped = raw_line.strip()
            
            # 빈 줄 처리
            if self._ONLY_WS.match(raw_line):
                chunks.append(CodeChunk(
                    code="\n",
                    start_line=current_line,
                    end_line=current_line,
                    event="add",
                    chunk_type="empty"
                ))
                i += 1
                current_line += 1
                continue
            
            # 주석 라인 처리
            if self._COMMENT_LINE.match(stripped):
                chunks.append(CodeChunk(
                    code=raw_line,
                    start_line=current_line,
                    end_line=current_line,
                    event="add",
                    chunk_type="comment"
                ))
                i += 1
                current_line += 1
                continue

            # 단독 '}' 처리
            if self._ONLY_CLO.match(stripped):
                self.brace_level -= 1
                if self.current_context_stack:
                    self.current_context_stack.pop()
                i += 1
                current_line += 1
                continue


                # 코드 청크 분석
            chunk = self._analyze_code_line(raw_line, current_line)
            if chunk:
                chunks.append(chunk)
            
            i += 1
            current_line += 1
            
        return chunks
    
    def _reset_state(self):
        """파서 상태 초기화"""
        self.current_context_stack = []
        self.brace_level = 0
        self.in_multiline_comment = False
    
    def _handle_multiline_comment(self, line: str) -> bool:
        """멀티라인 주석 처리"""
        if self.in_multiline_comment:
            if self._MULTILINE_COMMENT_END.search(line):
                self.in_multiline_comment = False
            return True
        else:
            if self._MULTILINE_COMMENT_START.search(line):
                self.in_multiline_comment = True
                if not self._MULTILINE_COMMENT_END.search(line):
                    return True
        return False
    
    def _analyze_code_line(self, raw_line: str, line_no: int) -> Optional[CodeChunk]:
        """코드 라인 분석하여 청크 생성"""
        stripped = raw_line.strip()
        
        # Pragma/Import 지시문
        if (self._PRAGMA_DIRECTIVE.match(stripped) or 
            self._IMPORT_DIRECTIVE.match(stripped)):
            return CodeChunk(
                code=stripped,
                start_line=line_no,
                end_line=line_no,
                event="add",
                chunk_type="directive"
            )
        
        # Using 지시문
        using_match = self._USING_DIRECTIVE.match(stripped)
        if using_match:
            library_name = using_match.group(1)
            target_type = using_match.group(2).strip()
            return CodeChunk(
                code=stripped,
                start_line=line_no,
                end_line=line_no,
                event="add",
                chunk_type="using_directive",
                context_info={
                    "library_name": library_name,
                    "target_type": target_type
                }
            )
        
        # Contract/Interface/Library 시작
        contract_match = self._CONTRACT_START.match(stripped)
        if contract_match:
            contract_type = contract_match.group(1)  # contract, interface, library
            contract_name = contract_match.group(2)

            if self._OPEN_BLK.search(stripped):
                self.brace_level += 1
                self.current_context_stack.append(f"{contract_type}:{contract_name}")
                code = f"{stripped}\n}}"  # 헤더 + 가짜 닫는 괄호
                return CodeChunk(
                    code=code,
                    start_line=line_no,
                    end_line=line_no + 1,
                    event="add",
                    chunk_type=contract_type,
                    context_info={
                        "name": contract_name,
                        "type": contract_type
                    }
                )
        
        # 함수/생성자/수정자 시작
        if self._FUNCTION_START.match(stripped):
            if self._FUNCTION_START.match(stripped):
                if self._OPEN_BLK.search(stripped):
                    self.brace_level += 1
                    func_info = self._extract_function_info(stripped)
                    self.current_context_stack.append(
                        f"function:{func_info.get('name', 'anonymous')}"
                    )
                    code = f"{stripped}\n}}"  # 헤더 + 가짜 닫는 괄호
                    return CodeChunk(
                        code=code,
                        start_line=line_no,
                        end_line=line_no + 1,
                        event="add",
                        chunk_type="function",
                        context_info=func_info
                    )

        # { 로 끝나는 기타 블록 (if, for, while 등)
        if self._OPEN_BLK.search(stripped):
            self.brace_level += 1
            block_type = self._determine_block_type(stripped)
            self.current_context_stack.append(f"block:{block_type}")
            code = f"{stripped}\n}}"
            return CodeChunk(
                code=code,
                start_line=line_no,
                end_line=line_no + 1,
                event="add",
                chunk_type="block",
                context_info={"block_type": block_type}
            )

        # 세미콜론으로 끝나는 문장
        if self._ONE_LINER.search(stripped):
            stmt_type = self._determine_statement_type(stripped)
            return CodeChunk(
                code=stripped,
                start_line=line_no,
                end_line=line_no,  # 한 줄만 차지!
                event="add",
                chunk_type="statement",
                context_info={
                    "statement_type": stmt_type,
                    "context": self._get_current_context()
                }
            )

        # 기타 처리되지 않은 라인
        return CodeChunk(
            code=stripped,
            start_line=line_no,
            end_line=line_no,
            event="add",
            chunk_type="unknown",
            context_info={"context": self._get_current_context()}
        )
    
    def _extract_function_info(self, line: str) -> Dict:
        """함수 정보 추출"""
        parts = line.split()
        if not parts:
            return {"name": "anonymous", "type": "function"}
        
        func_type = parts[0]  # function, constructor, modifier 등
        
        if func_type == "constructor":
            return {"name": "constructor", "type": "constructor"}
        elif func_type in ["fallback", "receive"]:
            return {"name": func_type, "type": func_type}
        elif len(parts) > 1:
            # function name(...) 형태에서 이름 추출
            name_part = parts[1]
            if '(' in name_part:
                name = name_part.split('(')[0]
            else:
                name = name_part
            return {"name": name, "type": func_type}
        
        return {"name": "anonymous", "type": func_type}
    
    def _determine_block_type(self, line: str) -> str:
        """블록 타입 결정"""
        stripped = line.strip()
        
        if stripped.startswith("if"):
            return "if"
        elif stripped.startswith("else if"):
            return "else_if"
        elif stripped.startswith("else"):
            return "else"
        elif stripped.startswith("for"):
            return "for"
        elif stripped.startswith("while"):
            return "while"
        elif stripped.startswith("do"):
            return "do_while"
        elif stripped.startswith("try"):
            return "try"
        elif stripped.startswith("catch"):
            return "catch"
        elif stripped.startswith("assembly"):
            return "assembly"
        elif stripped.startswith("unchecked"):
            return "unchecked"
        elif stripped.startswith("struct"):
            return "struct"
        elif stripped.startswith("enum"):
            return "enum"
        else:
            return "unknown_block"
    
    def _determine_statement_type(self, line: str) -> str:
        """문장 타입 결정"""
        stripped = line.strip()
        
        if "=" in stripped and not any(op in stripped for op in ["==", "!=", "<=", ">="]):
            return "assignment"
        elif stripped.startswith("return"):
            return "return"
        elif stripped.startswith("require"):
            return "require"
        elif stripped.startswith("assert"):
            return "assert"
        elif stripped.startswith("revert"):
            return "revert"
        elif stripped.startswith("emit"):
            return "emit"
        elif "(" in stripped and ")" in stripped:
            return "function_call"
        elif any(keyword in stripped for keyword in ["uint", "int", "bool", "address", "string", "bytes"]):
            return "variable_declaration"
        else:
            return "expression"
    
    def _get_current_context(self) -> str:
        """현재 컨텍스트 반환"""
        if not self.current_context_stack:
            return "global"
        return self.current_context_stack[-1]
