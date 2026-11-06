/**
 * Hand-authored, deterministic architectural microbuild. Front elevation follows
 * the supplied Song'en Building photograph; unseen elevations are inferred.
 * Coordinates use the same editable sample bricks as the apple, not LDraw meshes.
 */
const U = .58, H = .56;
export const SONGEN_EXAMPLE = Object.freeze({
  id: 'songen-example', title: '厦大 · 颂恩楼',
  description: '颂恩楼、嘉庚楼群与芙蓉湖畔的校园一景。',
  sourceImage: './assets/songen-reference.jpg', thumbnail: './assets/songen-preview.png',
});

export function createSongenPieces() {
  const pieces = [];
  const C = { stone:'#d6d1c3', light:'#eee7d7', shade:'#b9b8b0', trim:'#ded9cb',
    red:'#963f36', roof:'#a3473e', roofAlt:'#b35245', dark:'#484d49',
    glass:'#417a87', blue:'#3f6674', pale:'#6a9ba6', frame:'#96aca8',
    water:'#669c9e', green:'#657d43', leaf:'#849954', earth:'#82674d' };
  function add(w,d,h,x,bottom,z,color,type='body',options={}) {
    const shape=options.shape || (h<.3?'tile':'brick');
    const dims=[w,d].sort((a,b)=>a-b);
    pieces.push({id:`songen-${String(pieces.length+1).padStart(4,'0')}`,
      partId:`${shape}-${dims[0]}x${dims[1]}`,w,d,h,
      x:x*U,y:bottom+h/2,z:z*U,rx:0,ry:0,rz:0,
      layer:Math.max(1,Math.floor((bottom+.01)/H)+1),type,shape,color,
      ...options});
  }
  // A course is tiled into separate, staggered bricks instead of a solid box.
  function course(x,z,w,d,bottom,color,type='masonry',h=H,max=4,studs=true,depth=2) {
    for(let dz=0;dz<d-1e-6;dz+=depth) for(let dx=0;dx<w-1e-6;dx+=max) {
      const bw=Math.min(max,w-dx),bd=Math.min(depth,d-dz);
      add(bw,bd,h,x-w/2+dx+bw/2,bottom,z-d/2+dz+bd/2,color,type,{studs});
    }
  }
  function band(x,z,w,d,y,color=C.trim,h=.19,type='cornice') {
    // Hollow perimeter keeps the model light and the assembly readable.
    for(const side of [-1,1]) {
      for(let a=-w/2;a<w/2-1e-6;a+=4) {
        const span=Math.min(4,w/2-a);
        add(span,1,h,x+a+span/2,y,z+side*(d/2-.5),color,type);
      }
      for(let a=-d/2+1;a<d/2-1-1e-6;a+=4) {
        const span=Math.min(4,d/2-1-a);
        add(1,span,h,x+side*(w/2-.5),y,z+a+span/2,color,type);
      }
    }
  }
  function pane(x,z,y,w=1,h=1.1,side=0,color=C.glass,type='window') {
    // Smooth tiles standing on their edges, with thin depth and no top studs.
    if(side) add(.31,w,h,x,y,z,color,type,{shape:'tile',studs:false});
    else add(w,.31,h,x,y,z,color,type,{shape:'tile',studs:false});
  }
  function arch(cx,z,spring,radius,thickness=.24,segments=16,color=C.trim,type='arch') {
    for(let i=0;i<segments;i++) {
      const angle=(i+.5)*Math.PI/segments;
      const len=2*radius*Math.sin(Math.PI/segments/2)+.025;
      add(len/U,.65,thickness,cx+Math.cos(angle)*radius/U,
        spring+Math.sin(angle)*radius-thickness/2,z,color,type,
        {shape:'tile',studs:false,rz:angle+Math.PI/2});
    }
  }
  function archedGlass(cx,z,bottom,width,height,type='arched-window') {
    const r=width*U/2,spring=bottom+height-r;
    for(let col=0;col<width;col++) {
      const xx=(col+.5-width/2)*U,top=spring+Math.sqrt(Math.max(0,r*r-xx*xx));
      for(let yy=bottom;yy<top-.05;yy+=.72) {
        pane(cx+xx/U,z,yy,.91,Math.min(.65,top-yy),0,col%3===0?C.pale:C.glass,type);
      }
    }
    for(let col=0;col<=width;col++) {
      const xx=(col-width/2)*U,top=spring+Math.sqrt(Math.max(0,r*r-xx*xx));
      add(.12,.43,top-bottom,cx+xx/U,bottom,z+.035,C.frame,type,{studs:false});
    }
    for(let yy=bottom+.72;yy<spring;yy+=.72) add(width,.43,.09,cx,yy,z+.04,C.frame,type,{studs:false});
    arch(cx,z+.05,spring,r,.25,18,C.trim,type);
    for(const s of [-1,1]) add(.42,.65,spring-bottom,cx+s*(width/2+.12),bottom,z,C.trim,type,{studs:false});
    add(width+1,.9,.19,cx,bottom-.19,z,C.light,type);
  }
  function roof(cx,cz,w,d,y,levels,type='roof') {
    // Terraced hip roof: red tile courses, dark projecting eaves, raised corners.
    band(cx,cz,w+1,d+1,y,C.dark,.19,type.startsWith('campus-')?'campus-eaves':'eaves');
    for(let level=0;level<levels;level++) {
      const ww=w-level*1.1,dd=d-level*.85;
      band(cx,cz,ww,dd,y+.19+level*.32,level%3===1?C.roofAlt:C.roof,.33,type);
    }
    const topW=w-(levels-1)*1.1,topD=d-(levels-1)*.85;
    course(cx,cz,topW,topD,y+.19+levels*.32,C.roof,type,.19,4,false);
    for(const sx of [-1,1]) for(const sz of [-1,1]) {
      add(2,1,.19,cx+sx*(w/2-.6),y+.18,cz+sz*(d/2-.25),C.dark,type.startsWith('campus-')?'campus-upturned-eave':'upturned-eave',{rz:sx*.13});
    }
  }

  // A broad campus diorama: the tower stays on the central axis, four flanking
  // buildings sit on the landward side, and Furong Lake occupies the foreground.
  course(0,8,104,76,0,C.dark,'base',.28,8,false,8);
  course(0,-7,100,42,.28,C.shade,'base',.28,8,false,6);
  for(let x=-50;x<50;x+=5) for(let z=-28;z<14;z+=6) {
    const d=Math.min(6,14-z);
    add(5,d,.19,x+2.5,.56,z+d/2,
      (x+z)%3===0?'#c9c5b9':'#d9d5c9','plaza',{studs:false});
  }
  const shoreAt=x=>14+Math.max(0,Math.abs(x)-28)*.36;
  // Brick-tiled water changes gently in tone toward the near bank. A stepped
  // planted shore softens the rectangular display base without hiding the lake.
  // Keep overlapping landscape surfaces on distinct levels: inset lawn .66,
  // shore .72, plaza .75, inlaid paths .775, and promenade .80. Coplanar tops
  // otherwise compete in the depth buffer and flash white as the camera moves.
  for(let x=-50;x<50;x+=4) for(let z=14;z<44;z+=4) {
    const w=Math.min(4,50-x),d=Math.min(4,44-z),cx=x+w/2,cz=z+d/2;
    if(cz<shoreAt(cx)) {
      add(w,d,.38,cx,.28,cz,'#829462','lakeside-lawn',{shape:'plate',studs:false});
    } else {
      const shade=z>32?'#4f8991':(x+z)%12===0?'#699fa3':'#5b959c';
      add(w,d,.19,cx,.28,cz,shade,'lake',{studs:false});
    }
  }
  for(let x=-48;x<48;x+=4) {
    const z=shoreAt(x+2);
    add(4,1.2,.27,x+2,.45,z,C.light,'shoreline',{studs:false});
    add(4,2,.19,x+2,.61,z-1.6,C.trim,'promenade',{studs:false});
  }
  // Shallow granite steps lead down from the entrance square toward the water.
  for(let i=0;i<3;i++) add(18-i*2,1.2,.13,0,.56-i*.08,13.2+i*.9,C.light,'lakeside-stairs',{studs:false});
  for(const side of [-1,1]) {
    add(1,37,.19,side*16.6,.585,-6.5,C.light,'axial-path',{studs:false});
    add(14,1,.19,side*25,.585,10,C.light,'cross-path',{studs:false});
  }
  // Broken, muted strips suggest the tower's reflection, rather than a second
  // building on the lake. They remain ordinary, editable smooth tiles.
  for(let row=0;row<11;row++) {
    const z=16.7+row*1.65,w=8.5-row*.36,offset=Math.sin(row*2.3)*.6;
    add(w,.65,.04,offset,.475,z,row<2?'#8faeaa':'#739b9f','lake-reflection',{studs:false});
    if(row%2===0) add(2.6,.3,.04,offset+1.2,.48,z+.55,'#8eb0ad','lake-reflection',{studs:false});
  }
  for(const [x,z,len] of [[-29,24,5],[19,32,7],[-20,38,4],[32,25,3],[9,41,6],[-7,29,3]])
    add(len,.16,.04,x,.48,z,'#93b9b6','water-ripple',{studs:false});

  const ground=.75;
  // Podium walls: red granite body and pale, rusticated corner piers.
  for(let row=0;row<8;row++) {
    const y=ground+row*H;
    for(const side of [-1,1]) {
      band(side*9.25,0,7.5,16,y,row%3===0?C.shade:C.stone,H,'podium');
      for(const front of [-1,1]) course(side*7.5,front*8.3,6,1,y,row%2?C.red:'#a34c40','red-podium');
    }
    course(0,-7.5,11,1,y,C.stone,'podium');
  }
  archedGlass(0,8.6,ground,8,3.8,'entrance');
  // Columns and an entrance keystone articulate the projecting stone arch.
  for(const side of [-1,1]) for(let row=0;row<6;row++)
    add(1,1.5,H,side*4.7,ground+row*H,8.55,C.light,'entrance-pier');
  add(.7,1.2,.56,0,ground+3.8,8.8,C.light,'keystone');
  for(let i=0;i<5;i++) add(11+i*.8,1,.15,0,.75+i*.15,11.3-i*.7,C.trim,'stairs',{studs:false});
  band(0,0,27,17,ground+8*H,C.light,.28);
  band(0,0,25.5,15,ground+8*H+.28,C.shade,.19);

  const body=ground+8*H+.47;
  const floor=1.40, floors=14, bodyTop=body+floor*floors;
  // Main tower: staggered stone corners, a continuous turquoise middle stripe,
  // fourteen floors of individual windows, and slender facade mullions.
  for(let f=0;f<floors;f++) {
    const y=body+f*floor;
    for(const side of [-1,1]) {
      for(let row=0;row<3;row++) {
        // Four corner returns; small setbacks expose the slit windows.
        for(const face of [-1,1]) {
          add(4,2,floor/3,side*10,y+row*floor/3,face*6,C.stone,'tower-pier');
          add(1,1,floor/3,side*7.5,y+row*floor/3,face*6.5,C.stone,'tower-pier');
        }
      }
      for(const face of [-1,1]) {
        pane(side*9.5,face*7.07,y+.39,.55,.72,0,C.blue,'slit-window');
        add(.8,.5,.1,side*9.5,y+.29,face*7.10,C.shade,'window-sill',{studs:false});
      }
      // Side elevation is an inferred, repeating stone-and-glass bay system.
      for(let col=0;col<6;col++) {
        const z=-4.75+col*1.9;
        pane(side*12.05,z,y+.24,1.35,floor-.56,1,(col+f)%4===0?C.pale:C.blue);
        add(.44,1.75,.18,side*12.08,y+floor-.27,z,C.trim,'side-spandrel',{studs:false});
      }
      add(.8,12,.19,side*11.65,y+floor-.19,0,C.trim,'side-floor');
    }
    for(const face of [-1,1]) {
      for(let col=0;col<14;col++) {
        const x=col-6.5;
        // Shallow segmental arch in the top of the main curtain wall.
        const archTop=bodyTop-.22-1.45*(x/6.5)**2;
        const windowTop=Math.min(y+floor-.32,archTop);
        if(windowTop>y+.23) pane(x,face*6.82,y+.20,.90,windowTop-y-.20,0,
          Math.abs(x)<1?C.pale:(col+f)%5===0?C.blue:C.glass);
        if(archTop<y+floor) {
          const start=Math.max(y,archTop);
          add(1,1,y+floor-start,x,start,face*6.75,C.stone,'facade-arch-spandrel',{studs:false});
        }
      }
      add(14,.7,.16,0,y+floor-.25,face*6.91,C.shade,'floor-spandrel',{studs:false});
      add(14,.55,.10,0,y+.13,face*7.01,C.light,'window-transom',{studs:false});
    }
  }
  for(const side of [-1,1]) for(let col=0;col<=6;col++)
    add(.46,.35,floor*floors,side*12.12,body,-5.7+col*1.9,C.stone,'side-mullion',{studs:false});
  for(const face of [-1,1]) {
    for(let col=0;col<=14;col++) {
      const x=col-7;
      for(let f=0;f<floors;f+=2) {
        const y=body+f*floor,top=Math.min(y+floor*2-.05,bodyTop-.22-1.45*(x/7)**2);
        if(top>y) add(col===7?.26:.14,.50,top-y,x,
          y,face*7.06,col===7?C.pale:C.trim,'mullion',{studs:false});
      }
    }
    // A pale segmental stone arch above the tall window grid.
    for(let i=0;i<14;i++) {
      const x=i-6.5,y=bodyTop-.22-1.45*(x/6.5)**2;
      add(1,.9,.28,x,y,face*7.04,C.light,'curtain-arch',{studs:false,rz:-.55*x/6.5});
    }
  }
  band(0,0,25.5,15.5,bodyTop,C.light,.28);
  band(0,0,24.5,14.5,bodyTop+.28,C.shade,.19);

  // Stepped crown, twin red-roofed turrets and the signature arched blue window.
  const crown=bodyTop+.47;
  for(let row=0;row<9;row++) {
    const width=row<3?22:row<6?19:16;
    for(const face of [-1,1]) {
      const gap=7;
      for(const side of [-1,1]) course(side*(gap/2+(width-gap)/4),face*5.25,
        (width-gap)/2,1.5,crown+row*H,C.stone,'crown');
    }
    for(const side of [-1,1]) add(1.5,9,H,side*(width/2-.75),crown+row*H,0,C.stone,'crown-side');
  }
  for(const face of [-1,1]) {
    // Opaque stone above the curved opening prevents the rear arch showing
    // through the otherwise hollow crown when the model is viewed head-on.
    for(let col=-3;col<=3;col++) {
      const radius=3*U,top=crown+.55+3.5-radius+Math.sqrt(Math.max(0,radius*radius-(col*U)**2))+.12;
      add(1,1.25,crown+5.04-top,col,top,face*5.5,C.stone,'arch-spandrel',{studs:false});
    }
    archedGlass(0,face*6.12,crown+.55,6,3.5,'great-arch-window');
    for(const side of [-1,1]) {
      for(let row=0;row<3;row++) for(let col=0;col<3;col++)
        pane(side*(4.4+col*.9),face*6.17,crown+.3+row*.84,.65,.55,0,C.blue,'crown-window');
      // Square turret faces with a small round medallion, cornice and clerestory.
      for(let row=0;row<4;row++) add(4,2,H,side*9,crown+row*H,face*5.6,C.stone,'turret');
      add(1.15,1.15,.16,side*9,crown+1.2,face*6.75,C.shade,'medallion',{shape:'round',rx:Math.PI/2,studs:false});
      add(.72,.72,.12,side*9,crown+1.22,face*6.88,C.stone,'medallion',{shape:'round',rx:Math.PI/2,studs:false});
      band(side*9,face*4.7,4.8,4.6,crown+2.24,C.light,.19,'turret-cornice');
      for(let col=0;col<3;col++) pane(side*9+col-.99,face*7.01,crown+2.5,.65,.5,0,C.blue,'turret-window');
      roof(side*9,face*4.6,4.8,4.8,crown+3.12,3,'turret-roof');
    }
    for(const side of [-1,1]) for(let i=0;i<4;i++)
      add(.22,.45,.39,side*(3.7+i*.7),crown+4.22,face*6.12,C.dark,'crown-vent',{studs:false});
  }
  const neck=crown+5.04;
  for(let row=0;row<2;row++) band(0,0,17,11,neck+row*H,C.stone,H,'upper-wall');
  for(const face of [-1,1]) for(let col=0;col<13;col++) {
    pane(col-6,face*5.6,neck+.36,.65,.59,0,C.blue,'clerestory');
    add(.18,.45,.72,col-6.5,neck+.28,face*5.8,C.trim,'clerestory-frame',{studs:false});
  }
  band(0,0,19,13,neck+1.12,C.dark,.22,'main-eaves');
  // Broad, unmistakable Chinese red hip roof, tapering to a dark ridge pavilion.
  roof(0,0,19,13,neck+1.34,8,'main-roof');
  const ridge=neck+1.34+.19+8*.32+.19;
  for(const face of [-1,1]) {
    add(7,.7,.56,0,ridge,face*1.7,C.dark,'ridge-pavilion');
    for(let col=0;col<5;col++) pane(col-2,face*2.1,ridge+.12,.45,.28,0,C.shade,'ridge-window');
    for(let i=0;i<7;i++) add(1,.8,.19,i-3,ridge+.56+Math.pow(Math.abs(i-3)/3,2)*.35,
      face*2.0,C.dark,'ridge-eave',{studs:false,rz:(i-3)*.07});
  }
  add(.14,.14,2.5,0,ridge+.5,0,C.shade,'antenna',{studs:false});
  add(.65,.25,.72,0,neck+2,6.52,C.shade,'roof-plaque',{studs:false});

  // Four surrounding blocks interpret the campus wings visible in the source
  // photograph. The unseen plans and rear elevations are deliberately inferred.
  function campusBuilding(cx,cz,floors,outer=false) {
    const w=13,d=18,storey=1.56;
    band(cx,cz,w+1,d+1,ground,C.shade,.22,'campus-foundation');
    for(let f=0;f<floors;f++) {
      const y=ground+.22+f*storey;
      for(const sx of [-1,1]) for(const sz of [-1,1]) for(let row=0;row<2;row++)
        add(2,2,storey/2,cx+sx*(w/2-1),y+row*storey/2,cz+sz*(d/2-1),C.stone,'campus-corner');
      for(const face of [-1,1]) {
        for(const side of [-1,1]) add(4.5,1,storey,cx+side*2.25,y,cz+face*8.35,
          f===0?C.red:'#ba6957','campus-wall',{studs:false});
        for(let col=0;col<6;col++) pane(cx+(col-2.5)*1.6,cz+face*9.04,y+.35,.88,.83,0,
          (col+f)%4===0?C.pale:C.blue,'campus-window');
        add(10,.65,.19,cx,y+storey-.19,cz+face*9.10,C.light,'campus-floor',{studs:false});
        for(let col=0;col<=6;col++) add(.35,.45,storey,cx+(col-3)*1.6,y,cz+face*9.12,C.stone,'campus-pier',{studs:false});
      }
      for(const side of [-1,1]) {
        add(.7,14,storey,cx+side*6.1,y,cz,C.stone,'campus-side-wall',{studs:false});
        for(let col=0;col<7;col++) pane(cx+side*6.55,cz+(col-3)*2,y+.35,.85,.83,1,
          (col+f)%3===0?C.glass:C.blue,'campus-side-window');
        add(.6,16,.19,cx+side*6.55,y+storey-.19,cz,C.light,'campus-side-floor',{studs:false});
      }
    }
    const top=ground+.22+floors*storey;
    band(cx,cz,w+1,d+1,top,C.light,.28,'campus-cornice');
    // An arched front light set into a terracotta stepped gable is the
    // characteristic motif visible on the flanking buildings in the photo.
    const archBase=top+.28;
    for(let row=0;row<5;row++) {
      const span=row<3?13:row===3?11:9;
      for(const side of [-1,1]) add((span-6)/2,1,H,cx+side*(3+(span-6)/4),
        archBase+row*H,cz+8.55,C.red,'campus-gable');
      add(span,1,H,cx,archBase+row*H,cz-8.5,C.red,'campus-rear-gable');
    }
    archedGlass(cx,cz+9.12,archBase+.15,6,2.42,'campus-arch');
    for(let col=-3;col<=3;col++) {
      const r=3*U,topArc=archBase+.15+2.42-r+Math.sqrt(Math.max(0,r*r-(col*U)**2))+.14;
      if(topArc<archBase+2.8) add(1,1,archBase+2.8-topArc,cx+col,topArc,cz+8.55,C.red,'campus-arch-spandrel',{studs:false});
    }
    for(const side of [-1,1]) add(1,16,2.24,cx+side*5.9,archBase,cz,C.stone,'campus-upper-side');
    roof(cx,cz,outer?14:13.6,18.8,archBase+2.8,4,'campus-roof');
    for(const side of [-1,1]) add(1.2,1.2,.84,cx+side*5.5,archBase+3.3,cz+6.7,C.red,'campus-roof-finial');
  }
  for(const side of [-1,1]) {
    campusBuilding(side*25.5,-1,5);
    campusBuilding(side*42,-6,6,true);
    // Low colonnaded links leave the taller buildings visually separate.
    const cx=side*16.5;
    add(7,5,.19,cx,2.4,-7,C.red,'campus-link-roof');
    for(const dx of [-2.5,0,2.5]) add(.7,.7,1.65,cx+dx,ground,-4.9,C.light,'campus-colonnade');
    add(7,1,1.65,cx,ground,-9,C.stone,'campus-link-wall');
  }
  function tree(x,z,index=0) {
    const lift=index%3*.16;
    // Extend the foundation into the inset lawn; its visible top stays at .97.
    add(2.5,2.5,.31,x,.66,z,C.light,'tree-planter',{shape:'tile',studs:false});
    add(.5,.5,1.65+lift,x,.97,z,C.earth,'tree-trunk',{shape:'round',studs:false});
    add(3.7,3.2,.56,x,2.10+lift,z,index%4===0?'#8c9550':C.green,'tree-crown',{shape:'plate'});
    add(3,2.8,.56,x+.2,2.66+lift,z,C.leaf,'tree-crown',{shape:'plate'});
    add(2,2,.38,x-.15,3.22+lift,z,C.green,'tree-crown',{shape:'plate'});
  }
  for(const side of [-1,1]) {
    for(const [index,x] of [11,18,25,34,43,48].entries()) tree(side*x,shoreAt(x)-3.9,index);
    for(const [index,x] of [17,34,46].entries()) tree(side*x,-21,index+1);
    for(const x of [16,31,44]) {
      const z=shoreAt(x)-3.5;
      add(5,1.8,.31,side*x,.66,z,C.light,'planter',{shape:'tile',studs:false});
      add(4.5,1.3,.38,side*x,.97,z,C.green,'hedge',{shape:'plate'});
    }
    for(const x of [9,21,37]) {
      const z=shoreAt(x)-1.5;
      add(3,1,.19,side*x,1.05,z,C.earth,'bench');
      for(const offset of [-.9,.9]) add(.4,.7,.3,side*x+offset,.75,z,C.dark,'bench-leg',{studs:false});
    }
    for(const x of [6.8,30.5,46.5]) {
      const z=shoreAt(x)-1.6;
      add(.25,.25,1.6,side*x,.75,z,C.dark,'path-light',{studs:false});
      add(.65,.65,.19,side*x,2.35,z,C.light,'path-light',{studs:false});
    }
  }
  // Foreground swans echo the reference photograph and give the lake scale.
  for(const [x,z] of [[-7,32.5],[1,36]]) {
    add(2.2,1.4,.38,x,.48,z,'#353d3c','swan',{shape:'round',studs:false});
    add(.3,.35,1.15,x+.87,.65,z,'#353d3c','swan-neck',{studs:false,rz:-.18});
    add(.55,.45,.25,x+.65,1.66,z,'#353d3c','swan-head',{studs:false});
    add(.28,.25,.15,x+.29,1.6,z,'#b65542','swan-beak',{studs:false});
    add(3.6,.13,.04,x-.4,.48,z+1.1,'#93b9b6','water-ripple',{studs:false});
  }
  return pieces;
}
