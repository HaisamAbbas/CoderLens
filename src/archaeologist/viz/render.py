"""Render a dependency graph (file-level or symbol-level) as a self-contained,
interactive HTML page — vanilla-JS force-directed canvas, no external libraries.

Consumes the generic {nodes, links, groups, subtitle} shape from `viz.export`.
The *linked* variant also embeds the symbol graph, so clicking a file drills into
its symbols in-place (works even as a static, offline artifact).

- render() / render_page()               single graph (body-only / full document)
- render_linked() / render_linked_page() file graph + drill-into-symbols
"""

import json

_TEMPLATE = r"""<title>__TITLE__</title>
<style>
  :root{
    --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
    --hair:#e1e0d9;
    --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#8f8d86;
    --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
    --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
  }
  @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --hair:#2c2c2a; --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#8a8a84;
  }}
  :root[data-theme="dark"]{
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --hair:#2c2c2a; --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#8a8a84;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--plane);color:var(--ink);font-family:var(--sans);
    height:100vh;display:flex;flex-direction:column;overflow:hidden}
  .head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
    padding:16px 20px 12px;border-bottom:1px solid var(--hair)}
  .title h1{margin:0;font-size:20px;letter-spacing:-.01em;text-wrap:balance}
  .title p{margin:3px 0 0;font-size:13px;color:var(--ink2);max-width:64ch}
  .tools{display:flex;align-items:center;gap:8px;flex-shrink:0}
  .chip{font-size:12px;color:var(--ink2);padding:5px 9px;border:1px solid var(--hair);
    border-radius:999px;white-space:nowrap}
  .chip b{color:var(--ink);font-variant-numeric:tabular-nums}
  button{font-family:var(--sans);font-size:12px;color:var(--ink);background:var(--surface);
    border:1px solid var(--hair);border-radius:8px;padding:6px 11px;cursor:pointer}
  button:hover{border-color:var(--muted)}
  button:focus-visible{outline:2px solid var(--c1);outline-offset:2px}
  .crumb{display:none;align-items:center;gap:8px;padding:8px 20px;border-bottom:1px solid var(--hair);
    font-size:12px;font-family:var(--mono);color:var(--ink2)}
  .crumb a{color:var(--c1);cursor:pointer}
  .crumb b{color:var(--ink);word-break:break-all}
  .main{flex:1;display:flex;min-height:0}
  .stage{flex:1;position:relative;min-width:0}
  canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
  .rail{width:326px;flex-shrink:0;border-left:1px solid var(--hair);background:var(--surface);
    overflow-y:auto;padding:16px 18px}
  .rail h2{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
  .rail .lead{font-size:13px;color:var(--ink2);line-height:1.5;margin:0 0 16px}
  .fname{font-family:var(--mono);font-size:14px;word-break:break-all;margin:2px 0}
  .modchip{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;color:#fff;font-family:var(--mono)}
  .drill{margin-top:14px;width:100%;text-align:left;border-color:var(--c1);color:var(--c1)}
  .metrics{display:flex;gap:18px;margin:14px 0 8px;flex-wrap:wrap}
  .metric .n{font-size:20px;font-variant-numeric:tabular-nums}
  .metric .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
  .grp{margin-top:16px}
  .grp .gh{font-size:12px;color:var(--ink2);margin:0 0 6px;font-weight:600}
  .grp .gh span{color:var(--muted);font-weight:400}
  ul.files{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:1px}
  ul.files li{font-family:var(--mono);font-size:12px;padding:5px 7px;border-radius:6px;cursor:pointer;
    display:flex;justify-content:space-between;gap:8px;color:var(--ink2)}
  ul.files li:hover{background:var(--plane);color:var(--ink)}
  ul.files li .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;align-self:center}
  ul.files li .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  ul.files li .w{color:var(--muted);font-variant-numeric:tabular-nums}
  .legend{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:10px 20px;
    border-top:1px solid var(--hair);font-size:12px;color:var(--ink2)}
  .legend .k{display:flex;align-items:center;gap:6px}
  .legend .k i{width:11px;height:11px;border-radius:50%;display:inline-block}
  .legend .sep{flex:1}
  .tip{position:absolute;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--ink);
    color:var(--plane);font-family:var(--mono);font-size:11.5px;padding:6px 9px;border-radius:7px;
    z-index:5;white-space:nowrap;transform:translate(-50%,-140%)}
  .back{font-size:12px;color:var(--c1);cursor:pointer;display:inline-block;margin-bottom:12px}
  @media (max-width:760px){.main{flex-direction:column}
    .rail{width:auto;border-left:none;border-top:1px solid var(--hair);max-height:44vh}}
</style>

<div class="head">
  <div class="title"><h1 id="ttl">__TITLE__</h1><p id="sub"></p></div>
  <div class="tools">
    <span class="chip"><b id="s-nodes">0</b> nodes</span>
    <span class="chip"><b id="s-links">0</b> edges</span>
    <button id="fit">Fit</button><button id="theme">Theme</button>
  </div>
</div>
<div class="crumb" id="crumb"></div>

<div class="main">
  <div class="stage"><canvas id="cv"></canvas><div class="tip" id="tip"></div></div>
  <aside class="rail" id="rail"></aside>
</div>
<div class="legend" id="legend"></div>

<script>
const FILES = __FILES__;
const SYM = __SYM__;                 // null unless linked
const FILE_TITLE = "__TITLE__";

const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const tip=document.getElementById('tip'), rail=document.getElementById('rail'), crumb=document.getElementById('crumb');
let CO={};
function readColors(){const cs=getComputedStyle(document.documentElement),g=n=>cs.getPropertyValue(n).trim();
  CO={ink:g('--ink'),ink2:g('--ink2'),muted:g('--muted'),hair:g('--hair'),surface:g('--surface'),
      c1:g('--c1'),c2:g('--c2'),c3:g('--c3'),c4:g('--c4'),mono:g('--mono')};}
let GROUPS=[];
const groupIndex=k=>{const i=GROUPS.findIndex(g=>g.key===k);return i<0?3:Math.min(i,3);};
const colorForGroup=k=>CO['c'+(groupIndex(k)+1)];
const groupLabel=k=>{const g=GROUPS.find(g=>g.key===k);return g?g.label:k;};
const radius=n=>4+Math.sqrt(n.degree)*1.8;

// ---- model (rebuilt by loadGraph) ----
let nodes=[],links=[],byId=new Map(),outMap=new Map(),inMap=new Map(),maxDeg=1,isFiles=true;
function buildModel(data){
  GROUPS=data.groups||[];
  nodes=data.nodes.map(n=>({...n,x:0,y:0,vx:0,vy:0,fx:null,fy:null}));
  byId=new Map(nodes.map(n=>[n.id,n]));
  links=data.links.map(l=>({s:byId.get(l.source),t:byId.get(l.target),w:l.weight||1})).filter(l=>l.s&&l.t);
  maxDeg=Math.max(1,...nodes.map(n=>n.degree));
  outMap=new Map();inMap=new Map();nodes.forEach(n=>{outMap.set(n.id,[]);inMap.set(n.id,[]);});
  links.forEach(l=>{outMap.get(l.s.id).push(l);inMap.get(l.t.id).push(l);});
  nodes.forEach((n,i)=>{const a=i/nodes.length*Math.PI*2;n.x=Math.cos(a)*250;n.y=Math.sin(a)*250;});
  document.getElementById('sub').textContent=data.subtitle||'';
  document.getElementById('s-nodes').textContent=nodes.length;
  document.getElementById('s-links').textContent=links.length;
}

let view={scale:1,px:0,py:0},W=0,H=0,DPR=1;
function resize(){const r=cv.parentElement.getBoundingClientRect();
  DPR=Math.min(2,window.devicePixelRatio||1);W=r.width;H=r.height;
  cv.width=W*DPR;cv.height=H*DPR;ctx.setTransform(DPR,0,0,DPR,0,0);}
function fit(){if(!nodes.length)return;let a=1e9,b=1e9,c=-1e9,d=-1e9;
  nodes.forEach(n=>{a=Math.min(a,n.x);b=Math.min(b,n.y);c=Math.max(c,n.x);d=Math.max(d,n.y);});
  const pad=80,w=c-a||1,h=d-b||1;view.scale=Math.min((W-pad)/w,(H-pad)/h,2.4);
  view.px=W/2-(a+c)/2*view.scale;view.py=H/2-(b+d)/2*view.scale;}
const toScreen=n=>({x:n.x*view.scale+view.px,y:n.y*view.scale+view.py});
const toWorld=(sx,sy)=>({x:(sx-view.px)/view.scale,y:(sy-view.py)/view.scale});

let alpha=1;
function tick(){
  for(const n of nodes){n.vx+=-n.x*0.015*alpha;n.vy+=-n.y*0.015*alpha;}
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i],b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+0.01;
    const f=4200/d2*alpha,d=Math.sqrt(d2);dx/=d;dy/=d;a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;}
  for(const l of links){let dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)+0.01;
    const f=(d-78)*0.012*alpha*Math.min(3,l.w);dx/=d;dy/=d;
    l.s.vx+=dx*f;l.s.vy+=dy*f;l.t.vx-=dx*f;l.t.vy-=dy*f;}
  for(const n of nodes){if(n.fx!==null){n.x=n.fx;n.y=n.fy;n.vx=0;n.vy=0;continue;}
    n.vx*=0.82;n.vy*=0.82;n.x+=Math.max(-20,Math.min(20,n.vx));n.y+=Math.max(-20,Math.min(20,n.vy));}
  if(alpha>0.03)alpha*=0.992;
}

let hover=null,selected=null;
function neighborsOf(id){const s=new Set([id]);
  (outMap.get(id)||[]).forEach(l=>s.add(l.t.id));(inMap.get(id)||[]).forEach(l=>s.add(l.s.id));return s;}
function draw(){
  tick();ctx.clearRect(0,0,W,H);const nb=selected?neighborsOf(selected.id):null;
  for(const l of links){const a=toScreen(l.s),b=toScreen(l.t);let col=CO.hair,wdt=1,al=0.9;
    if(selected){const on=l.s.id===selected.id||l.t.id===selected.id;
      if(on){col=colorForGroup(selected.group);wdt=1.6;al=1;}else{al=0.13;}}
    ctx.globalAlpha=al;ctx.strokeStyle=col;ctx.lineWidth=wdt;
    ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
  ctx.globalAlpha=1;
  for(const n of nodes){const p=toScreen(n),r=radius(n)*Math.max(0.7,Math.min(1.4,view.scale));
    const dim=selected&&!nb.has(n.id);ctx.globalAlpha=dim?0.28:1;
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=colorForGroup(n.group);ctx.fill();
    ctx.lineWidth=(n===selected||n===hover)?2.5:1.2;
    ctx.strokeStyle=(n===selected||n===hover)?CO.ink:CO.surface;ctx.stroke();
    if(n===hover||n===selected||n.degree>maxDeg*0.35){ctx.globalAlpha=dim?0.4:1;
      ctx.font='11px '+CO.mono;ctx.textAlign='center';ctx.textBaseline='top';
      ctx.lineWidth=3;ctx.strokeStyle=CO.surface;ctx.strokeText(n.label,p.x,p.y+r+3);
      ctx.fillStyle=CO.ink;ctx.fillText(n.label,p.x,p.y+r+3);}}
  ctx.globalAlpha=1;requestAnimationFrame(draw);
}
function pick(sx,sy){let best=null,bd=1e9;
  for(const n of nodes){const p=toScreen(n),r=radius(n)*Math.max(0.7,Math.min(1.4,view.scale))+3;
    const d=(p.x-sx)**2+(p.y-sy)**2;if(d<r*r&&d<bd){bd=d;best=n;}}return best;}

function loadGraph(data, filesMode){
  isFiles=filesMode; selected=null; hover=null; tip.style.opacity=0;
  buildModel(data); readColors(); buildLegend(); overview();
  alpha=1; for(let i=0;i<260;i++) tick(); fit();
}

// ---- interaction ----
let drag=null,panning=false,last=null,moved=false;
cv.addEventListener('mousedown',e=>{const n=pick(e.offsetX,e.offsetY);moved=false;
  if(n){drag=n;n.fx=n.x;n.fy=n.y;}else{panning=true;last={x:e.offsetX,y:e.offsetY};}});
window.addEventListener('mousemove',e=>{const rect=cv.getBoundingClientRect(),sx=e.clientX-rect.left,sy=e.clientY-rect.top;
  if(drag){moved=true;const w=toWorld(sx,sy);drag.fx=w.x;drag.fy=w.y;alpha=Math.max(alpha,0.3);return;}
  if(panning){moved=true;view.px+=sx-last.x;view.py+=sy-last.y;last={x:sx,y:sy};return;}
  const n=pick(sx,sy);hover=n;cv.style.cursor=n?'pointer':'default';
  if(n){const p=toScreen(n);tip.style.opacity=1;tip.style.left=p.x+'px';tip.style.top=p.y+'px';tip.textContent=n.label+'  ·  '+n.meta;}
  else tip.style.opacity=0;});
window.addEventListener('mouseup',()=>{if(drag&&!moved)select(drag);else if(panning&&!moved)select(null);
  if(drag){drag.fx=null;drag.fy=null;}drag=null;panning=false;});
cv.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.1:1/1.1;
  const wx=(e.offsetX-view.px)/view.scale,wy=(e.offsetY-view.py)/view.scale;
  view.scale=Math.max(0.2,Math.min(5,view.scale*f));view.px=e.offsetX-wx*view.scale;view.py=e.offsetY-wy*view.scale;},{passive:false});
document.getElementById('fit').onclick=()=>{alpha=Math.max(alpha,0.2);fit();};
document.getElementById('theme').onclick=()=>{const cur=document.documentElement.getAttribute('data-theme');
  const dark=cur?cur==='dark':matchMedia('(prefers-color-scheme:dark)').matches;
  document.documentElement.setAttribute('data-theme',dark?'light':'dark');readColors();buildLegend();};
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{readColors();buildLegend();});

// ---- drill (linked mode) ----
function drill(file){
  if(!SYM) return;
  const inFile=SYM.nodes.filter(n=>n.file===file);
  if(!inFile.length){ return; }
  const ids=new Set(inFile.map(n=>n.id)); const nbr=new Set();
  SYM.links.forEach(l=>{if(ids.has(l.source))nbr.add(l.target);if(ids.has(l.target))nbr.add(l.source);});
  let keep=new Set([...ids,...nbr]);
  let nds=SYM.nodes.filter(n=>keep.has(n.id));
  if(nds.length>140){ keep=ids; nds=inFile; }
  const lks=SYM.links.filter(l=>keep.has(l.source)&&keep.has(l.target));
  const gmap={class:'Classes',method:'Methods',function:'Functions'};
  const groups=[...new Set(nds.map(n=>n.group))].map(k=>({key:k,label:gmap[k]||k}));
  loadGraph({nodes:nds,links:lks,groups,
    subtitle:'Symbols in '+file+' and their direct callers/callees. Click a symbol for its links.'}, false);
  document.getElementById('ttl').textContent=file.split('/').pop()+' — symbols';
  crumb.style.display='flex'; crumb.innerHTML='';
  const a=document.createElement('a'); a.textContent='← '+FILE_TITLE; a.onclick=backToFiles;
  const sep=document.createElement('span'); sep.textContent='›';
  const b=document.createElement('b'); b.textContent=file;
  crumb.appendChild(a); crumb.appendChild(sep); crumb.appendChild(b);
}
function backToFiles(){crumb.style.display='none';document.getElementById('ttl').textContent=FILE_TITLE;loadGraph(FILES,true);}

// ---- rail ----
function buildLegend(){const el=document.getElementById('legend');el.innerHTML='';
  GROUPS.forEach(g=>{const k=document.createElement('span');k.className='k';
    const i=document.createElement('i');i.style.background=colorForGroup(g.key);
    k.appendChild(i);k.appendChild(document.createTextNode(' '+g.label));el.appendChild(k);});
  const sep=document.createElement('span');sep.className='sep';el.appendChild(sep);
  const note=document.createElement('span');note.textContent='● larger = more connected';el.appendChild(note);}
function rowEl(n,weight){const li=document.createElement('li');
  const dot=document.createElement('span');dot.className='dot';dot.style.background=colorForGroup(n.group);
  const nm=document.createElement('span');nm.className='nm';nm.textContent=n.label;nm.title=n.meta;
  li.appendChild(dot);li.appendChild(nm);
  if(weight!=null){const w=document.createElement('span');w.className='w';w.textContent=weight;li.appendChild(w);}
  li.onclick=()=>select(n);return li;}
function overview(){rail.innerHTML='';
  const h=document.createElement('h2');h.textContent='How to read this';rail.appendChild(h);
  const p=document.createElement('p');p.className='lead';p.textContent=document.getElementById('sub').textContent;rail.appendChild(p);
  const g=document.createElement('div');g.className='grp';
  const gh=document.createElement('div');gh.className='gh';gh.innerHTML='Most connected <span>· by degree</span>';g.appendChild(gh);
  const ul=document.createElement('ul');ul.className='files';
  [...nodes].sort((a,b)=>b.degree-a.degree).slice(0,10).forEach(n=>ul.appendChild(rowEl(n,n.degree)));
  g.appendChild(ul);rail.appendChild(g);}
function select(n){selected=n;if(!n){overview();return;}
  rail.innerHTML='';
  const back=document.createElement('span');back.className='back';back.textContent='← all';back.onclick=()=>select(null);rail.appendChild(back);
  const fn=document.createElement('div');fn.className='fname';fn.textContent=n.label;rail.appendChild(fn);
  const meta=document.createElement('div');meta.style.cssText='font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:6px;word-break:break-all';meta.textContent=n.meta;rail.appendChild(meta);
  const mc=document.createElement('span');mc.className='modchip';mc.style.background=colorForGroup(n.group);mc.textContent=groupLabel(n.group);rail.appendChild(mc);
  const met=document.createElement('div');met.className='metrics';
  (n.stats||[]).forEach(([l,v])=>{met.innerHTML+='<div class="metric"><div class="n">'+v+'</div><div class="l">'+l+'</div></div>';});
  rail.appendChild(met);
  if(isFiles && SYM){const b=document.createElement('button');b.className='drill';b.textContent='🔬 Explore symbols in this file →';b.onclick=()=>drill(n.id);rail.appendChild(b);}
  const ins=(inMap.get(n.id)||[]).slice().sort((a,b)=>b.w-a.w);
  const outs=(outMap.get(n.id)||[]).slice().sort((a,b)=>b.w-a.w);
  rail.appendChild(listEl('Breaks if removed','depends on this',ins.map(l=>[l.s,l.w])));
  rail.appendChild(listEl('Depends on','calls into these',outs.map(l=>[l.t,l.w])));}
function listEl(title,sub,pairs){const g=document.createElement('div');g.className='grp';
  const gh=document.createElement('div');gh.className='gh';gh.innerHTML=title+' <span>· '+sub+'</span>';g.appendChild(gh);
  const ul=document.createElement('ul');ul.className='files';
  if(!pairs.length){const li=document.createElement('li');li.textContent='— none —';li.style.cursor='default';ul.appendChild(li);}
  pairs.forEach(([n,w])=>ul.appendChild(rowEl(n,w)));g.appendChild(ul);return g;}

// ---- boot ----
readColors(); resize();
loadGraph(FILES, true);
window.addEventListener('resize',()=>{resize();fit();});
draw();
</script>
"""


def _body(files: dict, sym: dict | None, title: str) -> str:
    return (_TEMPLATE
            .replace("__FILES__", json.dumps(files))
            .replace("__SYM__", json.dumps(sym) if sym else "null")
            .replace("__TITLE__", title))


def _wrap(body: str, title: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{title}</title></head><body>{body}</body></html>")


def render(data: dict, title: str = "Dependency Atlas") -> str:
    return _body(data, None, title)


def render_linked(combined: dict, title: str = "Dependency Atlas") -> str:
    return _body(combined["files"], combined["symbols"], title)


def render_page(data: dict, title: str = "Dependency Atlas") -> str:
    return _wrap(render(data, title), title)


def render_linked_page(combined: dict, title: str = "Dependency Atlas") -> str:
    return _wrap(render_linked(combined, title), title)
