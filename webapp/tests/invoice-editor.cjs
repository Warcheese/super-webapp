const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const handlers = {};
let focused = null, scrolled = null;
function row(index, description='', id='') {
  const fields = Object.fromEntries(Object.entries({id, description, date:'2026-11-02', code:'', price:'10', quantity:'1', DELETE:''}).map(([key,value])=>[key,{value,checked:false,focus(){focused=index;},scrollIntoView(){scrolled=index;}}]));
  return {index,fields,querySelector(selector){const key=selector.match(/-([A-Za-z]+)"/)[1];return fields[key];},scrollIntoView(){scrolled=index;}};
}
const rows = [row(2), row(1,'Zweite Leistung','12'), row(0,'Erste Leistung','11')];
const management = {value:'3'};
const list = {
  querySelectorAll(){return rows;},
  insertAdjacentHTML(position, html){assert.equal(position,'afterbegin');rows.unshift(row(Number(html.match(/items-(\d+)-/)[1])));},
  get firstElementChild(){return rows[0];}
};
const control = id => ({addEventListener(event,fn){handlers[`${id}:${event}`]=fn;}});
const picker = {value:'1',append(){}};
const total = {textContent:''};
const elements = {'#invoice-form':control('form'),'#item-list':list,'#id_items-TOTAL_FORMS':management,
  '#service-data':{textContent:JSON.stringify([{id:1,description:'Neue Vorlage',code:'42',price:'20'}])},
  '#service-picker':picker,'#item-template':{innerHTML:'<div name="items-__prefix__-description"></div>'},
  '#live-total':total,'#add-item':control('item'),'#add-service':control('service')};
vm.runInNewContext(fs.readFileSync('billing/static/billing/app.js','utf8'),{
  document:{querySelector:selector=>elements[selector]||null,querySelectorAll:()=>[],createElement:()=>({})},
  alert(){},Date,Number,String,JSON
});
handlers['service:click']();
assert.equal(rows[0].index,2);
assert.equal(rows[0].fields.description.value,'Neue Vorlage');
assert.equal(management.value,'3','Reuse the unpersisted blank extra row');
handlers['item:click']();
assert.equal(rows[0].index,3);assert.equal(Number(management.value),4);
assert.equal(focused,3);assert.equal(scrolled,3);
rows[0].fields.description.value='Neue manuelle Leistung';
handlers['service:click']();
assert.deepEqual(rows.map(r=>r.index),[4,3,2,1,0]);
assert.equal(rows[0].fields.description.value,'Neue Vorlage');
assert.equal(Number(management.value),5);
assert.equal(focused,4);assert.equal(scrolled,4);
assert.equal(rows[4].fields.description.value,'Erste Leistung');
assert.equal(rows[3].fields.description.value,'Zweite Leistung');
// An emptied persisted row must keep its position and must not be overwritten by a template.
rows.forEach(r=>{if(!r.fields.id.value)r.fields.DELETE.checked=true;});
rows[3].fields.description.value='';
handlers['service:click']();
assert.equal(rows[0].index,5);
assert.equal(rows.find(r=>r.index===1).fields.description.value,'');
management.value='100';
const before=rows.length;
handlers['item:click']();assert.equal(rows.length,before);
console.log('Invoice editor: prepend, stable form indices, template reuse, focus, scrolling and row limit passed.');
