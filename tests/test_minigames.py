import unittest
from unittest.mock import patch

from cs2map import mechanics, minigames
from cs2map.project import OpError


class MiniGameSchema(unittest.TestCase):
    def points(self, m, spec):
        if 'wall' in spec:
            return ([0, 0, 0], None)
        at = spec.get('at', [0.5, 0.5]); return ([at[0] * 1000, at[1] * 1000, spec.get('z', 0)], None)

    def valid(self, kind='gallery', **extra):
        raw = {'op': 'add_minigame', 'name': 'test_game', 'kind': kind,
               'start': {'room': 'gallery', 'at': [.1, .2], 'z': 40},
               'targets' if kind != 'collect' else 'items': [
                   {'room': 'gallery', 'at': [.8, .2], 'z': 0},
                   {'room': 'gallery', 'at': [.8, .5], 'z': 0}], **extra}
        with patch.object(minigames.layout, 'point', side_effect=self.points):
            return minigames.validate(raw, object())

    def test_three_kinds_resolve_room_points_and_defaults(self):
        gallery = self.valid()
        race = self.valid('race', vehicle='drone1', radius=48)
        collect = self.valid('collect')
        self.assertEqual(gallery['popup'], [1.0, 2.5])
        self.assertEqual(race['vehicle'], 'drone1')
        self.assertEqual(collect['items'][0]['at'], [.8, .2])
        self.assertEqual(gallery['points'][0], [800.0, 200.0, 0])

    def test_schema_rejects_wrong_fields_and_bounds(self):
        with self.assertRaises(OpError): self.valid('collect', popup=[1, 2])
        with self.assertRaises(OpError): self.valid('gallery', vehicle='car1')
        with self.assertRaises(OpError): self.valid('gallery', popup=[3, 1])
        with self.assertRaises(OpError): self.valid('race', radius=3)
        with self.assertRaises(OpError): self.valid('gallery', unknown=1)
        with self.assertRaises(OpError): minigames.validate({'op':'add_minigame','name':'x','kind':'gallery','start':{},'targets':[]}, object())

    def test_generation_uses_shared_fragments_and_private_names(self):
        spec = self.valid()
        code, xml, css, entities, names = minigames.generate(spec)
        self.assertTrue(code.startswith(minigames.MARKER))
        self.assertIn('function Core(', code)
        self.assertIn('function Hud(', code)
        self.assertIn('function MiniGame(', code)
        self.assertIn('OnBulletImpact', code)
        self.assertIn('SHOOTING GALLERY', xml)
        self.assertIn('mcp_m_test_game_panel', xml)
        self.assertIn('#mcp_m_test_game_panel.Hidden', css)
        self.assertNotIn('HUDNAME', xml + css)
        self.assertIn('M_MINIGAME', code)
        self.assertTrue(all(e['keyvalues']['targetname'].startswith('m9_test_game') or e['keyvalues']['targetname'].startswith('mcp_m_') for e in entities))

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_generated_script_runs_on_engine_double(self):
        spec = self.valid()
        code, *_ = minigames.generate(spec)
        output = mechanics.run_harness(code, 'D.tick(); console.log("M_MINIGAME_OK");')
        self.assertIn('M_MINIGAME_OK', output)

    def runtime(self, kind, assertions, **extra):
        spec = self.valid(kind, **extra)
        code, *_ = minigames.generate(spec)
        fixture = '''
const A=D.assert, E=config.entities;
D.entities[E.start]=new D.Entity(E.start);
D.entities[E.hud]=new D.Hud(E.hud);
const names=config.kind==='collect'?E.items:E.targets;
const bodies=names.map((n,i)=>{const e=new D.Entity(n);e.pos={x:config.points[i][0],y:config.points[i][1],z:config.points[i][2]+(config.kind==='gallery'?10:0)};D.entities[n]=e;return e;});
D.T.traceLine=()=>({hitEntity:D.entities[E.start]});
D.H.OnActivate();
const state=()=>D.state(E.state);
const start=()=>{D.tick();D.tick(CSInputs.USE);A.equal(state().active,true);D.tick();};
'''
        output = mechanics.run_harness(code, fixture + assertions + '\nconsole.log("M_RUNTIME_OK");')
        self.assertIn('M_RUNTIME_OK', output)

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_gallery_native_damage_score_zones_and_same_shot_targets(self):
        self.runtime('gallery', '''
start();
const weapon=new D.Entity('gun');weapon.owner=D.pawn;
const centre=e=>({...e.pos,z:e.pos.z+32});
bodies[0].TakeDamage({damage:20});
D.H.OnBulletImpact({weapon,hitEntity:bodies[0],position:centre(bodies[0])});
D.H.OnBulletImpact({weapon,hitEntity:bodies[0],position:centre(bodies[0])});
D.tick();A.equal(state().score,10); // event + native polling do not double count
D.H.OnBulletImpact({weapon,hitEntity:bodies[1],position:{...centre(bodies[1]),x:bodies[1].pos.x+20}});
A.equal(state().score,15); // a penetrating shot can hit another shield
D.advance(2.2);bodies[0].TakeDamage({damage:7});D.tick();A.equal(state().score,25);
D.advance(31);A.equal(state().active,false);A.equal(state().record,25);
A.match(D.entities[E.hud].text,/DONE 25/);
start();D.pawn.alive=false;D.tick();A.equal(state().active,false);A.equal(state().record,25);
A.ok(D.fired.some(v=>v.includes('hinge_0:SetMotorTargetAngle')));
''', popup=[2,2])

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_race_order_penalty_and_vehicle_are_read_only(self):
        self.runtime('race', '''
const body=new D.Entity('d8_drone1_body');D.entities[body.name]=body;
body.pos={x:0,y:0,z:52};start();
body.pos={...bodies[1].pos,z:52};D.tick();A.equal(state().penalty,5);D.advance(.3);A.equal(state().penalty,5);A.equal(state().next,0);
body.pos={...bodies[0].pos,z:52};D.tick();A.equal(state().next,1);
body.pos={...bodies[1].pos,z:52};D.tick();A.equal(state().active,false);A.equal(state().next,2);A.ok(state().record>=5);
A.equal(body.teleports,undefined);A.equal(body.color.a,255);A.equal(state().vehicle,'d8_drone1_body');
''', vehicle='drone1', radius=60)

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_collect_finish_reset_and_rotation(self):
        self.runtime('collect', '''
start();D.advance(.3);A.ok(bodies[0].ang.yaw>0);
D.pawn.pos={...bodies[0].pos};D.tick();A.equal(state().score,1);A.equal(bodies[0].color.a,0);
D.tick();A.equal(state().score,1);
D.pawn.pos={...bodies[1].pos};D.tick();A.equal(state().active,false);A.equal(state().record,2);
A.match(D.entities[E.hud].text,/DONE 2/);
D.H.OnBeginRoundRestart();A.equal(bodies[0].color.a,255);A.equal(state().record,2);
''')

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_reset_clears_per_player_visibility_overrides(self):
        self.runtime('collect', '''
start();const panel=E.hud+'_panel', h=D.entities[E.hud];
A.equal(h.classes['0'+panel+'Hidden'],false);
D.H.OnBeginRoundRestart();
A.equal(h.classes['0'+panel+'Hidden'],undefined);
A.equal(h.classes['*'+panel+'Hidden'],true);
A.equal(state().active,false);
''')

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_second_player_sees_busy_without_stealing_run(self):
        self.runtime('gallery', '''
const other=new D.Pawn();
const c={IsValid:()=>true,IsBot:()=>false,IsConnected:()=>true,GetPlayerSlot:()=>1,GetPlayerPawn:()=>other};
other.GetPlayerController=()=>c;
Instance.GetAllPlayerControllers=()=>[D.controller,c];
start();other.keys=CSInputs.USE;D.tick();
A.equal(state().active,true);A.equal(D.count('start'),1);A.match(D.last('busy'),/slot.*1/);
A.match(D.entities[E.hud].texts['1'+E.hud+'_panel'+E.hud+'_status'],/BUSY/);
const weapon=new D.Entity('gun');weapon.owner=other;
D.H.OnBulletImpact({weapon,hitEntity:bodies[0],position:{...bodies[0].pos,z:bodies[0].pos.z+32}});
A.equal(state().score,0);
''')

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_missing_vehicle_and_timeout_do_not_create_record(self):
        self.runtime('race', '''
D.tick(CSInputs.USE);A.equal(state().active,false);A.match(D.last('refused'),/vehicle_missing/);
D.entities.drone1=new D.Entity('drone1');start();D.advance(2);A.equal(state().active,false);A.equal(state().record,0);
''', vehicle='drone1', time=1)

    @unittest.skipUnless(__import__('shutil').which('node'), 'Node unavailable')
    def test_round_recreates_entities_without_stale_health_or_hud(self):
        self.runtime('gallery', '''
start();const old=bodies[0];old.valid=false;
const replacement=new D.Entity(names[0]);replacement.pos={...old.pos};D.entities[names[0]]=replacement;
D.entities[E.hud]=new D.Hud(E.hud);D.H.OnRoundStart();start();
replacement.TakeDamage({damage:5});D.tick();D.advance(.15);A.equal(state().score,10);
A.equal(old.health,100000);A.match(D.entities[E.hud].text,/SCORE 10/);
''', popup=[2,2])


if __name__ == '__main__':
    unittest.main()
