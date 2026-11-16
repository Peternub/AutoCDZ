"""Строгие модели заданий и проверка ответов без учётных данных."""

import ast
import operator
import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class QuestionKind(StrEnum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    SHORT = "short"
    NUMBER = "number"
    MATCH = "match"
    ORDER = "order"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)


class Question(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    kind: QuestionKind
    text: str = Field(min_length=1, max_length=12000)
    options: list[Option] = Field(default_factory=list, max_length=100)
    right: list[Option] = Field(default_factory=list, max_length=100)
    calculation: str | None = Field(default=None, max_length=200)


class Assignment(StrictModel):
    external_id: str = Field(min_length=1, max_length=100)
    subject: str = Field(max_length=200)
    title: str = Field(max_length=500)
    description: str = Field(max_length=20000)
    due_at: str = Field(max_length=40)
    questions: list[Question] = Field(default_factory=list, max_length=100)
    has_materials: bool = False
    diary_done: bool = False


class Pair(StrictModel):
    left: str
    right: str


class Answer(StrictModel):
    values: list[str]
    pairs: list[Pair]
    explanation: str = Field(max_length=6000)
    confidence: float = Field(ge=0, le=1)


class UnsafeContent(ValueError):
    pass


def solver_payload(question: Question):
    # Не сериализуем исходный ответ МЭШ, профиль или объект подключения.
    payload = question.model_dump(mode="json", exclude={"id"})
    text = str(payload)
    patterns = (
        r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}",
        r"(?:\+7|8)[\s()-]*\d{3}[\s()-]*\d{3}[\s()-]*\d{2}[\s()-]*\d{2}",
        r"(?i)(?:bearer\s+|password|пароль|authorization|cookie|access_token|refresh_token)",
        r"https?://", r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise UnsafeContent("Вопрос содержит возможные персональные данные или секреты")
    return payload


def calculate(expression):
    """Ограниченная арифметика без eval и исполнения пользовательского кода."""
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    if len(expression) > 200:
        raise ValueError("Выражение слишком длинное")
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 60:
        raise ValueError("Выражение слишком сложное")

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = Decimal(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            result = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            result = operations[type(node.op)](visit(node.left), visit(node.right))
        else:
            raise ValueError("Неподдерживаемая операция")
        if not result.is_finite() or abs(result) > Decimal("1e20"):
            raise ValueError("Число вне допустимого диапазона")
        return result

    return visit(tree.body)


def validate_answer(question: Question, answer: Answer):
    ids = [option.id for option in question.options]
    if len(set(ids)) != len(ids):
        raise ValueError("Повторяющиеся идентификаторы вариантов")
    values = answer.values
    if question.kind != QuestionKind.MATCH and answer.pairs:
        raise ValueError("Лишние пары в ответе")
    if question.kind == QuestionKind.MATCH:
        right = {option.id for option in question.right}
        pairs = answer.pairs
        if values or not ids or len(pairs) != len(ids) or {p.left for p in pairs} != set(ids) or len({p.left for p in pairs}) != len(pairs):
            raise ValueError("Неполное соответствие")
        if len(right) != len(question.right) or any(p.right not in right for p in pairs):
            raise ValueError("Неизвестный элемент соответствия")
    elif question.kind in (QuestionKind.SINGLE, QuestionKind.MULTIPLE, QuestionKind.ORDER):
        if not values or len(values) != len(set(values)) or not set(values) <= set(ids):
            raise ValueError("Неизвестный или повторный вариант")
        if question.kind == QuestionKind.SINGLE and len(values) != 1:
            raise ValueError("Ожидается один вариант")
        if question.kind == QuestionKind.ORDER and set(values) != set(ids):
            raise ValueError("Неполная последовательность")
    else:
        if len(values) != 1 or not values[0].strip() or len(values[0]) > 2000:
            raise ValueError("Ожидается один краткий ответ")
        if question.kind == QuestionKind.NUMBER:
            try:
                value = Decimal(values[0].replace(",", "."))
                if not value.is_finite():
                    raise ValueError("Некорректное число")
                if question.calculation and abs(value - calculate(question.calculation)) > Decimal("1e-9"):
                    raise ValueError("Числовая проверка не пройдена")
            except (InvalidOperation, ArithmeticError, SyntaxError) as error:
                raise ValueError("Числовая проверка не пройдена") from error
    return answer
