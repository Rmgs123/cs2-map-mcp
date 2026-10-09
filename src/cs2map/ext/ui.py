"""HUD themes (set_ui_theme, hud style keys) and own layouts (add_hud); see ui.py."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import compiler, mechanics, ui
from ..config import ensure_inside
from ..project import OpError

NAME = re.compile(r'^[a-z][a-z0-9_]{1,39}$')


def add_hud(project, m, op, refs):
    """An own panorama layout: xml + css (+ images), compiled, and its custom_hud_layout entity."""
    extra = set(op) - {'op', 'name', 'xml', 'css', 'images', 'origin', 'ref'}
    if extra:
        raise OpError(f'unknown add_hud fields {sorted(extra)}; allowed: name, xml, css, images, origin')
    name = op.get('name')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise OpError('add_hud.name: 2..40 chars a-z0-9_, starts with a letter (it is the layout and entity name)')
    layout = 'mcp_hud_' + name
    xml = op.get('xml', '')
    css = op.get('css', '')
    try:
        ids = ui.check_layout(xml, css)
    except ui.UIError as e:
        raise OpError(str(e)) from None
    include = f'file://{{resources}}/styles/custom_game/{layout}.css'
    if include not in xml:
        xml = re.sub(r'<\s*root\s*>', f'<root><styles><include src="{include}" /></styles>', xml, count=1)
    images = op.get('images') or {}
    if not isinstance(images, dict) or len(images) > 32:
        raise OpError('add_hud.images: {"name.png": "<local png path>"} (up to 32), used as file://{images}/custom_game/'
                      + layout + '/name.png')
    if m.entities(targetname=name):
        raise OpError(f'an entity named {name} already exists')
    origin = op.get('origin', [0, 0, 0])
    ent = m.new_entity('custom_hud_layout', {'targetname': name,
                                             'layout': f'panorama/layout/custom_game/{layout}.vxml'},
                       origin, defaults=project.catalog.defaults('custom_hud_layout'))
    m.attach(ent)
    if op.get('ref'):
        refs[op['ref']] = ent['nodeID']
    log = None
    if not mechanics_preview(project):
        written = []
        for img, src in images.items():
            if not re.fullmatch(r'[a-z0-9_-]+\.png', img):
                raise OpError(f'image name {img!r}: a-z0-9_- and .png')
            source = Path(src)
            if not source.is_file() or source.suffix.lower() != '.png':
                raise OpError(f'image {src!r} is not a local .png file')
            dest = ensure_inside(project.s.content_addon / 'panorama' / 'images' / 'custom_game' / layout / img,
                                 project.s.content_addon)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            written.append(dest)
        log = mechanics.ensure_hud(project, layout, xml, css)
        if written:
            job = compiler.compile_assets(project.s, project.jobs, written, force=True, timeout=60)
            project.job_status(job.id, 60, 0)
    return {'op': 'add_hud', 'id': ent['nodeID'], 'name': name, 'panels': ids,
            'script': f'Instance.FindEntityByName("{name}") is a CustomHudLayout: SetHasClass(panel, "Hidden", false), '
                      'SetDialogVariableString(panel, var, text), SetInputCaptureEnabled(slot, true) + '
                      'Instance.OnCustomHudClicked for Buttons',
            **({'ref': op['ref']} if op.get('ref') else {})}


def mechanics_preview(project) -> bool:
    """True in a dry run (map_edit dry_run or the preview the mechanics wrappers make): nothing is written."""
    from ..project import DRY_RUN
    return bool(DRY_RUN.get())


def register(mcp, project):
    project.register_op('set_ui_theme', ui.set_theme_op)
    project.register_op('add_hud', add_hud)
    project.register_preprocessor(ui.preprocess)
