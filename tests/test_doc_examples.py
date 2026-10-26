"""Every map_edit example in the guides (docs/TOOLS.md, docs/tools/*.md) passes dry_run on a fixture map.

A ```json block counts when it is an operation, a list of operations, {"name", "operations"} or JSON lines of
operations. The fixture has the room, layout and terrain names the guides use (layout `docs` with yard, gallery,
lane, street; layout `port` with pier; terrain `land`; facades on the street) and the assets their examples name
(signs exit/warehouse, decal graffiti, models a_barrier and bus, script round_log, ambience event m_hall). An example that
builds on an earlier example of the same section (its @refs, rooms or entities) is checked together with them.
Run alone: python -m unittest tests.test_doc_examples
"""
import json
import re
import unittest
from pathlib import Path

from tests.helpers import HAVE_CS2, ROOT, agent_suffix

MAP = 'test_doc_examples' + agent_suffix()
BARE = 'test_doc_examples_bare' + agent_suffix()
DOCS = [ROOT / 'docs' / 'TOOLS.md'] + sorted((ROOT / 'docs' / 'tools').glob('*.md'))
# The cross-map rule (one definition per controllable name in the whole addon) depends on the other maps there.
ADDON_STATE = ()

ROOMS = [
    {'op': 'add_layout', 'name': 'docs', 'wall': 16, 'rooms': [
        {'id': 'yard', 'origin': [0, 0, 0], 'size': [1024, 1024, 384], 'ceiling': False},
        {'id': 'gallery', 'from': 'yard', 'side': '+x', 'size': [512, 1024, 256]},
        {'id': 'lane', 'from': 'yard', 'side': '-y', 'size': [256, 768, 320], 'ceiling': False, 'align': 'start'},
        {'id': 'street', 'from': 'yard', 'side': '+y', 'size': [1024, 384, 512], 'ceiling': False}],
     'openings': [{'between': ['yard', 'gallery'], 'width': 192, 'height': 160},
                  {'between': ['yard', 'lane'], 'width': 160, 'height': 224},
                  {'between': ['yard', 'street'], 'width': 256, 'height': 224}]},
    {'op': 'add_entity', 'classname': 'info_player_terrorist', 'origin': {'room': 'yard', 'at': [0.2, 0.5], 'z': 8}},
    {'op': 'add_entity', 'classname': 'info_player_counterterrorist', 'origin': {'room': 'yard', 'at': [0.8, 0.5], 'z': 8}},
    {'op': 'add_button', 'targetname': 'my_lift_lower', 'mins': [100, 100, 40], 'maxs': [108, 116, 56],
     'targets': [{'name': 'door', 'input': 'Open'}]},
]
FIXTURE = ROOMS + [
    {'op': 'add_layout', 'name': 'port', 'wall': 16, 'rooms': [
        {'id': 'quay', 'origin': [3000, 0, 0], 'size': [768, 768, 256], 'ceiling': False},
        {'id': 'pier', 'from': 'quay', 'side': '+x', 'size': [512, 256, 256], 'ceiling': False, 'floor_z': 32}],
     'openings': [{'between': ['quay', 'pier'], 'width': 192, 'height': 160}]},
    {'op': 'add_terrain', 'name': 'land', 'bounds': [[-2048, -2048], [2048, 2048]], 'base_z': -400, 'cell': 128,
     'ops': [{'op': 'hill', 'center': [-1200, -1200], 'radius': 600, 'height': 100}]},
    {'op': 'add_facade', 'name': 'main_south', 'on': {'room': 'street', 'side': '-y', 'from': 0, 'to': 1},
     'floors': [2, 3], 'floor_height': 144, 'house_width': 320, 'style': 'building_inferno', 'seed': 3},
]


def prepare_assets(raw):
    """The assets the examples name; made once in the addon (it keeps them)."""
    from cs2map import server
    s = server.project.s
    for sign in ('exit', 'warehouse'):
        if not any((s.content_addon / f'materials/{f}/{sign}/{sign}.json').is_file() for f in ('mcp', 'mcp_decals')):
            raw['sign_create'](sign, sign.upper(), 'plate', None, None, 3.0, 'arialbd.ttf')
    if not (s.game_addon / 'materials/mcp_decals/graffiti/graffiti.vmat_c').exists():
        from PIL import Image, ImageDraw
        art = ROOT / '.local' / 'tmp' / 'doc_graffiti.png'
        art.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new('RGBA', (256, 128), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((16, 16, 240, 112), fill=(220, 40, 120, 255))
        im.save(art)
        try:
            raw['decal_create']('graffiti', str(art), [200, 100], None, 20, 20)
        finally:
            art.unlink(missing_ok=True)
    if not (s.game_addon / 'models/mcp/a_barrier/a_barrier.vmdl_c').exists():
        raw['model_build']('a_barrier', None, 'jersey_barrier', None, 'hull', 'concrete', None)
    if not (s.game_addon / 'models/mcp/bus/bus.vmdl_c').exists():      # the bus example's own body (its size only)
        raw['model_build']('bus', None, 'crate', {'width': 590, 'depth': 140, 'height': 150}, 'hull', 'metal', None)
    if not (s.game_addon / 'maps/scripts/mcp/round_log.vjs_c').exists():
        raw['script_write']('round_log', 'import { Instance } from "cs_script/point_script";\n'
                                         'Instance.Msg("round_log: loaded");\n')
    from cs2map import sound
    if not (s.game_addon / 'sounds/mcp/m_hall.vsnd_c').exists():
        raw['sound_generate']('m_hall', 'hum', 2, {'gain': .3})
    raw['sound_event']('m_hall', 'ambient', 'sounds/mcp/m_hall.vsnd', None, .5, 1, 1500, None, None, None, None, 1.5)


def examples():
    """(file, index, operations) of every map_edit example in the guides."""
    out = []
    for path in DOCS:
        for i, m in enumerate(re.finditer(r'```json\n(.*?)```', path.read_text(encoding='utf-8'), re.S)):
            raw = m.group(1)
            try:
                items = [json.loads(raw)]
            except ValueError:
                try:
                    items = [json.loads(line) for line in raw.splitlines() if line.strip()]
                except ValueError:
                    continue
            ops = []
            for data in items:
                if isinstance(data, dict) and 'op' in data:
                    ops.append(data)
                elif isinstance(data, dict) and isinstance(data.get('operations'), list):
                    ops += data['operations']
                elif isinstance(data, list) and data and all(isinstance(x, dict) and 'op' in x for x in data):
                    ops += data
            if ops:
                out.append((path.name, i, ops))
    return out


class Examples(unittest.TestCase):
    def test_examples_are_found(self):
        self.assertGreater(len(examples()), 50)

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_every_map_edit_example_passes_dry_run(self):
        from cs2map import server                      # the project with every extension's operations
        from cs2map.surface import RAW
        project = server.project
        prepare_assets(RAW)
        project.map_create(MAP, 'basic', overwrite=True)
        project.map_edit(MAP, FIXTURE)
        project.map_create(BARE, 'basic', overwrite=True)
        project.map_edit(BARE, ROOMS)
        failures, earlier = [], {}

        def attempt(name, ops):
            try:
                project.map_edit(name, ops, dry_run=True)
                return None
            except Exception as exc:                   # the message says which example and why
                return str(exc)

        for name, index, ops in examples():
            before = earlier.setdefault(name, [])
            error = attempt(MAP, ops)
            if error and before:                       # builds on earlier examples of the same section
                error = attempt(MAP, before + ops) and error
            if error and ('already exists' in error or 'several layouts' in error):   # full fixture differs
                error = attempt(BARE, ops)
            if error and not any(s in error for s in ADDON_STATE):
                failures.append(f'{name} #{index}: {error[:240]}')
            before += ops
        self.assertEqual(failures, [], '\n'.join(failures))


if __name__ == '__main__':
    unittest.main()
