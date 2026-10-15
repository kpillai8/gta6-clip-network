/* Local MVP dashboard. All third-party text is escaped before interpolation. */
const state = { token: sessionStorage.getItem('clipline_token') || '', tab:'overview', filter:'pending_review', creators:[], clips:[], assets:[], jobs:[], publications:[], overview:{} };
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const h = s => String(s ?? '').replace(/[&<>"']/g, x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const fmt = n => String(Number(n ?? 0).toLocaleString('en-US'));
const niceStatus = s => (s||'unknown').replaceAll('_',' ');
const stamp = s => { if(!s)return '';const d=new Date(s);return Number.isNaN(+d)?'':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});};
const toast = (msg,error=false) => { const el=$('#toast');el.textContent=msg;el.className=error?'error':'';el.style.display='block';clearTimeout(toast.timeout);toast.timeout=setTimeout(()=>el.style.display='none',4300); };
async function api(path,options={}) {
  const headers={ Authorization:`Bearer ${state.token}`, ...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}), ...(options.headers||{})};
  let response;
  try { response=await fetch('/api'+path,{...options,headers}); } catch(e){throw new Error('Cannot connect to backend: '+e.message)}
  if(response.status===401){showLogin();throw new Error('Administrator token is invalid or expired');}
  if(!response.ok){let detail='Request failed';try{const data=await response.json();detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail);}catch{}throw new Error(detail);}
  return response.json();
}
function showLogin(){state.token='';sessionStorage.removeItem('clipline_token');$('#auth-screen').classList.remove('hidden');$('#app').classList.add('hidden');}
function showApp(){$('#auth-screen').classList.add('hidden');$('#app').classList.remove('hidden');}
async function refresh(){
  try {
    const [overview,creators,clips,assets,jobs,pubs]=await Promise.all([api('/overview'),api('/creators'),api('/clips'),api('/assets'),api('/jobs'),api('/publications')]);
    Object.assign(state,{overview,creators,clips,assets,jobs,publications:pubs});
    renderAll();
  }catch(e){toast(e.message,true)}
}
function nav(tab){
  state.tab=tab;
  $$('.page').forEach(p=>p.classList.add('hidden'));
  $(`#page-${tab}`).classList.remove('hidden');
  $$('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
  $('#breadcrumb').textContent=({overview:'Overview',review:'Review queue',creators:'Creators',sources:'Source library',exports:'Exports'})[tab];
  window.scrollTo({top:0,behavior:'smooth'});
}
function renderAll(){
  const o=state.overview;
  $('#metric-creators').textContent=fmt(o.creators);$('#metric-live').textContent=fmt(o.live);
  $('#metric-review').textContent=fmt(o.pending_review);$('#metric-approved').textContent=fmt(o.approved);
  $('#review-badge').textContent=fmt(o.pending_review);
  $('#integrations').innerHTML=[
    ['TW','Twitch monitor',o.twitch_configured?'Credentials configured':'Add API credentials',o.twitch_configured],
    ['YT','YouTube monitor',o.youtube_configured?'API key configured':'Add API key',o.youtube_configured],
    ['AI','Multimodal AI',o.ai_configured?'Frame analysis + transcripts enabled':'Audio heuristic mode; add OPENAI_API_KEY',o.ai_configured],
    ['↗','Social publishing','Manual export only',false]
  ].map(([icon,name,status,on])=>`<div class="integration"><div class="int-icon">${h(icon)}</div><div class="integration-main"><strong>${h(name)}</strong><small>${h(status)}</small></div><span class="connection ${on?'ok':''}">${on?'READY':'NOT CONNECTED'}</span></div>`).join('');
  const top=state.creators.slice(0,5);
  $('#overview-creators').innerHTML=top.length?top.map(c=>`<div class="creator-tile"><div class="tile-initial">${h(c.name.slice(0,2).toUpperCase())}</div><strong>${h(c.name)}</strong><small><span class="state-dot ${c.status==='live'?'live':''}"></span>${h(niceStatus(c.status))}</small></div>`).join(''):'<div class="helper">Add creators to start monitoring.</div>';
  $('#source-creator').innerHTML='<option value="">Unassigned / self-owned</option>'+state.creators.map(c=>`<option value="${h(c.id)}">${h(c.name)}</option>`).join('');
  renderCreators();renderReview();renderSources();renderExports();
}
function renderCreators(){
  $('#creator-list').innerHTML=state.creators.length?state.creators.map(c=>`<div class="creator-row" data-creator="${h(c.id)}"><div><div class="name">${h(c.name)}</div><small><span class="state-dot ${c.status==='live'?'live':''}"></span>${h(niceStatus(c.status))}${c.streams.some(s=>s.gta_related&&s.is_live)?' · GTA 6 metadata':''}</small></div><label>Twitch login<input class="login-input" value="${h(c.twitch_login||'')}" placeholder="Twitch username"></label><label>YouTube channel ID<input class="youtube-input" value="${h(c.youtube_channel_id||'')}" placeholder="UC..."></label><button class="button secondary save-creator" data-id="${h(c.id)}">Save</button></div>`).join(''):'<div class="empty">No creators yet.</div>';
  $$('.save-creator').forEach(b=>b.addEventListener('click',async()=>{
    const row=b.closest('.creator-row');
    try{await api('/creators/'+b.dataset.id,{method:'PATCH',body:JSON.stringify({twitch_login:row.querySelector('.login-input').value,youtube_channel_id:row.querySelector('.youtube-input').value})});toast('Creator channel settings saved');await refresh();}catch(e){toast(e.message,true)}
  }));
}
let videoUrls=[];
function clearVideoUrls(){videoUrls.forEach(x=>URL.revokeObjectURL(x));videoUrls=[];}
async function showVideo(id,button){
  button.disabled=true;button.textContent='Loading preview…';
  try{
    const response=await fetch('/api/clips/'+id+'/media',{headers:{Authorization:'Bearer '+state.token}});
    if(!response.ok)throw new Error('Video unavailable or rights invalid');
    const url=URL.createObjectURL(await response.blob());videoUrls.push(url);
    const video=document.createElement('video');video.src=url;video.controls=true;video.playsInline=true;video.preload='metadata';
    button.parentElement.replaceChildren(video);video.play().catch(()=>{});
  }catch(e){button.disabled=false;button.textContent='Load preview';toast(e.message,true)}
}
function renderReview(){
  clearVideoUrls();
  const clips=state.clips.filter(c=>state.filter==='all'||c.status===state.filter);
  $$('.filter').forEach(b=>b.classList.toggle('active',b.dataset.clipFilter===state.filter));
  $('#review-content').innerHTML=clips.length?clips.map(c=>{
    const canReview=c.status==='pending_review';const rights=c.allowed_platforms||[];
    return `<article class="clip-card" data-clip="${h(c.id)}"><div class="clip-media">${c.ready?`<button class="load-video" data-id="${h(c.id)}">▶ Load video preview</button>`:'<div class="helper">Render is processing or unavailable</div>'}</div><div class="clip-body"><div class="clip-meta"><span>${h(c.creator_name)}</span><span class="clip-score">${h(c.score)}/100</span><span>${h((c.end_seconds-c.start_seconds).toFixed(1))} sec</span></div><div class="clip-title">${h(c.title)}</div><div class="clip-meta"><span>${h(c.method==='audio_heuristic'?'Audio heuristic (not AI vision)':c.method)}</span><span>${h(niceStatus(c.status))}</span></div><p class="clip-caption">${h(c.caption)}</p><div class="clip-rights">Rights declared: ${h(c.rights_holder)} · ${h(c.rights_basis)}<div>Destinations: ${h(rights.join(', ')||'NONE — BLOCKED')}</div></div><div class="clip-actions">${canReview?`<button class="button primary approve" data-id="${h(c.id)}">✓ Approve</button><button class="button danger reject" data-id="${h(c.id)}">✕ Reject</button><button class="button ghost revise" data-id="${h(c.id)}">↻ Revise</button><button class="button ghost edit-caption" data-id="${h(c.id)}">✎ Caption</button>`:`<span class="tag ${c.status==='approved'?'completed':''}">${h(niceStatus(c.status))}</span>`}${c.ready?`<button class="button secondary download" data-id="${h(c.id)}">↓ Export</button>`:''}</div><p class="helper">${h(c.rationale)}</p></div></article>`;
  }).join(''):`<div class="empty"><div class="empty-icon">◫</div><strong>No clips in this view</strong><p>Upload an authorized source and run the processing worker to generate reviewable cuts.</p><button class="button primary" data-go="sources">Add source footage</button></div>`;
  $$('.load-video').forEach(b=>b.addEventListener('click',()=>showVideo(b.dataset.id,b)));
  $$('.clip-card').forEach(async card=>{
    const id=card.dataset.clip;
    if(!state.clips.find(c=>c.id===id)?.ready)return;
    try{
      const response=await fetch('/api/clips/'+id+'/media?kind=poster',{headers:{Authorization:'Bearer '+state.token}});
      if(response.ok){const url=URL.createObjectURL(await response.blob());videoUrls.push(url);card.querySelector('.clip-media').style.background=`linear-gradient(#0005,#0008),url('${url}') center/contain no-repeat #090a0d`;}
    }catch{}
  });
  $$('.edit-caption').forEach(b=>b.addEventListener('click',async()=>{
    const c=state.clips.find(x=>x.id===b.dataset.id);
    const caption=prompt('Edit the posting caption (not burned-in on-video text):',c.caption);
    if(caption===null)return;
    try{await api('/clips/'+c.id+'/caption',{method:'PATCH',body:JSON.stringify({caption})});toast('Caption saved');await refresh();}catch(e){toast(e.message,true)}
  }));
  $$('.approve').forEach(b=>b.addEventListener('click',async()=>{
    const c=state.clips.find(x=>x.id===b.dataset.id);const allowed=c.allowed_platforms;
    if(!allowed?.length){toast('Rights invalid or expired',true);return;}
    if(!confirm(`Approve this clip for manual export to ${allowed.join(' and ')}? This does not auto-post.`))return;
    try{await api('/clips/'+c.id+'/approve',{method:'POST',body:JSON.stringify({platforms:allowed})});toast('Approved. Added to export center.');await refresh();}catch(e){toast(e.message,true)}
  }));
  $$('.reject').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('Reject this clip?'))return;try{await api('/clips/'+b.dataset.id+'/reject',{method:'POST'});toast('Clip rejected');await refresh();}catch(e){toast(e.message,true)}}));
  $$('.revise').forEach(b=>b.addEventListener('click',async()=>{const note=prompt('Describe how AI should revise this cut. An API key is required and your approval will be requested again.');if(!note)return;try{await api('/clips/'+b.dataset.id+'/revision',{method:'POST',body:JSON.stringify({note})});toast('AI revision queued. It will return to review after rendering.');await refresh();}catch(e){toast(e.message,true)}}));
  $$('.download').forEach(b=>b.addEventListener('click',()=>downloadClip(b.dataset.id)));
  $('#review-content [data-go]')?.addEventListener('click',e=>nav(e.target.dataset.go));
}
async function downloadClip(id){
  try{
    const response=await fetch('/api/clips/'+id+'/media',{headers:{Authorization:'Bearer '+state.token}});
    if(!response.ok)throw new Error('Unable to export media — check rights and render status');
    const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`clipline-${id.slice(0,10)}.mp4`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  }catch(e){toast(e.message,true)}
}
function renderSources(){
  $('#assets-content').innerHTML=state.assets.length?state.assets.map(a=>`<div class="asset-row"><div style="flex:1;min-width:0"><div class="asset-title">${h(a.filename)}</div><div class="asset-sub">${h(a.duration_seconds.toFixed(1))} sec · ${h(a.rights_basis)} · ${h(a.rights_platforms.join(', ')||'No current rights')}</div></div><button class="button ${a.revoked?'ghost':'danger'} revoke" data-id="${h(a.id)}" ${a.revoked?'disabled':''}>${a.revoked?'Revoked':'Revoke'}</button></div>`).join(''):'<div class="helper">No footage uploaded yet.</div>';
  $('#jobs-content').innerHTML=state.jobs.length?state.jobs.slice(0,12).map(j=>`<div class="job-row"><div style="flex:1"><div class="asset-title">${h(j.kind)} job</div><div class="asset-sub">${h(stamp(j.created_at))} · attempts: ${h(j.attempts)} ${j.error?'· '+h(j.error):''}</div></div><span class="tag ${h(j.status)}">${h(j.status)}</span>${j.status==='failed'&&j.attempts<3?`<button class="button secondary retry" data-id="${h(j.id)}">Retry</button>`:''}</div>`).join(''):'<div class="helper">No processing jobs yet.</div>';
  $$('.revoke').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('Revoke this asset? Its clips will become ineligible for export.'))return;try{await api('/assets/'+b.dataset.id+'/revoke',{method:'POST'});toast('Rights revoked and publications blocked');await refresh();}catch(e){toast(e.message,true)}}));
  $$('.retry').forEach(b=>b.addEventListener('click',async()=>{try{await api('/jobs/'+b.dataset.id+'/retry',{method:'POST'});toast('Job returned to queue');await refresh();}catch(e){toast(e.message,true)}}));
}
function renderExports(){
  const ready=state.publications.filter(p=>!p.status.startsWith('blocked'));
  $('#export-content').innerHTML=ready.length?ready.map(p=>{
    const c=state.clips.find(c=>c.id===p.clip_id);if(!c)return '';
    const localTime=p.scheduled_at?new Date(p.scheduled_at).toLocaleString():'';
    return `<div class="export-row" style="align-items:start;gap:16px"><div style="flex:1;min-width:0"><div class="asset-title">${h(c.title)} <span class="pill green">${h(p.platform.toUpperCase())}</span></div><div class="asset-sub">${h(c.creator_name)} · ${h(c.caption)}</div><div class="asset-sub">${h(localTime?'Planned local time: '+localTime:'Not scheduled')} · ${h(niceStatus(p.status))}</div><div class="clip-actions"><button class="button secondary copy-caption" data-id="${h(p.id)}">Copy caption</button><button class="button secondary export-manifest" data-id="${h(p.id)}">View manifest</button><button class="button secondary plan-post" data-id="${h(p.id)}">${p.scheduled_at?'Change time':'Plan post time'}</button>${p.scheduled_at?`<button class="button ghost clear-plan" data-id="${h(p.id)}">Clear time</button>`:''}</div></div><button class="button primary export-download" data-id="${h(c.id)}">↓ MP4</button></div>`;
  }).join(''):'<div class="empty"><div class="empty-icon">↗</div><strong>No approved clips yet</strong><p>Approve a rights-cleared clip in Review first. Publication planning does not publish automatically.</p></div>';
  $$('.export-download').forEach(b=>b.addEventListener('click',()=>downloadClip(b.dataset.id)));
  $$('.copy-caption').forEach(b=>b.addEventListener('click',async()=>{
    const p=state.publications.find(p=>p.id===b.dataset.id);
    const c=state.clips.find(c=>c.id===p?.clip_id);
    if(c){try{await navigator.clipboard.writeText(c.caption);toast('Caption copied')}catch(e){toast('Clipboard access unavailable',true)}}
  }));
  $$('.plan-post').forEach(b=>b.addEventListener('click',async()=>{
    const p=state.publications.find(p=>p.id===b.dataset.id);
    const proposed=prompt('Planned local date/time (YYYY-MM-DD HH:MM). Reminder: this does not auto-post.',p.scheduled_at?new Date(p.scheduled_at).toLocaleString('sv-SE').slice(0,16):'');
    if(proposed===null)return;
    const d=new Date(proposed.replace(' ','T'));
    if(Number.isNaN(+d))return toast('Enter a valid local date and time',true);
    try{await api('/publications/'+p.id+'/schedule',{method:'PATCH',body:JSON.stringify({scheduled_at:d.toISOString()})});toast('Manual publishing plan saved');await refresh()}catch(e){toast(e.message,true)}
  }));
  $$('.clear-plan').forEach(b=>b.addEventListener('click',async()=>{
    try{await api('/publications/'+b.dataset.id+'/schedule',{method:'PATCH',body:JSON.stringify({scheduled_at:null})});toast('Plan cleared');await refresh()}catch(e){toast(e.message,true)}
  }));
  $$('.export-manifest').forEach(b=>b.addEventListener('click',async()=>{
    try{const manifest=await api('/publications/'+b.dataset.id+'/manifest');
      const blob=new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`clipline-${b.dataset.id.slice(0,8)}-manifest.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      toast('Export manifest downloaded');}catch(e){toast(e.message,true)}
  }));
}

async function login(){
  state.token=$('#admin-token').value.trim();if(!state.token)return toast('Enter the administrator token',true);
  try{await api('/overview');sessionStorage.setItem('clipline_token',state.token);showApp();await refresh();}catch(e){toast(e.message,true)}
}
$('#sign-in').addEventListener('click',login);
$('#admin-token').addEventListener('keydown',e=>{if(e.key==='Enter')login();});
$('#sign-out').addEventListener('click',showLogin);
$$('[data-tab]').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.tab)));
$$('[data-go]').forEach(b=>b.addEventListener('click',()=>nav(b.dataset.go)));
$('#refresh-dashboard').addEventListener('click',refresh);$('#refresh-review').addEventListener('click',refresh);
$$('[data-clip-filter]').forEach(b=>b.addEventListener('click',()=>{state.filter=b.dataset.clipFilter;renderReview();}));
$('#check-live').addEventListener('click',async()=>{
  const b=$('#check-live');b.disabled=true;b.textContent='Checking…';
  try{const response=await api('/monitor/check',{method:'POST'});toast(`Checked ${response.checked_creators} creators. Live: ${response.live_detected}. ${response.errors.join('; ')}`);await refresh();}catch(e){toast(e.message,true)}finally{b.disabled=false;b.textContent='↻ Check live activity';}
});
$('#add-creator-form').addEventListener('submit',async e=>{
  e.preventDefault();const data=Object.fromEntries(new FormData(e.target));
  try{await api('/creators',{method:'POST',body:JSON.stringify(data)});e.target.reset();toast('Creator added');await refresh();}catch(err){toast(err.message,true)}
});
$('#upload-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.target;const data=new FormData(form);
  const platforms=['tiktok','instagram'].filter(p=>data.has('rights_'+p));
  if(!platforms.length)return toast('Select at least one licensed publishing destination',true);
  data.delete('rights_tiktok');data.delete('rights_instagram');data.delete('rights-affirmation');
  data.set('rights_platforms',platforms.join(','));
  const b=$('#upload-button');b.disabled=true;b.textContent='Uploading & validating…';
  try{const result=await api('/assets',{method:'POST',body:data});toast(`Authorized source queued (${result.duration_seconds.toFixed(1)}s). Run the worker to process.`);form.reset();await refresh();}catch(err){toast(err.message,true)}finally{b.disabled=false;b.innerHTML='Upload and queue analysis <span>↗</span>';}
});
if(state.token){api('/overview').then(()=>{showApp();refresh()}).catch(()=>showLogin());}
