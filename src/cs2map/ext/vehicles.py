"""Register the car kind; add_controllable remains the shared operation."""
from .. import vehicles


def register(mcp, project):
    vehicles.register()
    original = project.extra_ops['add_controllable']
    # Mark deliberate wheel mounts with the existing visual-check inset contract.
    # Only this kind is wrapped; all other controllables go straight to their handler.
    def add(p, m, op, refs):
        result = original(p, m, op, refs)
        if op.get('kind') == 'car':
            n = result['entities']
            body = m.entities(targetname=n['body'])[0]
            for target in n['wheels']:
                m.entities(targetname=target)[0].set('cs2map_inset', 'string', str(body['nodeID']))
        return result
    project.extra_ops['add_controllable'] = add
