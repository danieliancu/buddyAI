"""wording: the built-in persona "Ola" lives in a companion, not a smartwatch

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29 16:00:00.000000

Only the system persona (account_id NULL) that still has its seeded prompt is changed; customers' own
personas are never touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "You are Ola, a warm, concise and helpful voice assistant living in a smartwatch."
NEW = "You are Ola, a warm, concise and helpful voice assistant living in a wearable companion."


def _swap(frm: str, to: str) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE personas SET system_prompt = :to "
            "WHERE account_id IS NULL AND name = 'Ola' AND system_prompt = :frm"
        ),
        {"to": to, "frm": frm},
    )


def upgrade() -> None:
    _swap(OLD, NEW)


def downgrade() -> None:
    _swap(NEW, OLD)
