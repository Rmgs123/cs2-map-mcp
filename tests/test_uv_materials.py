import tempfile
import unittest
from pathlib import Path

from cs2map import mesh, uv
from cs2map.config import Settings
from cs2map.dmx import Element


class DummyProject:
    def __init__(self, root):
        self.s = Settings(Path(root) / 'cs2', 'test_addon')

    def texture_size(self, material):
        return (64, 64)


def mesh_node(material='materials/dev/gray.vmat'):
    node = Element('CMapMesh', '')
    node.set('nodeID', 'int', 7)
    node.set('origin', 'vector3', (0.0, 0.0, 0.0))
    node.set('meshData', 'element', mesh.box((0, 0, 0), (128, 64, 16), material).to_dmx())
    return node


class SetUv(unittest.TestCase):
    def test_fit_changes_only_uv_data_and_preserves_topology(self):
        with tempfile.TemporaryDirectory() as td:
            project = DummyProject(td)
            node = mesh_node()
            md = node['meshData']
            topology = {k: list(md[k]) for k in ('edgeVertexIndices', 'edgeNextIndices', 'faceEdgeIndices')}
            result = uv.apply_set_uv(project, node, {'faces': 'top', 'mode': 'fit', 'scale': 'auto', 'rotate': 90})
            self.assertEqual(result['faces'], 1)
            for key, before in topology.items():
                self.assertEqual(list(md[key]), before)
            self.assertNotEqual(list(md['faceVertexData']['streams'][0]['data']), [(0.0, 0.0)] * 24)

    def test_explicit_world_scale_and_offset(self):
        with tempfile.TemporaryDirectory() as td:
            project = DummyProject(td)
            node = mesh_node()
            uv.apply_set_uv(project, node, {'faces': 'all', 'mode': 'world', 'scale': [0.5, 0.25],
                                            'offset': [4, 8]})
            scales = next(s for s in node['meshData']['faceData']['streams'] if s['standardAttributeName'] == 'textureScale')
            self.assertEqual(tuple(scales['data'][0]), (0.5, 0.25))


if __name__ == '__main__':
    unittest.main()

