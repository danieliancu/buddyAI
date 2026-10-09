"""Longest question 15 s -> 30 s on existing watches

Device settings are stored whole (device_settings.data), so the new default (DeviceSettings.max_listen_s = 30)
only reaches new watches. Watches still on the old default (15) move to 30; a value someone chose (anything
else) is kept. The settings version goes up so the watch takes the new listening window at its next sync.
Downgrade: 30 -> 15 (a watch that had chosen 30 before this migration also goes back to 15).

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-09 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SETTINGS = sa.table(
    'device_settings',
    sa.column('device_id', sa.String),
    sa.column('version', sa.Integer),
    sa.column('data', sa.JSON),
)


def _move(old: int, new: int) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(SETTINGS.c.device_id, SETTINGS.c.version, SETTINGS.c.data)).all()
    for device_id, version, data in rows:
        if isinstance(data, dict) and data.get('max_listen_s') == old:
            conn.execute(
                SETTINGS.update()
                .where(SETTINGS.c.device_id == device_id)
                .values(data={**data, 'max_listen_s': new}, version=(version or 0) + 1)
            )


def upgrade() -> None:
    _move(15, 30)


def downgrade() -> None:
    _move(30, 15)
