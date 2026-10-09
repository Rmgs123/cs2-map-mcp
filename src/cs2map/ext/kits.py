"""Stock-part catalogue MCP tools and placement operation."""
from __future__ import annotations
from .. import kits


def register(mcp, project):
    @mcp.tool()
    def kit_list(category: str | None = None, style: str | None = None,
                 mount: str | None = None, tags: list[str] | None = None) -> dict:
        """Find stock parts with sizes in CS2 inches and game thumbnail paths.

        Filters are exact; tags require all supplied tags. Example:
        kit_list(category="street", tags=["bench"]). Open kit_gallery's PNG
        contact sheet to choose by appearance, then place its full_key.
        """
        return kits.kit_list(project,category,style,mount,tags)

    @mcp.tool()
    def kit_info(key: str) -> dict:
        """Inspect kit/key: measured bounds, local front, mount, collision, thumb.

        verified=game means a frame was opened and the front reviewed;
        verified=bounds means native measurement only.
        """
        return {'ok':True, **kits.part(project,key)}

    @mcp.tool()
    def kit_gallery(kit: str | None = None, category: str | None = None,
                    keys: list[str] | None = None, offset: int = 0, limit: int = 12) -> dict:
        """Build a_gallery (full), capture CS2 and return a labelled PNG sheet.

        Choose exactly one of kit/category/keys; offset>=0 and limit=1..12.
        Large cars/trees get individual full-scale thumbnails. If pending,
        repeat the identical call to resume build/capture. Open sheet/thumbs;
        the tool never promotes verified automatically.
        """
        return kits.gallery(project,kit,category,keys,offset,limit)

    # Layout visits nested points recursively. Run this resolver first to keep
    # the inward wall normal available for floor-mounted benches as well.
    project.register_preprocessor(kits.preprocess)
    project.preprocessors.remove(kits.preprocess)
    project.preprocessors.insert(0,kits.preprocess)
    project.register_op('add_kit_part',kits.add_kit_part)
