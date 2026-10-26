"""
Domain/Annotation.py

During 및 Post annotation을 표현하는 클래스들
"""

from __future__ import annotations
from typing import Dict, Any, Optional, List
from Domain.IR import Expression


class DuringAnnotation:
    """@During annotation을 표현하는 클래스"""
    
    def __init__(self, annotation_type: str, line_no: int, 
                 var_ref: Optional[Expression] = None,
                 comp_op: Optional[str] = None,
                 value_expr: Optional[Expression] = None,
                 lhs_expr: Optional[Expression] = None,
                 rhs_expr: Optional[Expression] = None):
        self.annotation_type = annotation_type  # "beforeAfter", "assignCurrent", "returnExpression", etc.
        self.line_no = line_no
        self.var_ref = var_ref
        self.comp_op = comp_op
        self.value_expr = value_expr
        self.lhs_expr = lhs_expr
        self.rhs_expr = rhs_expr
    
    def to_dict(self) -> Dict[str, Any]:
        """직렬화를 위한 딕셔너리 변환"""
        return {
            'type': self.annotation_type,
            'line_no': self.line_no,
            'var_ref': self._serialize_expression(self.var_ref) if self.var_ref else None,
            'comp_op': self.comp_op,
            'value_expr': self._serialize_expression(self.value_expr) if self.value_expr else None,
            'lhs_expr': self._serialize_expression(self.lhs_expr) if self.lhs_expr else None,
            'rhs_expr': self._serialize_expression(self.rhs_expr) if self.rhs_expr else None,
        }
    
    def _serialize_expression(self, expr: Expression) -> Dict[str, Any]:
        """Expression 객체를 딕셔너리로 직렬화"""
        if expr is None:
            return None
        
        return {
            'context': getattr(expr, 'context', None),
            'identifier': getattr(expr, 'identifier', None),
            'operator': getattr(expr, 'operator', None),
            'left': self._serialize_expression(getattr(expr, 'left', None)),
            'right': self._serialize_expression(getattr(expr, 'right', None)),
            'literal': getattr(expr, 'literal', None),
            'member': getattr(expr, 'member', None),
            'index': self._serialize_expression(getattr(expr, 'index', None)),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DuringAnnotation':
        """딕셔너리에서 DuringAnnotation 객체 복원"""
        return cls(
            annotation_type=data['type'],
            line_no=data['line_no'],
            var_ref=cls._deserialize_expression(data.get('var_ref')),
            comp_op=data.get('comp_op'),
            value_expr=cls._deserialize_expression(data.get('value_expr')),
            lhs_expr=cls._deserialize_expression(data.get('lhs_expr')),
            rhs_expr=cls._deserialize_expression(data.get('rhs_expr')),
        )
    
    @classmethod
    def _deserialize_expression(cls, data: Optional[Dict[str, Any]]) -> Optional[Expression]:
        """딕셔너리에서 Expression 객체 복원"""
        if data is None:
            return None
            
        expr = Expression()
        expr.context = data.get('context')
        expr.identifier = data.get('identifier')
        expr.operator = data.get('operator')
        expr.literal = data.get('literal')
        expr.member = data.get('member')
        
        if data.get('left'):
            expr.left = cls._deserialize_expression(data['left'])
        if data.get('right'):
            expr.right = cls._deserialize_expression(data['right'])
        if data.get('index'):
            expr.index = cls._deserialize_expression(data['index'])
            
        return expr


class PostAnnotation:
    """@Post annotation을 표현하는 클래스"""
    
    def __init__(self, annotation_type: str, line_no: int,
                 var_ref: Optional[Expression] = None,
                 comp_op: Optional[str] = None,
                 value_expr: Optional[Expression] = None,
                 lhs_expr: Optional[Expression] = None,
                 rhs_expr: Optional[Expression] = None):
        self.annotation_type = annotation_type  # "entryExit", "returnExpression", "returnVariable", etc.
        self.line_no = line_no
        self.var_ref = var_ref
        self.comp_op = comp_op
        self.value_expr = value_expr
        self.lhs_expr = lhs_expr
        self.rhs_expr = rhs_expr
    
    def to_dict(self) -> Dict[str, Any]:
        """직렬화를 위한 딕셔너리 변환"""
        return {
            'type': self.annotation_type,
            'line_no': self.line_no,
            'var_ref': self._serialize_expression(self.var_ref) if self.var_ref else None,
            'comp_op': self.comp_op,
            'value_expr': self._serialize_expression(self.value_expr) if self.value_expr else None,
            'lhs_expr': self._serialize_expression(self.lhs_expr) if self.lhs_expr else None,
            'rhs_expr': self._serialize_expression(self.rhs_expr) if self.rhs_expr else None,
        }
    
    def _serialize_expression(self, expr: Expression) -> Dict[str, Any]:
        """Expression 객체를 딕셔너리로 직렬화 (DuringAnnotation과 동일)"""
        if expr is None:
            return None
        
        return {
            'context': getattr(expr, 'context', None),
            'identifier': getattr(expr, 'identifier', None),
            'operator': getattr(expr, 'operator', None),
            'left': self._serialize_expression(getattr(expr, 'left', None)),
            'right': self._serialize_expression(getattr(expr, 'right', None)),
            'literal': getattr(expr, 'literal', None),
            'member': getattr(expr, 'member', None),
            'index': self._serialize_expression(getattr(expr, 'index', None)),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PostAnnotation':
        """딕셔너리에서 PostAnnotation 객체 복원"""
        return cls(
            annotation_type=data['type'],
            line_no=data['line_no'],
            var_ref=cls._deserialize_expression(data.get('var_ref')),
            comp_op=data.get('comp_op'),
            value_expr=cls._deserialize_expression(data.get('value_expr')),
            lhs_expr=cls._deserialize_expression(data.get('lhs_expr')),
            rhs_expr=cls._deserialize_expression(data.get('rhs_expr')),
        )
    
    @classmethod
    def _deserialize_expression(cls, data: Optional[Dict[str, Any]]) -> Optional[Expression]:
        """딕셔너리에서 Expression 객체 복원 (DuringAnnotation과 동일)"""
        if data is None:
            return None

        expr = Expression()
        expr.context = data.get('context')
        expr.identifier = data.get('identifier')
        expr.operator = data.get('operator')
        expr.literal = data.get('literal')
        expr.member = data.get('member')

        if data.get('left'):
            expr.left = cls._deserialize_expression(data['left'])
        if data.get('right'):
            expr.right = cls._deserialize_expression(data['right'])
        if data.get('index'):
            expr.index = cls._deserialize_expression(data['index'])

        return expr


class CompoundDuringAnnotation:
    """
    논리 연산자(&&, ||)로 연결된 여러 during clause를 표현하는 클래스

    예: // @During x > 10 && y < 20 || z == 30
    """

    def __init__(self, line_no: int, clauses: List[Dict[str, Any]], logic_ops: List[str]):
        """
        Args:
            line_no: 소스 코드 라인 번호
            clauses: 각 clause의 정보를 담은 dict 리스트
                     예: [{"kind": "beforeAfter", "var": expr, "op": ">"}, ...]
            logic_ops: 논리 연산자 리스트 ('&&' or '||')
                      clauses가 N개면 logic_ops는 N-1개
        """
        self.line_no = line_no
        self.clauses = clauses
        self.logic_ops = logic_ops

    def to_dict(self) -> Dict[str, Any]:
        """직렬화를 위한 딕셔너리 변환"""
        return {
            'type': 'compound_during',
            'line_no': self.line_no,
            'clauses': self.clauses,
            'logic_ops': self.logic_ops
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CompoundDuringAnnotation':
        """딕셔너리에서 CompoundDuringAnnotation 객체 복원"""
        return cls(
            line_no=data['line_no'],
            clauses=data['clauses'],
            logic_ops=data['logic_ops']
        )


class CompoundPostAnnotation:
    """
    논리 연산자(&&, ||)로 연결된 여러 post clause를 표현하는 클래스

    예: // @Post x(Entry < Exit) && Unchanged(y)
    """

    def __init__(self, line_no: int, clauses: List[Dict[str, Any]], logic_ops: List[str]):
        """
        Args:
            line_no: 소스 코드 라인 번호
            clauses: 각 clause의 정보를 담은 dict 리스트
                     예: [{"kind": "entryExit", "var": expr, "op": "<"}, ...]
            logic_ops: 논리 연산자 리스트 ('&&' or '||')
                      clauses가 N개면 logic_ops는 N-1개
        """
        self.line_no = line_no
        self.clauses = clauses
        self.logic_ops = logic_ops

    def to_dict(self) -> Dict[str, Any]:
        """직렬화를 위한 딕셔너리 변환"""
        return {
            'type': 'compound_post',
            'line_no': self.line_no,
            'clauses': self.clauses,
            'logic_ops': self.logic_ops
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CompoundPostAnnotation':
        """딕셔너리에서 CompoundPostAnnotation 객체 복원"""
        return cls(
            line_no=data['line_no'],
            clauses=data['clauses'],
            logic_ops=data['logic_ops']
        )