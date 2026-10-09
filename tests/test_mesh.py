import math
import unittest

from tests.helpers import HAVE_CS2, check_topology, signed_volume, valve_map

from cs2map import dmx, mesh


class PrimitiveTopology(unittest.TestCase):
    def check(self, m, volume=None, closed=True):
        el = m.to_dmx()
        check_topology(self, el, closed)
        if closed:
            vol = signed_volume(el)
            self.assertGreater(vol, 0, 'faces must point outwards')
            if volume is not None:
                self.assertAlmostEqual(vol, volume, delta=abs(volume) * 1e-6 + 1e-3)
        return el

    def test_box(self):
        self.check(mesh.box((0, 0, 0), (256, 128, 64)), 256 * 128 * 64)

    def test_wedges(self):
        for rise in ('+x', '-x', '+y', '-y'):
            with self.subTest(rise=rise):
                self.check(mesh.wedge((0, 0, 0), (256, 128, 64), rise), 256 * 128 * 64 / 2)

    def test_cylinder(self):
        el = self.check(mesh.cylinder((0, 0), 64, 0, 100, 24))
        area = 0.5 * 24 * 64 * 64 * math.sin(2 * math.pi / 24)
        self.assertAlmostEqual(signed_volume(el), area * 100, delta=1)

    def test_prism_axes(self):
        tri = [(0, 0), (100, 0), (0, 50)]
        for axis in ('x', 'y', 'z'):
            with self.subTest(axis=axis):
                self.check(mesh.prism(tri, 10, 30, axis), 100 * 50 / 2 * 20)

    def test_stairs(self):
        for rise in ('+x', '-x', '+y', '-y'):
            for steps in (1, 2, 8):
                with self.subTest(rise=rise, steps=steps):
                    m = mesh.stairs((0, 0, 0), (256, 96, 128), steps, rise)
                    run = 256 if rise in ('+x', '-x') else 96
                    width = 96 if rise in ('+x', '-x') else 256
                    expected = sum((run / steps) * (128 / steps * k) for k in range(1, steps + 1)) * width
                    self.check(m, expected)

    def test_heightfield(self):
        m = mesh.heightfield((0, 0, 0), (512, 512, 0), (8, 6), lambda x, y: 20 * math.sin(x / 90) * math.cos(y / 70))
        self.check(m)

    def test_open_quad(self):
        m = mesh.Mesh()
        m.face([(0, 0, 0), (100, 0, 0), (100, 100, 0), (0, 100, 0)])
        el = m.to_dmx()
        check_topology(self, el, closed=False)
        self.assertEqual(el['edgeFaceIndices'].count(-1), 4)

    def test_flipped_face_is_rejected(self):
        m = mesh.box((0, 0, 0), (10, 10, 10))
        m.faces[0].verts.reverse()
        with self.assertRaises(mesh.MeshError):
            m.to_dmx()

    def test_degenerate_rejected(self):
        with self.assertRaises(mesh.MeshError):
            mesh.box((0, 0, 0), (0, 10, 10))
        with self.assertRaises(mesh.MeshError):
            mesh.Mesh().face([(0, 0, 0), (1, 0, 0), (2, 0, 0)])

    def test_encodes(self):
        root = dmx.Element('DmElement', 'root')
        root.set('mesh', 'element', mesh.stairs((0, 0, 0), (100, 50, 50), 4).to_dmx())
        doc = dmx.Document(root=root, format='vmap', format_version=40)
        back = dmx.loads(dmx.dumps(doc))
        self.assertEqual(back.root['mesh']['edgeNextIndices'], root['mesh']['edgeNextIndices'])


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class MatchesValve(unittest.TestCase):
    def test_box_matches_valve_trigger_box(self):
        """Our box must reproduce the per-corner normal/tangent/UV of a box Hammer made."""
        doc = dmx.load(valve_map(r'csgo\maps\templates\template_wingman.vmap'))
        target = None
        for e in dmx.walk(doc.root, 'CMapEntity'):
            if e['entity_properties'].get('classname') == 'func_bomb_target':
                target = e['children'][0]
        valve = target['meshData']
        origin = target['origin']

        def corners(el):
            pos = el['vertexData']['streams'][0]['data']
            fvd = {s.name: s['data'] for s in el['faceVertexData']['streams']}
            out = {}
            for h, v in enumerate(el['edgeVertexIndices']):
                i = el['edgeVertexDataIndices'][h]
                n = tuple(round(c, 3) for c in fvd['normal:0'][i])
                key = (tuple(round(c, 3) for c in pos[v]), n)
                uv = fvd['texcoord:0'][i]
                out[key] = (tuple(round(c, 3) + 0.0 for c in fvd['tangent:0'][i]),
                            (round(uv[0] % 1, 4), round(uv[1] % 1, 4)))
            return out

        ours = mesh.box((-128, -128, -32), (128, 128, 32), material='materials/tools/toolstrigger.vmat')
        mine = corners(ours.to_dmx(origin=origin, texture_size=(64, 64)))
        theirs = corners(valve)
        self.assertEqual(set(mine), set(theirs))
        for key in theirs:
            self.assertEqual(mine[key], theirs[key], key)
        self.assertEqual(len(ours.to_dmx()['edgeVertexIndices']), len(valve['edgeVertexIndices']))


if __name__ == '__main__':
    unittest.main()
