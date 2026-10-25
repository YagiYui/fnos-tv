"""创建初始数据库表

Revision ID: create_initial_tables
Revises: ba791c53110e
Create Date: 2025-09-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'create_initial_tables'
down_revision: Union[str, Sequence[str], None] = 'ba791c53110e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Preserve installations that already have some or all baseline tables."""
    from core.db.migrations import ensure_schema
    ensure_schema(op)


def downgrade() -> None:
    """删除数据库表."""
    op.drop_table('video_config_url')
    op.drop_table('video_config_list')
    op.drop_table('record')