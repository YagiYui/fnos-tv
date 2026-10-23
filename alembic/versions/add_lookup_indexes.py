"""Add non-unique indexes for existing media-scoped lookups; preserve every row."""
from alembic import op
from sqlalchemy import inspect

revision = 'add_lookup_indexes'
down_revision = 'create_initial_tables'
branch_labels = None
depends_on = None

INDEXES = (
    ('record', 'guid'),
    ('video_config_list', 'guid'),
    ('video_config_url', 'guid'),
    ('video_config_url', 'parent_guid'),
)


def upgrade():
    for table, column in INDEXES:
        name = f'ix_{table}_{column}'
        existing = next((item for item in inspect(op.get_bind()).get_indexes(table) if item['name'] == name), None)
        if existing:
            if existing['column_names'] != [column] or existing['unique']:
                raise RuntimeError('Conflicting lookup index: ' + name)
        else:
            op.create_index(name, table, [column], unique=False)


def downgrade():
    # This revision alone is reversible. Never continue into the destructive older revisions.
    for table, column in reversed(INDEXES):
        name = f'ix_{table}_{column}'
        if any(item['name'] == name for item in inspect(op.get_bind()).get_indexes(table)):
            op.drop_index(name, table_name=table)
