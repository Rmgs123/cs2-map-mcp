"""MCP tools and map_edit operations for reusable map structure."""
from __future__ import annotations

from .. import structure


def register(mcp, project):
    @mcp.tool()
    def group_nodes(map: str, ids: list[int | str], name: str = '',
                    dry_run: bool = False, force: bool = False) -> dict:
        """Group direct child nodes into a named CMapGroup.

        All ids must be direct children of the same parent.  The group keeps
        the selected nodes together, so move/duplicate/delete on its id acts
        on the complete subtree.
        """
        return project.map_edit(map, [{'op': 'group', 'ids': ids, 'name': name}], dry_run, force)

    @mcp.tool()
    def prefab_save(map: str, ids: list[int | str], name: str) -> dict:
        """Save selected direct map nodes as maps/prefabs/e_<name>.vmap.

        Coordinates are translated so the selected bounds centre is [0,0,0].
        The source map is unchanged; an existing prefab is backed up first.
        """
        with project._lock:
            return structure.prefab_save(project, map, ids, name)

    @mcp.tool()
    def prefab_list() -> dict:
        """List reusable e_*.vmap prefabs in the addon."""
        return structure.prefab_list(project)

    @mcp.tool()
    def prefab_info(name: str) -> dict:
        """Inspect one prefab's root group, node count and summary."""
        return structure.prefab_info(project, name)

    @mcp.tool()
    def decal_materials(query: str = 'decal', limit: int = 50) -> dict:
        """Find stock/addon materials suitable for projected decals and overlays."""
        return project.asset_search(query, 'material', limit)

    def group(p, vmap, op, refs):
        name = op.get('name', '')
        if not isinstance(name, str) or len(name) > 128:
            raise structure.OpError('name must be a string of at most 128 characters')
        out = structure.group_nodes(vmap, op.get('ids'), name, refs)
        if op.get('ref'):
            refs[op['ref']] = out['id']
            out['ref'] = op['ref']
        return out

    def ungroup(p, vmap, op, refs):
        if op.get('ref'):
            raise structure.OpError('ungroup removes the group and cannot define ref')
        return structure.ungroup_node(vmap, op.get('id'), refs)

    def add_prefab(p, vmap, op, refs):
        return structure.add_prefab(p, vmap, op, refs)

    def add_decal(p, vmap, op, refs):
        return structure.add_decal(p, vmap, op, refs)

    project.register_op('group', group)
    project.register_op('ungroup', ungroup)
    project.register_op('add_prefab', add_prefab)
    project.register_op('add_decal', add_decal)
    project.register_op('add_sign', structure.add_sign)
    project.register_op('add_world_text', structure.add_world_text)
