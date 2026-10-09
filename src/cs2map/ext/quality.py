"""MCP quality and lighting tools (loaded automatically by server.py)."""
from __future__ import annotations

from .. import checks, compiler, lighting, playtest, game


def run_lighting_build(project, name: str, preset: str = 'full', cubemaps: bool = True, timeout: int = 1800) -> dict:
    """Synchronous orchestration used by the MCP tool and direct integration tests."""
    job = compiler.build_map(project.s, project.jobs, name, preset, timeout)
    result = project.job_status(job.id, timeout, 30)
    if result['state'] != 'succeeded' or not result['result'].get('ok'):
        return {'map':name,'build':result,'cubemaps':None,'ok':False}
    cube = compiler.build_cubemaps(project.s,name, min(600,timeout)) if cubemaps else None
    return {'map':name,'build':result,'cubemaps':cube,'ok':bool(result['result'].get('ok') and (cube is None or cube.get('ok')))}


def register(mcp, project):
    @mcp.tool()
    def lighting_prepare_shaft() -> dict:
        """Compile an opt-in beam='additive' with Valve's shaft shader/masks and material fog disabled.

        Run before map_edit add_light(kind='shaft', beam='additive'). Stock beams remain unchanged.
        """
        return lighting.prepare_shaft_material(project)

    @mcp.tool()
    def check_map(name: str) -> dict:
        """Audit spawns, gameplay volumes, navigation seeds, entities, assets and lighting."""
        return checks.check_map(project, name)

    @mcp.tool()
    def lighting_measure(path: str) -> dict:
        """Measure PNG luminance: mean and clipped_high/clipped_low fractions."""
        return lighting.lighting_measure(path)

    @mcp.tool()
    def playtest_run(name: str, bots: int = 10, seconds: int = 60, spectate: bool = True) -> dict:
        """Load a map, run a bounded bot round, and return status/error samples.
        spectate=true (default): you watch as a spectator, bots play alone (5 v 5 for bots=10)."""
        return playtest.playtest_run(project, name, bots, seconds, spectate)

    @mcp.tool()
    def playtest_views(name: str, views='auto', offset: int = 0, limit: int | None = None) -> dict:
        """Capture triangle-derived interiors/exteriors. Use limit=6, offset=0/6/... for bounded auto batches."""
        return playtest.playtest_views(project, name, views,offset,limit)

    @mcp.tool()
    def lighting_bake_cubemaps(name: str, timeout: int = 600) -> dict:
        """Bake runtime cubemaps; return a compact result and a failure reason."""
        return lighting.bake_cubemaps(project, name, timeout)

    @mcp.tool()
    def lighting_build(name: str, preset: str = 'full', cubemaps: bool = True, timeout: int = 1800) -> dict:
        """Run a fresh map build and, when requested, bake runtime cubemaps after it."""
        return run_lighting_build(project, name, preset, cubemaps, timeout)

    def lighting_op(project, vmap, op, refs):
        return lighting.apply_preset(project, vmap, op.get('preset', 'day'),op.get('fog'))

    project.register_op('lighting_preset', lighting_op)
    from .. import maptypes
    project.register_op('set_map_type', maptypes.set_type_op)
    project.register_op('add_light', lighting.add_light)
    project.register_op('light_rooms', lighting.light_rooms)

