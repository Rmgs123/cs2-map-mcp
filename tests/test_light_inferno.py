import unittest

from cs2map import lighting, mesh
from cs2map.project import Project
from cs2map.vmap import VMap
from tests.helpers import HAVE_CS2, SETTINGS


@unittest.skipUnless(HAVE_CS2, 'CS2 fixture template needed')
class InfernoLighting(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.map = VMap.from_template(SETTINGS.cs2_path, 'empty')
        # A small closed room gives the preset a real authored bound for its
        # postprocess and combined-probe volume.
        for lo, hi in [
            ([-256, -256, -16], [256, 256, 0]),
            ([-256, -256, 256], [256, 256, 272]),
            ([-272, -256, 0], [-256, 256, 256]),
            ([256, -256, 0], [272, 256, 256]),
            ([-256, -272, 0], [256, -256, 256]),
            ([-256, 256, 0], [256, 272, 256]),
        ]:
            self.map.attach(self.map.new_mesh(mesh.box(lo, hi)))

    def test_inferno_uses_shipped_values_and_probe(self):
        result = lighting.apply_preset(self.project, self.map, 'inferno')
        self.assertEqual(result['preset'], 'inferno')
        sky = next(e for e in self.map.entities('env_sky')
                   if e['entity_properties'].get('targetname') == 'mcp_sky')
        props = sky['entity_properties']
        self.assertEqual(props['skyname'], 'materials/skybox/test/s2_de_inferno_sky01.vmat')
        self.assertEqual(props['brightnessscale'], '0.3')  # quarter calibration; Valve is .5
        self.assertEqual(tuple(round(x) for x in sky['angles']), (0, 245, 0))
        light_sky = self.map.entities('env_sky', targetname='mcp_light_sky')
        self.assertEqual(len(light_sky), 1)
        self.assertEqual(tuple(round(x) for x in light_sky[0]['angles']), (0, 40, 0))

        sun = self.map.entities('light_environment')[0]
        self.assertEqual(tuple(round(x) for x in sun['angles']), (63, 218, 0))
        sunp = sun['entity_properties']
        self.assertEqual(sunp['color'], '255 247 235')
        self.assertEqual(sunp['brightness'], '3')
        self.assertEqual(sunp['skytexture'], 'mcp_light_sky')
        self.assertEqual(sunp['bouncescale'], '1.75')
        self.assertEqual(sunp['skybouncescale'], '1.5')

        post = self.map.entities('post_processing_volume')[0]['entity_properties']
        self.assertEqual(post['postprocessing'],
                         'lighting/postprocessing/de_inferno_prefab/de_inferno_prefab.vpost')
        self.assertEqual(post['minexposure'], '0.8')
        self.assertEqual(post['maxexposure'], '1.1')
        fog = self.map.entities('env_gradient_fog')[0]['entity_properties']
        self.assertEqual(fog['fogstart'], '500')
        self.assertEqual(fog['fogend'], '15000')
        self.assertEqual(fog['fogcolor'], '128 172 212')
        self.assertEqual(fog['heightfog'], '1')
        cubemap = self.map.entities('env_cubemap_fog')[0]['entity_properties']
        self.assertEqual(cubemap['cubemapfogstartdistance'], '800')
        self.assertEqual(cubemap['cubemapfoglodbiase'], '0.2')
        self.assertEqual(cubemap['cubemapfogskyentity'], 'mcp_sky')

        probes = self.map.entities('env_combined_light_probe_volume')
        self.assertEqual(len(probes), 1)
        probe = probes[0]
        self.assertEqual(probe['entity_properties']['voxel_size'], '24.0')
        self.assertEqual(probe['entity_properties']['voxelize'], '1')
        self.assertEqual(result['probe_id'], probe['nodeID'])

    def test_inferno_is_idempotent_and_old_day_has_no_new_artifacts(self):
        lighting.apply_preset(self.project, self.map, 'inferno')
        lighting.apply_preset(self.project, self.map, 'inferno')
        self.assertEqual(len(self.map.entities('env_sky')), 2)
        self.assertEqual(len(self.map.entities('env_gradient_fog')), 1)
        self.assertEqual(len(self.map.entities('env_cubemap_fog')), 1)
        self.assertEqual(len(self.map.entities('env_combined_light_probe_volume')), 1)

        other = VMap.from_template(SETTINGS.cs2_path, 'empty')
        lighting.apply_preset(self.project, other, 'day')
        self.assertEqual(len(other.entities('env_combined_light_probe_volume')), 0)
        self.assertEqual(other.entities('env_sky')[0]['entity_properties']['skyname'],
                         'materials/skybox/sky_de_dust2.vmat')

    def test_cubemap_fog_links_the_visible_sky_with_an_existing_name(self):
        sky = self.map.new_entity('env_sky', {'targetname': 'custom_sky'},
                                  defaults=self.project.catalog.defaults('env_sky'))
        self.map.attach(sky)
        lighting.apply_preset(self.project, self.map, 'inferno')
        fog = self.map.entities('env_cubemap_fog')[0]['entity_properties']
        self.assertEqual(fog['cubemapfogskyentity'], 'custom_sky')
        # Moving the disabled lighting sky ahead of the visible one must not
        # make the next application enable or overwrite the light sky.
        light = self.map.entities('env_sky', targetname='mcp_light_sky')[0]
        self.map.remove(sky)
        self.map.attach(sky)
        lighting.apply_preset(self.project, self.map, 'inferno')
        self.assertEqual(light['entity_properties']['StartDisabled'], '1')
        self.assertEqual(light['entity_properties']['brightnessscale'], '2')

    def test_all_legacy_presets_keep_their_values_on_a_fresh_scene(self):
        # Literal baseline: changing PRESETS itself must also fail regression.
        baseline = {
            'day': ((50, 135, 0), '255 248 232', '1.0', '255 255 255', '1.0',
                    'materials/skybox/sky_de_dust2.vmat', 0.0),
            'sunset': ((18, 225, 0), '255 166 108', '0.72', '255 190 145', '0.50',
                       'materials/skybox/cs_italy_s2_skybox_2.vmat', -0.35),
            'night': ((8, 35, 0), '115 145 210', '0.22', '80 110 180', '0.22',
                      'materials/skybox/sky_rain_night_01.vmat', 0.15),
            'overcast': ((62, 20, 0), '205 220 235', '0.55', '180 200 220', '0.82',
                         'materials/skybox/sky_overcast_01.vmat', -0.15),
            'indoor': ((75, 0, 0), '185 200 220', '0.16', '40 50 70', '0.18',
                       'materials/skybox/sky_black.vmat', 0.0),
        }
        for preset, expected in baseline.items():
            with self.subTest(preset=preset):
                other = VMap.from_template(SETTINGS.cs2_path, 'empty')
                result = lighting.apply_preset(self.project, other, preset)
                sun = other.entities('light_environment')[0]
                self.assertEqual(tuple(sun['angles']), expected[0])
                for key, value in zip(('color', 'brightness', 'skycolor', 'skyintensity'), expected[1:5]):
                    self.assertEqual(sun['entity_properties'][key], value)
                self.assertEqual(sun['entity_properties']['directlight'], '3')
                sky = other.entities('env_sky')[0]['entity_properties']
                self.assertEqual(sky['brightnessscale'], expected[4])
                self.assertEqual(sky['skyname'], expected[5])
                self.assertEqual(result['exposure'], expected[6])
                self.assertFalse(other.entities('env_combined_light_probe_volume'))
                self.assertFalse(other.entities('env_cubemap_fog'))


if __name__ == '__main__':
    unittest.main()
