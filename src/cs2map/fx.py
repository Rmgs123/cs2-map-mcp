"""Installed stock particles and FGD precipitation; no guessed asset paths."""
from __future__ import annotations

from . import checks, lighting


def prepare_weather(project):
    """Create map-independent point weather, derived from installed rain/snow operators."""
    import uuid
    from . import kv3, resource, sound
    results = []
    for effect in ('rain', 'snow'):
        stock = project.game.read(EFFECTS[effect] + '_c')
        offset, size = resource.blocks(stock)['DATA']
        if stock[offset:offset+4] != b'\x03\x33\x56\x4b':
            raise ValueError('installed weather is not the checked KV3 v3 particle format')
        guid = uuid.UUID(bytes_le=stock[offset+4:offset+20])
        header = kv3.HEADER_GENERIC.replace('generic:version{7412167c-06e9-4698-aff2-e63eb59037e7}', f'vpcf54:version{{{guid}}}')
        def literal(x): return {'m_nType':'PF_TYPE_LITERAL','m_flLiteralValue':float(x)}
        renderer = {'_class':'C_OP_RenderTrails' if effect == 'rain' else 'C_OP_RenderSprites',
                    'm_vecTexturesInput':[{'m_hTexture':kv3.Flagged('resource',
                        'materials/particle/rain_streak.vtex' if effect == 'rain' else 'materials/particle/smoke1/smoke1.vtex')}],
                    'm_nOutputBlendMode':'PARTICLE_OUTPUT_BLEND_MODE_ADD'}
        if effect == 'rain': renderer.update(m_flMinLength=8.,m_flMaxLength=36.,m_flRadiusScale=literal(.8))
        tree = {'_class':'CParticleSystemDefinition','m_nMaxParticles':2048,
                'm_BoundingBoxMin':[-176.,-176.,-700.], 'm_BoundingBoxMax':[176.,176.,32.],
                'm_ConstantColor':[200,220,255,255], 'm_bShouldSort':False,
                'm_Renderers':[renderer],
                'm_Operators':[{'_class':'C_OP_Decay'},{'_class':'C_OP_BasicMovement'},
                               {'_class':'C_OP_FadeOut','m_flFadeOutTimeMin':.1,'m_flFadeOutTimeMax':.1}],
                'm_Initializers':[{'_class':'C_INIT_InitFloat','m_InputValue':literal(.8),'m_nOutputField':7},
                    {'_class':'C_INIT_InitFloat','m_InputValue':literal(.7 if effect=='rain' else 1.8)},
                    {'_class':'C_INIT_InitFloat','m_InputValue':literal(.8 if effect=='rain' else 6.),'m_nOutputField':1},
                    {'_class':'C_INIT_CreateWithinSphereTransform','m_fRadiusMax':160., 'm_vecDistanceBias':[1.,1.,0.],
                     'm_LocalCoordinateSystemSpeedMin':[0.,0.,-800. if effect=='rain' else -90.],
                     'm_LocalCoordinateSystemSpeedMax':[0.,0.,-800. if effect=='rain' else -90.]}],
                'm_Emitters':[{'_class':'C_OP_ContinuousEmitter','m_flEmitRate':literal(1400 if effect=='rain' else 220)}],
                'm_nBehaviorVersion':5}
        path = project.s.content_addon / f'particles/mcp/m_{effect}.vpcf'
        path.parent.mkdir(parents=True,exist_ok=True)
        backup = sound._backup(project,path)
        path.write_text(kv3.dumps(tree,header),encoding='utf-8')
        try: result = sound._compile(project,path)
        except Exception:
            if backup:
                import shutil
                shutil.copy2(backup,path)
                sound._compile(project,path)
            raise
        results.append({'effect':effect,'particle':f'particles/mcp/m_{effect}.vpcf',**result})
    return {'ok':all(r['ok'] for r in results),'weather':results}

EFFECTS = {
    'fire_small':'particles/burning_fx/env_fire_small.vpcf',
    'fire_large':'particles/burning_fx/env_fire_large.vpcf',
    'smoke':'particles/ambient_fx/ambient_smokestack.vpcf',
    'steam':'particles/maps/de_train/train_steam_leak.vpcf',
    'sparks':'particles/ambient_fx/env_sparks_omni.vpcf',
    'dust':'particles/ambient_fx/dustmotes_64.vpcf',
    'rain':'particles/rain_fx/rain.vpcf',
    'snow':'particles/rain_fx/snow.vpcf',
    'ash':'particles/rain_fx/ash.vpcf',
}


def search(project, query='', limit=30):
    if not isinstance(query,str) or not 1 <= limit <= 100:
        raise ValueError('query must be text; limit must be between 1 and 100')
    aliases = {k:v for k,v in EFFECTS.items() if query.casefold() in k or query.casefold() in v}
    return {'aliases':aliases, **project.asset_search(query,'particle',limit)}


def add_fx(project,m,op,refs):
    effect=op.get('effect')
    if effect == 'lightning': return add_lightning(project,m,op,refs)
    if not isinstance(effect,str):
        raise ValueError('effect must be an alias or an installed .vpcf path')
    path=EFFECTS.get(effect,effect).replace('\\','/')
    if not path.startswith('particles/') or not path.endswith('.vpcf') or '..' in path.split('/') or not checks._asset_exists(project,path):
        raise ValueError(f'particle {path!r} does not exist; use fx_search')
    mins,maxs=op.get('mins'),op.get('maxs')
    area=mins is not None or maxs is not None
    if area and op.get('origin') is None:
        # rain/snow fill a box; the origin is only needed for point effects
        lo,hi=lighting._vector(mins,'mins'),lighting._vector(maxs,'maxs')
        origin=[(lo[i]+hi[i])/2 for i in range(3)]
    else:
        origin=lighting._vector(op.get('origin'))
    for field in ('start','light'):
        if field in op and not isinstance(op[field],bool):
            raise ValueError(f'{field} must be true or false')
    start=op.get('start',True)
    name=lighting._name(m,op.get('name'),'mcp_fx')
    if area and effect in {'rain','snow'} and op.get('backend','native') == 'particle':
        return weather_volume(project,m,op,refs,name,effect,start)
    if op.get('backend','native') not in {'native','particle'}:
        raise ValueError('backend must be particle or native')
    if area:
        if effect not in {'rain','snow'}:
            raise ValueError('mins/maxs are supported only for rain and snow aliases')
        mins,maxs=lighting._vector(mins,'mins'),lighting._vector(maxs,'maxs')
        if any(mins[i]>=maxs[i] for i in range(3)):
            raise ValueError('mins must be less than maxs (absolute world coordinates)')
        cls='func_precipitation'
        kv={'targetname':name,'subclass_name':f'precipitation_{effect}','StartDisabled':str(int(not start))}
        ent=m.new_entity(cls,kv,[(mins[i]+maxs[i])/2 for i in range(3)],defaults=project.catalog.defaults(cls))
        for mesh in project._new_meshes(m,{'shape':'box','mins':mins,'maxs':maxs},'materials/tools/toolsprecipitation.vmat',None):
            m.attach(mesh,ent)
        inputs={'start':'Enable','stop':'Disable'}
    else:
        cls='info_particle_system'
        kv={'targetname':name,'effect_name':path,'start_active':str(int(start))}
        ent=m.new_entity(cls,kv,origin,lighting._vector(op.get('angles',[0,0,0]),'angles'),
                         defaults=project.catalog.defaults(cls))
        inputs={'start':'Start','stop':'Stop'}
    m.attach(ent)
    ids=[ent['nodeID']]
    if op.get('light',effect.startswith('fire_')):
        lamp=lighting.add_light(project,m,{'kind':'fire','origin':[origin[0],origin[1],origin[2]+24],
                                         'model':False,'name':name+'_light','brightness':600},refs)
        ids.extend(lamp['ids'])
    if op.get('ref'):
        refs[op['ref']]=ent['nodeID']
    return {'op':'add_fx','id':ent['nodeID'],'ids':ids,'name':name,'effect':path,
            'area':area,'inputs':inputs,**({'light_name':name+'_light'} if len(ids)>1 else {})}


def weather_volume(project,m,op,refs,name,effect,start):
    import math
    if len(name)>48: raise ValueError('weather name must be at most 48 characters')
    path=f'particles/mcp/m_{effect}.vpcf'
    if not checks._asset_exists(project,path):
        raise ValueError('Run fx_prepare_weather first to compile point rain/snow assets')
    lo,hi=lighting._vector(op.get('mins'),'mins'),lighting._vector(op.get('maxs'),'maxs')
    if any(lo[i]>=hi[i] for i in range(3)): raise ValueError('mins must be less than maxs')
    height=640 if effect=='rain' else 540
    if abs((hi[2]-lo[2])-height) > .01:
        raise ValueError(f'{effect} particle layer must be {height} units tall; maxs.z = mins.z + {height}')
    nx,ny=[max(1,math.ceil((hi[i]-lo[i])/224)) for i in (0,1)]
    if nx*ny>64: raise ValueError('weather supports at most 64 tiles; split or reduce the area')
    # One relay gives all child systems a reliable Start/Stop target.
    relay=m.new_entity('logic_relay',{'targetname':name,'FastRetrigger':True},lo,
                        defaults=project.catalog.defaults('logic_relay'))
    ids=[relay['nodeID']]
    for ix in range(nx):
        for iy in range(ny):
            target=f'{name}_{ix}_{iy}'
            if m.entities(targetname=target): raise ValueError('weather helper name already exists')
            pos=[lo[0]+(ix+.5)*(hi[0]-lo[0])/nx,lo[1]+(iy+.5)*(hi[1]-lo[1])/ny,hi[2]]
            ent=m.new_entity('info_particle_system',{'targetname':target,'effect_name':path,'start_active':start},
                             pos,defaults=project.catalog.defaults('info_particle_system'))
            m.attach(ent);ids.append(ent['nodeID'])
            m.add_connection(relay,'OnUser1',target,'Start')
            m.add_connection(relay,'OnUser2',target,'Stop')
    m.attach(relay)
    if op.get('ref'):refs[op['ref']]=relay['nodeID']
    return {'op':'add_fx','id':relay['nodeID'],'ids':ids,'name':name,'effect':path,'area':True,
            'tiles':nx*ny,'inputs':{'start':'FireUser1','stop':'FireUser2'},
            'note':'fixed outdoor layer; tiles extend up to 160 units beyond bounds; no roof occlusion'}


def add_lightning(project,m,op,refs):
    origin=lighting._vector(op.get('origin',[0,0,128]))
    duration=lighting._number(op.get('duration',.4),'duration',.05,2)
    interval=lighting._number(op.get('interval',12),'interval',3,300)
    start=op.get('start',True)
    if not isinstance(start,bool): raise ValueError('start must be true or false')
    name=lighting._name(m,op.get('name'),'mcp_lightning')
    if len(name)>48: raise ValueError('lightning name must be at most 48 characters')
    flash_name=name+'_flash'
    if m.entities(targetname=flash_name): raise ValueError('lightning flash name already exists')
    flash=m.new_entity('env_fade',{'targetname':flash_name,'spawnflags':1,'duration':duration,
                       'holdtime':0,'renderamt':180,'rendercolor':'210 225 255'},origin,
                       defaults=project.catalog.defaults('env_fade'))
    timer=m.new_entity('logic_timer',{'targetname':name,'StartDisabled':not start,'RefireTime':interval},origin,
                       defaults=project.catalog.defaults('logic_timer'))
    m.add_connection(timer,'OnTimer',flash_name,'Fade')
    for e in (flash,timer):m.attach(e)
    if op.get('ref'):refs[op['ref']]=timer['nodeID']
    return {'op':'add_fx','id':timer['nodeID'],'ids':[timer['nodeID'],flash['nodeID']],
            'name':name,'effect':'lightning','duration':duration,'interval':interval,
            'inputs':{'start':'Enable','stop':'Disable','flash':'FireTimer'},
            'note':'screen flash via env_fade; does not change sky/world lighting'}
