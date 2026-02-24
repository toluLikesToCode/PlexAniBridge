"""add provider-neutral sync_history columns

Revision ID: 7a3d2f1f9c0a
Revises: 90496c989bdd
Create Date: 2026-02-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a3d2f1f9c0a"
down_revision: Union[str, None] = "90496c989bdd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sync_history", schema=None) as batch_op:
        batch_op.add_column(sa.Column("server_provider", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("server_guid", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("server_rating_key", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("server_child_rating_key", sa.String(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "server_type",
                sa.Enum(
                    "MOVIE",
                    "SHOW",
                    "SEASON",
                    "EPISODE",
                    name="mediatype",
                    create_type=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_sync_history_server_provider", ["server_provider"], unique=False
        )
        batch_op.create_index(
            "ix_sync_history_server_guid", ["server_guid"], unique=False
        )
        batch_op.create_index(
            "ix_sync_history_server_type", ["server_type"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("sync_history", schema=None) as batch_op:
        batch_op.drop_index("ix_sync_history_server_type")
        batch_op.drop_index("ix_sync_history_server_guid")
        batch_op.drop_index("ix_sync_history_server_provider")
        batch_op.drop_column("server_type")
        batch_op.drop_column("server_child_rating_key")
        batch_op.drop_column("server_rating_key")
        batch_op.drop_column("server_guid")
        batch_op.drop_column("server_provider")
