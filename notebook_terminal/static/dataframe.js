function render({model, el}) {
  el.className = 'nbterm-grid';
  let rows = model.get('rows') || [];
  let columns = model.get('columns') || [];
  let filtered = rows.slice();
  let page = 0, pageSize = 25, sortCol = null, sortAsc = true;
  const toolbar=document.createElement('div'); toolbar.className='nbterm-grid-toolbar';
  const search=document.createElement('input'); search.type='search'; search.placeholder='Filtruj wszystkie kolumny…';
  const select=document.createElement('select'); [10,25,50,100].forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=`${n} wierszy`;if(n===25)o.selected=true;select.appendChild(o)});
  const status=document.createElement('span'); toolbar.append(search,select,status);
  const tableWrap=document.createElement('div'); tableWrap.className='nbterm-grid-wrap';
  const table=document.createElement('table'); table.className='nbterm-grid-table'; tableWrap.appendChild(table);
  const pager=document.createElement('div'); pager.className='nbterm-grid-pager';
  const prev=document.createElement('button');prev.textContent='‹';prev.type='button';
  const next=document.createElement('button');next.textContent='›';next.type='button';
  const pageLabel=document.createElement('span');pager.append(prev,pageLabel,next);
  el.append(toolbar,tableWrap,pager);
  function compare(a,b){ if(a==null&&b==null)return 0;if(a==null)return 1;if(b==null)return -1;const an=Number(a),bn=Number(b);if(!Number.isNaN(an)&&!Number.isNaN(bn))return an-bn;return String(a).localeCompare(String(b),undefined,{numeric:true,sensitivity:'base'}); }
  function apply(){ const q=search.value.trim().toLowerCase(); filtered=!q?rows.slice():rows.filter(r=>columns.some(c=>String(r[c]??'').toLowerCase().includes(q))); if(sortCol) filtered.sort((a,b)=>(sortAsc?1:-1)*compare(a[sortCol],b[sortCol])); const pages=Math.max(1,Math.ceil(filtered.length/pageSize)); page=Math.min(page,pages-1); renderTable(); }
  function renderTable(){ table.innerHTML='';const thead=document.createElement('thead'),tr=document.createElement('tr');columns.forEach(c=>{const th=document.createElement('th');th.textContent=c+(sortCol===c?(sortAsc?' ▲':' ▼'):'');th.addEventListener('click',()=>{if(sortCol===c)sortAsc=!sortAsc;else{sortCol=c;sortAsc=true}apply()});tr.appendChild(th)});thead.appendChild(tr);table.appendChild(thead);const tbody=document.createElement('tbody');const start=page*pageSize;filtered.slice(start,start+pageSize).forEach(r=>{const tr=document.createElement('tr');columns.forEach(c=>{const td=document.createElement('td');const v=r[c];td.textContent=v==null?'':String(v);tr.appendChild(td)});tbody.appendChild(tr)});table.appendChild(tbody);const pages=Math.max(1,Math.ceil(filtered.length/pageSize));status.textContent=`${filtered.length} / ${rows.length} wierszy`;pageLabel.textContent=`Strona ${page+1} z ${pages}`;prev.disabled=page<=0;next.disabled=page>=pages-1; }
  search.addEventListener('input',()=>{page=0;apply()});select.addEventListener('change',()=>{pageSize=Number(select.value);page=0;apply()});prev.addEventListener('click',()=>{if(page>0){page--;renderTable()}});next.addEventListener('click',()=>{if((page+1)*pageSize<filtered.length){page++;renderTable()}});apply();
}
export default {render};
