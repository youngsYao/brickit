/** The model service is configured locally; API keys are never stored in the browser. */
export async function aiSettingsRequest(path, input, {signal} = {}) {
  let response;
  try {
    response = await fetch(`/api/ai/${path}`, {
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
  const panel=root.querySelector('#ai-settings-panel'), form=panel.querySelector('form');
  const url=form.elements.baseUrl, key=form.elements.apiKey, model=form.elements.model;
  const refresh=panel.querySelector('#ai-models-refresh'), save=panel.querySelector('#ai-settings-save');
  const status=panel.querySelector('#ai-settings-status'), badge=panel.querySelector('#ai-settings-badge');
  let loaded=false, loading=false, saving=false, hasApiKey=false, savedModel='', listValid=false;
  let modelsController=null, hoverTimer, leaveTimer, pointerInside=false;

  function message(text, type='') {status.textContent=text;status.dataset.type=type;}
  function controls() {
    url.disabled=loading||saving;key.disabled=loading||saving;
    model.disabled=loading||saving||!listValid;
    refresh.disabled=loading||saving||!url.value.trim()||(!key.value.trim()&&!hasApiKey);
    save.disabled=loading||saving||!listValid||!model.value;
    form.setAttribute('aria-busy',String(loading||saving));
  }
  function clearModels() {
    modelsController?.abort();modelsController=null;listValid=false;
    model.replaceChildren(new Option('先获取可用模型',''));controls();
  }
  async function loadModels() {
    if(!url.reportValidity()||(!key.value.trim()&&!hasApiKey)) return;
    const selected=model.value||savedModel;
    clearModels();
    const controller=new AbortController();modelsController=controller;
    refresh.disabled=true;refresh.textContent='正在获取…';
    message('正在从服务地址获取模型…');
    try {
      const result=await aiSettingsRequest('models',{baseUrl:url.value.trim(),apiKey:key.value.trim()},{signal:controller.signal});
      if(controller.signal.aborted) return;
      const ids=[...new Set((result.models||[]).map(item=>item.id).filter(id=>typeof id==='string'&&id))];
      if(!ids.length) throw new Error('此服务没有返回可用模型，请检查地址和密钥。');
      model.replaceChildren(new Option('请选择模型',''),...ids.map(id=>new Option(id,id)));
      if(ids.includes(selected)) model.value=selected;
      else if(ids.length===1) model.value=ids[0];
      listValid=true;
      message(`已获取 ${ids.length} 个模型${model.value?'':'，请选择后保存'}。`,'success');
    } catch(error) {
      if(error.name!=='AbortError') message(error.message,'error');
    } finally {
      if(modelsController===controller) {
        modelsController=null;refresh.textContent='获取模型';controls();
      }
    }
  }
  async function loadSettings() {
    if(loaded||loading) return;
    loading=true;controls();message('正在读取设置…');
    try {
      const current=await aiSettingsRequest('settings');
      url.value=current.baseUrl||'';hasApiKey=!!current.hasApiKey;savedModel=current.model||'';
      key.placeholder=hasApiKey?'已保存 · 留空继续使用':'输入 API Key';
      badge.textContent=savedModel?'已配置':'待配置';
      badge.dataset.ready=String(!!savedModel&&hasApiKey);
      loaded=true;
    } catch(error) {message(error.message,'error');}
    finally {loading=false;controls();}
    if(loaded) {
      if(url.value&&hasApiKey) await loadModels();
      else message('填写服务地址和密钥，获取可用模型。');
    }
  }
  function open() {
    clearTimeout(leaveTimer);clearTimeout(hoverTimer);
    panel.hidden=false;trigger.setAttribute('aria-expanded','true');loadSettings();
  }
  function close({restoreFocus=false}={}) {
    clearTimeout(hoverTimer);clearTimeout(leaveTimer);
    panel.hidden=true;trigger.setAttribute('aria-expanded','false');
    if(restoreFocus) trigger.focus({preventScroll:true});
  }
  root.addEventListener('pointerenter',event=>{
    if(event.pointerType==='touch') return;
    pointerInside=true;clearTimeout(leaveTimer);hoverTimer=setTimeout(open,120);
  });
  root.addEventListener('pointerleave',()=>{
    pointerInside=false;clearTimeout(hoverTimer);
    leaveTimer=setTimeout(()=>{if(!root.contains(document.activeElement)&&!saving)close();},250);
  });
  trigger.addEventListener('click',()=>{if(panel.hidden)open();else close();});
  trigger.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'){event.preventDefault();open();panel.querySelector('button').focus();}
  });
  root.addEventListener('focusout',()=>{
    setTimeout(()=>{if(!pointerInside&&!root.contains(document.activeElement)&&!saving)close();},0);
  });
  panel.addEventListener('keydown',event=>{
    event.stopPropagation();
    if(event.key==='Escape'){event.preventDefault();close({restoreFocus:true});}
  });
  trigger.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
  root.addEventListener('keydown',event=>event.stopPropagation());
  panel.querySelector('#ai-settings-close').addEventListener('click',()=>close({restoreFocus:true}));
  document.addEventListener('pointerdown',event=>{if(!root.contains(event.target))close();});
  document.addEventListener('keydown',event=>{
    if(!panel.hidden&&event.key==='Escape') {
      event.preventDefault();event.stopPropagation();
      close({restoreFocus:root.contains(document.activeElement)});
    }
  },true);
  for(const field of [url,key]) field.addEventListener('input',()=>{
    clearModels();refresh.textContent='获取模型';message('地址或密钥已更改，请重新获取模型。');
  });
  model.addEventListener('change',controls);
  refresh.addEventListener('click',loadModels);
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(!listValid||!model.value||saving||!form.reportValidity())return;
    saving=true;controls();save.textContent='正在保存…';message('正在保存设置…');
    try {
      const result=await aiSettingsRequest('settings',{baseUrl:url.value.trim(),apiKey:key.value.trim(),model:model.value});
      url.value=result.baseUrl;savedModel=result.model;hasApiKey=!!result.hasApiKey;key.value='';
      key.placeholder='已保存 · 留空继续使用';badge.textContent='已配置';badge.dataset.ready='true';
      message('设置已保存，下次生成将使用所选模型。','success');
      await onSaved(result);
    } catch(error) {message(error.message,'error');}
    finally {saving=false;save.textContent='保存设置';controls();}
  });
  controls();
}
