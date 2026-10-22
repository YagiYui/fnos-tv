"""Non-destructive baseline for fresh and pre-Alembic installations."""
from sqlalchemy import Column, String, inspect
from core.db.model import Base


def ensure_schema(op):
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    columns = {column['name'] for column in inspect(bind).get_columns('video_config_url')}
    if 'parent_guid' not in columns:
        op.add_column('video_config_url', Column('parent_guid', String(), nullable=True))
