import bricklayer as bl
from brickwright import TestContext

C=bl.Color

def create_build():
    b=bl.LegoBuild()
    def add(pid,col,x,y,z,rot=bl.Rotation.DEG_0,name=None):
        return b.add(bl.Element(pid,col),(x,y,z),rotation=rot,name=name)
    def brick(w,d,h,x,y,z,col=C.LIGHT_BLUISH_GRAY,name=None):
        return b.add(bl.brick(w,d,h,col),(x,y,z),name=name)
    def plate(w,d,x,y,z,col=C.LIGHT_BLUISH_GRAY,name=None):
        return b.add(bl.plate(w,d,col),(x,y,z),name=name)
    # Four independent stone footings, united above by the three vaults.
    for i,x in enumerate((0,8,20,28)):
        b.add(bl.plate(6,4,C.LIGHT_BLUISH_GRAY),(x-1,-1,0),rotation=bl.Rotation.DEG_90,name=f'footing_{i}')
        plate(4,4,x-1,0,1/3,C.WHITE)
        plate(4,4,x-1,0,2/3,C.LIGHT_BLUISH_GRAY)
        for yy in (0,2):
            brick(2,2,3,x,yy,1)
        add('3001',C.LIGHT_BLUISH_GRAY,x,0,4,bl.Rotation.DEG_90)
        b.add(bl.plate(4,2,C.LIGHT_BLUISH_GRAY),(x,0,5),rotation=bl.Rotation.DEG_90)
    # Genuine arch pieces repeated through the full four-stud wall depth.
    for y in range(4):
        add('3308a',C.LIGHT_BLUISH_GRAY,1,y,16/3,name=f'left_arch_{y}')
        add('3308a',C.LIGHT_BLUISH_GRAY,21,y,16/3,name=f'right_arch_{y}')
        add('6108',C.LIGHT_BLUISH_GRAY,9,y,22/3,name=f'central_arch_{y}')
    for x in (0,9,20,29):
        for k in range(2):
            add('3010',C.LIGHT_BLUISH_GRAY,x,0,16/3+k,bl.Rotation.DEG_90)
    for x in (8,21):
        for k in range(3):
            add('3010',C.LIGHT_BLUISH_GRAY,x,0,22/3+k,bl.Rotation.DEG_90)
    # Central entablature; staggered broad bricks connect all four arch slices.
    for k in range(3):
        for y in (0,2):
            if k==1 and y==0:
                brick(2,2,1,8,y,31/3+k)
                brick(4,2,1,18,y,31/3+k)
                brick(8,1,1,10,1,31/3+k)
                # One substantial four-stud-high framed plaque, attached by SNOT.
                plaque=bl.LegoBuild()
                plaque.add(bl.plate(10,4,C.WHITE))
                for yy in (0,3):
                    plaque.add(bl.tile(6,1,C.LIGHT_BLUISH_GRAY),(0,yy,1/3))
                    plaque.add(bl.tile(4,1,C.LIGHT_BLUISH_GRAY),(6,yy,1/3))
                for xx in (0,9):
                    plaque.add(bl.tile(2,1,C.LIGHT_BLUISH_GRAY),(xx,1,1/3),rotation=bl.Rotation.DEG_90)
                for xx in (1,3,5,7):
                    plaque.add(bl.tile(2,1,C.WHITE),(xx,1,1/3))
                    plaque.add(bl.Element('2412b',C.PEARL_GOLD),(xx,2,1/3))
                for sx in (10,12,14,16):
                    mount=add('11211',C.LIGHT_BLUISH_GRAY,sx,0,31/3+k)
                    if sx==10:
                        b.connect(plaque,plaque.find_connector('antistud',toward=(0.5,2.5,0)),to=mount.find_connector('stud',facing=bl.Direction.FRONT,toward=bl.Direction.RIGHT),name='framed_plaque')
            else:
                brick(6,2,1,8,y,31/3+k)
                brick(8,2,1,14,y,31/3+k)
    # Column bases, tall slim pilasters, and projecting capitals on both faces.
    for x in (0,8,20,28):
        for y in (-1,4):
            brick(2,1,1,x,y,1/3,C.WHITE)
            plate(3,1,x,y,4/3,C.LIGHT_BLUISH_GRAY)
            brick(1,1,3,x,y,5/3,C.WHITE)
            plate(2,1,x,y,14/3,C.WHITE)
            plate(2,1,x,y,5,C.LIGHT_BLUISH_GRAY)
            plate(3,1,x,y,16/3,C.WHITE)
            if x in (8,20):
                brick(2,1,3,x,y,17/3,C.WHITE)
                brick(2,1,3,x,y,26/3,C.WHITE)
                plate(2,1,x,y,35/3,C.WHITE)
                brick(2,1,1,x,y,12)
    # Continuous projecting stone cornices tie the pilasters into each bay.
    for yy in (-1,4):
        if yy==4:
            plate(10,1,10,yy,13,C.WHITE)
        for xx in (0,22):
            plate(8,1,xx,yy,7,C.WHITE)
    def roof(x,w,z,tall,label):
        # Deep orange roof, shallow flared skirt below a steeper upper pitch.
        plate(w,6,x,-1,z,C.DARK_BLUISH_GRAY)
        if w==16:
            plate(16,8,x,-2,z+1/3,C.ORANGE)
        else:
            plate(w,6,x,-2,z+1/3,C.ORANGE)
            plate(w,2,x,4,z+1/3,C.ORANGE)
        h=z+2/3
        brick(w,2,1,x,1,h,C.ORANGE) if w==10 else [brick(8,2,1,xx,1,h,C.ORANGE) for xx in (x,x+8)]
        # Catalog slopes preserve an unmistakable curved-pitch pavilion profile.
        segments=[(x,1,C.WHITE),(x+1,4,C.ORANGE),(x+5,4,C.ORANGE),(x+9,1,C.WHITE)] if w==10 else [(x,1,C.WHITE),(x+1,3,C.ORANGE),(x+4,4,C.ORANGE),(x+8,4,C.ORANGE),(x+12,3,C.ORANGE),(x+15,1,C.WHITE)]
        for xx,ww,col in segments:
            for yy,rot in ((-2,bl.Rotation.DEG_0),(3,bl.Rotation.DEG_180)):
                b.add(bl.slope(ww,3,1,col),(xx,yy,h),rotation=rot)
            # The center is two bricks tall; side roofs gain a full second tier.
            upper=2 if tall else 1
            for yy,rot in ((0,bl.Rotation.DEG_0),(2,bl.Rotation.DEG_180)):
                if upper==1:
                    b.add(bl.slope(ww,2,1,col),(xx,yy,h+1),rotation=rot)
                else:
                    dx=0
                    while dx<ww:
                        sw=min(2,ww-dx)
                        b.add(bl.slope(sw,2,2,col),(xx+dx,yy,h+1),rotation=rot)
                        dx+=sw
        ridge=h+1+(2 if tall else 1)
        plate(w,2,x,1,ridge,C.WHITE,name=f'{label}_ridge_{x}')
        # Curved inverted pieces form swept upward ends, rather than square posts.
        ends=((x-2,bl.Rotation.DEG_270),(x+w-2,bl.Rotation.DEG_90)) if tall else (((x-2,bl.Rotation.DEG_270),) if label=='left' else ((x+w-2,bl.Rotation.DEG_90),))
        for xx,rot in ends:
            for yy in (-2,5):
                add('13547',C.WHITE,xx,yy,z,rot)
        for xx,rot in ends:
            # Rising ridge end tiles continue the upturned eave motif.
            b.add(bl.slope(1,2,1,C.WHITE),((x if xx==x-2 else x+w-2),1,ridge+1/3),rotation=(bl.Rotation.DEG_90 if xx==x-2 else bl.Rotation.DEG_270))
    roof(-2,10,22/3,False,'left')
    roof(22,10,22/3,False,'right')
    roof(7,16,40/3,True,'central')
    return b


def run_tests():
    b=create_build()
    tests=TestContext(b)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    tests.check('part count 1–250',1 <= b.brick_count <= 250,str(b.brick_count))
    tests.check('three deep arched entrances', all(f'{bay}_arch_{y}' in b.names for bay in ('left','central','right') for y in range(4)))
    tests.check('raised central roof', b.named('central_ridge_7').center.z > b.named('left_ridge_-2').center.z+3)
    tests.check('grounded footings',abs(float(b.bounding_box[0].z))<0.001)
    tests.check('deep substantial roof', b.named('central_ridge_7').center.z >= 17 and b.depth >= 8)
    tests.check('large framed plaque', b.named('framed_plaque').width >= 10)
    tests.check('curved upturned eaves',sum(n for e,n in b.list_elements().items() if e.ldraw_id=='13547')==8)
    return tests.report()
