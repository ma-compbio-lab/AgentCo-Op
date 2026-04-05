from __future__ import annotations

import ast
import operator
from typing import Any, Mapping


class ExpressionEvaluationError(ValueError):
    """Raised when an expression is invalid or uses unsupported syntax."""


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.Not: operator.not_,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_COMPARE_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}

_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}


def evaluate_expression(expr: str, context: Mapping[str, Any]) -> Any:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionEvaluationError(str(exc)) from exc
    evaluator = _SafeEvaluator(context)
    return evaluator.visit(tree.body)


def evaluate_predicate(
    expr: str,
    context: Mapping[str, Any],
    *,
    default_on_error: bool | None = False,
) -> bool | None:
    try:
        return bool(evaluate_expression(expr, context))
    except ExpressionEvaluationError:
        return default_on_error


class _SafeEvaluator(ast.NodeVisitor):
    def __init__(self, context: Mapping[str, Any]):
        self.context = context

    def generic_visit(self, node: ast.AST) -> Any:
        raise ExpressionEvaluationError(f"Unsupported expression node: {node.__class__.__name__}")

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id in self.context:
            return self.context[node.id]
        if node.id in {"True", "False", "None"}:
            return {"True": True, "False": False, "None": None}[node.id]
        raise ExpressionEvaluationError(f"Unknown name: {node.id}")

    def visit_List(self, node: ast.List) -> list[Any]:
        return [self.visit(item) for item in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> tuple[Any, ...]:
        return tuple(self.visit(item) for item in node.elts)

    def visit_Set(self, node: ast.Set) -> set[Any]:
        return {self.visit(item) for item in node.elts}

    def visit_Dict(self, node: ast.Dict) -> dict[Any, Any]:
        return {self.visit(key): self.visit(value) for key, value in zip(node.keys, node.values)}

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        if isinstance(node.op, ast.And):
            for value in node.values:
                result = self.visit(value)
                if not result:
                    return False
            return True
        if isinstance(node.op, ast.Or):
            for value in node.values:
                result = self.visit(value)
                if result:
                    return True
            return False
        raise ExpressionEvaluationError(f"Unsupported boolean operator: {node.op.__class__.__name__}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_type = type(node.op)
        if op_type not in _UNARY_OPERATORS:
            raise ExpressionEvaluationError(f"Unsupported unary operator: {op_type.__name__}")
        return _UNARY_OPERATORS[op_type](self.visit(node.operand))

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_type = type(node.op)
        if op_type not in _BINARY_OPERATORS:
            raise ExpressionEvaluationError(f"Unsupported binary operator: {op_type.__name__}")
        return _BINARY_OPERATORS[op_type](self.visit(node.left), self.visit(node.right))

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for operator_node, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            op_type = type(operator_node)
            if op_type not in _COMPARE_OPERATORS:
                raise ExpressionEvaluationError(f"Unsupported comparison operator: {op_type.__name__}")
            if not _COMPARE_OPERATORS[op_type](left, right):
                return False
            left = right
        return True

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        return self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        value = self.visit(node.value)
        index = self.visit(node.slice)
        try:
            return value[index]
        except Exception as exc:
            raise ExpressionEvaluationError(str(exc)) from exc

    def visit_Slice(self, node: ast.Slice) -> slice:
        lower = self.visit(node.lower) if node.lower is not None else None
        upper = self.visit(node.upper) if node.upper is not None else None
        step = self.visit(node.step) if node.step is not None else None
        return slice(lower, upper, step)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        value = self.visit(node.value)
        if isinstance(value, Mapping):
            if node.attr not in value:
                raise ExpressionEvaluationError(f"Unknown key: {node.attr}")
            return value[node.attr]
        try:
            return getattr(value, node.attr)
        except AttributeError as exc:
            raise ExpressionEvaluationError(str(exc)) from exc

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ExpressionEvaluationError("Only direct function calls are allowed")
        function_name = node.func.id
        if function_name not in _ALLOWED_FUNCTIONS:
            raise ExpressionEvaluationError(f"Function not allowed: {function_name}")
        function = _ALLOWED_FUNCTIONS[function_name]
        args = [self.visit(arg) for arg in node.args]
        kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords}
        return function(*args, **kwargs)

