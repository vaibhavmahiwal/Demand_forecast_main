"""Add project_name column

Revision ID: add_project_name_column
Revises: 
Create Date: 2025-10-03 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_project_name_column'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Add project_name column to project table
    op.add_column('project', sa.Column('project_name', sa.String(length=200), nullable=False, server_default='Unnamed Project'))

def downgrade():
    # Remove project_name column if needed
    op.drop_column('project', 'project_name')
