/** Local connection settings. Saved credentials never travel back to the browser. */
export async function aiSettingsRequest(path, input, {signal} = {}) {
  let response;
  try {
    response = await fetch('/api/ai/'+path, {
      method: input === undefined ? 'GET' : 'POST',
      headers: {Accept:'application/json', ...(input === undefined ? {} : {'Content-Type':'application/json'})},
      credentials:'same-origin', cache:'no-store', signal,
      ...(input === undefined ? {} : {body:JSON.stringify(input)}),
    });
  } catch(error) {
    if(error.name === 'AbortError') throw error;
    throw new Error('暂时无法连接本地服务，请稍后重试。');
  }
  const body = await response.json().catch(()=>null);
  if(!response.ok) throw new Error(body?.error?.message || '设置暂时无法完成，请稍后重试。');
  if(!body || typeof body !== 'object') throw new Error('服务返回的数据无法识别。');
  return body;
}

export function initializeAiSettings({onSaved = ()=>{}} = {}) {
  const root=document.querySelector('#ai-settings'), trigger=root?.querySelector('#user-settings-button');
  if(!trigger) return;
  const panel=root.querySelector('#ai-settings-panel'), badge=panel.querySelector('#ai-settings-badge');
  const description=panel.querySelector('#ai-settings-description'), tabs=[...panel.querySelectorAll('[role=tab]')];
  let active=0, hoverTimer, leaveTimer, pointerInside=false;
  const profiles=[
    createProfile('ai', '', '#ai-settings-form'),
    createProfile('ai-image', 'image/', '#ai-image-settings-form'),
  ];

  function positionPanel() {
    if(panel.hidden) return;
    const rect=trigger.getBoundingClientRect(), viewport=window.visualViewport;
    const height=viewport?.height||innerHeight, offset=viewport?.offsetTop||0;
    const top=Math.min(Math.max(offset+12,rect.bottom+10),Math.max(offset+12,offset+height-180));
    // CSS also clamps during resize, before the next resize event is delivered.
    panel.style.left='auto';
    panel.style.right='clamp(12px, '+Math.max(12,innerWidth-rect.right)+'px, max(12px, calc(100vw - 412px)))';
    panel.style.top=top+'px';
    panel.style.maxHeight='min('+Math.max(120,offset+height-top-12)+'px, calc(100svh - '+top+'px - 12px))';
  }
  function updateBadge() {
    const current=profiles[active];
    badge.textContent=current.ready?'已配置':'待配置';badge.dataset.ready=String(current.ready);
  }
  function selectTab(index, {focus=false}={}) {
    active=index;
    tabs.forEach((tab,i)=>{tab.setAttribute('aria-selected',String(i===index));tab.tabIndex=i===index?0:-1;});
    profiles.forEach((profile,i)=>profile.form.hidden=i!==index);
    description.textContent=index===0?'根据参考图设计积木结构并检查模型。':'根据文字描述，或图片与修改想法，生成四视角参考图。';
    panel.scrollTop=0;updateBadge();positionPanel();profiles[index].load();
    if(focus) tabs[index].focus({preventScroll:true});
  }
  function createProfile(stem, apiPrefix, selector) {
    const form=panel.querySelector(selector), url=form.elements.baseUrl, key=form.elements.apiKey, model=form.elements.model;
    const refresh=form.querySelector('#'+stem+'-models-refresh'), save=form.querySelector('#'+stem+'-settings-save');
    const status=form.querySelector('#'+stem+'-settings-status');
    let loaded=false, loading=false, fetching=false, saving=false, hasApiKey=false, savedModel='', listValid=false;
    let modelsController=null;
    function message(text, type='') {status.textContent=text;status.dataset.type=type;}
    function controls() {
      url.disabled=loading||saving;key.disabled=loading||saving;
      model.disabled=loading||saving||fetching||!listValid;
      refresh.disabled=loading||saving||fetching||!url.value.trim()||(!key.value.trim()&&!hasApiKey);
      save.disabled=loading||saving||fetching||!listValid||!model.value;
      form.setAttribute('aria-busy',String(loading||saving||fetching));
    }
    function clearModels() {
      modelsController?.abort();modelsController=null;fetching=false;listValid=false;
      model.replaceChildren(new Option('先获取可用模型',''));refresh.textContent='获取模型';controls();
    }
    async function loadModels() {
      if(fetching||saving||!url.reportValidity()||(!key.value.trim()&&!hasApiKey)) return;
      const selected=model.value||savedModel;
      const controller=new AbortController();modelsController=controller;fetching=true;
      const timeout=setTimeout(()=>controller.abort('timeout'),35000);
      refresh.textContent='正在获取…';controls();message('正在从服务地址获取模型…');
      try {
        const result=await aiSettingsRequest(apiPrefix+'models',{baseUrl:url.value.trim(),apiKey:key.value.trim()},{signal:controller.signal});
        if(controller.signal.aborted) return;
        const ids=[...new Set((result.models||[]).map(item=>item.id).filter(id=>typeof id==='string'&&id))];
        if(!ids.length) throw new Error('此服务没有返回可用模型，请检查地址和密钥。');
        model.replaceChildren(new Option('请选择模型',''),...ids.map(id=>new Option(id,id)));
        if(ids.includes(selected)) model.value=selected;
        else if(ids.length===1) model.value=ids[0];
        listValid=true;
        message('已获取 '+ids.length+' 个模型'+(model.value?'':'，请选择后保存')+'。','success');
      } catch(error) {
        if(!controller.signal.aborted) message(error.message,'error');
        else if(controller.signal.reason==='timeout') message('获取模型超时，请检查网络后重试。','error');
      } finally {
        clearTimeout(timeout);
        if(modelsController===controller) {modelsController=null;fetching=false;refresh.textContent='获取模型';controls();}
      }
    }
    async function load() {
      if(loaded||loading) return;
      loading=true;controls();message('正在读取设置…');
      try {
        const current=await aiSettingsRequest(apiPrefix+'settings');
        url.value=current.baseUrl||'';hasApiKey=!!current.hasApiKey;savedModel=current.model||'';
        key.placeholder=hasApiKey?'已保存 · 留空继续使用':apiPrefix?'输入生图 API Key':'输入 API Key';
        // Preserve the current model if the provider cannot list models right now.
        model.replaceChildren(new Option(savedModel||'先获取可用模型',savedModel));listValid=!!savedModel;
        loaded=true;updateBadge();
      } catch(error) {message(error.message,'error');}
      finally {loading=false;controls();positionPanel();}
      if(loaded) {
        if(url.value&&hasApiKey) await loadModels();
        else message(apiPrefix?'填写独立的生图服务地址和密钥，获取模型。':'填写服务地址和密钥，获取可用模型。');
      }
    }
    for(const field of [url,key]) field.addEventListener('input',()=>{
      clearModels();message('地址或密钥已更改，请重新获取模型。');
    });
    model.addEventListener('change',controls);refresh.addEventListener('click',loadModels);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!listValid||!model.value||saving||fetching||!form.reportValidity())return;
      saving=true;controls();save.textContent='正在保存…';message('正在保存设置…');
      try {
        const result=await aiSettingsRequest(apiPrefix+'settings',{baseUrl:url.value.trim(),apiKey:key.value.trim(),model:model.value});
        url.value=result.baseUrl;savedModel=result.model;hasApiKey=!!result.hasApiKey;key.value='';
        key.placeholder='已保存 · 留空继续使用';updateBadge();
        message(apiPrefix?'生图设置已保存，下次生成将先创建四视角参考图。':'设置已保存，下次生成将使用所选建模模型。','success');
        await onSaved({...result,kind:apiPrefix?'image':'model'});
      } catch(error) {message(error.message,'error');}
      finally {saving=false;save.textContent='保存设置';controls();}
    });
    controls();
    return {form,load,get ready(){return !!savedModel&&hasApiKey},get saving(){return saving}};
  }
  function open() {
    clearTimeout(leaveTimer);clearTimeout(hoverTimer);
    panel.hidden=false;trigger.setAttribute('aria-expanded','true');positionPanel();profiles[active].load();
  }
  function close({restoreFocus=false}={}) {
    clearTimeout(hoverTimer);clearTimeout(leaveTimer);
    panel.hidden=true;trigger.setAttribute('aria-expanded','false');
    if(restoreFocus) trigger.focus({preventScroll:true});
  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener('click',()=>selectTab(index));
    tab.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      selectTab(next,{focus:true});
    });
  });
  root.addEventListener('pointerenter',event=>{
    if(event.pointerType==='touch') return;
    pointerInside=true;clearTimeout(leaveTimer);hoverTimer=setTimeout(open,120);
  });
  root.addEventListener('pointerleave',()=>{
    pointerInside=false;clearTimeout(hoverTimer);
    leaveTimer=setTimeout(()=>{if(!root.contains(document.activeElement)&&!profiles.some(p=>p.saving))close();},250);
  });
  trigger.addEventListener('click',()=>{if(panel.hidden)open();else close();});
  trigger.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();open();tabs[active].focus();}
  });
  root.addEventListener('focusout',()=>{
    setTimeout(()=>{if(!pointerInside&&!root.contains(document.activeElement)&&!profiles.some(p=>p.saving))close();},0);
  });
  root.addEventListener('keydown',event=>event.stopPropagation());
  panel.querySelector('#ai-settings-close').addEventListener('click',()=>close({restoreFocus:true}));
  document.addEventListener('pointerdown',event=>{if(!root.contains(event.target))close();});
  document.addEventListener('keydown',event=>{
    if(!panel.hidden&&event.key==='Escape') {
      event.preventDefault();event.stopPropagation();close({restoreFocus:root.contains(document.activeElement)});
    }
  },true);
  window.addEventListener('resize',positionPanel);
  window.visualViewport?.addEventListener('resize',positionPanel);
}
