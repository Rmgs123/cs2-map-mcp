"""S streetlife extension."""
from __future__ import annotations

from .. import streetlife


def register(mcp, project):
    project.register_op('dress_street', streetlife.dress_street)

    @mcp.tool()
    def street_set_info(style: str = 'inferno') -> dict:
        """Return compact role counts and the local measured street-set JSON path."""
        data = streetlife._load_set(style)
        return {'ok': True, 'style': style,
                'models': len({m for role in data['roles'].values() for m in role['models']}),
                'roles': {name: {'models': len(role['models']), 'mount': role['mount']}
                          for name, role in data['roles'].items()}, 'path': str(streetlife.DATA)}
