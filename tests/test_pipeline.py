"""Packages applied by hash and the generator manifest (pipeline.py). Writes scratch maps in the addon."""
import json
import shutil
import unittest
from pathlib import Path

from tests.helpers import HAVE_CS2, ROOT, agent_suffix

MAP = 'test_pipeline' + agent_suffix()
WORK = ROOT / '.local' / 'tmp' / ('test_pipeline' + agent_suffix())


def box(x, name=None, size=64):
    op = {'op': 'add_mesh', 'shape': {'shape': 'box', 'mins': [x, 0, 0], 'maxs': [x + size, size, size]},
          'material': 'materials/dev/dev_measuregeneric01.vmat'}
    if name:
        op['ref'] = name
    return op


def write(name, data):
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / name
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class Plan(unittest.TestCase):
    """The pure part: which packages are replayed."""

    def pkgs(self, *specs):
        from cs2map.pipeline import Package
        return [Package(tag, digest, needs) for tag, digest, needs in specs]

    def state(self, order, **packages):
        return {'order': list(order), 'packages': packages}

    def entry(self, digest, needs=None, touches=(), nodes=1):
        return {'hash': digest, 'needs': needs, 'touches': list(touches), 'nodes': nodes}

    def test_nothing_changed(self):
        from cs2map.pipeline import plan
        st = self.state('ab', a=self.entry('1'), b=self.entry('2'))
        todo = plan(st, {'a': 1, 'b': 1}, self.pkgs(('a', '1', None), ('b', '2', None)))
        self.assertEqual(todo['replay'], [])
        self.assertEqual(todo['remove'], [])

    def test_undeclared_needs_replay_everything_after_the_change(self):
        from cs2map.pipeline import plan
        st = self.state('abc', a=self.entry('1'), b=self.entry('2'), c=self.entry('3'))
        todo = plan(st, dict.fromkeys('abc', 1), self.pkgs(('a', '1', None), ('b', 'X', None), ('c', '3', None)))
        self.assertEqual(todo['replay'], ['b', 'c'])
        self.assertEqual(todo['skip'], ['a'])

    def test_declared_needs_keep_independent_packages(self):
        from cs2map.pipeline import plan
        st = self.state('abcd', a=self.entry('1', []), b=self.entry('2', ['a']), c=self.entry('3', []),
                        d=self.entry('4', ['b']))
        todo = plan(st, dict.fromkeys('abcd', 1), self.pkgs(('a', 'X', []), ('b', '2', ['a']), ('c', '3', []), ('d', '4', ['b'])))
        self.assertEqual(todo['replay'], ['a', 'b', 'd'])
        self.assertEqual(todo['skip'], ['c'])

    def test_a_package_whose_nodes_were_used_is_replayed_too(self):
        from cs2map.pipeline import plan
        # b changed a's nodes last time (touches): replaying b alone would stack its change on the old one.
        st = self.state('ab', a=self.entry('1', []), b=self.entry('2', ['a'], touches=['a']))
        todo = plan(st, {'a': 1, 'b': 1}, self.pkgs(('a', '1', []), ('b', 'X', ['a'])))
        self.assertEqual(todo['replay'], ['a', 'b'])

    def test_removed_and_missing(self):
        from cs2map.pipeline import plan
        st = self.state('abc', a=self.entry('1', []), b=self.entry('2', []), c=self.entry('3', []))
        todo = plan(st, {'a': 1, 'c': 1}, self.pkgs(('a', '1', []), ('b', '2', [])))
        self.assertEqual(todo['remove'], ['c'])
        self.assertEqual(todo['replay'], ['b'])
        self.assertIn('missing', todo['why']['b'])

    def test_a_tag_made_by_plain_replace_batch_is_not_removed(self):
        from cs2map.pipeline import plan
        st = self.state('ax', a=self.entry('1', []), x=self.entry(None))
        todo = plan(st, {'a': 1, 'x': 1}, self.pkgs(('a', '1', []),))
        self.assertEqual(todo['remove'], [])

    def test_a_game_update_replays_the_packages_applied_before_it(self):
        from cs2map.pipeline import plan
        # 12.11: a CS2 update added a keyvalue to info_map_parameters; the map then differed from a clean replay.
        old, new, unknown = self.entry('1', []), self.entry('2', []), self.entry('3', [])
        old['game'], new['game'] = 'before', 'now'
        st = self.state('abc', a=old, b=new, c=unknown)
        pkgs = self.pkgs(('a', '1', []), ('b', '2', []), ('c', '3', []))
        todo = plan(st, dict.fromkeys('abc', 1), pkgs, game='now')
        self.assertEqual(todo['replay'], ['a'])
        self.assertIn('game was updated', todo['why']['a'])
        self.assertEqual(plan(st, dict.fromkeys('abc', 1), pkgs)['replay'], [])     # the game is not known: no claim


class Files(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_three_forms_of_a_package_file(self):
        from cs2map.pipeline import read_package
        a = read_package(write('args_yard.json', {'name': 'm', 'operations': [box(0)]}))
        b = read_package(write('p2.json', [{'op': 'replace_batch', 'tag': 'yard'}, box(0)]))
        c = read_package(write('p3.json', {'package': 'yard', 'needs': [], 'operations': [box(0)]}))
        self.assertEqual((a.tag, b.tag, c.tag), ('yard', 'yard', 'yard'))
        self.assertEqual(a.hash, b.hash)
        self.assertNotEqual(a.hash, c.hash, 'declared needs are part of the hash')
        self.assertEqual(len(b.operations), 1)

    def test_the_hash_follows_the_content_not_the_formatting(self):
        from cs2map.pipeline import read_package
        one = read_package(write('args_a.json', {'operations': [box(0)]}))
        (WORK / 'args_a.json').write_text(json.dumps({'operations': [box(0)]}, indent=3), encoding='utf-8')
        self.assertEqual(read_package(WORK / 'args_a.json').hash, one.hash)
        self.assertNotEqual(read_package(write('args_a.json', {'operations': [box(1)]})).hash, one.hash)

    def test_bad_files_are_named(self):
        from cs2map.pipeline import PipelineError, read_package, read_packages
        with self.assertRaises(PipelineError) as ctx:
            read_package(write('args_bad.json', {'operations': 5}))
        self.assertIn('args_bad.json', str(ctx.exception))
        (WORK / 'broken.json').write_text('{', encoding='utf-8')
        with self.assertRaises(PipelineError):
            read_package(WORK / 'broken.json')
        with self.assertRaises(PipelineError) as ctx:
            read_packages([str(write('args_a.json', [box(0)])), str(write('a.json', [box(0)]))])
        self.assertIn('two packages', str(ctx.exception))
        with self.assertRaises(PipelineError) as ctx:
            read_packages([str(write('args_c.json', {'needs': ['nope'], 'operations': [box(0)]}))])
        self.assertIn('nope', str(ctx.exception))
        with self.assertRaises(PipelineError):
            read_packages(['C:/Windows/win.ini'])


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class Apply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map.project import Project
        cls.p = Project()

    def setUp(self):
        self.p.map_create(MAP, 'empty', overwrite=True)

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def files(self, **packages):
        return [str(write(f'args_{tag}.json', data)) for tag, data in packages.items()]

    def apply(self, files, **kw):
        from cs2map import pipeline
        return pipeline.apply(self.p, MAP, pipeline.read_packages(files), **kw)

    def meshes(self):
        return self.p.map_info(MAP, kind='mesh')['total_matching']

    def test_only_changed_packages_are_replayed_and_the_result_is_a_clean_replay(self):
        from cs2map import pipeline
        files = self.files(a=[box(0), box(100)], b=[box(200)], c=[box(300), box(400), box(500)])
        first = self.apply(files)
        self.assertEqual(first['replayed'], ['a', 'b', 'c'])
        self.assertEqual(self.meshes(), 6)
        again = self.apply(files)
        self.assertEqual(again['replayed'], [])
        self.assertIn('up to date', again['note'])
        files = self.files(a=[box(0), box(100)], b=[box(200), box(250)], c=[box(300), box(400), box(500)])
        third = self.apply(files)
        self.assertEqual(third['replayed'], ['b', 'c'], 'needs are not declared: everything after b goes again')
        self.assertEqual(self.meshes(), 7)
        check = pipeline.verify(self.p, MAP, pipeline.read_packages(files), 'empty')
        self.assertTrue(check['ok'], check)

    def test_declared_needs_replay_less(self):
        from cs2map import pipeline
        files = self.files(a={'needs': [], 'operations': [box(0)]}, b={'needs': [], 'operations': [box(200)]},
                           c={'needs': ['a'], 'operations': [box(300)]})
        self.apply(files)
        files = self.files(a={'needs': [], 'operations': [box(0)]}, b={'needs': [], 'operations': [box(210)]},
                           c={'needs': ['a'], 'operations': [box(300)]})
        self.assertEqual(self.apply(files)['replayed'], ['b'])
        files = self.files(a={'needs': [], 'operations': [box(5)]}, b={'needs': [], 'operations': [box(210)]},
                           c={'needs': ['a'], 'operations': [box(300)]})
        self.assertEqual(self.apply(files)['replayed'], ['a', 'c'])
        self.assertTrue(pipeline.verify(self.p, MAP, pipeline.read_packages(files), 'empty')['ok'])

    def test_a_package_that_changes_another_ones_node(self):
        from cs2map import pipeline
        from cs2map.project import OpError
        target = {'op': 'add_entity', 'classname': 'info_target', 'origin': [0, 0, 0],
                  'keyvalues': {'targetname': 'pipe_probe'}, 'ref': 'probe'}
        move = {'op': 'move', 'id': '@probe', 'offset': [10, 0, 0]}
        with self.assertRaises(OpError) as ctx:
            self.apply(self.files(a={'needs': [], 'operations': [target]}, b={'needs': [], 'operations': [move]}))
        self.assertIn('needs', str(ctx.exception))
        files = self.files(a={'needs': [], 'operations': [target]}, b={'needs': ['a'], 'operations': [move]})
        self.apply(files)
        move['offset'] = [0, 20, 0]
        files = self.files(a={'needs': [], 'operations': [target]}, b={'needs': ['a'], 'operations': [move]})
        out = self.apply(files)
        self.assertEqual(out['replayed'], ['a', 'b'], 'b moved a node of a: a is rebuilt, the move is not stacked')
        node = self.p.map_info(MAP, classname='info_target')['nodes'][0]
        self.assertEqual(node['origin'], [0.0, 20.0, 0.0])
        self.assertTrue(pipeline.verify(self.p, MAP, pipeline.read_packages(files), 'empty')['ok'])

    def test_a_node_found_by_its_name_counts_as_used(self):
        target = {'op': 'add_entity', 'classname': 'info_target', 'origin': [0, 0, 0],
                  'keyvalues': {'targetname': 'pipe_named'}}
        update = {'op': 'update', 'id': 'pipe_named', 'keyvalues': {'spawnflags': 1}}
        files = self.files(a=[target], b=[update])
        self.apply(files)
        state = self.p.packages_state(self.p.load(MAP))
        self.assertEqual(state['packages']['b']['touches'], ['a'])

    def test_removed_package_and_bad_package(self):
        from cs2map.project import OpError
        from cs2map.pipeline import PipelineError
        self.apply(self.files(a=[box(0)], b=[box(200)]))
        with self.assertRaises(PipelineError) as ctx:
            self.apply(self.files(a=[box(0)]))               # a forgotten file must not delete a zone
        self.assertIn('leaves out', str(ctx.exception))
        self.assertEqual(self.meshes(), 2)
        out = self.apply(self.files(a=[box(0)]), complete=True)       # a manifest is the whole map
        self.assertEqual(out['removed'], ['b'])
        self.assertEqual(self.meshes(), 1)
        with self.assertRaises(OpError) as ctx:
            self.apply(self.files(a=[box(0)], bad=[box(300), {'op': 'add_entity', 'classname': 'no_such_class'}]))
        self.assertIn('package bad #1', str(ctx.exception))
        self.assertEqual(self.meshes(), 1, 'nothing was saved')
        self.assertNotIn('bad', self.p.packages_state(self.p.load(MAP))['packages'])

    def test_dry_run_writes_nothing(self):
        out = self.apply(self.files(a=[box(0)]), dry_run=True)
        self.assertTrue(out['dry_run'])
        self.assertEqual(out['would_replay'], ['a'])
        self.assertTrue(out['ok'])
        self.assertEqual(self.meshes(), 0)
        bad = self.apply(self.files(a=[box(0), {'op': 'add_entity', 'classname': 'no_such_class'}]), dry_run=True)
        self.assertFalse(bad['ok'])
        self.assertIn('a #1', bad['failed'][0])

    def test_release_waits_for_a_clean_replay(self):
        from cs2map import pipeline
        from cs2map.vmap import MapError
        files = self.files(a={'needs': [], 'operations': [box(0)]}, b={'needs': [], 'operations': [box(200)]})
        self.apply(files)
        self.assertIsNone(pipeline.release_gate(self.p.s, MAP), 'replayed whole: clean')
        files = self.files(a={'needs': [], 'operations': [box(0)]}, b={'needs': [], 'operations': [box(210)]})
        self.assertEqual(self.apply(files)['replayed'], ['b'])
        self.assertIn('dry_run=true', pipeline.release_gate(self.p.s, MAP))
        with self.assertRaises(MapError):
            self.p.map_build(MAP, 'release')
        ids = [n['id'] for n in self.p.map_info(MAP, kind='mesh')['nodes']]
        self.p.map_edit(MAP, [{'op': 'move', 'id': ids[0], 'offset': [0, 0, 8]}])      # a hand edit of a package
        check = self.apply(files, dry_run=True)
        self.assertFalse(check['ok'])
        self.assertEqual(check['differs_from_clean_replay'], ['a'])
        self.assertIsNotNone(pipeline.release_gate(self.p.s, MAP))
        self.p.map_edit(MAP, [{'op': 'move', 'id': ids[0], 'offset': [0, 0, -8]}])
        check = self.apply(files, dry_run=True)
        self.assertTrue(check['ok'], check)
        self.assertIsNone(pipeline.release_gate(self.p.s, MAP), 'proved equal to a clean replay')

    def test_hand_edits_of_a_package_are_noticed(self):
        files = self.files(a={'needs': [], 'operations': [box(0), box(100)]},
                           b={'needs': [], 'operations': [box(200)]})
        self.apply(files)
        ids = [n['id'] for n in self.p.map_info(MAP, kind='mesh')['nodes']]
        self.p.map_edit(MAP, [{'op': 'delete', 'ids': ids[:1]}])          # one of a's two nodes
        out = self.apply(files)
        self.assertEqual(out['replayed'], ['a'])
        self.assertIn('outside the packages', out['why']['a'])
        self.assertEqual(self.meshes(), 3)

    def test_a_map_built_without_packages_is_not_doubled(self):
        """12.11: a 24 500-node map made earlier from the same files by plain map_edit calls has no package record."""
        from cs2map import pipeline
        from cs2map.pipeline import PipelineError
        self.p.map_edit(MAP, [box(100 * i) for i in range(pipeline.LOOSE_NODES + 2)])
        files = self.files(a=[box(0)])
        dry = self.apply(files, dry_run=True)
        self.assertIn('doubled', dry['warning'])
        with self.assertRaises(PipelineError) as ctx:
            self.apply(files)
        self.assertIn('map_create', str(ctx.exception))
        self.assertEqual(self.meshes(), pipeline.LOOSE_NODES + 2, 'nothing was added')
        self.p.map_edit(MAP, [{'op': 'replace_batch', 'tag': 'base'}])        # those nodes stay on purpose
        self.assertEqual(self.apply(files)['replayed'], ['a'])
        self.assertEqual(self.meshes(), pipeline.LOOSE_NODES + 3)
        self.assertEqual(self.apply(files)['replayed'], [], 'the mark is not a package to remove or replay')

    def test_plain_map_edit_with_one_marker_still_works(self):
        r = self.p.map_edit(MAP, [{'op': 'replace_batch', 'tag': 'market'}, box(0), box(100)])
        self.assertEqual(r['results'][0]['removed'], 0)
        self.assertEqual(r['results'][0]['tagged'], 2)
        r = self.p.map_edit(MAP, [{'op': 'replace_batch', 'tag': 'market'}, box(0)])
        self.assertEqual(r['results'][0]['removed'], 2)
        self.assertEqual(self.meshes(), 1)


@unittest.skipUnless(HAVE_CS2, 'CS2 install not found')
class Manifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cs2map.project import Project
        cls.p = Project()

    def setUp(self):
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True)
        self.p.map_create(MAP, 'empty', overwrite=True)
        (WORK / 'sizes.json').write_text('{"count": 2}', encoding='utf-8')
        (WORK / 'gen.py').write_text(
            'import json, sys\n'
            'count = json.load(open("sizes.json"))["count"]\n'
            'ops = [{"op": "add_mesh", "shape": {"shape": "box", "mins": [i * 100, 0, 0], "maxs": [i * 100 + 64, 64, 64]},\n'
            '        "material": "materials/dev/dev_measuregeneric01.vmat"} for i in range(count)]\n'
            'json.dump({"operations": ops}, open(f"args_{sys.argv[1]}.json", "w"))\n', encoding='utf-8')
        write('pipeline.json', {'map': MAP, 'create': {'template': 'empty'}, 'steps': [
            {'run': 'gen.py', 'args': ['rows'], 'packages': ['args_rows.json']},
            {'run': 'gen.py', 'args': ['more'], 'packages': ['args_more.json']}]})

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def run_manifest(self):
        from cs2map import pipeline
        return pipeline.run(self.p, MAP, WORK / 'pipeline.json', say=lambda text: None)

    def test_generators_run_only_when_stale(self):
        first = self.run_manifest()
        self.assertEqual(len(first['generators_run']), 2)
        self.assertEqual(first['replayed'], ['rows', 'more'])
        again = self.run_manifest()
        self.assertEqual(again['generators_run'], [])
        self.assertEqual(again['replayed'], [])
        (WORK / 'sizes.json').write_text('{"count": 3}', encoding='utf-8')    # a file the generators read
        third = self.run_manifest()
        self.assertEqual(len(third['generators_run']), 2)
        self.assertIn('sizes.json', third['generators_run'][0]['why'])
        self.assertEqual(self.p.map_info(MAP, kind='mesh')['total_matching'], 6)

    def test_a_failing_generator_is_loud(self):
        from cs2map.pipeline import PipelineError
        (WORK / 'gen.py').write_text('raise SystemExit("no terrain file")\n', encoding='utf-8')
        with self.assertRaises(PipelineError) as ctx:
            self.run_manifest()
        self.assertIn('no terrain file', str(ctx.exception))
        self.assertIn('gen.py', str(ctx.exception))

    def test_a_step_that_reads_a_later_steps_file_is_run_again(self):
        """12.11: kerb and fence generators of a town map read the package a later generator writes."""
        box_ops = ('[{"op": "add_mesh", "shape": {"shape": "box", "mins": [i * 100, %d, 0], "maxs": [i * 100 + 64, %d, 64]},'
                   ' "material": "materials/dev/dev_measuregeneric01.vmat"} for i in range(n)]')
        (WORK / 'early.py').write_text(
            'import json\n'
            'try:\n    n = len(json.load(open("args_late.json"))["operations"])\nexcept OSError:\n    n = 0\n'
            f'json.dump({{"operations": {box_ops % (200, 264)}}}, open("args_early.json", "w"))\n', encoding='utf-8')
        (WORK / 'late.py').write_text(
            'import json\nn = json.load(open("sizes.json"))["count"]\n'
            f'json.dump({{"operations": {box_ops % (0, 64)}}}, open("args_late.json", "w"))\n', encoding='utf-8')
        write('pipeline.json', {'map': MAP, 'steps': [
            {'run': 'early.py', 'packages': ['args_early.json'], 'needs': []},
            {'run': 'late.py', 'packages': ['args_late.json'], 'needs': []}]})
        first = self.run_manifest()
        self.assertEqual([(g['run'], g.get('pass', 1)) for g in first['generators_run']],
                         [('early.py', 1), ('late.py', 1), ('early.py', 2)])
        self.assertNotIn('unsettled', first)
        self.assertEqual(self.p.map_info(MAP, kind='mesh')['total_matching'], 4, 'early saw what late wrote')
        self.assertEqual(self.run_manifest()['generators_run'], [])
        # two generators that change each other's input for ever are named, not looped over
        (WORK / 'early.py').write_text(
            'import json, os\n'
            'try:\n    n = json.load(open("ping.json"))["n"]\nexcept OSError:\n    n = 0\n'
            'json.dump({"n": n + 1}, open("pong.json", "w"))\n'
            f'n = 1\njson.dump({{"operations": {box_ops % (200, 264)}}}, open("args_early.json", "w"))\n', encoding='utf-8')
        (WORK / 'late.py').write_text(
            'import json\nn = json.load(open("pong.json"))["n"]\njson.dump({"n": n + 1}, open("ping.json", "w"))\n'
            f'n = 1\njson.dump({{"operations": {box_ops % (0, 64)}}}, open("args_late.json", "w"))\n', encoding='utf-8')
        out = self.run_manifest()
        self.assertEqual(out['unsettled'], ['early.py'])
        self.assertIn('stale again', out['warning'])
        self.assertEqual(len(out['generators_run']), 6)

    def test_a_generator_sees_sets_in_the_same_order_in_every_run(self):
        """12.11: a generator looped over set(materials); its package had another order of operations each run."""
        from cs2map import pipeline
        (WORK / 'order.py').write_text(
            'import json, os\n'
            'names = set("wall_%d" % i for i in range(40))\n'
            'json.dump({"order": list(names), "env": sorted(k for k in os.environ if k.startswith("PYTHON"))},\n'
            '          open("order.json", "w"))\n', encoding='utf-8')
        seen = []
        for _ in range(3):
            pipeline.run_generator(WORK, {'run': 'order.py'}, lambda text: None)
            seen.append(json.loads((WORK / 'order.json').read_text(encoding='utf-8')))
        self.assertEqual(seen[0]['order'], seen[1]['order'])
        self.assertEqual(seen[1]['order'], seen[2]['order'])
        self.assertEqual(seen[0]['env'], ['PYTHONHASHSEED', 'PYTHONUTF8'])     # nothing of the user's Python setup

    def test_tool_steps_call_the_published_tools_once(self):
        """12.11: the first manifest with tool steps (script_write before its package) failed on a missing table."""
        from unittest import mock
        from cs2map import surface
        from cs2map.pipeline import PipelineError
        calls = []
        (WORK / 'probe.json').write_text('{"name": "a", "code": "x"}', encoding='utf-8')
        steps = [{'tool': 'probe', 'args': 'probe.json'}, {'tool': 'probe', 'args': {'name': 'b', 'code': 'y'}},
                 {'run': 'gen.py', 'args': ['rows'], 'packages': ['args_rows.json']}]
        write('pipeline.json', {'map': MAP, 'steps': steps})
        with mock.patch.dict(surface.PUBLIC, {'probe': lambda **kw: calls.append(kw) or {'ok': True}}):
            first = self.run_manifest()
            self.assertEqual([c['name'] for c in calls], ['a', 'b'])
            self.assertEqual(len(first['tools_called']), 2)
            self.assertNotIn('tools_called', self.run_manifest())           # same arguments: not called again
            (WORK / 'probe.json').write_text('{"name": "a", "code": "z"}', encoding='utf-8')
            self.assertEqual(len(self.run_manifest()['tools_called']), 1)
            self.assertEqual(calls[-1], {'name': 'a', 'code': 'z'})
        with mock.patch.dict(surface.PUBLIC, {'probe': lambda **kw: {'ok': False, 'error': 'no compiler'}}):
            (WORK / 'probe.json').write_text('{"name": "a", "code": "w"}', encoding='utf-8')
            with self.assertRaises(PipelineError) as ctx:
                self.run_manifest()
            self.assertIn('no compiler', str(ctx.exception))
        for name in ('map_edit', 'no_such_tool'):
            write('pipeline.json', {'map': MAP, 'steps': [{'tool': name, 'args': {}}] + steps[2:]})
            with self.assertRaises(PipelineError) as ctx:
                self.run_manifest()
            self.assertIn('cannot be a pipeline step', str(ctx.exception))

    def test_bad_manifests(self):
        from cs2map.pipeline import PipelineError, read_manifest
        for bad in ({}, {'steps': []}, {'steps': [{'nothing': 1}]}, {'steps': [{'run': 'a.py', 'surprise': 1}]}):
            with self.assertRaises(PipelineError):
                read_manifest(write('bad.json', bad))


if __name__ == '__main__':
    unittest.main()
