"""Lighting presets and image metrics used by the quality extension."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time
import math
import re

from .dmx import Element


def bake_cubemaps(project, name: str, timeout: int = 600) -> dict:
    """Persist full runtime diagnostics and return a compact bake result."""
    from . import compiler, game, vconsole
    from .config import check_name

    check_name(name, 'map name')
    if not 1 <= timeout <= 600:
        raise ValueError('timeout must be between 1 and 600 seconds')
    try:
        raw = compiler.build_cubemaps(project.s, name, timeout)
    except (game.GameError, vconsole.VConsoleError, OSError) as exc:
        raw = {'map': name, 'ok': False, 'errors': [str(exc)]}
    log = project.s.logs / f'cubemaps-{name}-{time.time_ns()}.json'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
    out = {key: raw.get(key) for key in
           ('map', 'ok', 'sentinel', 'vpk_fresh', 'cubemap_entry',
            'restored_r_always_render_all_windows')}
    out['log'] = str(log)
    if not raw.get('ok'):
        out['reason'] = '; '.join(str(e)[:240] for e in (raw.get('errors') or [])[-3:]) or (
            'cubemap bake did not confirm a fresh cubemap VPK')
    return out

PRESETS: dict[str, dict[str, Any]] = {
    'day': {'angles': (50, 135, 0), 'color': '255 248 232', 'brightness': '1.0', 'skycolor': '255 255 255',
            'skyintensity': '1.0', 'sky': 'materials/skybox/sky_de_dust2.vmat', 'exposure': '0.0'},
    'sunset': {'angles': (18, 225, 0), 'color': '255 166 108', 'brightness': '0.72', 'skycolor': '255 190 145',
               'skyintensity': '0.50', 'sky': 'materials/skybox/cs_italy_s2_skybox_2.vmat', 'exposure': '-0.35'},
    'night': {'angles': (8, 35, 0), 'color': '115 145 210', 'brightness': '0.22', 'skycolor': '80 110 180',
              'skyintensity': '0.22', 'sky': 'materials/skybox/sky_rain_night_01.vmat', 'exposure': '0.15'},
    'overcast': {'angles': (62, 20, 0), 'color': '205 220 235', 'brightness': '0.55', 'skycolor': '180 200 220',
                 'skyintensity': '0.82', 'sky': 'materials/skybox/sky_overcast_01.vmat', 'exposure': '-0.15'},
    'indoor': {'angles': (75, 0, 0), 'color': '185 200 220', 'brightness': '0.16', 'skycolor': '40 50 70',
               'skyintensity': '0.18', 'sky': 'materials/skybox/sky_black.vmat', 'exposure': '0.0'},
    # Values read from the shipped de_inferno.vpk map (CS2, October 2026).
    # The visible sky is reduced from Valve's 0.5 to 0.3 after full-build
    # quarter frames: its exposed horizon otherwise clips excessively.
    # The separate light sky is disabled in the game and exists only as the
    # environment's skytexture target, so the visible sky can use the
    # photographed Inferno material without changing the sun's irradiance.
    'inferno': {
        'angles': (63, 218, 0), 'color': '255 247 235', 'brightness': '3',
        'skycolor': '136 199 255', 'skyintensity': '0.875',
        'sky': 'materials/skybox/test/s2_de_inferno_sky01.vmat', 'skybrightness': '0.3',
        'skyangles': (0, 245, 0),
        'light_sky': 'materials/skybox/skymodel_hosekwilkie_ref/'
                    'skymodel_hosekwilkie_albedo_0_30_turbidity_02_0_elevation_65_0.vmat',
        'light_sky_brightness': '2', 'light_sky_angles': (0, 40, 0),
        'exposure': '0.0', 'minexposure': '0.8',
        'maxexposure': '1.1', 'fadetime': '1.5', 'exposurespeedup': '1',
        'exposurespeeddown': '1',
        'postprocessing': 'lighting/postprocessing/de_inferno_prefab/de_inferno_prefab.vpost',
        'sun': {'brightnessscale': '1', 'skybouncescale': '1.5', 'bouncescale': '1.75',
                'skytexturescale': '1', 'angulardiameter': '0.3', 'directlight': '1',
                'rendertocubemaps': '1', 'castshadows': '1'},
        # The shipped map's fogend is 15000 (8192 doubled the haze on skylines).
        'default_fog': {'mode': 'gradient', 'density': 1.0, 'start_distance': 500,
                        'draw_distance': 15000, 'heightfog': True, 'start_height': 0,
                        'end_height': 10000},
        'fogcolor': '128 172 212',
        'cubemap_fog': {'cubemapfogsource': '1', 'cubemapfoglodbiase': '0.2',
                        'cubemapfogstartdistance': '800', 'cubemapfogenddistance': '280000',
                        'cubemapfogfalloffexponent': '1', 'cubemapheightfog': '1',
                        'cubemapfogheightstart': '-2000', 'cubemapfogheightend': '35000',
                        'cubemapfogheightexponent': '1', 'cubemapfogmaxopacity': '1'},
    },
}


def lighting_measure(path: str | Path) -> dict[str, float | str]:
    """Return mean luminance and clipped pixel fractions for a PNG/JPEG."""
    import numpy as np
    from PIL import Image

    p = Path(path)
    with Image.open(p) as im:
        rgb = im.convert('RGB')
        sample = rgb.copy()
        sample.thumbnail((1024, 1024))
        pixels = np.asarray(sample, dtype=np.uint8)
        total = max(int(pixels.shape[0] * pixels.shape[1]), 1)
        mean = float(pixels.mean()) / 255.0
        clipped_high = float(np.all(pixels >= 250, axis=2).sum()) / total
        clipped_low = float(np.all(pixels <= 5, axis=2).sum()) / total
    return {'path': str(p), 'mean': round(mean, 5), 'clipped_high': round(clipped_high, 5),
            'clipped_low': round(clipped_low, 5)}


def _props(ent: Element) -> Element:
    return ent['entity_properties']


def _set(ent: Element, values: dict[str, Any]) -> None:
    props = _props(ent)
    for key, value in values.items():
        if isinstance(value, (tuple, list)):
            value = ' '.join(str(x) for x in value)
        props.set(key, 'string', str(value))


def apply_preset(project, m, preset: str, fog: str | dict | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError(f'preset must be one of {sorted(PRESETS)}')
    cfg = PRESETS[preset]
    effective_fog = fog if fog is not None else cfg.get('default_fog')
    fog_mode, fog_options = validate_fog(effective_fog)
    changed: list[int] = []
    sky = _visible_sky(m) if preset == 'inferno' else next(iter(m.entities('env_sky')), None)
    if sky is None:
        sky = m.new_entity('env_sky', {'targetname': 'mcp_sky', 'skyname': cfg['sky']}, (0, 0, 0), defaults=project.catalog.defaults('env_sky'))
        m.attach(sky)
    _set(sky, {'targetname': _props(sky).get('targetname') or 'mcp_sky', 'skyname': cfg['sky'],
               'brightnessscale': cfg.get('skybrightness', cfg['skyintensity']),
               'tint_color': cfg.get('skytint', '255 255 255'), 'fog_type': '1'})
    changed.append(sky['nodeID'])
    if preset == 'inferno':
        sky['angles'] = tuple(float(x) for x in cfg['skyangles'])
        _set(sky, {'StartDisabled': '0'})
    sun_sky_target = _props(sky).get('targetname') or 'mcp_sky'
    if preset == 'inferno':
        light_sky = next(iter(m.entities('env_sky', targetname='mcp_light_sky')), None)
        if light_sky is None:
            light_sky = m.new_entity('env_sky', {
                'targetname': 'mcp_light_sky', 'skyname': cfg['light_sky'],
                'brightnessscale': cfg['light_sky_brightness'], 'tint_color': '255 255 255',
                'StartDisabled': '1'}, defaults=project.catalog.defaults('env_sky'))
            m.attach(light_sky)
        else:
            _set(light_sky, {'skyname': cfg['light_sky'], 'brightnessscale': cfg['light_sky_brightness'],
                              'tint_color': '255 255 255', 'StartDisabled': '1'})
        light_sky['angles'] = tuple(float(x) for x in cfg['light_sky_angles'])
        changed.append(light_sky['nodeID'])
        sun_sky_target = 'mcp_light_sky'
    sun = next(iter(m.entities('light_environment')), None)
    if sun is None:
        sun = m.new_entity('light_environment', {'targetname': 'mcp_sun'}, (0, 0, 512), cfg['angles'],
                            defaults=project.catalog.defaults('light_environment'))
        m.attach(sun)
    sun['angles'] = tuple(float(x) for x in cfg['angles'])
    sun_values = {'targetname': _props(sun).get('targetname') or 'mcp_sun', 'color': cfg['color'],
               'brightness': cfg['brightness'], 'skycolor': cfg['skycolor'], 'skyintensity': cfg['skyintensity'],
               'skytexture': sun_sky_target, 'directlight': '3', 'castshadows': '1'}
    sun_values.update(cfg.get('sun', {}))
    _set(sun, sun_values)
    changed.append(sun['nodeID'])

    volumes = m.entities('post_processing_volume')
    if not volumes:
        from .checks import _map_bounds
        bounds = _map_bounds(list(__import__('cs2map.checks', fromlist=['_all_meshes'])._all_meshes(m)))
        if bounds:
            lo, hi = bounds
            margin = 64
            spec = {'shape': 'box', 'mins': [lo[0] - margin, lo[1] - margin, lo[2] - margin],
                    'maxs': [hi[0] + margin, hi[1] + margin, hi[2] + 256]}
            meshes = project._new_meshes(m, spec, 'materials/tools_postprocess_volume.vmat', None)
            vol = m.new_entity('post_processing_volume', {'targetname': 'mcp_post', 'master': '1'},
                               [(lo[i] + hi[i]) / 2 for i in range(3)], defaults=project.catalog.defaults('post_processing_volume'))
            for mesh in meshes:
                m.attach(mesh, vol)
            m.attach(vol)
            volumes = [vol]
    for vol in volumes:
        _set(vol, {'postprocessing': cfg.get('postprocessing', 'postprocess/mcp_lab.vpost'),
                   'enableexposure': '1', 'exposurecompensation': cfg['exposure'],
                   'minexposure': cfg.get('minexposure', '0.7'),
                   'maxexposure': cfg.get('maxexposure', '1.15'),
                   'fadetime': cfg.get('fadetime', '1.0'),
                   'exposurespeedup': cfg.get('exposurespeedup', '1'),
                   'exposurespeeddown': cfg.get('exposurespeeddown', '1'), 'master': '1'})
        changed.append(vol['nodeID'])
    if effective_fog is not None:
        changed.extend(apply_fog(project, m, fog_mode, preset, fog_options))
    # Every preset lights the geometry that already exists with combined light-probe volumes (indirect light and
    # reflections); a preset applied before any geometry adds none.
    probe_id = _ensure_probe(project, m)
    if probe_id is not None:
        changed.append(probe_id)
    return {'op': 'lighting_preset', 'preset': preset, 'ids': changed, 'exposure': float(cfg['exposure']),
            **({'fog': effective_fog} if effective_fog is not None else {}),
            **({'probe_id': probe_id} if probe_id is not None else {}),
            **({'fog_note':'volumetric scattering is unconfirmed in installed CS2; use gradient for visible haze'}
               if fog_mode == 'volumetric' else {})}


def validate_fog(fog):
    opts = dict(fog) if isinstance(fog,dict) else {}
    mode = opts.pop('mode', 'volumetric') if isinstance(fog,dict) else fog
    if fog is not None and mode not in {'none','light','heavy','gradient','volumetric'}:
        raise ValueError('fog must be none, light, heavy, gradient, volumetric or an options object')
    if set(opts)-{'density','draw_distance','start_distance','heightfog','start_height','end_height','mins','maxs'}:
        raise ValueError('fog options: mode, density, draw_distance, start_distance, heightfog, start_height, end_height, mins, maxs')
    if 'density' in opts: opts['density']=_number(opts['density'],'fog density',.01,3)
    if 'draw_distance' in opts: opts['draw_distance']=_number(opts['draw_distance'],'fog draw_distance',128,16384)
    if 'start_distance' in opts: opts['start_distance']=_number(opts['start_distance'],'fog start_distance',0,8192)
    if 'heightfog' in opts and not isinstance(opts['heightfog'],bool):
        raise ValueError('fog heightfog must be true or false')
    for key in ('start_height','end_height'):
        if key in opts: opts[key]=_number(opts[key],f'fog {key}',-100000,100000)
    if 'start_distance' in opts and 'draw_distance' in opts and opts['start_distance'] >= opts['draw_distance']:
        raise ValueError('fog start_distance must be below draw_distance')
    if 'mins' in opts or 'maxs' in opts:
        opts['mins'],opts['maxs']=_vector(opts.get('mins'),'fog mins'),_vector(opts.get('maxs'),'fog maxs')
        if mode!='volumetric' or any(opts['mins'][i]>=opts['maxs'][i] for i in range(3)):
            raise ValueError('fog bounds require volumetric mode and ordered mins/maxs')
    return mode,opts


def apply_fog(project, m, fog: str, preset: str, options=None) -> list[int]:
    """Explicit fog selection updates owned volumes, disables template fog."""
    options=options or {}
    changed = []
    for cls in ('env_gradient_fog','env_cubemap_fog','env_volumetric_fog_controller','env_volumetric_fog_volume'):
        for ent in m.entities(cls):
            _set(ent, {'StartDisabled':'1'})
            changed.append(ent['nodeID'])
    if fog == 'none':
        return changed
    strength = options.get('density', {'light':.12,'heavy':.8,'gradient':.3,'volumetric':.3}[fog])
    tint = PRESETS.get(preset, {}).get('fogcolor') or (
        '255 216 180' if preset == 'sunset' else ('135 165 210' if preset == 'night' else '220 230 240'))
    if fog in {'light','heavy','gradient'}:
        # CS2's game-specific FGD exposes gradient fog. Keep the legacy
        # volumetric recipe separate until paired frames prove scattering.
        kv = {'targetname':'mcp_atmos_gradient','StartDisabled':'0','fogcolor':tint,
              'fogstrength':'1','fogmaxopacity':min(1.,strength),'fogstart':'8',
              'fogend':options.get('draw_distance',1200),'fogfalloffexponent':'1',
              'heightfog':'0','fadetime':'0.1'}
        if 'start_distance' in options:
            kv['fogstart'] = options['start_distance']
        if 'heightfog' in options:
            kv['heightfog'] = '1' if options['heightfog'] else '0'
        if 'start_height' in options:
            kv['fogstartheight'] = options['start_height']
        if 'end_height' in options:
            kv['fogendheight'] = options['end_height']
        ent = next(iter(m.entities('env_gradient_fog',targetname='mcp_atmos_gradient')),None)
        if ent is None:
            ent=m.new_entity('env_gradient_fog',kv,defaults=project.catalog.defaults('env_gradient_fog'))
            m.attach(ent)
        else:
            _set(ent,kv)
        changed.append(ent['nodeID'])
        if preset == 'inferno':
            changed.extend(_apply_cubemap_fog(project, m))
        return changed
    ent = next(iter(m.entities('env_volumetric_fog_controller',targetname='mcp_atmos_fog')),None)
    # The controller has its own irradiance bounds (base.fgd's
    # ``volumetric_fog_controller(box_mins, box_maxs)``).  A volume entity alone
    # only clamps the fog. Whether this changes rendering in CS2 must be
    # established by paired game frames; it is not implied by the FGD helper.
    from .playtest import CameraGeometry
    explicit_bounds = (options.get('mins'), options.get('maxs')) if 'mins' in options else None
    scene_bounds = explicit_bounds or CameraGeometry(m).bounds
    centre = [0.0, 0.0, 0.0]
    rel_mins, rel_maxs = [-4096.0, -4096.0, -4096.0], [4096.0, 4096.0, 4096.0]
    if scene_bounds:
        lo, hi = scene_bounds
        centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
        rel_mins = [lo[i] - centre[i] - 64 for i in range(3)]
        rel_maxs = [hi[i] - centre[i] + 64 for i in range(3)]
    kv = {'targetname':'mcp_atmos_fog','StartDisabled':'0','IsMaster':'1','FogStrength':strength,
          'TintColor':tint,'DrawDistance':options.get('draw_distance',1200),'FadeInStart':'8','FadeInEnd':'64',
          'IndirectEnabled':'1','IndirectStrength':'.5','NoiseStrength':'.15','NoiseSpeed':'.05',
          'NoiseScale':'0.01 0.01 0.01','WindSpeed':'.2','WindDirection':'1 0 0',
          'box_mins':rel_mins,'box_maxs':rel_maxs}
    if ent is None:
        ent = m.new_entity('env_volumetric_fog_controller',kv,centre,
                           defaults=project.catalog.defaults('env_volumetric_fog_controller'))
        m.attach(ent)
    else:
        ent['origin'] = tuple(centre)
        _set(ent,kv)
    changed.append(ent['nodeID'])
    if fog == 'volumetric':
        bounds=explicit_bounds or CameraGeometry(m).bounds
        if bounds:
            lo,hi=bounds
            centre=[(lo[i]+hi[i])/2 for i in range(3)]
            values={'targetname':'mcp_atmos_volume','StartDisabled':'0',
                    'box_mins':[lo[i]-centre[i]-32 for i in range(3)],
                    'box_maxs':[hi[i]-centre[i]+32 for i in range(3)],
                    'FogStrength':'1.0','Shape':'0','FalloffExponent':'1','IndirectUseLPVs':'1'}
            volume=next(iter(m.entities('env_volumetric_fog_volume',targetname='mcp_atmos_volume')),None)
            if volume is None:
                volume=m.new_entity('env_volumetric_fog_volume',values,centre,
                                    defaults=project.catalog.defaults('env_volumetric_fog_volume'))
                m.attach(volume)
            else:
                volume['origin']=tuple(centre)
                _set(volume,values)
            changed.append(volume['nodeID'])
    return changed


def _parse_vec3(value, default=None):
    """Parse an FGD vector key without widening the public fog API."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(float(x) for x in value)
    if isinstance(value, str):
        try:
            parts = value.replace(',', ' ').split()
            if len(parts) == 3:
                return tuple(float(x) for x in parts)
        except ValueError:
            pass
    return default


def _visible_sky(m):
    return next((ent for ent in m.entities('env_sky')
                 if _props(ent).get('targetname') != 'mcp_light_sky'), None)


def _apply_cubemap_fog(project, m) -> list[int]:
    """Apply the shipped Inferno cubemap-fog controller values."""
    cfg = PRESETS['inferno']['cubemap_fog']
    sky = _visible_sky(m)
    sky_target = (_props(sky).get('targetname') if sky is not None else None) or 'mcp_sky'
    ent = next(iter(m.entities('env_cubemap_fog', targetname='mcp_inferno_fog')), None)
    if ent is None:
        ent = next(iter(m.entities('env_cubemap_fog')), None)
    if ent is None:
        values = {'targetname': 'mcp_inferno_fog', 'StartDisabled': '0',
                  'cubemapfogskyentity': sky_target, **cfg}
        ent = m.new_entity('env_cubemap_fog', values,
                           defaults=project.catalog.defaults('env_cubemap_fog'))
        m.attach(ent)
    else:
        _set(ent, {'StartDisabled': '0', 'cubemapfogskyentity': sky_target, **cfg})
    return [ent['nodeID']]


def _ensure_probe(project, m) -> int | None:
    """Inferno-style unbaked probe volumes: a fine one over the playable area.

    Valve covers the whole map with one coarse low-priority volume (voxel 48,
    level -2) and lights the streets with fine local ones (voxel 24).  We do the
    same with two deterministic volumes: ``mcp_inferno_probe`` over playable
    solids, and ``mcp_inferno_probe_far`` over the skyline when there is one -
    outside any volume its props get only blue sky light in shadow.
    """
    from .visual import playable_bounds

    # Playable solids only: a far skyline ground or a lightmap helper box would
    # multiply the fine voxel count (and the bake time) without lighting the quarter.
    bounds = playable_bounds(m)
    if not bounds:
        return None
    lo, hi = bounds
    def covers(ent, lo, hi):
        props = _props(ent)
        centre = _parse_vec3(ent.get('origin'), (0.0, 0.0, 0.0))
        b0 = _parse_vec3(props.get('box_mins'))
        b1 = _parse_vec3(props.get('box_maxs'))
        return b0 is not None and b1 is not None and all(
            centre[i] + b0[i] <= lo[i] and centre[i] + b1[i] >= hi[i] for i in range(3))
    own = next((e for e in m.entities('env_combined_light_probe_volume')
                if _props(e).get('targetname') != 'mcp_inferno_probe_far' and covers(e, lo, hi)), None)
    if own is not None and _props(own).get('targetname') != 'mcp_inferno_probe':
        return own['nodeID']
    local = _probe(project, m, 'mcp_inferno_probe', lo, hi, 128.0, '24.0', '0')
    far = _far_bounds(project, m)
    if far and not all(far[0][i] >= lo[i] - 128 and far[1][i] <= hi[i] + 128 for i in range(3)):
        _probe(project, m, 'mcp_inferno_probe_far', far[0], far[1], 64.0, '48.0', '-2')
    return local


def _probe(project, m, name, lo, hi, margin, voxel, level) -> int:
    centre = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    values = {
        'targetname': name, 'clientSideEntity': '1',
        'StartDisabled': '0', 'voxelize': '1', 'flood_fill': '1',
        'voxel_size': voxel, 'bakefarz': '4096', 'indoor_outdoor_level': level,
        'box_mins': [lo[i] - centre[i] - margin for i in range(3)],
        'box_maxs': [hi[i] - centre[i] + margin for i in range(3)],
    }
    ent = next(iter(m.entities('env_combined_light_probe_volume', targetname=name)), None)
    if ent is None:
        ent = m.new_entity('env_combined_light_probe_volume', values, centre,
                           defaults=project.catalog.defaults('env_combined_light_probe_volume'))
        m.attach(ent)
    else:
        ent['origin'] = centre
        _set(ent, values)
    return ent['nodeID']


def _far_bounds(project, m):
    """Bounds of outdoor scenery (backdrop/skyline groups and their props), or None."""
    from . import checks, visual

    ids = set(visual.scenery_ids(m))
    for node in m.iter_nodes():          # add_skyline keeps its ground out of scenery_ids
        try:
            kind = json.loads(node.get('cs2map_outdoor') or '{}').get('kind')
        except (ValueError, TypeError, AttributeError):
            kind = None
        if kind in ('add_backdrop', 'add_skyline'):
            ids.update(n.get('nodeID') for n in m.iter_nodes(node))
    if not ids:
        return None
    boxes = [b for n, b in checks._all_meshes(m) if n.get('nodeID') in ids]
    for ent in m.entities():
        model = _props(ent).get('model') if ent.get('nodeID') in ids else None
        world = model and visual._model_bounds(project, model, ent.get('origin'), ent.get('angles'), ent.get('scales'))
        if world:
            boxes.append(world)
    return checks._map_bounds([(None, b) for b in boxes]) if boxes else None


def _number(value, label, low, high):
    if isinstance(value,bool):
        raise ValueError(f'{label} must be a number')
    try:
        number = float(value)
    except (ValueError,TypeError):
        raise ValueError(f'{label} must be a number') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{label} must be between {low} and {high}')
    return number


def _vector(value, label='origin'):
    if not isinstance(value,(list,tuple)) or len(value) != 3:
        raise ValueError(f'{label} must have three numbers')
    return [_number(v,label,-1e6,1e6) for v in value]


def _name(m, requested, prefix):
    if requested is not None:
        if not isinstance(requested,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',requested):
            raise ValueError('name must be 1..64 letters, digits or underscores, starting with a letter/_')
        if m.entities(targetname=requested):
            raise ValueError(f'name {requested!r} already exists')
        return requested
    i = 1
    while m.entities(targetname=f'{prefix}_{i}'):
        i += 1
    return f'{prefix}_{i}'


PARK_DEPTH = 6000    # a switched-off lamp waits this far below its place

# Stock beam materials (unlit translucent; the mask is a soft rectangle fading along V,
# the way Valve uses them on world meshes in de_dust2 / cs_italy).
SHAFT_MATERIALS = {'soft': 'materials/effects/smoke/lightshaft_001.vmat',
                   'smoky': 'materials/effects/smoke/lightshaft_003_smokey.vmat',
                   'dusty': 'materials/effects/smoke/lightshaft_004.vmat',
                   # Stock soft already has F_ADDITIVE_BLEND=1. O's copy keeps
                   # csgo_effects and all masks, disabling material fog only.
                   'additive': 'materials/mcp_o/lightshaft_additive.vmat'}


def prepare_shaft_material(project) -> dict:
    """Compile a fog-independent copy of Valve's installed additive shaft."""
    import shutil
    from . import compiler, vtex
    from .config import ensure_inside
    # Parameters/textures were read from lightshaft_001.vmat_c DATA with native
    # resourceinfo, not inferred from shader names. The stock material itself
    # already uses additive blending; removing fog prevents fog-colour emission.
    textures = {
        'TextureColor': 'materials/effects/smoke/smoke_shaft_002_mask_psd_8bf92c64.vtex',
        'TextureMask1': 'materials/effects/smoke/lightshaft_001_vmat_g_tmask1_2b2ebb3f.vtex',
        'TextureMask2': 'materials/effects/smoke/dust_004_mask_psd_bbe62bc8.vtex',
        'TextureMask3': 'materials/effects/smoke/dust_004_mask_psd_bbe62bc8.vtex',
    }
    if any(not project.game.exists(path+'_c') for path in textures.values()):
        raise ValueError('installed lightshaft masks are missing')
    texture_dir = ensure_inside(project.s.content_addon/'materials/mcp_o/shaft_masks', project.s.content_addon/'materials/mcp_o')
    exported = {}
    for slot, path in textures.items():
        if path not in exported:
            decoded = vtex.export(project.game, path, texture_dir)
            if not decoded.get('complete'):
                raise ValueError('cannot export installed shaft mask: '+str(decoded.get('errors')))
            exported[path] = Path(decoded['maps']['color']).relative_to(project.s.content_addon).as_posix()
    textures = {slot:exported[path] for slot,path in textures.items()}
    params = {'F_ADDITIVE_BLEND': 1, 'F_DEPTH_FEATHER': 1, 'F_RENDER_BACKFACES': 1,
              'g_bFogEnabled': 0, 'g_nTextureAddressModeU': 0, 'g_nTextureAddressModeV': 0,
              'g_flColorBoost': 1, 'g_flFadeDistance': 482.456, 'g_flFadeFalloff': 1.197,
              'g_flFadeMax': 1, 'g_flFadeMin': 0, 'g_flFeatherDistance': 130.841,
              'g_flFeatherFalloff': 1, 'g_flFresnelExponent': 1.18, 'g_flFresnelFalloff': 1,
              'g_flFresnelMax': 1, 'g_flFresnelMin': 0, 'g_flOpacityScale': 1,
              'g_vColorTint': '[1 1 1 0]', 'g_vMask1PanSpeed': '[0 -0.013 0 0]',
              'g_vMask1Scale': '[0.05 0.1 0 0]', 'g_vMask2PanSpeed': '[-0.009 -0.009 0 0]',
              'g_vMask2Scale': '[0.4 0.2 0 0]', 'g_vMask3PanSpeed': '[0 -0.01 0 0]',
              'g_vMask3Scale': '[0.5 0.25 0 0]', 'g_vTexCoordScrollSpeed': '[0 0 0 0]', **textures}
    src = ensure_inside(project.s.content_addon / SHAFT_MATERIALS['additive'], project.s.content_addon/'materials/mcp_o')
    src.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if src.exists():
        backup = project.s.backups / 'materials' / str(time.time_ns()) / src.name
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, backup)
    body = '// Valve lightshaft_001 DATA: same additive shader/masks; fog disabled.\nLayer0\n{\n\tshader "csgo_effects.vfx"\n'
    # Preserve float parameter types in the compiled DATA; VMAT's parser
    # otherwise stores whole numbers such as "1" in m_intParams.
    body += ''.join(f'\t{k} "{float(v) if k.startswith("g_fl") else v}"\n' for k,v in params.items()) + '}\n'
    src.write_text(body, encoding='utf-8')
    expected = project.s.game_addon / (SHAFT_MATERIALS['additive']+'_c')
    before = expected.stat().st_mtime_ns if expected.exists() else -1
    job = compiler.compile_assets(project.s, project.jobs, [src], force=True, timeout=50)
    done = project.jobs.wait(job.id, 50)
    ok = done.state == 'succeeded' and done.result.get('ok') and not done.result.get('errors') and expected.exists() and expected.stat().st_mtime_ns > before
    return {'ok': bool(ok), 'state': done.state, 'material': SHAFT_MATERIALS['additive'], 'log': job.log,
            'errors': done.result.get('errors', []), 'backup': str(backup) if backup else None,
            'note': 'stock soft already additive; copy disables material fog; rebuild maps using it'}


def _direction(angles):
    """Unit vector of [pitch, yaw] (pitch > 0 points down, like light angles)."""
    p, y = math.radians(angles[0]), math.radians(angles[1])
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))


def shaft_geometry(project, m, op, angles):
    """Cross-section corners, direction and per-corner lengths of a light shaft.

    `window` {mins, maxs}: an opening box (thin along the wall normal); the beam is that
    rectangle swept along the light direction, so it lands exactly on the lit floor patch.
    Otherwise a width x height rectangle centred on `origin`, perpendicular to the beam.
    `sun: true` takes the direction from light_environment. Without `length` every corner
    is traced to the first solid surface (max 4096)."""
    from .checks import _asset_exists
    sun = op.get('sun', False)
    if not isinstance(sun, bool):
        raise ValueError('sun must be true or false')
    look = op.get('beam', 'soft')
    if look not in SHAFT_MATERIALS:
        raise ValueError('shaft beam must be ' + ', '.join(SHAFT_MATERIALS))
    material = SHAFT_MATERIALS[look]
    if not _asset_exists(project, material):
        raise ValueError(f'installed shaft material {material} is missing')
    if sun:
        env = next(iter(m.entities('light_environment')), None)
        if env is None:
            raise ValueError('sun: true needs a light_environment (map_create template "basic")')
        angles = [float(a) for a in env['angles']]
    d = _direction(angles)
    if sun and d[2] > -0.05:
        raise ValueError('the sun is at or below the horizon; a sun shaft needs pitch > 3 degrees')
    if 'window' in op:
        w = op['window']
        if not isinstance(w, dict) or set(w) != {'mins', 'maxs'}:
            raise ValueError('window must be {"mins":[x,y,z],"maxs":[x,y,z]} of the opening')
        lo, hi = _vector(w['mins'], 'window mins'), _vector(w['maxs'], 'window maxs')
        size = [hi[i] - lo[i] for i in range(3)]
        if any(v < 0 for v in size):
            raise ValueError('window mins must be below maxs')
        k = min(range(3), key=lambda i: size[i])        # wall normal axis
        if abs(d[k]) < 0.05:
            raise ValueError('the light runs along the wall; it cannot shine through this window')
        plane = hi[k] if d[k] > 0 else lo[k]          # face where the light leaves the opening
        a, b = [i for i in range(3) if i != k]
        corners = []
        for ca, cb in ((lo[a], lo[b]), (hi[a], lo[b]), (hi[a], hi[b]), (lo[a], hi[b])):
            c = [0.0, 0.0, 0.0]
            c[k], c[a], c[b] = plane, ca, cb
            corners.append(c)
    else:
        if 'origin' not in op:
            raise ValueError('a shaft needs window {mins,maxs} or origin + width')
        origin = _vector(op['origin'])
        width = _number(op.get('width', 96), 'shaft width', 8, 1024)
        height = _number(op.get('height', width), 'shaft height', 8, 1024)
        u = (-d[1], d[0], 0.0) if abs(d[2]) < 0.999 else (1.0, 0.0, 0.0)
        n = math.hypot(u[0], u[1]) or 1.0
        u = (u[0] / n, u[1] / n, 0.0)
        v = (d[1]*u[2]-d[2]*u[1], d[2]*u[0]-d[0]*u[2], d[0]*u[1]-d[1]*u[0])
        corners = [[origin[i] + su * u[i] * width / 2 + sv * v[i] * height / 2 for i in range(3)]
                   for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    if 'length' in op:
        lengths = [_number(op['length'], 'shaft length', 32, 4096)] * 4
    else:
        lengths = _trace_lengths(m, corners, d)
    shape = op.get('shape', 'star')
    if shape not in ('star', 'cross'):
        raise ValueError('shaft shape must be star (4 planes, default) or cross (2 planes)')
    return {'corners': corners, 'dir': d, 'lengths': lengths, 'material': material, 'sun': sun,
            'shape': shape, 'intensity': _number(op.get('intensity', 0.75), 'shaft intensity', 0.05, 1)}


def _trace_lengths(m, corners, d, limit=4096.0):
    from .checks import _is_solid_mesh, _ray, _triangles
    tris = [t for node in m.iter_nodes() if node.type == 'CMapMesh' and _is_solid_mesh(node)
            for t in _triangles(node)]
    centre = [sum(c[i] for c in corners) / 4 for i in range(3)]
    out = []
    for c in corners:
        # Start slightly inside the rectangle and 1 unit along the beam: corners lie on the frame.
        p = tuple(c[i] + (centre[i] - c[i]) * 0.02 + d[i] for i in range(3))
        hits = [t for tri in tris if (t := _ray(p, d, tri)) is not None and t > 0.5]
        if not hits:
            raise ValueError('the shaft hits no surface within 4096 units; give length')
        out.append(min(min(hits) + 1, limit))
    return out


def shaft_meshes(project, m, beam, color=None):
    """The beam as translucent planes through its axis, UV-mapped 0..1 across and along it.

    Valve builds shafts from planes with lightshaft materials (de_dust2, cs_italy). Their shader fades
    a plane seen edge-on, so "star" uses four planes (two diagonals + two mid-planes) and some plane
    always faces the camera; "cross" uses only the diagonals (a dark streak at some angles).
    Vertex-paint alpha fades every plane to 0 at its side edges. Each side of a plane is its own mesh
    (the material is single-sided and a double-sided strip is not a manifold)."""
    from .mesh import Mesh
    corners, d, lengths, material = beam['corners'], beam['dir'], beam['lengths'], beam['material']
    ends = [[c[i] + d[i] * t for i in range(3)] for c, t in zip(corners, lengths)]
    tw, th = project.texture_size(material)

    def sub(x, y):
        return [x[i] - y[i] for i in range(3)]

    def dot(x, y):
        return sum(x[i] * y[i] for i in range(3))

    def mid(x, y):
        return [(x[i] + y[i]) / 2 for i in range(3)]

    def uv_frame(a, b, ea, eb):
        """Texture axes so that U runs 0..1 from a to b and V 0.1..0.92 along the beam."""
        edge = sub(b, a)
        width = math.sqrt(dot(edge, edge))
        u = [x / width for x in edge]
        v = [d[j] - u[j] * dot(d, u) for j in range(3)]
        vn = math.sqrt(dot(v, v))
        v = [x / vn for x in v]
        length = max(dot(sub(ea, a), v), dot(sub(eb, a), v))
        v0, v1 = 0.1, 0.92            # skip the mask's dark top border; keep some light at the far end
        su, sv = width / tw, length / ((v1 - v0) * th)
        return dict(u_axis=tuple(u), v_axis=tuple(v), scale=(su, sv),
                    offset=(-dot(a, u) / su, th * v0 - dot(a, v) / sv))

    planes = [(corners[0], corners[2], ends[0], ends[2]), (corners[1], corners[3], ends[1], ends[3])]
    if beam['shape'] == 'star':
        mids = [mid(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        mid_ends = [mid(ends[i], ends[(i + 1) % 4]) for i in range(4)]
        planes += [(mids[0], mids[2], mid_ends[0], mid_ends[2]), (mids[1], mids[3], mid_ends[1], mid_ends[3])]
    clear, faint, solid = (1.0, 1.0, 1.0, 0.0), (1.0, 1.0, 1.0, 0.0), (1.0, 1.0, 1.0, 1.0)
    meshes = []
    for a, b, ea, eb in planes:
        n = sub(b, a)
        n = [d[1]*n[2]-d[2]*n[1], d[2]*n[0]-d[0]*n[2], d[0]*n[1]-d[1]*n[0]]
        frame = uv_frame(a, b, ea, eb)
        # Rows across the plane: at the opening (transparent, so the planes do not draw a star over the
        # window seen from inside, darker than fog behind it), 20% along the beam (full), far end (full).
        a1 = [a[j] + (ea[j] - a[j]) * 0.2 for j in range(3)]
        b1 = [b[j] + (eb[j] - b[j]) * 0.2 for j in range(3)]
        rows = [(a, mid(a, b), b, faint), (a1, mid(a1, b1), b1, solid), (ea, mid(ea, eb), eb, solid)]
        for side in (1, -1):
            mesh = Mesh()
            out = [side * x for x in n]
            for (l0, c0, r0, k0), (l1, c1, r1, k1) in zip(rows, rows[1:]):
                mesh.face_out([l0, c0, c1, l1], out, material, colors=[clear, k0, k1, clear], **frame)
                mesh.face_out([c0, r0, r1, c1], out, material, colors=[k0, clear, clear, k1], **frame)
            meshes.append(mesh)
    base = color if color is not None else (255, 244, 225)
    tint = tuple(int(round(c * beam['intensity'])) for c in base)
    ids = []
    for mesh in meshes:
        node = m.new_mesh(mesh, texture_size=project.texture_size,
                          options={'physicsType': 'none', 'disableShadows': 1, 'bakelighting': False,
                                   'renderToCubemaps': False, 'tintColor': (*tint, 255)})
        m.attach(node)
        ids.append(node['nodeID'])
    return ids

# scripts/light_styles.vdata in the installed game (style names for light_style)
LIGHT_STYLES = {'flicker_1', 'flicker_2', 'fluorescent_flicker', 'dimmer', 'candle_1', 'candle_2', 'candle_3',
                'gentle_pulse', 'slow_strong_pulse', 'slow_pulse_nofade', 'fast_strobe', 'slow_strobe'}


def add_light(project, m, op, refs):
    """FGD-backed visible lamps; fire uses a stationary, styled omni."""
    kind = op.get('kind','ceiling')
    if kind not in {'ceiling','wall','spot','shaft','fire','neon'}:
        raise ValueError('kind must be ceiling, wall, spot, shaft, fire or neon')
    window = op.get('window') if kind == 'shaft' and isinstance(op.get('window'), dict) else None
    if window is not None and 'origin' not in op:
        # The lamp of a window shaft sits in the opening.
        origin = [(a + b) / 2 for a, b in zip(_vector(window.get('mins'), 'window mins'),
                                              _vector(window.get('maxs'), 'window maxs'))]
    else:
        origin = _vector(op.get('origin'))
    angles = _vector(op.get('angles',[90,0,0] if kind in {'ceiling','spot','shaft'} else [0,0,0]),'angles')
    color, temperature = op.get('color'),op.get('temperature')
    if color is not None and temperature is not None:
        raise ValueError('choose color or temperature, not both')
    if color is not None:
        color = _vector(color,'color')
        if any(not 0 <= c <= 255 for c in color):
            raise ValueError('color channels must be between 0 and 255')
    else:
        temperature = _number(temperature if temperature is not None else
                              (6500 if kind == 'neon' else 2200 if kind == 'fire' else 3000),'temperature',1000,20000)
    brightness = op.get('brightness','normal')
    if isinstance(brightness,str):
        values = {'dim':300,'normal':1000,'bright':2400}
        if brightness not in values:
            raise ValueError('brightness must be dim, normal, bright or numeric lumens')
        brightness = values[brightness]
    brightness = _number(brightness,'brightness',1,100000)
    radius = _number(op.get('range',768),'range',32,8192)
    for key in ('shadows',):
        if key in op and not isinstance(op[key],bool):
            raise ValueError(f'{key} must be true or false')
    model = op.get('model')
    if model not in (None,False) and (not isinstance(model,str) or not model.endswith('.vmdl') or
            '..' in model or not __import__('cs2map.checks',fromlist=['_asset_exists'])._asset_exists(project,model)):
        raise ValueError('model must be false or an existing .vmdl path from asset_search')
    style = op.get('style')
    if style is not None and style not in LIGHT_STYLES:
        raise ValueError('style must be one of ' + ', '.join(sorted(LIGHT_STYLES)))
    start_off = op.get('start_off', False)
    if not isinstance(start_off, bool):
        raise ValueError('start_off must be true or false')
    if kind=='shaft':
        if start_off: raise ValueError('shaft beam is static; start_off is supported by other lamps')
        beam = shaft_geometry(project, m, op, angles)
        if beam['sun']:
            # Sunlight through an opening: the sun already lights the floor patch, only the beam is added.
            ids = shaft_meshes(project, m, beam, color)
            return {'op':'add_light','id':ids[0],'ids':ids,'kind':'shaft','sun':True,
                    'length':[round(t) for t in beam['lengths']],'material':beam['material']}
    name = _name(m,op.get('name'),'mcp_light')
    source_size = _number(op.get('size', 10), 'size', 0.5, 64)    # radius of the visible glowing source
    cls = 'light_barn' if kind in {'spot','shaft'} else 'light_omni2'
    kv = {'targetname':name,'brightness_units':'1','brightness_lumens':brightness,'range':radius,
          'colormode':'0' if color is not None else '1','castshadows':'2' if op.get('shadows',True) else '0',
          'directlight':'3' if kind == 'fire' else '1','bouncelight':'1',
          # a lamp model is the visible part: the glowing source sphere would double it
          'showlight':'0' if model is False or isinstance(model, str) else '1',
          'size_params':'3 64 .15' if kind == 'neon' else f'{source_size:g} 24 .15',
          'shape':'1' if kind == 'neon' else '0'}
    if color is not None:
        kv['color']=color
    else:
        kv['colortemperature']=temperature
    if kind == 'fire':
        kv.update({'light_style':'candle_1','castshadows':'1' if op.get('shadows',True) else '0','showlight':'0'})
    if style:
        # Baked direct light cannot animate: stationary (dynamic direct, baked shadows).
        kv['directlight'] = '3'
        kv['light_style'] = style
    if start_off:
        # CS2 lights are client-only entities: ent_fire/scripts cannot switch them. A switchable lamp
        # is a fully dynamic light parented to an invisible func_movelinear parked far below the map;
        # "<name> Open" brings it into place (on), "Close" parks it again (off). Verified in game.
        kv.update({'directlight': '2', 'bouncelight': '0', 'castshadows': '1' if op.get('shadows', True) else '0'})
    if cls == 'light_barn':
        kv.pop('showlight')
        kv.update({'luminaire_shape':'1','luminaire_size':'8','soft_x':'.3','soft_y':'.3'})
    switch = None
    if start_off:
        from . import physics
        switch = name
        name = name + '_light'
        kv['targetname'] = name
        if m.entities(targetname=name):
            raise ValueError(f'name {name!r} already exists')
        x, y, z = origin
        carrier = physics.add_mover(project, m, {'op': 'add_mover',
            'targetname': switch, 'mins': [x - 2, y - 2, z - PARK_DEPTH - 2], 'maxs': [x + 2, y + 2, z - PARK_DEPTH + 2],
            'to': [0, 0, PARK_DEPTH], 'speed': 40000, 'call': 'script', 'texture': 'materials/tools/toolsnodraw.vmat'}, refs)
        kv['parentname'] = switch
        origin = [x, y, z - PARK_DEPTH]
    ent=m.new_entity(cls,kv,origin,angles,defaults=project.catalog.defaults(cls))
    m.attach(ent)
    ids=[ent['nodeID']] + ([carrier['id']] if switch else [])
    if kind=='shaft':
        ids.extend(shaft_meshes(project, m, beam, color))
    if kind == 'spot' and model is not False:
        # Barn lights have no showlight key. A tiny visible disk supplies the
        # luminaire, with negligible lighting energy compared to the spot.
        disk=m.new_entity('light_rect',{'brightness_units':'1','brightness_lumens':'10','range':'32',
                                       'showlight':'1','shape':'1','size_params':'8 8 .15','directlight':'1',
                                       'castshadows':'0','colormode':kv['colormode'],
                                       **({'color':color} if color is not None else {'colortemperature':temperature})},
                          origin,angles,defaults=project.catalog.defaults('light_rect'))
        m.attach(disk)
        ids.append(disk['nodeID'])
    if isinstance(model,str):
        prop=m.new_entity('prop_static',{'model':model},origin,angles,defaults=project.catalog.defaults('prop_static'))
        m.attach(prop)
        ids.append(prop['nodeID'])
    if op.get('ref'):
        refs[op['ref']]=ent['nodeID']
    return {'op':'add_light','id':ent['nodeID'],'ids':ids,'name':switch or name,'kind':kind,
            'lumens':brightness,'range':radius,
            **({'length':[round(t) for t in beam['lengths']],'material':beam['material']} if kind=='shaft' else {}),
            **({'switch': {'on': f'{switch} Open', 'off': f'{switch} Close'}} if switch else {})}


def light_rooms(project,m,op,refs):
    from .playtest import find_rooms, CameraGeometry, _rotate_xy
    ids = op.get('ids')
    if ids is not None:
        if not isinstance(ids,list) or not ids:
            raise ValueError('ids must be a non-empty list of mesh/group IDs or refs')
        ids = [project.resolve(m,v,refs)['nodeID'] for v in ids]
    rooms=find_rooms(m,ids)
    scene=CameraGeometry(m)
    lights=[]
    existing=m.entities('light_omni2')+m.entities('light_rect')+m.entities('light_barn')
    skipped=0
    for room in rooms:
        lo,hi=room['local_bounds']
        nx=max(1,math.ceil((hi[0]-lo[0])/512))
        ny=max(1,math.ceil((hi[1]-lo[1])/512))
        for ix in range(nx):
            for iy in range(ny):
                pos=[lo[0]+(ix+.5)*(hi[0]-lo[0])/nx,lo[1]+(iy+.5)*(hi[1]-lo[1])/ny,hi[2]-24]
                pos=_rotate_xy(pos,room['yaw'])
                if not scene.clear(pos,12):
                    continue
                if any(math.dist(e['origin'],pos)<96 for e in existing):
                    skipped+=1
                    continue
                result=add_light(project,m,{'kind':'ceiling','origin':pos,'brightness':op.get('brightness','normal'),
                                            'temperature':op.get('temperature',3000)},refs)
                lights.append(result['id'])
                existing.append(m.node(result['id']))
    return {'op':'light_rooms','rooms':len(rooms),'ids':lights,'skipped_existing':skipped}

