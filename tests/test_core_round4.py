"""Core changes of the round-4 integration: console sharing, vertex paint, window shafts, use targets."""
import socket
import threading
import time
import unittest

from cs2map import checks, lighting, mesh, story, vconsole
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


class FakeConsole:
    """A VConsole-like server that echoes CMND text back as PRNT and accepts one client at a time."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.clients = 0
        self.closed = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.clients += 1
            self.closed.clear()
            buf = b''
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 12:
                    length = int.from_bytes(buf[8:10], 'big')
                    if len(buf) < length:
                        break
                    text = buf[12:length].rstrip(b'\0').decode()
                    buf = buf[length:]
                    if text.startswith('echo '):
                        payload = bytes(28) + text[5:].encode() + b'\0'
                        conn.sendall(vconsole._packet(b'PRNT', payload))
            conn.close()
            self.closed.set()


class ConsoleSharing(unittest.TestCase):
    def test_connection_is_released_after_idle(self):
        fake = FakeConsole()
        old = vconsole.IDLE_CLOSE
        vconsole.IDLE_CLOSE = 0.6
        try:
            ok, lines = vconsole.run_until(['echo hello_fake'], 'hello_fake', timeout=3, port=fake.port)
            self.assertTrue(ok, lines)
            self.assertFalse(fake.closed.is_set())
            # CS2 serves one console client: an idle process must hand the port back.
            self.assertTrue(fake.closed.wait(4))
            self.assertIsNone(vconsole._CONN)
            ok, _ = vconsole.run_until(['echo again'], 'again', timeout=3, port=fake.port)
            self.assertTrue(ok)
            self.assertEqual(fake.clients, 2)
            vconsole.release()
            self.assertTrue(fake.closed.wait(2))
        finally:
            vconsole.IDLE_CLOSE = old
            vconsole.release()
            fake.srv.close()

    def test_listener_keeps_connection_open(self):
        fake = FakeConsole()
        old = vconsole.IDLE_CLOSE
        vconsole.IDLE_CLOSE = 0.3
        try:
            conn = vconsole._connection(fake.port)
            held = []
            with conn.cv:
                conn.listeners.append(held)
            time.sleep(1.2)
            self.assertTrue(conn.alive)            # script_test / route_test hold a listener
            with conn.cv:
                conn.listeners.remove(held)
            self.assertTrue(fake.closed.wait(3))
        finally:
            vconsole.IDLE_CLOSE = old
            vconsole.release()
            fake.srv.close()


class VertexPaint(unittest.TestCase):
    def test_paint_stream_written_only_when_colors_given(self):
        plain = mesh.box((0, 0, 0), (16, 16, 16)).to_dmx()
        names = [s['semanticName'] for s in plain['faceVertexData']['streams']]
        self.assertNotIn('VertexPaintTintColor', names)
        m = mesh.Mesh()
        m.face([(0, 0, 0), (32, 0, 0), (32, 32, 0), (0, 32, 0)], colors=[(1, 1, 1, 0), (1, 1, 1, 1),
                                                                          (1, 1, 1, 1), (1, 1, 1, 0)])
        dmx = m.to_dmx()
        stream = next(s for s in dmx['faceVertexData']['streams'] if s['semanticName'] == 'VertexPaintTintColor')
        self.assertEqual(stream['vertexBufferLocation'], 1)
        dest, face_of = dmx['edgeVertexIndices'], dmx['edgeFaceIndices']
        alpha = {dest[h]: stream['data'][h][3] for h in range(len(dest)) if face_of[h] != -1}
        self.assertEqual(alpha, {0: 0.0, 1: 1.0, 2: 1.0, 3: 0.0})

    def test_face_out_reverses_colors_with_winding(self):
        m = mesh.Mesh()
        f = m.face_out([(0, 0, 0), (0, 32, 0), (32, 32, 0)], (0, 0, -1), colors=[(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1)])
        corner = {m.positions[v]: c for v, c in zip(f.verts, f.colors)}
        self.assertEqual(corner[(0.0, 32.0, 0.0)], (0, 1, 0, 1))


class WindowShaft(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.m = VMap.from_template(SETTINGS.cs2_path, 'basic')
        floor = self.m.new_mesh(mesh.box((-512, -512, -16), (1024, 1024, 0)))
        self.m.attach(floor)
        env = self.m.entities('light_environment')[0]
        env['angles'] = (45.0, 270.0, 0.0)

    @unittest.skipUnless(HAVE_CS2, 'needs the installed lightshaft material')
    def test_sun_window_beam_lands_on_the_floor(self):
        out = lighting.add_light(self.p, self.m, {'kind': 'shaft', 'sun': True,
                                                  'window': {'mins': [0, 512, 100], 'maxs': [100, 528, 200]}}, {})
        self.assertTrue(out['sun'])
        # 45 degrees down: a corner at height h travels about h * sqrt(2) to the floor.
        for got, want in zip(out['length'], [141, 141, 283, 283]):
            self.assertAlmostEqual(got, want, delta=4)
        self.assertEqual(len(out['ids']), 8)
        for node_id in out['ids']:
            node = self.m.node(node_id)
            self.assertEqual(node['physicsType'], 'none')
            self.assertFalse(checks._is_solid_mesh(node))
        self.assertFalse(self.m.entities('light_barn'))      # the sun lights the patch, no lamp

    def test_bad_requests_are_refused(self):
        cases = [({'kind': 'shaft', 'sun': True, 'window': {'mins': [0, 0, 0], 'maxs': [100, 16, 100]},
                   'shape': 'tube'}, 'shape'),
                 ({'kind': 'shaft', 'sun': 'yes', 'window': {'mins': [0, 0, 0], 'maxs': [1, 1, 1]}}, 'sun'),
                 ({'kind': 'shaft', 'window': {'mins': [0, 0, 0], 'maxs': [100, 100, 16]}, 'angles': [0, 0, 0]},
                  'along the wall')]
        for op, text in cases:
            with self.assertRaisesRegex(ValueError, text):
                lighting.add_light(self.p, self.m, op, {})


class UseTarget(unittest.TestCase):
    def test_never_solid_func_brush_is_refused(self):
        m = VMap.from_template(SETTINGS.cs2_path, 'empty')
        note = m.new_entity('func_brush', {'targetname': 'note', 'Solidity': '1'})
        m.attach(note)
        spec = story.validate({'op': 'add_story', 'name': 'u_story',
                               'stages': [{'id': 'read', 'objective': 'Read', 'until': {'use': 'note'}}]})
        with self.assertRaisesRegex(ValueError, 'Solidity'):
            story._prepare(spec, m)
        note['entity_properties']['Solidity'] = '2'
        story._prepare(spec, m)


class LayoutRoutes(unittest.TestCase):
    """route_test walks through add_layout openings; ladders join floors for check_map."""

    def setUp(self):
        from cs2map import layout, physics
        from tests.test_layout import warehouse
        self.layout = layout
        self.p = Project(); self.m = VMap.from_template(self.p.s.cs2_path, 'basic'); self.refs = {}
        self.p.register_op('add_layout', layout.add_layout)
        for name, fn in physics.OPERATIONS.items():
            self.p.register_op(name, fn)
        self.p.register_preprocessor(layout.preprocess)
        self.p._apply(self.m, warehouse(), self.refs)
        self.rooms = layout.plans(self.m)[0]['rooms']

    def centre(self, room):
        r = self.rooms[room]['interior']
        return [(r['mins'][0] + r['maxs'][0]) / 2, (r['mins'][1] + r['maxs'][1]) / 2, self.rooms[room]['floor_z']]

    def test_waypoints_pass_every_opening_on_the_way(self):
        from cs2map import route
        points = route.layout_waypoints(self.m, self.centre('back'), self.centre('hall'))
        self.assertEqual(len(points), 4)                       # before/after back|corr and corr|hall
        walls = [self.rooms['back']['interior']['maxs'][0], self.rooms['corr']['interior']['maxs'][0]]
        for (a, b), wall in zip((points[0:2], points[2:4]), walls):
            self.assertLess(a[0], wall); self.assertGreater(b[0], wall)
        self.assertEqual(route.layout_waypoints(self.m, self.centre('hall'), self.centre('hall')), [])
        # an explicit route gets the doorways inserted before its go step
        steps = route.through_openings(self.m, [{'go': self.centre('hall')}, {'wait': 1}], self.centre('back'))
        self.assertEqual([('go' in x, x.get('radius')) for x in steps],
                         [(True, 24)] * 4 + [(True, None), (False, None)])

    def test_ladder_joins_the_hall_floor_with_the_mezzanine(self):
        self.p._apply(self.m, {'op': 'add_entity', 'classname': 'info_player_terrorist',
                               'origin': {'room': 'back', 'at': [.5, .5], 'z': 8}}, self.refs)
        unreachable = lambda: [x for x in self.layout.check_layout(self.p, self.m)
                               if x['code'] == 'layout_room_unreachable']
        self.assertEqual(len(unreachable()), 1)                # the mezzanine
        mezz = self.rooms['mezz']['interior']
        y = (mezz['mins'][1] + mezz['maxs'][1]) / 2
        self.p._apply(self.m, {'op': 'add_ladder', 'mins': [mezz['mins'][0] - 16, y - 16, 0],
                               'maxs': [mezz['mins'][0], y + 16, self.rooms['mezz']['floor_z']]}, self.refs)
        self.assertEqual(unreachable(), [])


if __name__ == '__main__':
    unittest.main()
