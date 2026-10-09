"""MCP tools for CS2 point scripts."""
from __future__ import annotations

from .. import scripting


def register(mcp, project):
    @mcp.tool()
    def script_write(name: str, code: str) -> dict:
        """Write maps/scripts/mcp/<name>.js and compile it; compiler diagnostics are returned."""
        return scripting.script_write(project, name, code)

    @mcp.tool()
    def script_api(query: str = '', limit: int = 20) -> dict:
        """Search installed point_script.d.ts. Exact class/interface names return the entire commented body."""
        return scripting.script_api(project, query, limit)

    @mcp.tool()
    def script_test(map: str, steps: list[dict] | str = 'auto', timeout: float = 90) -> dict:
        """Run a scenario in CS2 under one lock (seconds, 1..90): real input, console checks, frames.
        Steps (one key each): cmd 'console', say 'chat', fire 'name input', walk [x,y,z] feet (teleport),
        go [x,y,z] (physical), press 'button', wait seconds, expect 'regex' (+within), shot 'name' (frame as is, no noclip),
        use true (E, 0.15 s), hold {"key":"attack","seconds":0.9} (always released; keys: attack attack2 use forward back
        left right jump duck reload walk turnleft turnright), aim [x,y,z] or 'entity_name' (turns the view; waits 1 s for
        the client), mouse {"dx":40,"dy":0} (real mouse pixels), focus true|false (game window in front / give it back).
        aim, hold, mouse and use need the game window in front (the runner keeps it there).
        steps='auto' follows the sole add_story in the map. Returns ok, checks, unique JS errors, shots, log path.
        Example: [{"use":true},{"expect":"D_CONTROL drone1 enter","within":2},{"hold":{"key":"forward","seconds":1}},{"shot":"d8_fly"}].
        """
        return scripting.script_test(project, map, steps, timeout)

    project.register_op('add_script', scripting.add_script)

