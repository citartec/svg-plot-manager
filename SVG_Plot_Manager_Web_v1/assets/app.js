'use strict';
const $ = id => document.getElementById(id);
const state={session:null,sha:null,filename:null,stats:null,layers:{},active:null,busy:false};
const fmt=(v,d=1)=>Number(v).toLocaleString(undefined,{maximumFractionDigits:d});
const show=(id,on)=>$(id).classList.toggle('hidden',!on);
const active=()=>state.layers[state.active];
const url=(file)=>`/api/download?session=${encodeURIComponent(state.session)}&layer=${encodeURIComponent(state.active)}&file=${encodeURIComponent(file)}`;
const exportUrl=()=>`/api/export?session=${encodeURIComponent(state.session)}&layer=${encodeURIComponent(state.active)}&reverse=${$('include-reversed').checked?'1':'0'}`;
const setStatus=(text,kind='')=>{ $('status').textContent=text;$('status').className='status '+kind;};
const request=async(response)=>{const data=await response.json();if(!response.ok)throw Error(data.error||'Server error');return data;};
function setBusy(value,message){
 state.busy=value;
 $('generate').disabled=value||!active();$('example').disabled=value;
 $('download-zip').disabled=value||!active()?.info;
 $('clear-sessions').disabled=value||!active()?.info;
 document.querySelectorAll('.layer-tab').forEach(e=>e.disabled=value);
 if(message)setStatus(message);
}
function defaults(){return {mode:'parts',parts:4,max_minutes:60,cap:false,max_contacts:300,observed_hours:0,from_path:1};}
function readFields(){
 return {mode:document.querySelector('input[name="split-mode"]:checked').value,
 parts:Number(document.querySelector('input[name="part-count"]:checked').value),
 max_minutes:Number($('max_minutes').value),cap:$('cap-contacts').checked,
 max_contacts:Number($('max_contacts').value), observed_hours:Number($('observed_hours').value),
 from_path:Number($('from_path').value)};
}
function fillFields(opts){
 const o=opts||defaults();
 document.querySelector(`input[name="split-mode"][value="${o.mode}"]`).checked=true;
 document.querySelector(`input[name="part-count"][value="${o.parts}"]`).checked=true;
 $('max_minutes').value=o.max_minutes;$('cap-contacts').checked=o.cap;
 $('max_contacts').value=o.max_contacts;$('observed_hours').value=o.observed_hours;
 $('from_path').value=o.from_path;
 syncFields();
}
function syncFields(){
 const v=readFields();show('parts-options',v.mode==='parts');show('time-options',v.mode==='time');
 show('contacts-field',v.cap);
}
function options(){
 const o=readFields();
 if(o.mode==='time'&&(!Number.isFinite(o.max_minutes)||o.max_minutes<=0))throw Error('Enter a maximum session time above zero.');
 if(o.cap&&(!Number.isInteger(o.max_contacts)||o.max_contacts<=0))throw Error('Pen-down limit must be a positive whole number.');
 if(!Number.isFinite(o.observed_hours)||o.observed_hours<0)throw Error('Known layer time must be zero or higher.');
 if(!Number.isInteger(o.from_path)||o.from_path<1)throw Error('Starting path must be at least 1.');
 if(o.from_path>active().paths)throw Error(`This layer has only ${active().paths} paths.`);
 return {equal_parts:o.mode==='parts'?o.parts:0,max_minutes:o.mode==='time'?o.max_minutes:0,
   max_contacts:o.cap?o.max_contacts:0,observed_hours:o.observed_hours,from_path:o.from_path};
}
function updateFields(){syncFields();if(active()){
 active().options=readFields();
 if(active().info)setStatus('Changes pending. Generate again to apply these limits.');
}}
function make(tag,props={},children=[]){const e=document.createElement(tag);for(const [k,v] of Object.entries(props)){
 if(k==='text')e.textContent=v; else if(k==='class')e.className=v;else e.setAttribute(k,v);
}for(const c of children)e.appendChild(c);return e;}
function renderLayerTabs(){
 const box=$('layer-tabs');box.replaceChildren();
 for(const layer of Object.values(state.layers)){
  const label=make('span',{class:'layer-name',text:layer.name});
  const labels=/^\s*\d{1,3}\s*[.·:_-]/.test(layer.name)?[label]:[make('span',{class:'layer-number',text:layer.id.replace('layer_','').padStart(2,'0')}),label];
  const btn=make('button',{type:'button',class:'layer-tab',role:'tab','aria-selected':String(state.active===layer.id),title:layer.name,'data-layer':layer.id},labels);
  if(layer.info)btn.append(make('span',{class:'has-jobs',title:'Sessions generated'}));
  btn.addEventListener('click',()=>switchLayer(layer.id));box.append(btn);
 }
 $('layer-count').textContent=Object.keys(state.layers).length;
}
async function upload(file){
 if(!file)return;
 if(!/\.svg$/i.test(file.name)){setStatus('Please choose an SVG file.','error');return;}
 if(file.size>25*1024*1024){setStatus('SVG exceeds the 25 MB upload limit.','error');return;}
 setBusy(true,'Loading and inspecting SVG…');
 try{
  const response=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'image/svg+xml','X-Plot-Manager':'1','X-Filename':encodeURIComponent(file.name)},body:file});
  const data=await request(response);
  state.session=data.session;state.sha=data.sha256;state.filename=data.filename;state.stats=data.stats;
  state.layers={};for(const x of data.stats.layers){state.layers[x.id]={...x,options:defaults(),info:null,selected:0,done:new Set(),storageKey:null};}
  state.active=null;
  $('source-name').textContent=data.filename;
  $('source-stats').textContent=`${fmt(data.stats.paths,0)} paths · ${fmt(data.stats.page_mm.width)} × ${fmt(data.stats.page_mm.height)} mm`;
  show('source-card',true);$('page-size').textContent=`${fmt(data.stats.page_mm.width)} × ${fmt(data.stats.page_mm.height)} mm`;
  document.title=`${data.filename} • SVG Plot Manager`;
  renderLayerTabs();
  switchLayer(data.stats.layers[0].id);
  setStatus(`Loaded ${data.stats.layers.length} pen layer(s). Select a layer to plan.`, 'good');
 }catch(err){setStatus(err.message,'error');}
 finally{setBusy(false);}
}
async function example(){try{const r=await fetch('/api/example');if(!r.ok)throw Error('Example not installed');const blob=await r.blob();await upload(new File([blob],'1_plotted_1a4.svg',{type:'image/svg+xml'}));}catch(err){setStatus(err.message,'error');}}
function switchLayer(id){
 if(!state.layers[id])return;
 if(state.active){active().options=readFields();}
 state.active=id;const l=active();fillFields(l.options);
 renderLayerTabs();$('layer-summary').textContent=`${fmt(l.paths,0)} paths · ${fmt(l.draw_m,2)} m · ${fmt(l.pen_contacts,0)} pen-down events`;
 show('layer-summary',true);
 $('metric-draw').textContent=`${fmt(l.draw_m,2)} m`;
 $('metric-contacts').textContent=fmt(l.pen_contacts,0);
 $('metric-jobs').textContent=l.info?fmt(l.info.batches.length,0):'—';
 $('preview-subtitle').textContent=l.name;
 if(l.info)renderJobs(l);else emptyJobs();
 setPreview(l.selected||0);
 $('generate').disabled=state.busy;
 setStatus(`Layer: ${l.name}`, 'good');
}
function emptyJobs(){
 $('jobs-body').replaceChildren();
 $('metric-jobs').textContent='—';$('session-count').textContent='0';$('job-description').textContent='Generate jobs for this pen layer.';
 show('filled-jobs',false);show('empty-jobs',true);
 $('download-zip').disabled=true;$('clear-sessions').disabled=true;$('selected-view').disabled=true;
}
function setPreview(job){
 if(!state.session||!active())return;
 const l=active();l.selected=job;
 $('preview').src=`/api/preview?session=${encodeURIComponent(state.session)}&layer=${encodeURIComponent(l.id)}&job=${job}&rev=${Date.now()}`;
 show('placeholder',false);show('preview',true);
 $('preview-indicator').textContent=job?`${l.name} · job ${job} highlighted`:l.info?`${l.name} · shaded sessions`:l.name;
 $('full-view').classList.toggle('active',job===0);
 $('selected-view').classList.toggle('active',job!==0);
 document.querySelectorAll('.session-item').forEach(e=>{const chosen=Number(e.dataset.job)===job;e.classList.toggle('selected',chosen);e.setAttribute('aria-pressed',String(chosen));});
}
function getColor(job){
 const shades=['#252525','#484848','#666666','#888888','#a1a1a1','#777777','#575757','#383838'];
 return shades[(job-1)%shades.length];
}
function readProgress(key){try{const v=JSON.parse(localStorage.getItem(key)||'[]');return new Set(v.filter(Number.isInteger));}catch{return new Set();}}
function saveProgress(){const l=active();if(!l?.storageKey)return;try{localStorage.setItem(l.storageKey,JSON.stringify([...l.done]));}catch{}}
function updateProgress(){
 const l=active();if(!l?.info)return;
 const all=l.info.batches.length,done=l.info.batches.filter(r=>l.done.has(r.first_path)).length;
 $('progress-text').textContent=`${done} of ${all} complete`;
 $('progress-bar').style.width=(all?100*done/all:0)+'%';
}
function renderJobs(l){
 const info=l.info,body=$('jobs-body');body.replaceChildren();
 $('metric-jobs').textContent=fmt(info.batches.length,0);
 $('session-count').textContent=fmt(info.batches.length,0);
 $('job-description').textContent=`~${fmt(info.totals.estimated_hours,1)} h · ${fmt(info.totals.drawing_m,1)} m drawn`;
 show('empty-jobs',false);show('filled-jobs',true);
 if(!l.storageKey){l.storageKey=`svgpm15:${state.sha}:${l.id}:${JSON.stringify(l.plannedOptions)}`;l.done=readProgress(l.storageKey);}
 for(const row of info.batches){
  const item=make('div',{class:'session-item',role:'button',tabindex:'0','data-job':String(row.job),'aria-pressed':'false'});
  const cb=make('input',{type:'checkbox',class:'checkbox','aria-label':`Mark job ${row.job} complete`});cb.checked=l.done.has(row.first_path);
  cb.addEventListener('click',e=>e.stopPropagation());
  cb.addEventListener('change',()=>{if(cb.checked)l.done.add(row.first_path);else l.done.delete(row.first_path);saveProgress();updateProgress();});
  item.append(cb,make('span',{class:'session-color',style:`background:${getColor(row.job)}`}));
  item.append(make('span',{class:'session-text'},[
   make('strong',{text:`${l.name} · ${String(row.job).padStart(2,'0')} · ${fmt(row.estimated_minutes,0)} min`}),
   make('small',{text:`${fmt(row.drawing_m,1)} m · paths ${row.first_path}–${row.last_path}`})]));
  const dl=make('a',{class:'session-download',href:url(row.filename),download:row.filename,title:`Download job ${row.job}`,'aria-label':`Download job ${row.job}`,text:'↓'});
  dl.addEventListener('click',e=>e.stopPropagation());item.append(dl);
  item.addEventListener('click',()=>setPreview(row.job));
  item.addEventListener('keydown',e=>{if(e.target===item&&(e.key==='Enter'||e.key===' ')){e.preventDefault();setPreview(row.job);}});
  body.append(item);
 }
 $('run-sheet').href=url('RUN_SHEET.md');$('csv').href=url('manifest.csv');$('pen-test').href=url('pen_test.svg');
 show('registration',false);
 $('download-zip').disabled=state.busy;$('clear-sessions').disabled=state.busy;$('selected-view').disabled=false;
 updateProgress();
}
async function generate(){
 if(!state.session||!active())return;
 let opts;try{opts=options();}catch(err){setStatus(err.message,'error');return;}
 const id=state.active;
 setBusy(true,'Splitting selected pen layer…');
 try{
  const data=await request(await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json','X-Plot-Manager':'1'},body:JSON.stringify({session:state.session,layer:id,options:opts})}));
  const l=state.layers[id];l.info=data.info;l.plannedOptions=opts;
  l.storageKey=`svgpm15:${state.sha}:${id}:${JSON.stringify(opts)}`;l.done=readProgress(l.storageKey);l.selected=0;
  renderLayerTabs();renderJobs(l);setPreview(0);
  const tooBig=l.info.oversized_source_paths?.length||0;
  setStatus(`${l.name}: ${l.info.batches.length} jobs generated.${tooBig?` ${tooBig} indivisible path(s) exceed the limit.`:''}`,tooBig?'error':'good');
 }catch(err){setStatus(err.message,'error');}finally{setBusy(false);}
}
async function clearSessions(){
 const l=active();if(!l?.info||state.busy)return;
 if(!confirm(`Clear the jobs and completion marks for "${l.name}"? Other layers will be kept.`))return;
 setBusy(true,'Clearing this layer’s jobs…');
 try{
  await request(await fetch('/api/clear',{method:'POST',headers:{'Content-Type':'application/json','X-Plot-Manager':'1'},body:JSON.stringify({session:state.session,layer:l.id})}));
  if(l.storageKey){try{localStorage.removeItem(l.storageKey);}catch{}}
  l.info=null;l.storageKey=null;l.plannedOptions=null;l.done=new Set();l.selected=0;
  renderLayerTabs();emptyJobs();setPreview(0);
  setStatus(`${l.name}: sessions cleared. Other pen layers unchanged.`,'good');
 }catch(err){setStatus(err.message,'error');}finally{setBusy(false);}
}
function resume(){
 const l=active();if(!l?.info)return;
 const next=l.info.batches.find(r=>!l.done.has(r.first_path));
 if(!next){setStatus('All sessions in this layer are complete.','good');return;}
 $('from_path').value=next.first_path;l.options=readFields();
 document.querySelector('.advanced').open=true;
 setStatus(`Next unfinished job starts at layer path ${next.first_path}. Generate a recovery plan to continue.`);
}
function initTheme(){
 let chosen='dark';try{chosen=localStorage.getItem('svgpm:theme')||'dark';}catch{}
 applyTheme(chosen);
 $('theme-toggle').addEventListener('click',()=>applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
}
function applyTheme(value){
 const t=value==='light'?'light':'dark';document.documentElement.dataset.theme=t;
 $('theme-toggle').textContent=t==='dark'?'☀ Light theme':'☾ Dark theme';
 try{localStorage.setItem('svgpm:theme',t);}catch{}
}
$('dropzone').addEventListener('click',()=>$('file').click());
$('dropzone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('file').click();}});
$('file').addEventListener('change',e=>upload(e.target.files[0]));
$('replace').addEventListener('click',()=>$('file').click());
for(const evt of ['dragover','dragenter'])$('dropzone').addEventListener(evt,e=>{e.preventDefault();$('dropzone').classList.add('drag');});
for(const evt of ['drop','dragleave'])$('dropzone').addEventListener(evt,e=>{e.preventDefault();$('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop',e=>upload(e.dataTransfer.files[0]));
$('example').addEventListener('click',example);$('generate').addEventListener('click',generate);
$('clear-sessions').addEventListener('click',clearSessions);
$('download-zip').addEventListener('click',()=>{if(active()?.info)location.href=exportUrl();});
$('full-view').addEventListener('click',()=>setPreview(0));
$('selected-view').addEventListener('click',()=>setPreview(active()?.selected||1));
$('resume').addEventListener('click',resume);
document.querySelectorAll('input[name="split-mode"],input[name="part-count"],#cap-contacts').forEach(e=>e.addEventListener('change',updateFields));
for(const id of ['max_minutes','max_contacts','observed_hours','from_path'])$(id).addEventListener('input',updateFields);
initTheme();syncFields();
