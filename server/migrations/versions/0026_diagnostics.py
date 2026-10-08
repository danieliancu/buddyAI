"""ola Diagnostics: diag_incidents, diagnostic columns on device_issues, devices.last_session_id

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-08 12:00:00.000000

Additive only, safe on a live database: one new table, new nullable columns. Every existing issue
becomes its own incident (legacy = true), classified only from what was stored: the reset reason of a
reboot and the drop reason of a lost connection. Nothing else is filled in, so old events never get
invented details. The mapping is frozen here on purpose (no app imports).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
T = sqlmodel.sql.sqltypes.UTCDateTime

_CRASH = "('panic', 'cpu_lockup')"
_FREEZE = "('int_wdt', 'task_wdt', 'wdt')"
_POWER = "('brownout', 'pwr_glitch')"
_ROUTINE = "('power_on', 'software', 'usb', 'jtag', 'external', 'deepsleep')"

# (category, confidence, reason_code, suspected_component, severity) from the stored kind/reason only
_CASE_CATEGORY = f"""CASE
    WHEN kind = 'reboot' THEN 'watch'
    WHEN kind = 'disconnect' AND reason = 'wifi_lost' THEN 'connection'
    WHEN kind = 'disconnect' AND reason = 'reconnect' THEN 'watch'
    ELSE 'undetermined' END"""
_CASE_CONFIDENCE = f"""CASE
    WHEN kind = 'reboot' AND (reason IN {_CRASH} OR reason IN {_FREEZE} OR reason IN {_POWER} OR reason IN {_ROUTINE})
        THEN 'confirmed'
    WHEN kind = 'disconnect' AND reason IN ('wifi_lost', 'reconnect') THEN 'confirmed'
    ELSE 'unknown' END"""
_CASE_REASON = f"""CASE
    WHEN kind = 'reboot' AND reason IN {_CRASH} THEN 'watch_crash'
    WHEN kind = 'reboot' AND reason IN {_FREEZE} THEN 'watch_freeze'
    WHEN kind = 'reboot' AND reason IN {_POWER} THEN 'watch_power_fault'
    WHEN kind = 'reboot' AND reason IN {_ROUTINE} THEN 'watch_restart'
    WHEN kind = 'reboot' THEN 'watch_restart_unknown'
    WHEN kind = 'disconnect' AND reason = 'wifi_lost' THEN 'wifi_lost'
    WHEN kind = 'disconnect' AND reason = 'reconnect' THEN 'reconnect_requested'
    WHEN kind = 'disconnect' AND reason = 'hello_timeout' THEN 'hello_timeout'
    WHEN kind = 'disconnect' THEN 'link_lost_unknown'
    WHEN kind = 'turn_interrupted' THEN 'turn_interrupted'
    WHEN kind = 'server_timeout' THEN 'watch_silent'
    ELSE 'unknown' END"""
_CASE_COMPONENT = f"""CASE
    WHEN kind = 'reboot' AND (reason IN {_CRASH} OR reason IN {_FREEZE}) THEN 'firmware'
    WHEN kind = 'reboot' AND reason IN {_POWER} THEN 'power'
    WHEN kind = 'disconnect' AND reason = 'wifi_lost' THEN 'wifi'
    ELSE NULL END"""
_CASE_DETECTED = "CASE WHEN kind IN ('reboot', 'disconnect') THEN 'watch' ELSE 'server' END"


def upgrade() -> None:
    op.create_table(
        'diag_incidents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', S(length=64), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('correlation_key', S(length=160), nullable=False),
        sa.Column('category', S(length=16), nullable=False),
        sa.Column('severity', S(length=8), nullable=False),
        sa.Column('confidence', S(length=12), nullable=False),
        sa.Column('reason_code', S(length=48), nullable=False),
        sa.Column('suspected_component', S(length=32), nullable=True),
        sa.Column('detected_by', S(length=8), nullable=False),
        sa.Column('primary_event_id', sa.Integer(), nullable=True),
        sa.Column('session_id', S(length=64), nullable=True),
        sa.Column('turn_id', sa.Integer(), nullable=True),
        sa.Column('fw_version', S(length=32), nullable=False),
        sa.Column('event_count', sa.Integer(), nullable=False),
        sa.Column('legacy', sa.Boolean(), nullable=False),
        sa.Column('occurred_at', T(), nullable=False),
        sa.Column('recovered_at', T(), nullable=True),
        sa.Column('updated_at', T(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_diag_incidents_correlation_key', 'diag_incidents', ['correlation_key'], unique=True)
    op.create_index('ix_diag_incidents_session_id', 'diag_incidents', ['session_id'])
    op.create_index('ix_diag_incidents_time', 'diag_incidents', ['occurred_at', 'id'])
    op.create_index('ix_diag_incidents_device_time', 'diag_incidents', ['device_id', 'occurred_at'])
    op.create_index('ix_diag_incidents_category_time', 'diag_incidents', ['category', 'occurred_at'])

    with op.batch_alter_table('device_issues') as b:
        b.add_column(sa.Column('incident_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('detected_by', S(length=8), nullable=True))
        b.add_column(sa.Column('category', S(length=16), nullable=True))
        b.add_column(sa.Column('confidence', S(length=12), nullable=True))
        b.add_column(sa.Column('suspected_component', S(length=32), nullable=True))
        b.add_column(sa.Column('reason_code', S(length=48), nullable=True))
        b.add_column(sa.Column('session_id', S(length=64), nullable=True))
        b.add_column(sa.Column('turn_id', sa.Integer(), nullable=True))
        b.add_column(sa.Column('occurred_at', T(), nullable=True))
        b.add_column(sa.Column('dedup_key', S(length=96), nullable=True))
    op.create_index('ix_device_issues_incident_id', 'device_issues', ['incident_id'])
    op.create_index('ix_device_issues_session_id', 'device_issues', ['session_id'])
    op.create_index('uq_device_issues_dedup', 'device_issues', ['device_id', 'dedup_key'], unique=True)

    with op.batch_alter_table('devices') as b:
        b.add_column(sa.Column('last_session_id', S(length=64), nullable=True))

    # Back-fill: one legacy incident per existing issue, linked by a key built from the issue id.
    op.execute(f"""
        INSERT INTO diag_incidents (device_id, account_id, correlation_key, category, severity, confidence, reason_code,
            suspected_component, detected_by, primary_event_id, session_id, turn_id, fw_version, event_count, legacy,
            occurred_at, recovered_at, updated_at)
        SELECT device_id, account_id, 'legacy:' || CAST(id AS VARCHAR(20)), {_CASE_CATEGORY}, severity, {_CASE_CONFIDENCE},
            {_CASE_REASON}, {_CASE_COMPONENT}, {_CASE_DETECTED}, id, NULL, NULL, fw_version, 1, TRUE,
            created_at, NULL, created_at
        FROM device_issues
    """)
    op.execute("""
        UPDATE device_issues SET incident_id = (
            SELECT i.id FROM diag_incidents i WHERE i.correlation_key = 'legacy:' || CAST(device_issues.id AS VARCHAR(20)))
    """)


def downgrade() -> None:
    with op.batch_alter_table('devices') as b:
        b.drop_column('last_session_id')
    op.drop_index('uq_device_issues_dedup', table_name='device_issues')
    op.drop_index('ix_device_issues_session_id', table_name='device_issues')
    op.drop_index('ix_device_issues_incident_id', table_name='device_issues')
    with op.batch_alter_table('device_issues') as b:
        for c in ('dedup_key', 'occurred_at', 'turn_id', 'session_id', 'reason_code', 'suspected_component',
                  'confidence', 'category', 'detected_by', 'incident_id'):
            b.drop_column(c)
    op.drop_index('ix_diag_incidents_category_time', table_name='diag_incidents')
    op.drop_index('ix_diag_incidents_device_time', table_name='diag_incidents')
    op.drop_index('ix_diag_incidents_time', table_name='diag_incidents')
    op.drop_index('ix_diag_incidents_session_id', table_name='diag_incidents')
    op.drop_index('ix_diag_incidents_correlation_key', table_name='diag_incidents')
    op.drop_table('diag_incidents')
