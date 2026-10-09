"""Feature-area extensions.

Each module here may define:

    def register(mcp, project):
        @mcp.tool()
        def my_tool(...): ...              # new MCP tools
        project.register_op('my_op', fn)   # new map_edit operation
        shapes.register_shape('my_shape', fn)

and is loaded automatically by cs2map.server.  One module per feature area,
owned by one developer/agent at a time (see CONTRIBUTING.md).
"""
