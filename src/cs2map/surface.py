"""The public MCP tools: the one surface AI clients see (owned by the integrator).

Feature modules (``ext/*.py``) and the core in ``server.py`` register their tool functions into ``RAW``
(a collector, not the MCP server). This module publishes a smaller, uniform set of tools on top of them:
similar tools are merged (one tool, a ``kind``/mode parameter), every description follows one style
(what it does -> main parameters -> example -> what it returns), and the JSON schemas are compacted.
New capabilities go into ``map_edit`` operations or parameters of these tools, not into new tools.
"""
from __future__ import annotations

import functools

from typing import Any, Literal

RAW: dict[str, Any] = {}


class Collector:
    """Stands in for FastMCP in ``register(mcp, project)``: tools go to RAW, everything else to the server."""

    def __init__(self, mcp):
        self._mcp = mcp

    def tool(self, *args, **kwargs):
        def deco(fn):
            RAW[kwargs.get('name') or fn.__name__] = fn
            return fn
        return deco

    def __getattr__(self, name):
        return getattr(self._mcp, name)


def compact_schema(schema):
    """Drop what a client does not need: titles, `anyOf [X, null]` of optional parameters, default additionalProperties."""
    if isinstance(schema, list):
        return [compact_schema(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == 'title' or (key == 'additionalProperties' and value is True) or (key == 'default' and value is None):
            continue
        out[key] = compact_schema(value)
    options = out.get('anyOf')
    if isinstance(options, list):
        kept = [o for o in options if o != {'type': 'null'}]
        if len(kept) == 1 and len(kept) < len(options):
            out.pop('anyOf')
            out = {**kept[0], **out}
        elif len(kept) < len(options):
            out['anyOf'] = kept
    return out


def register(mcp, project) -> list[str]:
    R = RAW

    def on_release(fn):
        """map="<addon>/<map>": the call works on that release addon (map_build(..., "release")) for its duration."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from .project import ADDON_SETTINGS
            key = 'map' if 'map' in kwargs else 'name' if 'name' in kwargs else None
            if key:
                token, kwargs[key] = project.released(kwargs[key])
            elif args:
                token, first = project.released(args[0])
                args = (first, *args[1:])
            else:
                token = None
            try:
                return fn(*args, **kwargs)
            finally:
                if token is not None:
                    ADDON_SETTINGS.reset(token)
        return wrapper

    # ---- start ---------------------------------------------------------------------------------------------
    @mcp.tool()
    def status() -> dict:
        """Check the environment: CS2 (path, running, Workshop Tools mode), the addon and its newest maps, Blender,
        Node.js, Source2Viewer, running and recent jobs, the latest crash dump. Call first.
        Returns {cs2, addon, blender, node, source2viewer, jobs_running, recent_jobs, ...}."""
        return R['status']()

    @mcp.tool()
    def read_guide(section: str | None = None) -> str:
        """The guide. No section: the core (workflow, every map_edit operation, shapes, entity recipes, sizes),
        the index of sections and of your local notes. section="vehicles"|"weapons"|"terrain"|...: one feature
        area; "my": your notes from earlier chats; "my:<name>": one note or recipe; "all": everything.
        Read the core before the first map_edit and a section before using its operations."""
        return R['read_guide'](section)

    @mcp.tool()
    def my_notes(action: Literal['list', 'read', 'write'] = 'list',
                 kind: Literal['note', 'recipe', 'style', 'feedback'] | None = None,
                 name: str | None = None, text: str | None = None, data: Any = None, append: bool = False) -> dict:
        """Your local layer my/ (kept between chats, never in git): note = a lesson or the user's taste, recipe =
        a batch that worked (text/data), style = an own look (data; map_edit "style":"my:<name>"), feedback = a
        problem with this MCP for its author (a tool error you did not cause, a wrong guide, a missing feature;
        name = tool, text = wanted / happened / workaround) appended to my/feedback.md. Read notes before work,
        write after findings. append=true adds to a note. Returns {notes, recipes, styles} | {text, data} | {files}."""
        return R['my_notes'](action, kind, name, text, data, append)

    # ---- maps ----------------------------------------------------------------------------------------------
    @mcp.tool()
    def map_create(name: str, template: Literal['basic', 'empty', 'defuse', 'hostage', 'wingman'] = 'basic',
                   overwrite: bool = False,
                   type: Literal['competitive', 'story', 'mode', 'sandbox', 'hideseek', 'custom'] | None = None) -> dict:
        """Create a map in the addon. name: a-z0-9_. template: basic (sky, sun, fog; recommended) | empty |
        defuse/hostage/wingman (Valve gameplay templates). type picks the checks and end tests (competitive,
        story, mode, sandbox, hideseek, custom = default; it never limits what you build).
        Example: map_create(name="arena", type="competitive"). Returns {map, path, type}."""
        return R['map_create'](name, template, overwrite, type)

    @mcp.tool()
    def map_list() -> dict:
        """Every map of the addon with its size and whether the compiled version is up to date. Returns {maps}."""
        return R['map_list']()

    @mcp.tool()
    def map_info(name: str, about: Literal['nodes', 'layout', 'geometry', 'terrain', 'street'] = 'nodes',
                 key: str | int | None = None, points: list[list[float]] | None = None,
                 classname: str | None = None, kind: Literal['entity', 'mesh', 'group'] | None = None,
                 ids: list[int] | None = None, full: bool = False, offset: int = 0, limit: int = 200) -> dict:
        """Read a map. about: nodes (default: id, type, origin, classname/targetname, bounds; filter by classname,
        kind, ids; full=true adds keyvalues and I/O) | layout (rooms: interiors, floor_z, openings; key=layout name)
        | geometry (one mesh: faces, closure, bounds; key=node id or @ref) | terrain (plans; key=terrain name;
        points=[[x,y],...] samples ground z, normal, slope) | street (centreline, slope, lots; key=street).
        Example: map_info(name="arena", about="terrain", key="hills", points=[[0,0]])."""
        if about == 'layout':
            return R['layout_info'](name, key)
        if about == 'geometry':
            if key is None:
                raise ValueError('about="geometry" needs key = a mesh node id or @ref')
            return R['geometry_info'](name, key)
        if about == 'terrain':
            if points is not None:
                if key is None:
                    raise ValueError('terrain points need key = the terrain name')
                return R['terrain_sample'](name, key, points)
            return R['terrain_info'](name, key)
        if about == 'street':
            return R['street_info'](name, key)
        return R['map_info'](name, classname, kind, ids, full, offset, limit)

    @mcp.tool()
    def map_edit(name: str, operations: list[dict[str, Any]], dry_run: bool = False, force: bool = False) -> dict:
        """Apply a batch of operations atomically (all or nothing, one save, automatic backup). Every operation
        and field is in the guide (read_guide); unknown fields are refused. dry_run=true validates without saving;
        "ref":"x" names a result for later "@x"; force=true writes a map open in Hammer without unsaved edits.
        Example: [{"op":"add_mesh","shape":{"shape":"box","mins":[0,0,-16],"maxs":[1024,1024,0]},
        "material":"materials/dev/dev_measuregeneric01.vmat"},
        {"op":"add_entity","classname":"info_player_terrorist","origin":[100,0,8],"angles":[0,90,0]}].
        Returns {ok, results: [{op, id, ...}], backup}."""
        return R['map_edit'](name, operations, dry_run, force)

    @mcp.tool()
    def map_backups(name: str, restore: str | None = None, force: bool = False) -> dict:
        """List the automatic backups of a map (newest first), or restore=<backup id> to roll back (the current
        file is backed up first). Returns {backups} or {restored}."""
        if restore:
            return R['map_restore'](name, restore, force)
        return R['map_backups'](name)

    @mcp.tool()
    def map_build(name: str, preset: Literal['fast', 'full', 'final', 'release'] = 'fast') -> dict:
        """Compile the map in the background. preset: fast (geometry, entities; seconds) | full (+ baked light,
        visibility, bot navigation: needed for bots) | final (slow, high-quality light) | release (own addon for
        the Workshop; guide section publish). Then poll job_status(job)."""
        return R['map_build'](name, preset)

    @mcp.tool()
    def job_status(job: str, wait_seconds: float = 20, log_lines: int = 15, cancel: bool = False) -> dict:
        """State of a background job (build, compile, Blender); waits up to wait_seconds (max 50) for the end.
        cancel=true stops it. Returns {state, result: {ok, errors, warnings, missing_assets}, log}."""
        if cancel:
            return R['job_cancel'](job)
        return R['job_status'](job, wait_seconds, log_lines)

    @mcp.tool()
    def check_map(name: str) -> dict:
        """Static audit of a map: spawns, gameplay volumes, navigation seeds, entities and their I/O, missing
        assets, lights, mechanics (controllables, weapons, mini-games). Returns {ok, problems: [{severity, code,
        id, message, fix}]}."""
        return R['check_map'](name)

    @mcp.tool()
    def visual_check(name: str, limit: int = 200) -> dict:
        """Scene audit from measured model bounds: floating or deeply intersecting props, unsupported lamps,
        open meshes, likely z-fighting, unbacked text, exposed foundations. Returns {ok, problems with ids and fixes
        (first `limit`, errors first), summary.by_code}."""
        return R['visual_check'](name, limit)

    # ---- references -----------------------------------------------------------------------------------------
    @mcp.tool()
    def asset_search(query: str = '', kind: Literal['material', 'model', 'prop', 'texture', 'sound', 'particle',
                                                    'decal', 'wear', 'smartprop', 'mine', 'any'] = 'material',
                     limit: int | None = None) -> dict:
        """Find stock CS2 and addon assets; all words must match. kind: material | model (files) | prop (stock
        props with measured size and notes; then prop_preview) | texture | sound (events and raw .vsnd) |
        particle (.vpcf and aliases like fire_small) | decal | wear (facade wear overlays) | smartprop | any |
        mine (the user's files in my/inbox: photos, models, sounds, with the tool that turns each into an asset).
        Example: asset_search(query="concrete floor"). Returns {results}."""
        if kind == 'mine':
            return R['my_inbox'](query, limit or 50)
        if kind == 'prop':
            return R['prop_search'](query, limit or 12)
        if kind == 'sound':
            return R['sound_search'](query, limit or 30)
        if kind == 'particle':
            return R['fx_search'](query, limit or 30)
        if kind == 'decal':
            return R['decal_materials'](query or 'decal', limit or 50)
        if kind == 'wear':
            return R['wear_materials'](query or 'de_inferno decals', limit or 50)
        return R['asset_search'](query, kind, limit or 50)

    @mcp.tool()
    def entity_info(classname: str | None = None, search: str | None = None) -> dict:
        """Entity reference from the game's FGD: keyvalues (defaults, choices), inputs, outputs. classname: one
        class; search: find classes. Example: entity_info(classname="func_door"). Returns {classes}."""
        return R['entity_info'](classname, search)

    @mcp.tool()
    def script_api(query: str = '', limit: int = 20) -> dict:
        """Search the installed cs_script API (point_script.d.ts); an exact class name returns its whole commented
        body. Example: script_api(query="CSPlayerPawn"). Returns {matches}."""
        return R['script_api'](query, limit)

    # ---- materials ------------------------------------------------------------------------------------------
    @mcp.tool()
    def material_create(name: str, image: str | None = None, pattern: dict[str, Any] | None = None,
                        roughness: float = 0.7, metalness: float = 0.0,
                        surface: Literal['concrete', 'brick', 'metal', 'wood', 'dirt', 'grass', 'gravel', 'sand',
                                         'tile', 'glass', 'plaster', 'rock', 'carpet', 'rubber', 'plastic',
                                         'cardboard', 'mud', 'snow', 'water', 'default'] = 'concrete',
                        kind: Literal['opaque', 'glass', 'emissive', 'water', 'decal'] = 'opaque',
                        options: dict[str, Any] | None = None) -> dict:
        """Create and compile a material. image: a local picture; or pattern {"pattern": bricks|tiles|planks|
        noise|plaster|checker|stripes|solid, "color": [r,g,b], "color2", "mortar", "count", "seed", "size",
        "variation"}. surface: footsteps/impacts. kind: glass (options tint, opacity, dirt) | emissive (color,
        glow 0..100, mask_image) | water (color, murk, flow) | decal. Normal map is derived.
        Example: material_create(name="red_brick", pattern={"pattern":"bricks","color":[150,60,45]}, surface="brick").
        Returns {material: materials/...vmat}."""
        return R['material_create'](name, image, pattern, roughness, metalness, surface, kind, options)

    @mcp.tool()
    def material_variant(name: str, base: str, tint: Any = None, saturation: float = 1, brightness: float = 1,
                         roughness_offset: float = 0, grime: float = 0, scale: float = 1) -> dict:
        """Tinted or aged copy of a stock or addon material (tint [r,g,b] or "#rrggbb", grime 0..1, scale of the
        texture). Example: material_variant(name="plaster_ochre", base="materials/de_inferno/...vmat",
        tint=[220,180,120]). Returns {material}."""
        return R['material_variant'](name, base, tint, saturation, brightness, roughness_offset, grime, scale)

    @mcp.tool()
    def material_from_reference(name: str, image: str, region: list[float] | None = None,
                                quad: list[list[float]] | None = None, real_size: list[float] | None = None,
                                surface: str = 'concrete', roughness: float | None = None, metalness: float = 0.0,
                                alpha: str = 'none') -> dict:
        """Tileable PBR material from a photo: crop (region [x0,y0,x1,y1]) or rectify (quad TL,TR,BR,BL pixels),
        flatten light, make seamless; normal/roughness/AO generated. real_size: [width,height] in units.
        Returns {material, preview}."""
        return R['material_from_reference'](name, image, region, quad, real_size, surface, roughness, metalness,
                                            alpha)

    @mcp.tool()
    def material_preview(materials: str | list[str], map_name: str | None = None, texture: bool = False,
                         mip: int = 0) -> dict:
        """Look at materials in the game: one material (or a list of up to 6, a gallery) on a lit service map,
        compiled and photographed. texture=true instead exports a stock material's colour texture to PNG
        (no game). Returns {shots} or {png}."""
        if texture:
            if not isinstance(materials, str):
                raise ValueError('texture=true exports one material or .vtex: pass a string')
            return R['texture_export'](materials, mip)
        if isinstance(materials, list):
            return R['material_gallery'](materials, map_name or 'b_gallery')
        return R['material_preview'](materials, map_name or 'b_preview')

    @mcp.tool()
    def decal_create(name: str, image: str | None = None, text: str | None = None, size: Any = None,
                     background: Any = None, tolerance: float = 20, softness: float = 20,
                     style: Literal['plate', 'stencil', 'neon', 'paper'] = 'plate', color: Any = None,
                     glow: float = 3.0, font: str = 'arialbd.ttf') -> dict:
        """Overlay material for add_decal / add_sign. image: local PNG with alpha (background="#ffffff" keys out a
        colour; size [w,h] in units). text: a sign rendered with a Windows font (Latin/Cyrillic), style plate |
        stencil | neon | paper, color, background, glow. Example: decal_create(name="cafe", text="CAFE",
        style="neon", color=[255,80,40]). Returns {material}."""
        if (image is None) == (text is None):
            raise ValueError('give exactly one of image (artwork) or text (a sign)')
        if text is not None:
            return R['sign_create'](name, text, style, color, background, glow, font)
        return R['decal_create'](name, image, size, background, tolerance, softness)

    # ---- models and props -----------------------------------------------------------------------------------
    @mcp.tool()
    def model_build(name: str, script: str | None = None, template: str | None = None, file: str | None = None,
                    params: dict[str, Any] | None = None, collision: Literal['hull', 'mesh', 'none'] = 'hull',
                    surface: str = 'default', physics: dict[str, Any] | None = None, scale: Any = 'auto',
                    weapon: dict[str, Any] | str | None = None) -> dict:
        """Make a prop model with Blender and compile it (models/mcp/<name>). One source: script (Blender Python
        file or text; inches, +Z up, front +X; objects named *_col = collision), template (crate, barrel, fence,
        door, lamp, table, pallet, railing, ... 15 in guide models; params) or file (FBX/OBJ/GLB, scale).
        physics: {mass, ...} for prop_physics.
        weapon: {base, grip, muzzle, scale} puts it on a stock gun's skeleton, held in the hands (guide weapons).
        Example: model_build(name="crate1", template="crate", params={"size":[48,48,48]}). Returns {model, bounds}."""
        if file is not None:
            if script or template:
                raise ValueError('give one source: script, template or file')
            return R['model_import'](name, file, scale, collision, surface, physics, weapon)
        return R['model_build'](name, script, template, params, collision, surface, physics, weapon)

    @mcp.tool()
    def model_preview(name: str, views: list[str] | None = None, size: int = 512, reference: str | None = None) -> dict:
        """Render an own model offline (Blender): views front|side|top|iso on one sheet; reference=<image> puts a
        photo beside it. Returns {preview: png}."""
        return R['model_preview'](name, views, size, reference)

    @mcp.tool()
    def model_info(name: str | None = None, physics: bool = False) -> dict:
        """Own models. No name: the list. name: its ModelDoc and source status. physics=true: does a compiled own
        or stock model (name or models/...vmdl) have a collision body. Returns {models} or {model, ...}."""
        if name is None:
            return R['model_list']()
        if physics:
            return R['physics_model_info'](name)
        return R['model_info'](name)

    @mcp.tool()
    def prop_preview(models: list[str], detail: bool = False, game: bool = False) -> dict:
        """Look at stock props before placing them: one labelled sheet (up to 16; detail=true two opposite views of
        up to 6). Red arrow = model +X (yaw 0), blue box = player. game=true only for broken offline previews.
        Returns {sheet, models: [{size, status}]}."""
        return R['prop_preview'](models, detail, game)

    @mcp.tool()
    def prop_note(model: str, category: str, tags: list[str], style: str, front: str, mount: str, by: str,
                  note: str = '') -> dict:
        """Record what you saw on a prop preview (shown by asset_search kind="prop" to every model later).
        category (furniture, lighting, street, vegetation, vehicle, ...), tags 3..6 words, style (inferno, italy,
        generic...), front +x|-x|+y|-y|none, mount floor|wall|ceiling|any, by = your model name, note <= 80 chars."""
        return R['prop_note'](model, category, tags, style, front, mount, by, note)

    @mcp.tool()
    def actor_build(name: str = 'walker', script: str | None = None, walk_speed: float = 62.0) -> dict:
        """Animated character prop (skeleton, idle/walk/turn clips) with Blender: default stylised walker or a
        script with one rig and named actions; walk_speed in units/s. Use it with map_edit add_actor.
        Returns {model, clips}."""
        return R['actor_build'](name, script, walk_speed)

    @mcp.tool()
    def assets_prepare(kind: Literal['drone', 'car', 'railgun', 'launcher', 'zapper', 'weather', 'light_shaft']) -> dict:
        """Build the shared assets of a feature once (operations also build them on first use): drone (quadcopter,
        rotor, remote), car (off-road SUV and wheel), railgun (AWP base) / launcher (Nova base + rocket) / zapper
        (taser base): weapon models held in the hands, for add_weapon; weather (rain and snow particles for add_fx),
        light_shaft (additive beam material for add_light kind=shaft). Returns {ok, models/sounds/materials, logs}."""
        if kind == 'drone':
            return R['control_build_assets']()
        if kind in ('zapper', 'railgun', 'launcher'):
            return R['weapon_prepare'](kind)
        if kind == 'weather':
            return R['fx_prepare_weather']()
        if kind == 'light_shaft':
            return R['lighting_prepare_shaft']()
        from . import vehicles
        vehicles.ensure_assets(project)
        return {'ok': True, 'models': [vehicles.BODY, vehicles.WHEEL]}

    # ---- styles, kits, prefabs -------------------------------------------------------------------------------
    @mcp.tool()
    def kit_info(key: str | None = None, category: str | None = None, style: str | None = None,
                 mount: str | None = None, tags: list[str] | None = None, gallery: bool = False,
                 kit: str | None = None, keys: list[str] | None = None, offset: int = 0, limit: int = 12) -> dict:
        """Measured stock building parts (kits). key="kit/part": one part (bounds, front, mount, collision).
        Otherwise a filtered list (category, style, mount, tags). gallery=true: a photographed contact sheet of a
        kit, category or keys (offset/limit up to 12). Example: kit_info(category="street", tags=["bench"])."""
        if key:
            return R['kit_info'](key)
        if gallery:
            return R['kit_gallery'](kit, category, keys, offset, limit)
        return R['kit_list'](category, style, mount, tags)

    @mcp.tool()
    def style_info(kind: Literal['building', 'theme', 'street_set', 'sky', 'mine'] = 'building',
                   name: str | None = None) -> dict:
        """Ready-made looks. building: parts, openings and materials of building_inferno/building_mirage (name =
        style); theme: interior palettes for dress_room (warehouse, office, dust, harbor, ...); street_set: street
        dressing roles (inferno); sky: verified skyboxes; mine: own styles saved in my/styles. No name: the list."""
        if kind == 'mine':
            return R['my_styles'](name)
        if kind == 'theme':
            return R['theme_info'](name or 'all')
        if kind == 'street_set':
            return R['street_set_info'](name or 'inferno')
        if kind == 'sky':
            return R['outdoor_sky'](name)
        return R['building_styles'](name)

    @mcp.tool()
    def reference_facade(image: str, quad: list[list[float]] | None = None, real_width: float | None = None) -> dict:
        """Rectify a facade photo (quad TL,TR,BR,BL pixels; real_width in units) and suggest floors, window grid and
        palette for add_building; returns a wall crop for material_from_reference. Open the PNG and correct by eye."""
        return R['reference_facade'](image, quad, real_width)

    @mcp.tool()
    def reference_compare(reference: str, shot: str) -> dict:
        """Two local images side by side at equal height (a photo and a game frame). Open it, name three
        differences, refine the map. Returns {png}."""
        return R['reference_compare'](reference, shot)

    @mcp.tool()
    def prefab_save(map: str, ids: list[int | str], name: str) -> dict:
        """Save map nodes (ids, direct children) as a reusable prefab maps/prefabs/e_<name>.vmap, centred at the
        origin; place it with map_edit add_prefab. Returns {prefab, nodes}."""
        return R['prefab_save'](map, ids, name)

    @mcp.tool()
    def prefab_info(name: str | None = None) -> dict:
        """Prefabs of the addon: no name = the list; name = its root group, node count and summary."""
        if name is None:
            return R['prefab_list']()
        return R['prefab_info'](name)

    # ---- sound ----------------------------------------------------------------------------------------------
    @mcp.tool()
    def sound_create(name: str, file: str | None = None, preset: str | None = None, seconds: float = 2,
                     params: dict[str, Any] | None = None, loop: bool = False) -> dict:
        """Make a sound (sounds/mcp/<name>.vsnd): file = import a local WAV/MP3/OGG (<=120 s; loop adds markers),
        or preset = synthesize beep|alarm|hum|wind|rain|drone|click|whoosh|rumble (seconds, params {seed, gain,
        frequency, channels}). Then sound_event makes a playable event. Returns {vsnd, duration, peak, log}."""
        if (file is None) == (preset is None):
            raise ValueError('give exactly one of file (import) or preset (synthesize)')
        if file is not None:
            return R['sound_import'](name, file, loop)
        if loop:
            params = {**(params or {}), 'loop': True}
        return R['sound_generate'](name, preset, seconds, params)

    @mcp.tool()
    def sound_event(name: str, kind: str, sound: str | None = None, sounds: list[str] | None = None,
                    volume: float = 1, pitch: float = 1, distance: float = 1500, interval: list[float] | None = None,
                    radius: list[float] | None = None, children: list[str] | None = None, reverb: str | None = None,
                    fade: float | None = None) -> dict:
        """Create the event mcp.<name> for add_sound. kind: oneshot | loop (3D, distance) | ambient | ui (2D) |
        random_around (interval [min,max] s, radius [min,max]) | soundscape (children events, reverb). sound or
        sounds: .vsnd paths. Example: sound_event(name="alarm", kind="loop", sound="sounds/mcp/alarm.vsnd").
        Returns {event}."""
        return R['sound_event'](name, kind, sound, sounds, volume, pitch, distance, interval, radius, children,
                                reverb, fade)

    # ---- scripts ----------------------------------------------------------------------------------------------
    @mcp.tool()
    def script_write(name: str, code: str) -> dict:
        """Write maps/scripts/mcp/<name>.js (cs_script) and compile it; attach it with map_edit add_script.
        Returns {script, syntax, compiler diagnostics}."""
        return R['script_write'](name, code)

    @mcp.tool()
    @on_release
    def script_test(map: str, steps: list[dict[str, Any]] | Literal['auto'] = 'auto', timeout: float = 90) -> dict:
        """Play a scenario in CS2 with real input, console checks and frames (one lock, timeout 1..90 s). Steps,
        one action each: cmd 'console' (+wait s), walk [x,y,z] (teleport, feet), go [x,y,z] (physical), use true
        (E), hold {"key":"attack|forward|back|left|right|jump|duck|reload|walk|use","seconds":1}, aim [x,y,z] or
        'entity', mouse {"dx":40,"dy":0} (real mouse), click [x,y] (window fractions: HUD buttons), expect 'regex'
        (+within s), shot 'name' (frame as seen), wait s, press 'button', fire 'name input', say 'chat', focus true|false. steps="auto" plays the map's
        add_story. Call game_reset before and after. Example: [{"use":true},{"expect":"enter","within":2},
        {"hold":{"key":"forward","seconds":1}},{"shot":"drive"}]. Returns {ok, checks, errors, shots, log}."""
        return R['script_test'](map, steps, timeout)

    # ---- light ------------------------------------------------------------------------------------------------
    @mcp.tool()
    @on_release
    def lighting_build(name: str, preset: str = 'full', cubemaps: bool = True, timeout: int = 1800,
                       cubemaps_only: bool = False) -> dict:
        """Fresh build with baked light and then runtime cubemaps (cubemaps_only=true bakes only the cubemaps of
        the compiled map). Returns {ok, build, cubemaps, reason}."""
        if cubemaps_only:
            return R['lighting_bake_cubemaps'](name, min(timeout, 600))
        return R['lighting_build'](name, preset, cubemaps, timeout)

    @mcp.tool()
    def lighting_measure(path: str) -> dict:
        """Brightness of a frame (PNG): mean luminance and the clipped bright/dark fractions. Returns {mean, ...}."""
        return R['lighting_measure'](path)

    # ---- game -------------------------------------------------------------------------------------------------
    @mcp.tool()
    @on_release
    def game_launch(map: str | None = None, open_in_hammer: str | None = None, width: int = 1600,
                    height: int = 900) -> dict:
        """Start CS2 Workshop Tools with the addon (windowed) and load a compiled map, or load it into the running
        session; open_in_hammer=<map> opens a map in Hammer for the user. The first start takes 30-90 s.
        Returns {loaded, ...}."""
        return R['game_launch'](map, width, height, open_in_hammer)

    @mcp.tool()
    def game_command(command: str, wait: float = 1.5, focus: bool = False) -> dict:
        """Console commands to the running CS2 (one per line); returns the printed output. Useful: sv_cheats 1,
        noclip, getpos, bot_add_ct, mp_restartgame 1, ent_fire <name> <input>, snd_showstart 1. focus=true keeps the
        game window in front (needed for setang before +attack). Returns {output}."""
        return R['game_command'](command, wait, focus)

    @mcp.tool()
    @on_release
    def game_screenshot(map: str | None = None, views: list[dict[str, Any]] | Literal['auto', 'rooms'] | None = None,
                        name: str | None = None, pos: list[float] | None = None, ang: list[float] | None = None,
                        hide_hud: bool = True, window: Literal['game', 'hammer'] = 'game', offset: int = 0,
                        limit: int | None = None) -> dict:
        """Photograph the game (PNG paths; open them). map: loaded first (reloaded if rebuilt). views: [{"name",
        "pos":[x,y,z] feet (eye +64), "ang":[pitch,yaw,0], "cmd": console run in place, "wait", "grep",
        "keep": true = no noclip (a script camera), "preview": true = also the map's loading-screen picture}] |
        "auto" (overviews, spawns, objectives, rooms) | "rooms" (one per layout room); offset/limit page auto views. Or one view by name/pos/ang. window="hammer" captures
        Hammer. Example: game_screenshot(map="arena", views=[{"name":"mid","pos":[0,0,64],"ang":[10,90,0]}]).
        Returns {shots: [{name, path}]}."""
        if views == 'rooms':
            if not map:
                raise ValueError('views="rooms" needs map')
            return R['layout_views'](map, None, offset, limit or 6)
        if views == 'auto' and (offset or limit):
            if not map:
                raise ValueError('views="auto" needs map')
            return R['playtest_views'](map, 'auto', offset, limit)
        return R['game_screenshot'](map, views, name, pos, ang, hide_hud, window)

    @mcp.tool()
    @on_release
    def game_reset(map: str | None = None) -> dict:
        """Clean game state between scenarios (~6 s): leave every vehicle/turret/drone, noclip off, HUD and
        viewmodel on, default accuracy, bots removed, keys released, mp_restartgame, live player. Returns {ok, done}."""
        return R['game_reset'](map)

    @mcp.tool()
    def game_crash_report(dump: str | None = None) -> dict:
        """Why CS2 crashed: the newest minidump's exception and module (server.dll = a script or entity), a hint,
        the last console lines and the full report path. Call when CS2 vanished or a tool says it crashed."""
        return R['game_crash_report'](dump)

    @mcp.tool()
    def game_stop() -> dict:
        """Close CS2 only if this tool started it (never the user's own session). Returns {stopped}."""
        return R['game_stop']()

    # ---- tests ------------------------------------------------------------------------------------------------
    @mcp.tool()
    @on_release
    def route_test(map: str, route: list[Any] | Literal['auto'] = 'auto', timeout: float = 90,
                   start: str | list[float] = 'spawn', terrain: str | None = None) -> dict:
        """Walk a route physically with real input (no teleports while walking). route: steps go [x,y,z] (jump/duck
        as needed), use 'name', wait s, ride {to_z}, climb_down, break 'name', push {name,to} | "auto" (the map's
        add_story). terrain=<name>: route is [[x,y],...] waypoints on that terrain. start: spawn | current | [x,y,z].
        Returns {reached, stuck, seconds per stage, shots, log}."""
        if terrain is not None:
            if not isinstance(route, list):
                raise ValueError('terrain routes are a list of [x,y] waypoints')
            return R['terrain_route_test'](map, terrain, route, timeout)
        return R['route_test'](map, route, timeout, start)

    @mcp.tool()
    @on_release
    def playtest(map: str, kind: Literal['bots', 'competitive', 'mode', 'course', 'timings'] = 'bots',
                 bots: int = 10, seconds: int = 60, round_seconds: int | None = None, spectate: bool = True,
                 timeout: float = 60, speed: float = 250, live: bool = True, offset: int = 0) -> dict:
        """Play the map with bots and report. kind: bots (a bounded round: movement, errors) | competitive (rounds,
        deaths, objectives; round_seconds shortens rounds) | mode (a native mode set by mode_setup: kills, respawn)
        | course (surf/bhop/kz timer and checkpoints) | timings (T/CT route times to objectives; live walks one
        route per call, repeat with offset). spectate=true: bots play alone. Returns a compact report."""
        if kind == 'competitive':
            return R['comp_playtest'](map, bots, seconds, round_seconds, spectate)
        if kind == 'mode':
            return R['mode_playtest'](map, bots, seconds)
        if kind == 'course':
            return R['mode_course_test'](map, int(timeout))
        if kind == 'timings':
            return R['comp_timings'](map, timeout, speed, live, offset)
        return R['playtest_run'](map, bots, seconds, spectate)

    @mcp.tool()
    @on_release
    def physics_probe(map: str, names: list[str]) -> dict:
        """Live state of named entities in the running map: count, position, angles, velocity, health. Compare
        before/after an action (a door opened, a crate fell). Returns {entities}."""
        return R['physics_probe'](map, names)

    @mcp.tool()
    def mode_setup(map: str, mode: str) -> dict:
        """Prepare a native game mode on a map: deathmatch, armsrace, wingman, casual, surf, bhop, kz, aim (mode
        entities and script). Build full afterwards; returns the launch command (the mode is chosen before loading)."""
        return R['mode_setup'](map, mode)

    # ---- the published schemas: compact ---------------------------------------------------------------------
    names = []
    for tool in mcp._tool_manager.list_tools():
        tool.parameters = compact_schema(tool.parameters)
        names.append(tool.name)
    return names
