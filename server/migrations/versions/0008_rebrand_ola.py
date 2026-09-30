"""rebrand: the built-in persona "Buddy" becomes "Ola" (brand: ola)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 17:00:00.000000

Only the system persona (account_id NULL) that still has its original seeded name and prompt is
renamed; customers' own personas are never touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("Buddy", "You are Buddy, a warm, concise and helpful voice assistant living in a smartwatch.")
NEW = ("Ola", "You are Ola, a warm, concise and helpful voice assistant living in a smartwatch.")


def _swap(frm: tuple[str, str], to: tuple[str, str]) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE personas SET name = :to_name, system_prompt = :to_prompt "
            "WHERE account_id IS NULL AND name = :name AND system_prompt = :prompt"
        ),
        {"to_name": to[0], "to_prompt": to[1], "name": frm[0], "prompt": frm[1]},
    )


def upgrade() -> None:
    _swap(OLD, NEW)


def downgrade() -> None:
    _swap(NEW, OLD)
