"""Unit and Blender smoke tests for the environment-model pipeline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT
from tests.helpers import HAVE_BLENDER


class Models(unittest.TestCase):
    def test_round2_templates_and_compact_result(self):
        from cs2map.config import Settings
        from cs2map.models import TEMPLATES, _result
        from cs2map.project import Project

        self.assertTrue({"sandbags", "jersey_barrier", "pallet", "railing",
                         "dumpster", "electrical_box"}.issubset(TEMPLATES))
        with tempfile.TemporaryDirectory() as td:
            p = Project(Settings(Path(td), "model_test"))
            p.s.content_addon.mkdir(parents=True)
            (p.s.content_addon / "models/mcp/compact").mkdir(parents=True)
            result = _result(p, "compact", {"bounds": [[0, 0, 0], [1, 1, 1]],
                                             "tris": 12,
                                             "remaps": [{"to": "materials/x.vmat"}]},
                             "hull", "default", p.s.content_addon / "models/mcp/compact/compact.vmdl",
                             {"ok": True, "log": "logs/compile.log", "output": "x_c"})
            self.assertTrue(result["ok"])
            self.assertEqual(result["materials"], ["materials/x.vmat"])
            self.assertEqual(set(result), {"ok", "model", "bounds", "tris", "materials",
                                           "collision", "compile", "preview", "warnings"})

    def test_vmdl_contains_render_material_and_physics(self):
        from cs2map import kv3
        from cs2map.config import Settings
        from cs2map.models import _make_vmdl
        from cs2map.project import Project

        with tempfile.TemporaryDirectory() as td:
            p = Project(Settings(Path(td), "model_test"))
            p.s.content_addon.mkdir(parents=True)
            vmdl, vmats, remaps = _make_vmdl(
                p, "unit_box", {"materials": [{"name": "crate", "image": ""}]}, "hull", "default")
            header, doc = kv3.loads(vmdl.read_text(encoding="utf-8"))
            self.assertEqual(header, kv3.HEADER_MODELDOC)
            classes = [c["_class"] for c in doc["rootNode"]["children"]]
            self.assertEqual(classes, ["RenderMeshList", "MaterialGroupList", "PhysicsShapeList"])
            self.assertEqual(remaps[0]["from"], "crate.vmat")
            self.assertTrue(vmats[0].exists())

    @unittest.skipUnless(HAVE_BLENDER, "Blender is not installed")
    def test_run_blender_json(self):
        from cs2map.models import run_blender
        from cs2map.project import Project

        p = Project()
        job = run_blender(p, 'print("JSON>{\\"models_smoke\\": true}")', timeout=60)
        state = p.jobs.wait(job.id, 60)
        self.assertEqual(state.state, "succeeded")
        self.assertTrue(state.result["models_smoke"])

    @unittest.skipUnless(HAVE_BLENDER, "Blender is not installed")
    def test_template_geometry_uv_and_overrides_in_blender(self):
        from cs2map.models import run_blender
        from cs2map.project import Project

        script = '''
import sys, math, json, importlib
import bpy
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src/cs2map/blender"))
from common import box, cylinder, finish, project_uv, STOCK_MATERIALS
from driver import material_records
names = ("crate", "barrel", "fence", "door", "window_frame", "pipe", "lamp",
         "table", "shelf", "sandbags", "jersey_barrier", "pallet", "railing",
         "dumpster", "electrical_box")
for name in names:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = importlib.import_module("templates."+name)
    objects = mod.build({})
    bpy.context.view_layer.update()
    lo = min((obj.matrix_world @ v.co).z for obj in objects for v in obj.data.vertices)
    assert abs(lo) < .001, (name, lo)
    assert all(obj.data.uv_layers for obj in objects), name
    assert all(obj.data.materials[0].get("source_vmat") in STOCK_MATERIALS.values() for obj in objects), name
    cols = [obj for obj in bpy.context.scene.objects if obj.name.endswith("_col")]
    assert cols, name
    if name == "jersey_barrier":
        mesh = objects[0].data
        mesh.calc_loop_triangles()
        volume = sum(mesh.vertices[t.vertices[0]].co.dot(
            mesh.vertices[t.vertices[1]].co.cross(mesh.vertices[t.vertices[2]].co))/6
            for t in mesh.loop_triangles)
        assert volume > 0, (name, volume)
    for override in ({"material":"materials/wood/plywood02.vmat"}, {"color":"#225588"}):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        objects = mod.build(override)
        targets = [obj.data.materials[0].get("source_vmat") for obj in objects]
        assert all(t == override.get("material") for t in targets), (name, targets)
bpy.ops.wm.read_factory_settings(use_empty=True)
obj = box("uv_a", (128,64,32), (0,0,16), bevel=0)
project_uv(obj,128)
poly = max(obj.data.polygons, key=lambda p:p.normal.z)
uv = [obj.data.uv_layers.active.data[i].uv for i in poly.loop_indices]
assert abs(max(p.x for p in uv)-min(p.x for p in uv)-1) < .001
assert abs(max(p.y for p in uv)-min(p.y for p in uv)-.5) < .001
project_uv(obj,256)
uv = [obj.data.uv_layers.active.data[i].uv for i in poly.loop_indices]
assert abs(max(p.x for p in uv)-min(p.x for p in uv)-.5) < .001
project_uv(obj,128,"face")
for poly in obj.data.polygons:
    if len(poly.vertices) == 4 and max(abs(n) for n in poly.normal) > .999:
        uv = [obj.data.uv_layers.active.data[i].uv for i in poly.loop_indices]
        assert abs(max(p.x for p in uv)-min(p.x for p in uv)-1) < .001
        assert abs(max(p.y for p in uv)-min(p.y for p in uv)-1) < .001
long = "models/generic/barrier_concrete_kit_01/barrier_concrete_01_bare.vmat"
from common import material, apply_material
apply_material(obj, material(long))
records = material_records([obj], Path.cwd() / ".local/scratch/A")
assert records[0]["material"] == long
# A real-world tile override must not change an object texture's single wrap.
obj = cylinder("object_uv",16,64,uv_mode="face")
before = [tuple(p.uv) for p in obj.data.uv_layers.active.data]
finish([obj], {"uv_tile_size":256})
assert before == [tuple(p.uv) for p in obj.data.uv_layers.active.data]
obj = cylinder("tile_uv",16,64)
before = [tuple(p.uv) for p in obj.data.uv_layers.active.data]
finish([obj], {"uv_tile_size":256})
for old, new in zip(before, obj.data.uv_layers.active.data):
    assert all(abs(old[i]/2-new.uv[i]) < .001 for i in (0,1))
print("JSON>"+json.dumps({"checked_templates":len(names),"uv_scale":True,"normals":True,"overrides":True}))
'''
        p = Project()
        job = run_blender(p, script, timeout=90)
        st = p.jobs.wait(job.id, 60)
        self.assertEqual(st.state, "succeeded", st.log)
        self.assertEqual(st.result.get("checked_templates"), 15)

    @unittest.skipUnless(HAVE_BLENDER, "Blender is not installed")
    def test_template_build_without_resourcecompiler(self):
        from cs2map.config import Settings
        from cs2map.models import model_build
        from cs2map.project import Project

        with tempfile.TemporaryDirectory() as td:
            p = Project(Settings(Path(td), "model_test"))
            # The isolated temp Project has no CS2 VPK, so exercise the
            # caller override here; stock remaps are covered by the real MCP
            # smoke on the configured addon.
            result = model_build(p, "unit_crate", template="crate",
                                 params={"height": 32, "material": "unit_custom"})
            self.assertEqual(result["model"], "models/mcp/unit_crate/unit_crate.vmdl")
            self.assertGreater(result["tris"], 0)
            self.assertTrue(Path(result["preview"]).exists())
            self.assertTrue(result["compile"]["skipped"])
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
