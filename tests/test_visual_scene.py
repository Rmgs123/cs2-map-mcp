"""visual_check findings for wall parts, cables, doors, paved slabs, bare clips and fences (visual_scene.py), the
add_mesh name/tags fields, the add_decal overlay check and the CS2-not-rendering hint."""
import unittest
from unittest.mock import patch

from cs2map import dressing, game, mesh, structure, visual, visual_scene
from cs2map.project import OpError, Project, mesh_label, mesh_tags
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS

AC = 'models/props/test/ac_unit_wall_mounted_01.vmdl'
AC_BOX = ((0.0, -18.0, 0.0), (14.0, 18.0, 27.0))      # back at local x = 0, front towards +x
GLASS = 'materials/test/glass_window.vmat'
DOOR = 'materials/test/door_metal.vmat'


def entry(m, origin, yaw=0.0, model=AC, local=AC_BOX):
    ent = m.new_entity('prop_static', {'model': model}, origin, (0, yaw, 0))
    m.attach(ent)
    world = visual._transformed(local, ent.get('origin'), ent.get('angles'), (1, 1, 1))
    return {'node': ent, 'id': ent.get('nodeID'), 'model': model, 'bounds': world, 'local_bounds': local,
            'class': 'prop_static', 'props': {}}


def no_mount(project, e):
    return None


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture needed')
class VisualScene(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        # A wall whose face is the plane x = 0 (looking +x), 512 wide, 256 tall.
        self.m.attach(self.m.new_mesh(mesh.box([-16, -256, 0], [0, 256, 256])))

    def walls(self, props):
        return visual_scene.wall_checks(self.p, props, visual._visible_meshes(self.m), no_mount, visual._frame)

    def codes(self, found):
        return [f['code'] for f in found]

    def test_wall_part_on_the_wall_passes_and_off_or_sunk_is_found(self):
        self.assertEqual(self.walls([entry(self.m, (0.5, 0, 64))]), [])
        off = self.walls([entry(self.m, (6, 0, 64))])
        self.assertEqual(self.codes(off), ['visual_wall_gap'])
        self.assertIn('6.0', off[0]['message'])
        sunk = self.walls([entry(self.m, (-7, 0, 64))])
        self.assertEqual(self.codes(sunk), ['visual_wall_gap'])
        self.assertIn('sunk', sunk[0]['message'])

    def test_turned_part_and_part_past_the_wall_end(self):
        # Turned 90 degrees: its back (local -x) now looks along -y onto a wall at y = 0.
        self.m.attach(self.m.new_mesh(mesh.box([200, -16, 0], [400, 0, 256])))
        self.assertEqual(self.walls([entry(self.m, (300, 0.5, 64), yaw=90)]), [])
        past = self.walls([entry(self.m, (0.5, 250, 64))])          # half of it beyond y = 256
        self.assertEqual(self.codes(past), ['visual_wall_gap'])
        self.assertIn('past the end', past[0]['message'])

    def test_unit_over_a_window_pane(self):
        self.m.attach(self.m.new_mesh(mesh.box([-2, -40, 40], [-1, 40, 120], material=GLASS),
                                      options={'physicsType': 'none'}))
        found = self.walls([entry(self.m, (0.5, 0, 64))])
        self.assertIn('visual_unit_over_window', self.codes(found))
        self.assertEqual(self.walls([entry(self.m, (0.5, 150, 64))]), [])

    def test_cable_end_in_the_air(self):
        dressing.add_cables(self.p, self.m, {'op': 'add_cables', 'start': [2, 0, 160], 'end': [300, 0, 170]}, {})
        found = visual_scene.cable_checks(self.m, [], visual._visible_meshes(self.m))
        self.assertEqual(self.codes(found), ['visual_cable_in_air'])
        self.assertIn('300', found[0]['message'])
        self.m.attach(self.m.new_mesh(mesh.box([298, -4, 0], [306, 4, 200])))       # a pole at the far end
        self.assertEqual(visual_scene.cable_checks(self.m, [], visual._visible_meshes(self.m)), [])

    def test_door_over_window_but_not_a_glazed_door(self):
        self.m.attach(self.m.new_mesh(mesh.box([-3, -40, 40], [-2, 40, 110], material=GLASS),
                                      options={'physicsType': 'none'}))
        self.m.attach(self.m.new_mesh(mesh.box([1, -24, 0], [3, 24, 96], material=DOOR)))
        self.assertEqual(self.codes(visual_scene.door_window_checks(visual._visible_meshes(self.m))),
                         ['visual_door_over_window'])
        m2 = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m2.attach(m2.new_mesh(mesh.box([1, -24, 0], [3, 24, 96], material=DOOR)))
        m2.attach(m2.new_mesh(mesh.box([1.5, -10, 50], [2.5, 10, 80], material=GLASS), options={'physicsType': 'none'}))
        self.assertEqual(visual_scene.door_window_checks(visual._visible_meshes(m2)), [])

    def test_paved_slab_over_flat_ground(self):
        plans = [{'bounds': ((-2000, -2000, -100), (2000, 2000, 100))}]
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m.attach(m.new_mesh(mesh.box([-200, -64, -30], [200, 64, 10], material='materials/test/asphalt.vmat')))
        low = m.new_mesh(mesh.box([-200, 300, -30], [200, 428, 2], material='materials/test/asphalt.vmat'))
        m.attach(low)
        with patch('cs2map.terrain.plans', return_value=plans), \
                patch('cs2map.terrain.sample', return_value={'z': 0.0}):
            found = visual_scene.paved_slab_checks(m, visual._solid_meshes(m), visual._paved)
        self.assertEqual(self.codes(found), ['visual_paved_slab'])
        self.assertNotEqual(found[0]['id'], low.get('nodeID'))

    def paved(self, boxes, kinds=('gap', 'patch'), zone=None):
        """visual_paved_gap / visual_paved_patch of asphalt boxes [(mins, maxs, material?)] over flat ground at z 0."""
        plans = [{'bounds': ((-4000, -4000, -100), (4000, 4000, 100))}]
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        for lo, hi, *mat in boxes:
            m.attach(m.new_mesh(mesh.box(lo, hi, material=mat[0] if mat else 'materials/test/asphalt.vmat')))
        with patch('cs2map.terrain.plans', return_value=plans), patch('cs2map.terrain.sample', return_value={'z': 0.0}):
            return visual_scene.paved_checks(m, visual._solid_meshes(m), visual._paved, kinds=kinds, zone=zone)

    def test_a_zone_judges_a_long_edge_by_its_part_in_the_zone(self):
        # 100 m of road standing 10 units over the ground; the zone holds 400 units of it (+64 around)
        road = ([-2000, -128, -30], [2000, 128, 10])
        whole = self.paved([road], kinds=('slab',))
        zone = visual_scene.Zone([[0, -300], [400, 300]], 64, 256)
        part = self.paved([road], kinds=('slab',), zone=zone)
        self.assertEqual((self.codes(whole), self.codes(part)), (['visual_paved_slab'], ['visual_paved_slab']))
        metres = lambda found: float(found[0]['message'].split(' m ')[0])
        self.assertGreater(metres(whole), 200)
        self.assertAlmostEqual(metres(part), 2 * 544 / 39.37, delta=0.2)      # both long edges, 17 samples of 32 units
        self.assertTrue(-64 <= part[0]['at'][0] <= 464, part[0]['at'])
        # nothing of it in the zone: no finding, although the mesh was loaded as a neighbour
        away = visual_scene.Zone([[0, 400], [400, 800]], 64, 256)
        self.assertEqual(self.paved([road], kinds=('slab',), zone=away), [])
        # a stray piece whose outline leaves what the zone audit loaded is not judged at all
        piece = ([-100, 600, -30], [100, 760, 4])
        self.assertEqual(self.codes(self.paved([piece], zone=visual_scene.Zone([[-200, 500], [200, 900]], 64, 256))),
                         ['visual_paved_patch'])
        self.assertEqual(self.paved([piece], zone=visual_scene.Zone([[-1000, 500], [-380, 900]], 64, 256)), [])

    def test_soil_between_two_pavings(self):
        road = ([-800, -128, -30], [800, 128, 4])                  # 40 m x 6.5 m: too big for a patch
        walk = ([-800, 168, -30], [800, 400, 4])                   # 40 units of bare ground between them
        found = self.paved([road, walk])
        self.assertEqual(self.codes(found), ['visual_paved_gap'])  # one finding for the pair, not one per side
        self.assertIn('up to 42 units wide', found[0]['message'])     # probed every 6 units: 40 reads as <= 42
        self.assertEqual(len(found[0]['ids']), 2)
        # joined edge to edge, or with a kerb in the strip: no gap
        self.assertEqual(self.paved([road, ([-800, 128, -30], [800, 400, 4])]), [])
        kerb = ([-800, 128, -30], [800, 168, 6], 'materials/test/plaster_wall.vmat')
        self.assertEqual(self.paved([road, walk, kerb]), [])
        # a step of more than 24 units is a terrace, and 200 units apart are two streets
        self.assertEqual(self.paved([road, ([-800, 168, -30], [800, 400, 40])]), [])
        self.assertEqual(self.paved([road, ([-800, 328, -30], [800, 600, 4])]), [])

    def test_a_stray_piece_of_paving(self):
        road = ([-800, -128, -30], [800, 128, 4])
        piece = ([-100, 600, -30], [100, 760, 4])                  # 5 x 4 m alone in the yard
        found = self.paved([road, piece])
        self.assertEqual(self.codes(found), ['visual_paved_patch'])
        self.assertIn('20.6 m2', found[0]['message'])
        self.assertEqual(self.paved([road, ([-100, 128, -30], [100, 288, 4])]), [])      # a driveway off the road
        wall = ([-100, 760, -30], [100, 776, 96], 'materials/test/plaster_wall.vmat')
        fence = ([-116, 600, -30], [-100, 760, 96], 'materials/test/plaster_wall.vmat')
        self.assertEqual(self.paved([road, piece, wall, fence]), [])                     # a porch in a corner

    def test_tag_paved_marks_paving(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        res = self.p._apply(m, {'op': 'add_mesh', 'name': 'east pad', 'tags': ['paved', 'east'],
                                'shape': {'shape': 'box', 'mins': [0, 0, 0], 'maxs': [64, 64, 8]}}, {})
        node = m.node(res['ids'][0])
        self.assertEqual((node.name, mesh_tags(node)), ('east pad', {'paved', 'east'}))
        self.assertTrue(visual._paved(node))
        with self.assertRaisesRegex(OpError, 'tags'):
            mesh_label({'tags': ['Bad Tag']})
        with self.assertRaisesRegex(OpError, 'unknown add_mesh'):
            self.p._apply(m, {'op': 'add_mesh', 'label': 'x', 'shape': {'shape': 'box', 'mins': [0, 0, 0],
                                                                         'maxs': [1, 1, 1]}}, {})

    def test_bare_clip_and_a_clip_behind_a_fence(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m.attach(m.new_mesh(mesh.box([-400, -400, -16], [400, 400, 0], material='materials/test/concrete_floor.vmat')))
        m.attach(m.new_mesh(mesh.box([-200, -4, 0], [200, 4, 128], material='materials/tools/toolsplayerclip.vmat')))
        run = lambda: visual_scene.clip_checks(m, visual._solid_meshes(m), visual._visible_meshes(m), [])
        self.assertEqual(self.codes(run()), ['visual_bare_clip'])
        m.attach(m.new_mesh(mesh.box([-200, 6, 0], [200, 8, 48], material='materials/test/fence_wood.vmat'),
                            options={'physicsType': 'none'}))
        self.assertEqual(run(), [])
        # A clip over a roof (a map's boundary) is no invisible wall for players.
        m2 = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m2.attach(m2.new_mesh(mesh.box([-400, -400, -16], [400, 400, 0], material='materials/test/roof_tiles.vmat')))
        m2.attach(m2.new_mesh(mesh.box([-200, -4, 0], [200, 4, 128], material='materials/tools/toolsplayerclip.vmat')))
        self.assertEqual(visual_scene.clip_checks(m2, visual._solid_meshes(m2), visual._visible_meshes(m2), []), [])

    def test_fence_without_collision(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        m.attach(m.new_mesh(mesh.box([-400, -400, -16], [400, 400, 0])))
        m.attach(m.new_mesh(mesh.box([-100, -2, 0], [100, 2, 72], material='materials/test/fence_wood.vmat'),
                            options={'physicsType': 'none'}))
        run = lambda: visual_scene.fence_checks(m, visual._solid_meshes(m), visual._render_only_meshes(m))
        self.assertEqual(self.codes(run()), ['visual_fence_no_collision'])
        m.attach(m.new_mesh(mesh.box([-100, -3, 0], [100, 3, 72], material='materials/tools/toolsplayerclip.vmat')))
        self.assertEqual(run(), [])


class OverlayAndHints(unittest.TestCase):
    def test_overlay_pattern(self):
        self.assertTrue(structure._OVERLAY.search('F_OVERLAY "1"'))
        self.assertTrue(structure._OVERLAY.search('shader "csgo_static_overlay.vfx"'))
        self.assertFalse(structure._OVERLAY.search('shader "csgo_complex.vfx"\nF_OVERLAY "0"'))

    def test_opaque_own_material_is_refused_on_a_decal(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'materials').mkdir()
            (root / 'materials' / 'plate.vmat').write_text('Layer0 { shader "csgo_complex.vfx" }', encoding='utf-8')
            (root / 'materials' / 'sticker.vmat').write_text('Layer0 { F_OVERLAY "1" }', encoding='utf-8')
            project = type('P', (), {'s': type('S', (), {'content_addon': root})()})()
            with self.assertRaisesRegex(OpError, 'not an overlay'):
                structure._overlay_material(project, 'materials/plate.vmat')
            structure._overlay_material(project, 'materials/sticker.vmat')
            structure._overlay_material(project, 'materials/decals/stock.vmat')     # not ours: not read

    def test_not_rendering_hint_names_the_cause(self):
        class W:
            hwnd = 1
        with patch.object(game, '_window', return_value=W()), patch.object(game.system, 'foreground', return_value=2), \
                patch.object(game.system, 'idle_seconds', return_value=3.0):
            text = game._not_rendering_hint()
        self.assertIn('not in front', text)
        self.assertIn('3 s ago', text)
        with patch.object(game, '_window', return_value=W()), patch.object(game.system, 'foreground', return_value=1), \
                patch.object(game.system, 'idle_seconds', return_value=600.0):
            self.assertIn('did not render', game._not_rendering_hint())


if __name__ == '__main__':
    unittest.main()
