"""virtual workers

Revision ID: 4e25f581d8bd
Revises: 593c59c4c834
Create Date: 2015-01-20 16:35:26.855490

"""

# revision identifiers, used by Alembic.
revision = '4e25f581d8bd'
down_revision = '593c59c4c834'

from alembic import op
import sqlalchemy as sa


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('manager', schema=None) as batch_op:
        batch_op.add_column(sa.Column('has_virtual_workers', sa.SmallInteger(), nullable=True))

    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('manager', schema=None) as batch_op:
        batch_op.drop_column('has_virtual_workers')

    ### end Alembic commands ###
