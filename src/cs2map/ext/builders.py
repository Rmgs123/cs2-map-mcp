"""Fences, railings, banks, bare trees and utility lines as map_edit operations; discovered by server.py."""
from .. import builders


def register(mcp, project):
    for name in ('add_fence', 'add_railing', 'add_bank', 'add_tree', 'add_utility_line'):
        project.register_op(name, getattr(builders, name))
