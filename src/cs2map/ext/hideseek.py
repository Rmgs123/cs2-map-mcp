"""Hide and seek: map_edit add_hideseek (hideseek.py)."""
from .. import hideseek


def register(mcp, project):
    project.register_op('add_hideseek', hideseek.add_hideseek)
