// 本机检查离线HTML；独立无窗口浏览器，不截图、不连接实验环境。
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const report=path.join(here,'批次与数据调度_图解.html');
const profile=await fs.mkdtemp(path.join(os.tmpdir(),'ks-batch-flow-edge-'));
const edge=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
 '--headless=new','--disable-gpu','--no-first-run','--disable-extensions',
 '--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'
],{windowsHide:true,stdio:'ignore'});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let ws,port,id=0;const pending=new Map(),errors=[];
function send(method,params={}){return new Promise((resolve,reject)=>{const n=++id,timer=setTimeout(()=>{pending.delete(n);reject(new Error('CDP timeout '+method));},10000);pending.set(n,{resolve,reject,timer});ws.send(JSON.stringify({id:n,method,params}));});}
const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
try{
 for(let i=0;i<100;i++){try{port=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break;}catch{await sleep(150);}}
 if(!port)throw new Error('Edge did not start');
 const pages=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
 ws=new WebSocket(pages.find(x=>x.type==='page').webSocketDebuggerUrl);
 await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
 ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params);if(pending.has(m.id)){const p=pending.get(m.id);clearTimeout(p.timer);pending.delete(m.id);if(m.error)p.reject(new Error(JSON.stringify(m.error)));else p.resolve(m.result);}});
 await send('Runtime.enable');await send('Page.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:pathToFileURL(report).href});
 let ready=false;for(let i=0;i<60;i++){ready=await evaluate('document.documentElement.dataset.reportReady === "true"');if(ready)break;await sleep(100);}
 if(!ready)throw new Error('Page not rendered');
 const mapping=[];
 for(const value of ['compute','control','resumed'])mapping.push(await evaluate(`(()=>{const s=document.getElementById('batchKind');s.value=${JSON.stringify(value)};s.dispatchEvent(new Event('change'));return {value:s.value,items:document.querySelectorAll('.mapping li').length,hasNote:document.getElementById('mappingNote').textContent.length>20};})()`));
 const states=[];
 for(let step=0;step<6;step++)for(const view of ['all','control','data']){
  const state=await evaluate(`(()=>{document.querySelector('[data-step="${step}"]').click();document.querySelector('[data-view="${view}"]').click();const svg=document.getElementById('flowDiagram'),texts=[...svg.querySelectorAll('text')].map(t=>({text:t.textContent,b:t.getBBox()})),overlap=[];for(let i=0;i<texts.length;i++)for(let j=i+1;j<texts.length;j++){const a=texts[i].b,b=texts[j].b;if(Math.min(a.x+a.width,b.x+b.width)>Math.max(a.x,b.x)+1&&Math.min(a.y+a.height,b.y+b.height)>Math.max(a.y,b.y)+1)overlap.push([texts[i].text,texts[j].text]);}return {step:svg.dataset.step,view:svg.dataset.view,arrows:svg.querySelectorAll('.edge').length,overflow:texts.filter(x=>x.b.x<0||x.b.x+x.b.width>1100||x.b.y<0||x.b.y+x.b.height>630).map(x=>x.text),overlap,invalid:/undefined|NaN/.test(svg.innerHTML)};})()`);
  states.push(state);
 }
 const timeline=[];
 for(const range of ['full','early','late'])for(let moment=0;moment<10;moment++){
  timeline.push(await evaluate(`(()=>{const zoom=document.getElementById('timelineZoom');zoom.value=${JSON.stringify(range)};zoom.dispatchEvent(new Event('change'));document.querySelector('button[data-moment="${moment}"]').click();const svg=document.getElementById('timelineDiagram'),texts=[...svg.querySelectorAll('text')].map(t=>({text:t.textContent,b:t.getBBox()})),overlap=[];for(let i=0;i<texts.length;i++)for(let j=i+1;j<texts.length;j++){const a=texts[i].b,b=texts[j].b;if(Math.min(a.x+a.width,b.x+b.width)>Math.max(a.x,b.x)+1&&Math.min(a.y+a.height,b.y+b.height)>Math.max(a.y,b.y)+1)overlap.push([texts[i].text,texts[j].text]);}return {range:svg.dataset.range,moment:svg.dataset.moment,spans:svg.querySelectorAll('.timeline-span').length,buttons:document.querySelectorAll('button[data-moment][aria-pressed=true]').length,explanation:document.getElementById('momentCopy').textContent.length,overflow:texts.filter(x=>x.b.x<0||x.b.x+x.b.width>1100||x.b.y<0||x.b.y+x.b.height>610).map(x=>x.text),overlap,invalid:/NaN|undefined/.test(svg.innerHTML)};})()`));
 }
 const plotClick=await evaluate(`(()=>{const z=document.getElementById('timelineZoom');z.value='full';z.dispatchEvent(new Event('change'));document.querySelector('[data-timeline-point="6"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));return document.getElementById('timelineDiagram').dataset.moment;})()`);
 const desktop=await evaluate(`({sections:document.querySelectorAll('section').length,firstSection:document.querySelector('section').id,pageOverflow:document.documentElement.scrollWidth>innerWidth,defaultFolded:[...document.querySelectorAll('details')].every(x=>!x.open),placeholder:/@@PATCH@@|\ufffd/.test(document.body.innerText)})`);
 const hrefs=await evaluate(`[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))`);
 const missing=[];for(const href of new Set(hrefs)){const u=new URL(href,pathToFileURL(report));if(u.protocol==='file:'&&u.pathname!==new URL(pathToFileURL(report)).pathname){try{await fs.access(fileURLToPath(u));}catch{missing.push(href);}}}
 const navigation=await evaluate(`(()=>{document.querySelector('[data-step="0"]').click();document.getElementById('next').click();const forward=document.getElementById('flowDiagram').dataset.step;document.getElementById('prev').click();return {forward,back:document.getElementById('flowDiagram').dataset.step};})()`);
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 const mobile=await evaluate(`({viewport:innerWidth,content:document.documentElement.scrollWidth,overflow:document.documentElement.scrollWidth>innerWidth})`);
 const pass=desktop.sections===6&&desktop.firstSection==='example'&&!desktop.pageOverflow&&desktop.defaultFolded&&!desktop.placeholder&&mapping.every(x=>x.items===9&&x.hasNote)&&states.every(x=>!x.overflow.length&&!x.overlap.length&&!x.invalid)&&timeline.every((x,i)=>x.moment===String(i%10)&&x.buttons===1&&x.explanation>25&&!x.overflow.length&&!x.overlap.length&&!x.invalid)&&plotClick==='6'&&navigation.forward==='1'&&navigation.back==='0'&&!mobile.overflow&&!missing.length&&!errors.length;
 const result={status:pass?'PASS':'REVIEW_REQUIRED',report,mapping,states,timeline,plotClick,desktop,mobile,navigation,missingLinks:missing,jsErrors:errors,screenshotSaved:false};
 await fs.writeFile(path.join(here,'批次图解检查.json'),JSON.stringify(result,null,2),'utf8');
 console.log(JSON.stringify(result,null,2));if(!pass)process.exitCode=1;
}finally{if(ws?.readyState===1){try{await send('Browser.close');}catch{}ws.close();}if(edge.exitCode===null)edge.kill();}
