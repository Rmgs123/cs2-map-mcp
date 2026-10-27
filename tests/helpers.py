import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from cs2map.config import load_settings  # noqa: E402

try:
    SETTINGS = load_settings()
    CS2_CONTENT = SETTINGS.cs2_path / 'content'
    HAVE_CS2 = (CS2_CONTENT / 'csgo' / 'maps' / 'templates' / 'template_wingman.vmap').exists()
except Exception:  # pragma: no cover
    SETTINGS, CS2_CONTENT, HAVE_CS2 = None, None, False
# CS2MAP_OFFLINE=1: tests that start resourcecompiler or CS2 skip (the user plays; jobs refuse those programs too).
HAVE_COMPILER = HAVE_CS2 and not os.environ.get('CS2MAP_OFFLINE')
# The Blender every pipeline uses (settings, Steam, tools/, Program Files): tests that need it skip without it.
try:
    HAVE_BLENDER = load_settings().blender_exe.is_file()
except Exception:  # pragma: no cover
    HAVE_BLENDER = False


def agent_suffix() -> str:
    """'_a' for CS2MAP_AGENT=A etc., so parallel agents' tests write different scratch maps."""
    agent = re.sub(r'[^a-z0-9]', '', os.environ.get('CS2MAP_AGENT', '').lower())
    return f'_{agent}' if agent else ''


def valve_map(rel: str) -> Path:
    return CS2_CONTENT / rel


def check_topology(testcase, mesh_el, closed=True):
    """Assert half-edge invariants of a CDmePolygonMesh element."""
    dest = mesh_el['edgeVertexIndices']
    opp = mesh_el['edgeOppositeIndices']
    nxt = mesh_el['edgeNextIndices']
    face = mesh_el['edgeFaceIndices']
    vedge = mesh_el['vertexEdgeIndices']
    fedge = mesh_el['faceEdgeIndices']
    n = len(dest)
    testcase.assertEqual(n % 2, 0)
    for arr in (opp, nxt, face, mesh_el['edgeDataIndices'], mesh_el['edgeVertexDataIndices']):
        testcase.assertEqual(len(arr), n)
    for h in range(n):
        testcase.assertEqual(opp[opp[h]], h)
        testcase.assertNotEqual(opp[h], h)
        # next half-edge starts where h ends
        testcase.assertEqual(dest[opp[nxt[h]]], dest[h])
        testcase.assertEqual(face[nxt[h]], face[h])
    for v, h in enumerate(vedge):
        testcase.assertEqual(dest[opp[h]], v)
    for f, h0 in enumerate(fedge):
        h, seen = h0, 0
        while True:
            testcase.assertEqual(face[h], f)
            h = nxt[h]
            seen += 1
            testcase.assertLess(seen, 10000)
            if h == h0:
                break
    if closed:
        testcase.assertNotIn(-1, face, 'mesh should be closed (watertight)')
    sizes = {'vertexData': len(vedge), 'faceVertexData': n, 'edgeData': n // 2, 'faceData': len(fedge)}
    for name, size in sizes.items():
        arr = mesh_el[name]
        testcase.assertEqual(arr['size'], size, name)
        for s in arr['streams']:
            testcase.assertEqual(len(s['data']), size, f'{name}.{s.name}')


def signed_volume(mesh_el):
    pos = mesh_el['vertexData']['streams'][0]['data']
    dest, nxt, fedge = mesh_el['edgeVertexIndices'], mesh_el['edgeNextIndices'], mesh_el['faceEdgeIndices']
    vol = 0.0
    for h0 in fedge:
        loop, h = [], h0
        while True:
            loop.append(pos[dest[h]])
            h = nxt[h]
            if h == h0:
                break
        a = loop[0]
        for b, c in zip(loop[1:], loop[2:]):
            vol += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
    return vol
