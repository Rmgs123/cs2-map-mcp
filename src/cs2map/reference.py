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
