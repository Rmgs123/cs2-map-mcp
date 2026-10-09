"""T turret kind; the operation remains the shared add_controllable."""
from .. import turrets


def register(mcp, project):
    turrets.register()
    original = project.extra_ops['add_controllable']

    def add(p, m, op, refs):
        result = original(p, m, op, refs)
        if op.get('kind') == 'turret':
            n = result['entities']
            base = m.entities(targetname=n['body'])[0]
            platform = m.entities(targetname=n['platform'])[0]
            barrel = m.entities(targetname=n['barrel'])[0]
            platform.set('cs2map_inset', 'string', str(base['nodeID']))
            barrel.set('cs2map_inset', 'string', str(platform['nodeID']))
        return result

    project.extra_ops['add_controllable'] = add
