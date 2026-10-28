const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const colors = {
  red:{name:'经典红',svg:'#c9362f',top:'#e45448',side:'#a32927'},
  green:{name:'青苹果',svg:'#8f9f51',top:'#acbb70',side:'#6f803e'},
  yellow:{name:'阳光黄',svg:'#e8b94d',top:'#f4d37c',side:'#bd912e'}
};
const steps = [
  {name:'稳固基座',detail:'先拼出小而稳的底部，让苹果站得住。'},
  {name:'向外延伸',detail:'扩大一圈，留意边缘交错的接缝。'},
  {name:'初见轮廓',detail:'继续向外铺开，苹果的曲线开始出现。'},
  {name:'饱满一点',detail:'用交错排列的积木，让果实更牢固。'},
  {name:'慢慢长大',detail:'沿轮廓拼满这一层，让苹果饱满起来。'},
  {name:'最宽一层',detail:'来到果实最宽的地方，轮廓更圆润。'},
  {name:'收拢边缘',detail:'从这里开始向内收，形成圆润肩部。'},
  {name:'圆润肩部',detail:'再向里收一圈，保留顶部的弧度。'},
  {name:'顶部收口',detail:'围合顶部，中间留出安装枝干的空位。'},
  {name:'点睛枝叶',detail:'装上棕色枝干与绿色叶片，苹果完成！'}
];
let state = {color:'red',step:null,exploded:false,rotating:false,playing:false,ready:false};
let model,blueprint=[],inventory=[],autoTimer,completionTimer,toastTimer;
let saveInFlight=false;
try {
  const saved=JSON.parse(localStorage.getItem('brick-apple-save'));
  if(saved && colors[saved.color])state.color=saved.color;
}catch{}

function icon(name) {return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`}
function brickSVG(type='body',w=2,d=2,layer=1,thumbnail=false) {
  const palette=type==='leaf'?{svg:'#869b56',top:'#b0bf88',side:'#667d42'}:type==='stem'?{svg:'#a38561',top:'#bda382',side:'#80633f'}:colors[state.color];
  if(!thumbnail) {
    const scale=Math.min(11,42/(w+d)),height=type==='leaf'?5:10;
    const project=(x,z)=>[32+(x-z)*scale,27+(x+z)*scale*.48];
    const a=project(-w/2,-d/2),b=project(w/2,-d/2),c=project(w/2,d/2),e=project(-w/2,d/2);
    const lower=p=>[p[0],p[1]+height];
    const polygon=(points,fill)=>`<polygon points="${points.map(p=>p.join(',')).join(' ')}" fill="${fill}"/>`;
    let drawing=polygon([e,c,lower(c),lower(e)],palette.svg)+polygon([c,b,lower(b),lower(c)],palette.side)+polygon([a,b,c,e],palette.top);
    for(let x=0;x<w;x++)for(let z=0;z<d;z++) {
      const [sx,sy]=project(x-(w-1)/2,z-(d-1)/2);
      drawing+=`<ellipse cx="${sx}" cy="${sy}" rx="${scale*.29}" ry="${scale*.16}" fill="${palette.svg}"/><ellipse cx="${sx}" cy="${sy-1.8}" rx="${scale*.29}" ry="${scale*.16}" fill="${palette.top}"/>`;
    }
    return `<svg viewBox="0 0 64 56" aria-hidden="true">${drawing}</svg>`;
  }
  const levels=thumbnail?Math.min(4,Math.ceil(layer/2.6)):1;
  const size=thumbnail?Math.max(12,Math.min(21,12+Math.sin(layer/10*Math.PI)*9)):22;
  const cx=32,baseY=thumbnail?47:42;
  let shapes='';
  for(let n=0;n<levels;n++) {
    const y=baseY-n*6;
    const sw=size*(n===levels-1&&layer>7?.82:1);
    shapes+=`<path d="M${cx-sw},${y-9}l${sw},-10 ${sw},10 -${sw},10Z" fill="${palette.top}"/><path d="M${cx-sw},${y-9}l${sw},10v9l-${sw},-10Z" fill="${palette.svg}"/><path d="M${cx},${y+1}l${sw},-10v9l-${sw},10Z" fill="${palette.side}"/>`;
    for(const [dx,dz] of [[-5,-5],[5,-5],[-5,3],[5,3]])shapes+=`<ellipse cx="${cx+dx}" cy="${y-9+dz*.5}" rx="3.3" ry="1.8" fill="${palette.svg}"/><ellipse cx="${cx+dx}" cy="${y-10.7+dz*.5}" rx="3.3" ry="1.8" fill="${palette.top}"/>`;
  }
  if(thumbnail&&layer===10) shapes+='<path d="M31 18v-6h4v9" stroke="#957347" stroke-width="3" fill="none"/><path d="m33 12 12-6 7 3-12 7Z" fill="#809b4e"/>';
  return `<svg class="${thumbnail?'step-mini':''}" viewBox="0 0 64 64" aria-hidden="true">${shapes}</svg>`;
}
function renderSteps() {
  $('#steps').innerHTML=steps.map((s,i)=>{
    const n=i+1,done=state.step!==null&&n<state.step,current=state.step===n;
    return `<button class="step${done?' done':''}${current?' current':''}" data-step="${n}" aria-label="第 ${n} 步：${s.name}" ${current?'aria-current="step"':''}>${brickSVG(n===10?'leaf':'body',2,2,n,true)}<span class="step-label"><span class="step-index">${String(n).padStart(2,'0')}</span><span class="step-name">${s.name}</span></span>${done?'<svg class="icon step-check"><use href="#i-check"/></svg>':''}</button>`;
  }).join('');
  $('#steps').querySelectorAll('[data-step]').forEach(button=>button.addEventListener('click',()=>{stopAuto();setStep(Number(button.dataset.step))}));
}
function renderGuide() {
  $('#guide-list').innerHTML=steps.map((s,i)=>`<button class="guide-step" data-guide-step="${i+1}">${brickSVG(i===9?'leaf':'body',2,2,i+1,true)}<div><strong>${String(i+1).padStart(2,'0')} / ${s.name}</strong><span>${s.detail}</span></div></button>`).join('');
  $$('#guide-list [data-guide-step]').forEach(button=>button.addEventListener('click',()=>{
    $('#guide-dialog').close();stopAuto();setStep(Number(button.dataset.guideStep));
    if(innerWidth<641)$('#stage').scrollIntoView({behavior:motionBehavior(),block:'center'});
  }));
}
function renderInventory() {
  $('#parts-total').textContent=blueprint.length;
  $('#parts-list').innerHTML=inventory.map(item=>{
    const name=item.type==='body'?`${colors[state.color].name}标准积木`:item.type==='stem'?'棕色枝干积木':'绿色叶片薄板';
    return `<div class="parts-row">${brickSVG(item.type,item.w,item.d)}<div class="parts-row-name"><strong>${name}</strong><span>${item.dims} 凸点${item.type==='leaf'?' · 薄板':''}</span></div><span class="parts-row-count">× <b>${item.count}</b></span></div>`;
  }).join('');
}
function syncUI() {
  const step=state.step;
  $('#step-count').textContent=step===null?'准备就绪':`${String(step).padStart(2,'0')} / 10${step===10?' · 已完成':''}`;
  $('#step-count').classList.toggle('in-progress',step!==null);
  $('#preview-label').textContent=step===null?'3D 实时预览':step===10?'我的苹果 · 已完成':`正在搭建 · 第 ${step} 步`;
  $('#build-status').textContent=step===null?'从稳固的基座，到最后一片绿叶。':step===0?'准备好了，让第一块积木落下。':step===10?'完成了！每一块积木，都有你的创意。':steps[step-1].detail;
  $('#build-button span').textContent=step===null||step===0?'开始搭建':step===10?'再搭一次':step===9?'加上枝叶':'搭建下一层';
  $('#build-button .arrow use').setAttribute('href',step===10?'#i-arrow-counter-clockwise':'#i-arrow-up-right');
  $('#auto-button span').textContent=state.playing?'暂停演示':step>0&&step<10?'继续演示':'自动演示';
  $('#auto-button .icon use').setAttribute('href',state.playing?'#i-pause':'#i-play');
  $('#auto-button').classList.toggle('playing',state.playing);
  $('#reset-build').hidden=step===null;
  $('#stage').dataset.step=step===null?'preview':step;
  $('#stage').dataset.color=state.color;
  $('#stage').dataset.exploded=state.exploded;
  $('#color-name').textContent=colors[state.color].name;
  $$('.color-swatch').forEach(button=>{const yes=button.dataset.color===state.color;button.classList.toggle('selected',yes);button.setAttribute('aria-pressed',yes)});
  $('#explode-button').setAttribute('aria-pressed',state.exploded);
  $('#explode-button').setAttribute('aria-label',state.exploded?'合拢模型':'拆分模型');
  $('#explode-button').dataset.tooltip=state.exploded?'合拢模型':'拆分模型';
  $('#view-toggle span').textContent=state.exploded?'分层视图':'完整模型';
  $('#view-toggle').setAttribute('aria-label',state.exploded?'切换为完整模型':'切换为分层视图');
  $('#rotate-button').setAttribute('aria-pressed',state.rotating);
  $('#rotate-button').setAttribute('aria-label',state.rotating?'停止旋转':'自动旋转');
  $('#rotate-button').dataset.tooltip=state.rotating?'停止旋转':'自动旋转';
  renderSteps();
}
function setStep(step,complete=true) {
  if(!state.ready)return;
  clearTimeout(completionTimer);
  state.step=Math.max(0,Math.min(10,step));
  model.setStep(state.step);syncUI();
  if(innerWidth<901){const active=$(`#steps [data-step="${state.step}"]`);if(active){const holder=$('#steps');holder.scrollTo({left:active.offsetLeft-holder.offsetLeft-20,behavior:motionBehavior()})}}
  if(state.step===10&&complete) {
    stopAuto();
    completionTimer=setTimeout(()=>{$('#complete-dialog').showModal()},900);
  }
}
function stopAuto(){clearTimeout(autoTimer);state.playing=false;syncUI()}
function autoTick() {
  if(!state.playing)return;
  const next=(state.step??0)+1;
  setStep(next);
  if(next<10)autoTimer=setTimeout(autoTick,1250);
}
function toggleAuto() {
  if(!state.ready)return;
  if(state.playing){stopAuto();return}
  clearTimeout(completionTimer);
  if(state.step===null||state.step===10)setStep(0,false);
  state.playing=true;syncUI();
  autoTimer=setTimeout(autoTick,state.step===0?280:500);
}
function toggleExploded(){if(!state.ready)return;state.exploded=!state.exploded;model.setExploded(state.exploded);syncUI()}
function toast(message) {
  clearTimeout(toastTimer);$('#toast span').textContent=message;$('#toast').classList.add('show');
  toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3100);
}
function motionBehavior(){return matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'}
function download(blob,name){const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=name;document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),5000)}
async function saveArtwork() {
  if(!state.ready||saveInFlight)return;
  saveInFlight=true;const saveButton=$('#save-button');saveButton.disabled=true;
  try {
    try{localStorage.setItem('brick-apple-save',JSON.stringify({color:state.color,step:state.step,savedAt:Date.now()}))}catch{}
    const blob=await model.snapshot();
    if(!blob)throw new Error('Empty export');
    download(blob,`brick-apple-${state.color}.png`);
    toast('作品已保存为图片，留住这一点小美好。');
  }catch{toast('图片暂时无法导出，请稍后再试。')}
  finally{saveInFlight=false;saveButton.disabled=false}
}

$$('[data-action]').forEach(button=>button.addEventListener('click',()=>{
  const action=button.dataset.action;
  if(action==='parts'){stopAuto();clearTimeout(completionTimer);$('#parts-dialog').showModal();return}
  if(action==='guide'){stopAuto();clearTimeout(completionTimer);$('#guide-dialog').showModal();return}
  document.querySelector('main').scrollIntoView({behavior:motionBehavior()});
}));
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close()}}));
$$('.color-swatch').forEach(button=>button.addEventListener('click',()=>{
  state.color=button.dataset.color;model?.setColor(state.color);syncUI();renderInventory();renderGuide();
}));
$('#build-button').addEventListener('click',()=>{if(!state.ready)return;stopAuto();setStep(state.step===null||state.step===10?1:state.step+1)});
$('#auto-button').addEventListener('click',toggleAuto);
$('#explode-button').addEventListener('click',toggleExploded);
$('#view-toggle').addEventListener('click',toggleExploded);
$('#rotate-button').addEventListener('click',()=>{if(!state.ready)return;state.rotating=!state.rotating;model.setAutoRotate(state.rotating);syncUI()});
$('#zoom-in').addEventListener('click',()=>model?.zoomBy(1.13));
$('#zoom-out').addEventListener('click',()=>model?.zoomBy(1/1.13));
$('#reset-view').addEventListener('click',()=>{model?.resetView();toast('视角已复位，重新看看你的苹果。')});
$('#reset-build').addEventListener('click',()=>{
  stopAuto();clearTimeout(completionTimer);state.step=null;state.exploded=false;model.setStep(null);model.setExploded(false);syncUI();
});
$('#save-button').addEventListener('click',saveArtwork);
$('#complete-save').addEventListener('click',()=>{$('#complete-dialog').close();saveArtwork()});
$('#download-parts').addEventListener('click',()=>{
  const rows=[['brick. 一颗苹果 · 零件清单'],['配色',colors[state.color].name],['零件类型','尺寸（凸点）','数量'],...inventory.map(i=>[i.type==='body'?`${colors[state.color].name}标准积木`:i.type==='stem'?'棕色枝干积木':'绿色叶片薄板',i.dims,i.count]),['总计','',blueprint.length]];
  const csv='\uFEFF'+rows.map(row=>row.map(v=>`"${String(v).replaceAll('"','""')}"`).join(',')).join('\r\n');
  download(new Blob([csv],{type:'text/csv;charset=utf-8'}),'brick-apple-parts.csv');toast('零件清单已下载。准备好，就开搭吧。');
});
window.addEventListener('keydown',e=>{
  if(!state.ready||document.querySelector('dialog[open]')||e.metaKey||e.ctrlKey||e.altKey)return;
  if(e.key==='ArrowRight'){e.preventDefault();stopAuto();setStep(state.step===null?1:Math.min(10,state.step+1))}
  if(e.key==='ArrowLeft'){e.preventDefault();stopAuto();setStep(state.step===null?9:Math.max(1,state.step-1),false)}
});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&state.playing)stopAuto()});
renderSteps();renderGuide();syncUI();
$('#build-button').disabled=true;$('#auto-button').disabled=true;$('#save-button').disabled=true;

try {
  const module=await import('./model.js');
  blueprint=module.BLUEPRINT;inventory=module.getInventory();
  $('#total-pieces').textContent=blueprint.length;$('#complete-total').textContent=blueprint.length;
  renderInventory();
  model=await module.createModel($('#scene'),{
    onPieceClick:piece=>{
      const type=piece.type==='body'?`${colors[state.color].name}积木`:piece.type==='leaf'?'绿色叶片':'棕色枝干';
      toast(`${type} ${piece.w} × ${piece.d} · 第 ${piece.layer} 步`);
    },
    onReady:()=>{$('#loading-state').classList.add('loaded');$('#loading-state').setAttribute('aria-hidden','true');$('#scene').setAttribute('aria-busy','false')}
  });
  model.setColor(state.color);state.ready=true;
  $('#build-button').disabled=false;$('#auto-button').disabled=false;$('#save-button').disabled=false;
  $('#stage').dataset.ready='true';
  // A stable read-only view is useful for lightweight interface verification.
  window.brickStudio={get state(){return {...state,model:model.state}},get inventory(){return inventory.map(p=>({...p}))}};
}catch(error) {
  console.error('The model could not start:',error);
  $('#loading-state').classList.add('error');
  $('#loading-state').innerHTML=`${icon('cube')}<span>3D 工作台暂时未能加载</span><button id="retry-scene">点击重新加载</button><span style="font-size:10px">请使用支持 WebGL 的浏览器</span>`;
  $('#retry-scene').addEventListener('click',()=>location.reload());
  $('#stage').dataset.ready='error';
}
window.addEventListener('pagehide',event=>{clearTimeout(autoTimer);clearTimeout(completionTimer);if(!event.persisted)model?.dispose()});
