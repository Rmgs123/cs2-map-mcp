import unittest

from tests.helpers import HAVE_CS2, CS2_CONTENT, ROOT

from cs2map import kv3


class KV3(unittest.TestCase):
    def test_roundtrip_values(self):
        data = {'a': 1, 'b': 2.5, 'c': 'text "q"', 'd': True, 'e': None, 'f': [1, 2, 3],
                'g': [{'_class': 'X', 'n': 'y'}], 'h': {}, 'i': [], 'j': kv3.Flagged('resource', 'models/x.vmdl'),
                'k': 'multi\nline', 'm.dotted': 1.0}
        header, back = kv3.loads(kv3.dumps(data))
        self.assertEqual(back, data)
        self.assertIn('kv3', header)

    @unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
    def test_valve_files_parse_and_roundtrip(self):
        files = [CS2_CONTENT / 'csgo_addons' / 'addon_template' / 'postprocess' / 'basic_linear_post.vpost',
                 CS2_CONTENT / 'csgo' / 'workshop' / 'content_examples' / 'breakable_glass' / 'breakable_glass_01.vmdl']
        for f in files:
            with self.subTest(f.name):
                header, value = kv3.loads(f.read_text(encoding='utf-8'))
                self.assertIsInstance(value, dict)
                self.assertEqual(kv3.loads(kv3.dumps(value, header))[1], value)


if __name__ == '__main__':
    unittest.main()
