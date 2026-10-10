// Execute the shipped autosave code with controlled input events and delayed responses.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
function setup() {
  const uploadListeners = new Map();
  const attachmentForm = {addEventListener:(name,fn)=>uploadListeners.set(name,fn)};
  const listeners = new Map(), timers = new Map(), requests = [];
  let nextTimer = 0, submitted = false;
  const field = value => ({value, addEventListener(name, fn) {this[name] = fn;}});
  const form = {elements: {date:field('2026-10-10'), body:field(''), revision:field('0'), action:field('save')}, dataset:{autosaveUrl:'/edit'}, addEventListener(name,fn){listeners.set(name,fn);}};
  const status = {textContent:''};
  const sandbox = {document:{querySelector:q=>q==='#clinical-note-form'?form:q==='#clinical-save-status'?status:q==='[data-note-attachment-form]'?attachmentForm:null,querySelectorAll:()=>[]}, window:{addEventListener:(name,fn)=>listeners.set(name,fn)}, setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer;}, clearTimeout:id=>timers.delete(id), HTMLFormElement:{prototype:{submit(){submitted=true;}}}, FormData:class {
    constructor(form){this.values=Object.fromEntries(Object.entries(form.elements).map(([key,value])=>[key,value.value]));}
    set(key,value){this.values[key]=value;}
  }, fetch:(url,options)=>new Promise(resolve=>requests.push({payload:options.body.values, resolve}))};
  vm.runInNewContext(fs.readFileSync('billing/static/billing/app.js','utf8'), sandbox);
  return {form,status,requests,listeners,uploadListeners,submitted:()=>submitted,
    edit(text){form.elements.body.value=text;form.elements.body.input();},
    tick(){const work=[...timers.values()];timers.clear();work.forEach(fn=>fn());},
    respond(index,code,data){requests[index].resolve({ok:code===200,status:code,headers:{get:()=> 'application/json'},json:async()=>data});}};
}
const settle=async()=>{for(let i=0;i<10;i++) await Promise.resolve();};
(async()=>{
  const a=setup();a.edit('Erster Befund');a.tick();assert.equal(a.requests.length,1);
  a.edit('Ergänzter Befund');a.tick();assert.equal(a.requests.length,1,'Only one save in flight');
  a.respond(0,200,{revision:1,saved_at:'10:00:00'});await settle();a.tick();
  assert.equal(a.requests[1].payload.body,'Ergänzter Befund');assert.equal(a.requests[1].payload.revision,1);
  a.respond(1,200,{revision:2,saved_at:'10:00:01'});await settle();
  assert.equal(a.form.elements.revision.value,2);assert.match(a.status.textContent,/Gespeichert/);
  const b=setup();b.edit('Konflikt');b.tick();b.respond(0,409,{error:'Bitte neu laden'});await settle();
  b.edit('Weiterer Text');b.tick();assert.equal(b.requests.length,1);assert.match(b.status.textContent,/neu laden/);
  await b.listeners.get('submit')({preventDefault(){},submitter:{dataset:{action:'save'}}});assert.equal(b.submitted(),false);
  const c=setup();c.edit('Aktueller Befund');c.tick();
  const submit=c.listeners.get('submit')({preventDefault(){},submitter:{dataset:{action:'finalize'}}});
  assert.equal(c.submitted(),false);c.respond(0,200,{revision:1,saved_at:'10:00:02'});await submit;
  assert.equal(c.submitted(),true);assert.equal(c.form.elements.action.value,'finalize');assert.equal(c.form.elements.revision.value,1);
  const d=setup();d.edit('Nicht gespeichert');let warned=false;d.listeners.get('beforeunload')({preventDefault(){warned=true;}});assert.equal(warned,true);
  const e=setup();e.edit('Text vor Upload');e.tick();
  const uploading=e.uploadListeners.get('submit')({preventDefault(){}});
  assert.equal(e.submitted(),false);e.edit('Aktueller Text vor Upload');
  e.respond(0,200,{revision:1,saved_at:'10:00:03'});await settle();
  assert.equal(e.requests[1].payload.body,'Aktueller Text vor Upload');
  e.respond(1,200,{revision:2,saved_at:'10:00:04'});await uploading;assert.equal(e.submitted(),true);
  const f=setup();f.edit('Konflikt vor Upload');f.tick();
  const failedUpload=f.uploadListeners.get('submit')({preventDefault(){}});
  f.respond(0,409,{error:'Bitte neu laden'});await failedUpload;assert.equal(f.submitted(),false);
  console.log('Autosave: serial requests, edits during save, conflict protection, finalize during save and unload warning passed.');
})().catch(error=>{console.error(error);process.exit(1);});
