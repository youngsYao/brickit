import bricklayer as bl
from brickwright import TestContext
C=bl.Color
R=bl.Rotation

def create_build():
    b=bl.LegoBuild()
    ivory=C.TAN
    teal=C.TRANS_GREEN
    terracotta=C.REDDISH_ORANGE
    def put(pid,c,x,y,z,r=R.DEG_0,name=None):
        return b.add(bl.Element(pid,c),(x,y,z),rotation=r,name=name)
    def brick(w,d,h,c,x,y,z,r=R.DEG_0,name=None):
        return b.add(bl.brick(w,d,h,c),(x,y,z),rotation=r,name=name)
    def plate(w,d,c,x,y,z,r=R.DEG_0,name=None):
        return b.add(bl.plate(w,d,c),(x,y,z),rotation=r,name=name)
    def win(w,h,x,y,z,r=R.DEG_0,name=None):
        return b.add(bl.window(w,1,h,frame_color=C.SAND_GREEN,pane_color=teal),(x,y,z),rotation=r,name=name)
    def ring(z):
        plate(16,2,ivory,0,-1,z,name='podium_front_cornice')
        plate(16,2,ivory,0,10,z)
        plate(8,2,ivory,0,2,z,R.DEG_90)
        plate(8,2,ivory,14,2,z,R.DEG_90)
    # Grounded plinth, broad stairs, and a masonry podium with a 12-stud portal.
    plate(16,16,C.LIGHT_BLUISH_GRAY,0,-2,0,name='foundation')
    for y in (0,10):
        plate(16,2,ivory,0,y,1/3)
        brick(16,1,1,ivory,0,y if y==0 else 11,2/3)
    for x in (0,15):
        brick(8,1,1,ivory,x,2,1/3,R.DEG_90)
        plate(8,1,ivory,x,2,4/3,R.DEG_90)
        plate(1,1,ivory,x,1,4/3)
        plate(1,1,ivory,x,10,4/3)
    for y in (0,11):
        brick(10,1,1,ivory,3,1 if y==0 else 10,2/3)
        for x in (0,14):
            brick(2,1,3,C.DARK_RED,x,y,5/3)
            brick(2,1,1,C.DARK_RED,x,y,14/3)
            plate(2,1,ivory,x,y,17/3)
        for x in (2,13):
            brick(1,1,1,ivory,x,y,5/3)
            plate(1,1,ivory,x,y,8/3)
        put('14707',ivory,2,y,3,name='entrance_arch' if y==0 else 'rear_arch')
        for x in (3,5,7,9,11):
            win(2,3,x,1 if y==0 else 10,5/3)
            brick(2,1,1,teal,x,1 if y==0 else 10,14/3)
    for x in (0,15):
        for y in (1,7):
            brick(4,1,3,C.DARK_RED,x,y,5/3,R.DEG_90)
        win(2,3,x,5,5/3,R.DEG_90)
        brick(10,1,1,C.DARK_RED,x,1,14/3,R.DEG_90)
        plate(10,1,ivory,x,1,17/3,R.DEG_90)
    plate(12,2,ivory,2,-2,1/3)
    plate(12,1,ivory,2,-1,2/3)
    ring(6)
    # Seven masonry pier modules; selected piers carry inset-looking slit tiles.
    for level in range(7):
        z=19/3+level*10/3
        for x in (0,13):
            for y in (0,10):
                brick(2,2,3,ivory,x,y,z)
                # Narrow teal slit in each front masonry pier, with stone above/below.
                if y == 0:
                    for dz, color in ((0,ivory),(1,C.DARK_TURQUOISE),(2,ivory)):
                        brick(2,1,1,color,x+2,y,z+dz,R.DEG_90,
                              name=f'pier_slit_{level}_{x}' if dz==1 else None)
                else:
                    brick(2,1,3,ivory,x+2,y,z,R.DEG_90)
                plate(3,2,ivory,x,-1 if y==0 and level==6 else y,z+3)
        # Rear has three banks, sides have two broad continuous window banks.
        for x,w in ((3,4),(7,2),(9,4)):
            put('4033c01' if w==4 else '4035c01',C.SAND_GREEN,x,11,z,name=f'rear_glass_{level}_{x}')
        plate(10,1,ivory,3,11,z+3)
        for x in (0,15):
            for y in (2,6):
                put('4033c01',C.SAND_GREEN,x,y,z,R.DEG_90)
            plate(8,1,ivory,x,2,z+3,R.DEG_90)
    # Train-window frames supply integral side mullions; no interior spines required.
    # Ten close-set glazed courses give the front its dense vertical rhythm.
    for level in range(10):
        z=19/3+level*7/3
        for i,x in enumerate((3,5,7,9,11)):
            # Two one-brick-high classic glazed windows per former large bay.
            # Integral glazing and a fine mid-rail yield twenty visible rows.
            put('3004' if x==7 else '27cc01',C.DARK_TURQUOISE if x==7 else C.SAND_GREEN,x,0,z,name=f'front_glass_{level}_{i}')
            put('3004' if x==7 else '27cc01',C.DARK_TURQUOISE if x==7 else C.SAND_GREEN,x,0,z+1,name=f'front_upper_row_{level}_{i}')
        plate(10,1,ivory,3,0,z+2)
    # Main curtain wall's wide, shallow arch and teal recessed spandrel.
    z=89/3
    for x in (0,13):
        for y in (0,10):
            brick(2,2,3,ivory,x,y,z)
            brick(2,1,3,ivory,x+2,y,z,R.DEG_90)
    put('14707',ivory,2,-1,z,name='main_shallow_arch')
    plate(12,1,ivory,2,-1,z+3)
    for x in (3,5,7,9,11):
        put('87544',teal,x,1,z)
    for x,w in ((3,4),(7,2),(9,4)):
        win(w,3,x,11,z)
    for x in (0,15):
        for y in (2,6):
            win(4,3,x,y,z,R.DEG_90)
    # Full diaphragm below a stepped, visibly separate crown.
    plate(16,6,ivory,0,0,z+3)
    plate(16,6,ivory,0,6,z+3)
    cz=33
    # Projecting pale corner towers, round medallions, green clerestories, hip caps.
    for x in (-1,13):
        for y in (-1,9):
            tag=f'turret_{x}_{y}'
            plate(6,4,ivory,x-1,y,cz,name=tag)
            tz=cz+1/3
            if y==-1:
                brick(1,1,3,ivory,x,y,tz)
                brick(1,1,3,ivory,x+3,y,tz)
                brick(2,1,1,ivory,x+1,y,tz)
                s=put('11211',ivory,x+1,y,tz+1)
                brick(2,1,1,ivory,x+1,y,tz+2)
                disk=bl.Element('14769',C.TAN)
                b.connect(disk,disk.find_connector('antistud',toward=bl.Direction.RIGHT),to=s.find_connector('stud',facing=bl.Direction.FRONT,toward=bl.Direction.RIGHT),name=tag+'_medallion')
            else:
                brick(4,1,3,ivory,x,y,tz)
            brick(4,1,3,ivory,x,y+3,tz)
            for xx in (x,x+3):
                brick(2,1,3,ivory,xx,y+1,tz,R.DEG_90)
            plate(6,4,ivory,x-1,y,tz+3)
            for yy in (y,y+3):
                brick(4,1,1,teal,x,yy,tz+10/3)
            for xx in (x,x+3):
                brick(2,1,1,teal,xx,y+1,tz+10/3,R.DEG_90)
            plate(4,4,ivory,x,y,tz+13/3)
            for xx,yy,r in ((x,y,R.DEG_270),(x+2,y,R.DEG_0),(x+2,y+2,R.DEG_90),(x,y+2,R.DEG_180)):
                put('3045',terracotta,xx,yy,tz+14/3,r)
    # Prominent round-arched upper window, framed independently of main arch.
    put('30528',ivory,4,0,cz,name='upper_front_arch')
    for xx in (5,7,9):
        put('87544',teal,xx,1,cz)
        put('87544',teal,xx,1,cz+3)
    plate(8,2,ivory,4,0,cz+6)
    # Recessed upper side and back walls, distinct from the four turrets.
    for x in (3,12):
        brick(6,1,1,ivory,x,3,cz,R.DEG_90)
        win(4,3,x,4,cz+1,R.DEG_90)
        brick(6,1,1,ivory,x,3,cz+4,R.DEG_90)
        brick(6,1,1,ivory,x,3,cz+5,R.DEG_90)
    for zz in (cz,cz+3):
        for x in (4,8):
            win(4,3,x,9,zz)
    # Top clerestory supports the oversailing roof.
    plate(10,6,ivory,3,3,cz+6)
    plate(10,2,ivory,3,9,cz+6)
    for y in (3,9):
        brick(10,1,1,C.DARK_TURQUOISE,3,y,cz+19/3)
    for x in (3,12):
        brick(3,1,1,C.DARK_TURQUOISE,x,4,cz+19/3,R.DEG_90)
        brick(2,1,1,C.DARK_TURQUOISE,x,7,cz+19/3,R.DEG_90)
    rz=cz+22/3
    plate(16,6,C.DARK_BROWN,-1,-1,rz,name='roof_support')
    plate(16,6,C.DARK_BROWN,-1,5,rz)
    plate(16,2,C.DARK_BROWN,-1,11,rz)
    plate(14,2,C.DARK_BROWN,15,-1,rz,R.DEG_90)
    # Cantilevered corner brackets turn the four eaves upward.
    for xx,yy,r in ((-3,-3,R.DEG_270),(17,-3,R.DEG_0),(17,13,R.DEG_90),(-3,13,R.DEG_180)):
        px = -2 if xx < 0 else 16
        py = -2 if yy < 0 else 12
        plate(2,2,C.DARK_BROWN,px,py,rz-1/3)
        put('3676',terracotta,xx,yy,rz,r,name=f'upturned_eave_{xx}_{yy}')
    # A shallow flared lower roof changes pitch into two steeper upper courses.
    for xx,yy,r in ((-1,-1,R.DEG_270),(14,-1,R.DEG_0),(14,10,R.DEG_90),(-1,10,R.DEG_180)):
        put('3675',terracotta,xx,yy,rz+1/3,r,name=f'eave_corner_{xx}_{yy}')
    for xx in (2,6,10):
        put('3297',terracotta,xx,-1,rz+1/3)
        put('3297',terracotta,xx,10,rz+1/3,R.DEG_180)
    for yy in (2,6):
        put('3297',terracotta,-1,yy,rz+1/3,R.DEG_270)
        put('3297',terracotta,14,yy,rz+1/3,R.DEG_90)
    def hip(x,y,w,d,h,steep=False):
        for xx,yy,r in ((x,y,R.DEG_270),(x+w-2,y,R.DEG_0),(x+w-2,y+d-2,R.DEG_90),(x,y+d-2,R.DEG_180)):
            put('78886' if steep else '3045',terracotta,xx,yy,h,r)
        for xx in range(x+2,x+w-2,2):
            put('3678b' if steep else '3039',terracotta,xx,y,h)
            put('3678b' if steep else '3039',terracotta,xx,y+d-2,h,R.DEG_180)
        for yy in range(y+2,y+d-2,2):
            put('3678b' if steep else '3039',terracotta,x,yy,h,R.DEG_270)
            put('3678b' if steep else '3039',terracotta,x+w-2,yy,h,R.DEG_90)
    hip(1,1,14,10,rz+4/3,steep=True)
    hip(2,2,12,8,rz+10/3,steep=True)
    # Upper roof now rises two further bricks, without altering its sound base.
    rz += 2
    plate(10,6,C.DARK_BROWN,3,3,rz+10/3)
    # Raised dark ridge pavilion, grille texture, upturned ridge ends, antenna.
    for y in (4,7):
        for x in (4,6,8,10):
            put('2877',C.DARK_BLUISH_GRAY,x,y,rz+11/3)
    for x in (4,11):
        brick(2,1,1,C.DARK_BLUISH_GRAY,x,5,rz+11/3,R.DEG_90)
    plate(10,4,C.DARK_BLUISH_GRAY,3,4,rz+14/3)
    for x,r in ((2,R.DEG_270),(12,R.DEG_90)):
        b.add(bl.inverted_slope(2,2,1,C.DARK_BLUISH_GRAY),(x,5,rz+5),rotation=r)
    put('3957a',C.LIGHT_BLUISH_GRAY,8,5,rz+5,name='aerial')
    # Present the elaborate main facade, not the intentionally quieter rear,
    # toward the renderer's default camera. Preserve all diagnostic names.
    oriented=bl.LegoBuild()
    turn=bl.Pose.from_rotation(R.DEG_180)
    for p in b.placements:
        oriented.add_raw(p.content,turn.compose(p.pose),name=b.name_of(p))
    return oriented


def run_tests():
    build=create_build()
    tests=TestContext(build)
    tests.check_no_floating()
    tests.check_no_collisions()
    tests.check_connected(expected_components=1)
    tests.check('catalog part count 1–600',1<=build.brick_count<=600,str(build.brick_count))
    tests.check('single complete tower',build.num_connected_components==1)
    tests.check('twenty fine front window rows',sum(n.startswith('front_glass_') or n.startswith('front_upper_row_') for n in build.names)==100)
    tests.check('three distinct arched features',all(n in build.names for n in ('entrance_arch','main_shallow_arch','upper_front_arch')))
    tests.check('four projecting roofed turrets',sum(n.startswith('turret_') and not n.endswith('medallion') for n in build.names)==4)
    tests.check('two round facade medallions',sum(n.endswith('medallion') for n in build.names)==2)
    tests.check('four upward swept main roof corners',sum(n.startswith('upturned_eave_') for n in build.names)==4)
    tests.check('fourteen narrow pier windows',sum(n.startswith('pier_slit_') for n in build.names)==14)
    tests.check('rear facade seven glazed courses',sum(n.startswith('rear_glass_') for n in build.names)==21)
    tests.check('grounded foundation',abs(build.bounding_box[0].z)<1e-6)
    tests.check('tall slender proportions',build.height/build.width>2)
    return tests.report()
