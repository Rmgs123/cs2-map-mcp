"""Local facade rectification and conservative suggestions, never auto acceptance."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw

from .config import PROJECT_ROOT, ensure_inside
from .project import OpError
from .textures import load as load_reference


def _input(value):
    if not isinstance(value,str) or not Path(value).is_file():
        raise OpError('image must be an existing local image path')
    path=Path(value)
    with Image.open(path) as im:
        if im.width*im.height>32_000_000:raise OpError('image exceeds 32 megapixels')
        im.verify()
    return path


OUTPUT=PROJECT_ROOT/'.local'/'cache'/'references'
KEEP=60   # newest outputs kept; older ones are regenerable and removed


def _output(kind):
    folder=ensure_inside(OUTPUT,PROJECT_ROOT)
    folder.mkdir(parents=True,exist_ok=True)
    for old in sorted(folder.glob('*.png'),key=lambda f:f.stat().st_mtime)[:-KEEP]:
        old.unlink(missing_ok=True)
    return folder/(kind+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.png')


def _runs(profile, threshold, minwidth):
    mask=profile>threshold;edges=np.diff(np.r_[False,mask,False].astype(int))
    return [(int(a),int(b)) for a,b in zip(np.where(edges==1)[0],np.where(edges==-1)[0]) if b-a>=minwidth]


def reference_facade(image, quad=None, real_width=None):
    path=_input(image)
    with Image.open(path) as src:w,h=src.size
    if quad is not None:
        q=np.asarray(quad,dtype=float)
        if q.shape!=(4,2) or not np.isfinite(q).all() or (q<0).any() or (q[:,0]>w).any() or (q[:,1]>h).any():
            raise OpError('quad needs four finite in-image corners: TL,TR,BR,BL')
        edges=[q[(i+1)%4]-q[i] for i in range(4)]
        cross=[float(edges[i][0]*edges[(i+1)%4][1]-edges[i][1]*edges[(i+1)%4][0]) for i in range(4)]
        if not all(c>1 for c in cross):raise OpError('quad must be convex, nonzero and ordered TL,TR,BR,BL')
    if real_width is not None:
        if isinstance(real_width,bool) or not isinstance(real_width,(int,float)) or not math.isfinite(real_width) or not 32<=real_width<=8192:
            raise OpError('real_width must be 32..8192 CS2 units')
    im=load_reference(path,quad=quad)
    im.thumbnail((1600,1600))
    rectified=_output('facade');im.save(rectified)
    a=np.asarray(im,dtype=float);gray=a.mean(axis=2);hh,ww=gray.shape
    # Dark, edge-rich window regions. Shadows/plants can trigger this heuristic.
    dx=np.abs(np.diff(gray,axis=1,prepend=gray[:,:1]));dy=np.abs(np.diff(gray,axis=0,prepend=gray[:1]))
    cutoff=float(np.percentile(gray,28))
    dark=gray<cutoff
    rows=dark.mean(axis=1);cols=dark.mean(axis=0)
    smooth=lambda v:np.convolve(v,np.ones(max(3,len(v)//80))/max(3,len(v)//80),mode='same')
    rowruns=_runs(smooth(rows),max(.15,float(rows.mean())+.035),max(5,hh//50))
    rowruns=[(a,b) for a,b in rowruns if a>hh*.04 and b<hh*.96 and b-a<hh*.35]
    colruns=_runs(smooth(cols),max(.15,float(cols.mean())+.035),max(5,ww//80))
    colruns=[(a,b) for a,b in colruns if a>ww*.03 and b<ww*.97 and b-a<ww*.4]
    floors=min(6,len(rowruns)) or None
    per_floor=min(8,len(colruns)) or None
    # Crop a bright, low-edge patch with size >=10% of facade, avoiding window candidates.
    pw,ph=max(8,ww//8),max(8,hh//8);best=None
    for y in range(0,max(1,hh-ph+1),max(1,ph//2)):
        for x in range(0,max(1,ww-pw+1),max(1,pw//2)):
            sl=np.s_[y:y+ph,x:x+pw]
            score=float((dx[sl]+dy[sl]).mean()+dark[sl].mean()*50)
            if best is None or score<best[0]:best=(score,[x,y,x+pw,y+ph])
    region=best[1];crop=_output('wall_crop');im.crop(tuple(region)).save(crop)
    palette=im.resize((128,128)).quantize(colors=5).convert('RGB').getcolors(16384)
    colors=[{'color':'#%02x%02x%02x'%rgb,'share':round(n/16384,3)} for n,rgb in sorted(palette,reverse=True)]
    positions=[round((a+b)/(2*ww),3) for a,b in colruns[:8]]
    suggestion={'windows':{'per_floor':per_floor or 3}}
    if positions:suggestion['windows']['positions']=positions
    return {'ok':True,'rectified':str(rectified),'size':list(im.size),'wall_crop':str(crop),'wall_region':region,
            'palette':colors,'estimated_floors':floors,'estimated_columns':per_floor,
            'facades':{'-y':suggestion},'real_width':real_width,
            'estimated_height':round(real_width*hh/ww,1) if real_width else None,
            'profiles':{'rows':[[a,b] for a,b in rowruns],'columns':[[a,b] for a,b in colruns],
                        'edge_mean':round(float((dx+dy).mean()),2)},
            'needs_visual_review':True,'note':'Heuristic suggestions only. Confirm floors/window grid and wall crop by eye; shadows, vegetation and arches confuse profiles.'}


def reference_pairs(pairs):
    """Rows of photo | game frame, numbered: one sheet for a block of photos of a real place (a town map,
    23-24.10: a local script made it for every block of 20 photos)."""
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 24:
        raise OpError('pairs: 1..24 [photo, frame] pairs of local images')
    rows = []
    for i, pair in enumerate(pairs, 1):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise OpError('each pair is [photo, frame]')
        ims = []
        for value in pair:
            with Image.open(_input(value)) as im:
                ims.append(im.convert('RGB'))
        h = 360
        ims = [im.resize((round(im.width * h / im.height), h), Image.Resampling.LANCZOS) for im in ims]
        row = Image.new('RGB', (sum(im.width for im in ims) + 48, h + 8), '#202020')
        ImageDraw.Draw(row).text((8, 8), str(i), fill='white')
        row.paste(ims[0], (40, 4))
        row.paste(ims[1], (48 + ims[0].width, 4))
        rows.append(row)
    out = Image.new('RGB', (max(r.width for r in rows), sum(r.height for r in rows) + 28), '#202020')
    draw = ImageDraw.Draw(out)
    draw.text((40, 8), 'PHOTO', fill='white')
    draw.text((48 + rows[0].width // 2, 8), 'GAME', fill='white')
    y = 28
    for row in rows:
        out.paste(row, (0, y))
        y += row.height
    path = _output('pairs')
    out.save(path)
    return {'ok': True, 'comparison': str(path), 'size': list(out.size), 'pairs': len(rows), 'needs_visual_review': True}


def reference_plan(project, spec):
    """The map from above, offline: walkable tops shaded by height, entities as dots, a grid in units, camera
    marks [label, x, y, yaw] with view arrows and an optional aerial picture under it aligned to the box."""
    from . import checks
    if not isinstance(spec, dict) or set(spec) - {'map', 'box', 'marks', 'image', 'grid', 'size'}:
        raise OpError('plan is {map, box: [[x0,y0],[x1,y1]], marks: [[label,x,y,yaw]], image, grid, size}')
    try:
        (x0, y0), (x1, y1) = ((float(v) for v in c) for c in spec['box'])
    except (KeyError, TypeError, ValueError):
        raise OpError('plan.box is [[x0, y0], [x1, y1]] in map units') from None
    x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    if x1 - x0 < 64 or y1 - y0 < 64:
        raise OpError('plan.box must be at least 64 units each way')
    size = int(spec.get('size', 1600))
    if not 256 <= size <= 4096:
        raise OpError('plan.size is 256..4096 px')
    k = size / max(x1 - x0, y1 - y0)
    w, h = max(1, round((x1 - x0) * k)), max(1, round((y1 - y0) * k))

    def px(x, y):
        return (x - x0) * k, h - (y - y0) * k
    from .terrain import expand_layers
    m = expand_layers(project, project.load(spec.get('map')))
    tops = []
    for node, b in checks._all_meshes(m):
        if b is None or b[1][0] < x0 or b[0][0] > x1 or b[1][1] < y0 or b[0][1] > y1:
            continue
        for tri in checks._triangles(node):
            n = checks._cross(checks._sub(tri[1], tri[0]), checks._sub(tri[2], tri[0]))
            ln = math.sqrt(checks._dot(n, n))
            if ln > 1e-9 and n[2] / ln > .3:
                tops.append((sum(p[2] for p in tri) / 3, n[2] / ln, tri))
    out = Image.new('RGB', (w, h), '#1c1f24')
    if spec.get('image'):
        with Image.open(_input(spec['image'])) as im:
            out = Image.blend(out, im.convert('RGB').resize((w, h), Image.Resampling.LANCZOS), .55)
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if tops:
        zs = sorted(t[0] for t in tops)
        lo, hi = zs[len(zs) // 50], zs[-1 - len(zs) // 50]
        for z, up, tri in sorted(tops, key=lambda t: t[0]):          # higher tops over lower ones
            t = 0 if hi <= lo else min(1, max(0, (z - lo) / (hi - lo)))
            shade = int((90 + 120 * t) * (.75 + .25 * up))
            draw.polygon([px(p[0], p[1]) for p in tri], fill=(shade, shade + 6, shade - 10, 150 if spec.get('image') else 255))
    out.paste(layer, (0, 0), layer)
    draw = ImageDraw.Draw(out)
    grid = float(spec.get('grid', 0) or 0) or 10 ** math.floor(math.log10(max(x1 - x0, y1 - y0) / 4))
    gx = math.ceil(x0 / grid) * grid
    while gx <= x1:
        draw.line([px(gx, y0), px(gx, y1)], fill=(70, 120, 170), width=1)
        draw.text((px(gx, y0)[0] + 2, h - 12), f'{gx:.0f}', fill=(120, 170, 220))
        gx += grid
    gy = math.ceil(y0 / grid) * grid
    while gy <= y1:
        draw.line([px(x0, gy), px(x1, gy)], fill=(70, 120, 170), width=1)
        draw.text((2, px(x0, gy)[1] - 12), f'{gy:.0f}', fill=(120, 170, 220))
        gy += grid
    for ent in m.entities():
        o = ent.get('origin')
        if o is not None and x0 <= o[0] <= x1 and y0 <= o[1] <= y1:
            cx, cy = px(o[0], o[1])
            draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(230, 200, 60))
    marks = spec.get('marks') or []
    if not isinstance(marks, list) or len(marks) > 64:
        raise OpError('plan.marks: up to 64 [label, x, y, yaw]')
    for mark in marks:
        if not isinstance(mark, (list, tuple)) or len(mark) not in (3, 4):
            raise OpError('each mark is [label, x, y] or [label, x, y, yaw] (yaw 0 = +x, 90 = +y)')
        cx, cy = px(float(mark[1]), float(mark[2]))
        draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(255, 70, 60), width=3)
        if len(mark) == 4:
            a = math.radians(float(mark[3]))
            draw.line([cx, cy, cx + math.cos(a) * 40, cy - math.sin(a) * 40], fill=(255, 70, 60), width=3)
        draw.text((cx + 9, cy - 16), str(mark[0])[:24], fill='white')
    path = _output('plan')
    out.save(path)
    return {'ok': True, 'plan': str(path), 'size': [w, h], 'units_per_px': round(1 / k, 3), 'grid': grid,
            'tops': len(tops), 'needs_visual_review': True}


def reference_compare(reference, shot):
    paths=[_input(reference),_input(shot)]
    ims=[]
    for path in paths:
        with Image.open(path) as im:ims.append(im.convert('RGB'))
    h=min(1000,max(im.height for im in ims))
    ims=[im.resize((round(im.width*h/im.height),h),Image.Resampling.LANCZOS) for im in ims]
    out=Image.new('RGB',(sum(im.width for im in ims)+8,h+32),'#202020')
    draw=ImageDraw.Draw(out);draw.text((8,8),'REFERENCE',fill='white');draw.text((ims[0].width+16,8),'OUR SHOT',fill='white')
    out.paste(ims[0],(0,32));out.paste(ims[1],(ims[0].width+8,32))
    path=_output('compare');out.save(path)
    return {'ok':True,'comparison':str(path),'size':list(out.size),'needs_visual_review':True}
