"""Embedding vectors in a column that works on both databases.

PostgreSQL: the pgvector `vector` type (untyped dimension; the row's `dims` says how many), searched with
`<=>` (cosine distance) in SQL. SQLite (development, tests): a JSON list, searched in Python. No extra
Python package: pgvector accepts and returns the text form "[0.1,0.2,...]".
"""

from __future__ import annotations

import json
import math
from typing import Any, Sequence

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator, UserDefinedType


class PgVector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"

    def bind_processor(self, dialect):
        def process(value):
            return None if value is None else to_text(value)

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or isinstance(value, list):
                return value
            return [float(x) for x in str(value).strip("[]").split(",") if x.strip()]

        return process


class EmbeddingVector(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PgVector())
        return dialect.type_descriptor(JSON())


def to_text(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """1 - cosine similarity (pgvector's `<=>`); 1.0 for a zero vector or different lengths."""
    if len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 1.0
    return 1.0 - dot / (na * nb)


def parse(value: Any) -> list[float]:
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, str):
        return [float(x) for x in json.loads(value)]
    return []
