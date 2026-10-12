'use strict';

const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => Array.from(root.querySelectorAll(selector));
const currency = n => {
  const v = Number(n || 0);
  return 'Rs ' + (Number.isFinite(v) ? v : 0).toLocaleString('en-PK', { maximumFractionDigits: v && Math.abs(v) < 100 ? 2 : 0 });
};
const shortNum = n => {
  const v = Math.abs(Number(n || 0));
  return v >= 1000000 ? `${(v/1000000).toFixed(1)}m` : v >= 1000 ? `${(v/1000).toFixed(v >= 10000 ? 0 : 1)}k` : Math.round(v).toString();
};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
const today = () => { const d=new Date(); const y=d.getFullYear(); const m=String(d.getMonth()+1).padStart(2,'0'); const day=String(d.getDate()).padStart(2,'0'); return `${y}-${m}-${day}`; };
const expenseCategories = ['Food & Drinks', 'Shopping', 'Transport', 'Bills & Utilities', 'Subscriptions', 'Family', 'Health', 'Education', 'Rent', 'Entertainment', 'Other'];
const incomeCategories = ['Video Editing', 'Motion Graphics', 'Content Marketing', 'Retainer', 'Consulting', 'Other Income'];
const palette = ['#a597fc','#5dd6ad','#f18c95','#e9b977','#73a8ed','#c785db','#6bd5d3','#ccad74','#7e9aa5','#de9fc0','#abb1ba'];
const pageTitles = { dashboard:['Overview','Good to have you here','A clear picture of your personal and freelance finances.'], transactions:['Transactions','Every rupee, accounted for.','Record client invoices, payments and personal spending.'], budgets:['Monthly budgets','Spend with a plan.','Give every month its own spending limits.'], goals:['Savings goals','Make your goals real.','Set targets and celebrate every bit of progress.'] };
const state = {csrf:'',email:'',month:today().slice(0,7),page:'dashboard',filter:'all',tx:[],summary:{},budgets:[],goals:[]};
let toastTimeout;
function toast(message, error=false) {const el=$('#toast'); el.textContent=message;el.classList.toggle('error',error);el.classList.remove('hidden');clearTimeout(toastTimeout);toastTimeout=setTimeout(()=>el.classList.add('hidden'),4400);}
function setAuthError(text) {const el=$('#auth-error');el.textContent=text;el.classList.toggle('hidden',!text);}
async function api(url, {method='GET', body, ...extra}={}) {
  const options={method,credentials:'same-origin',headers: {}, ...extra};
  if (body!==undefined) {options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  if (method!=='GET' && method!=='HEAD') options.headers['X-CSRF-Token']=state.csrf;
  let res;
  try {res=await fetch(url,options);}catch(e){throw new Error('Cannot reach the server. Check your connection.');}
  const content=await res.json().catch(()=>({}));
  if (!res.ok) {
    if(res.status===401 && url!=='/api/login') {showAuth(false);}
    throw new Error(content.error || `Request failed (${res.status})`);
  }
  return content;
}
function showAuth(isSetup, enabled=true){
  $('#auth-screen').classList.remove('hidden');$('#app-shell').classList.add('hidden');
  $('#setup-form').classList.toggle('hidden',!isSetup || !enabled);
  $('#login-form').classList.toggle('hidden',!!isSetup);
  $('#auth-heading').textContent=isSetup ? 'Set up your vault.' : 'Welcome back.';
  $('#auth-subtitle').textContent = isSetup ? (enabled ? 'Create your private owner account to get started.' : 'Configure OWNER_SETUP_KEY on the server to enable account creation.') : 'Your money. Your clients. Your peace of mind.';
  setAuthError('');
}
async function bootstrap(){
  try{
    const b=await api('/api/bootstrap'); state.csrf=b.csrf;
    if (!b.authenticated) {showAuth(b.setup_required,b.setup_enabled);return;}
    state.email=b.email; $('#account-email').textContent=b.email; $('.profile-avatar').textContent=b.email.charAt(0).toUpperCase();
    $('#auth-screen').classList.add('hidden');$('#app-shell').classList.remove('hidden');
    $('#month-input').value=state.month; await refresh();
  }catch(err){showAuth(false);setAuthError(err.message);}
}
function setPage(page){
  if(!pageTitles[page])return;
  state.page=page;
  $$('.page-view').forEach(el=>el.classList.toggle('hidden',el.id!==`${page}-page`));
  $$('.nav-link[data-page]').forEach(el=>el.classList.toggle('active',el.dataset.page===page));
  $('#breadcrumb-page').textContent=pageTitles[page][0];$('#page-heading').innerHTML=escapeHTML(pageTitles[page][1])+(page==='dashboard'?' <span class="greeting-star">✳</span>':'');
  $('#page-subtitle').textContent=pageTitles[page][2];
  $('#sidebar').classList.remove('open');
}
async function refresh(){
  try{
    const m=encodeURIComponent(state.month);
    const [summary, trans, budgets, goals] = await Promise.all([
      api(`/api/summary?month=${m}`),api(`/api/transactions?month=${m}`),api(`/api/budgets?month=${m}`),api('/api/goals')
    ]);
    state.summary=summary;state.tx=trans.transactions;state.budgets=budgets.budgets;state.goals=goals.goals;
    renderAll();$('#transactions-limit').classList.toggle('hidden',!trans.limited);
  }catch(err){toast(err.message,true);}
}
function renderAll(){renderDashboard();renderTransactions();renderBudgets();renderGoals();}
function setMoney(selector, value){$(selector).textContent=currency(value);}
function renderDashboard(){
  const s=state.summary;
  setMoney('#metric-income',s.income);setMoney('#metric-expense',s.expenses);setMoney('#metric-savings',s.savings);setMoney('#metric-pending',s.pending);
  $('#metric-savings').style.color=(s.savings<0?'#f38c93':'');
  drawChart(s.history || []);drawDonut(s.by_category || [],s.expenses||0);
  setMoney('#budget-summary-used',s.expenses);setMoney('#budget-summary-limit',s.budget);
  const pct=s.budget>0?(s.expenses/s.budget*100):0;
  $('#budget-overview-progress').style.width=`${Math.min(100,pct)}%`;
  $('#budget-overview-progress').classList.toggle('over',pct>100);
  $('#budget-summary-detail').textContent=s.budget ? `${Math.round(pct)}% of budget used · ${currency(s.budget - s.expenses)} remaining` : 'Set a monthly budget to get started.';
  renderClients(s.clients||[]);renderRecent(state.tx.slice(0,6));
}
function drawChart(history){
  const container=$('#cashflow-chart');
  if(!history.length){container.innerHTML='<div class="empty-state"><p>Chart will appear when you add transactions.</p></div>';return;}
  const W=710,H=220,L=42,R=12,T=18,B=34, plotW=W-L-R,plotH=H-T-B;
  const max=Math.max(1000,...history.map(d=>Math.max(d.income,d.expenses)))*1.12;
  const x=i=>L+i*plotW/11,y=v=>T+plotH-Number(v)/max*plotH;
  const line=key=>history.map((d,i)=>`${i===0?'M':'L'} ${x(i).toFixed(2)} ${y(d[key]).toFixed(2)}`).join(' ');
  const area=key=>`${line(key)} L ${x(11)} ${y(0)} L ${x(0)} ${y(0)} Z`;
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Income and expenses by month"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a597fc" stop-opacity=".22"/><stop offset="1" stop-color="#a597fc" stop-opacity="0"/></linearGradient></defs>`;
  for(let i=0;i<5;i++){const v=max*i/4;const yy=y(v);svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#2b3546" stroke-dasharray="3,6"/><text x="${L-9}" y="${yy+3}" text-anchor="end">${shortNum(v)}</text>`;}
  svg+=`<path d="${area('income')}" fill="url(#chart-fill)"/><path d="${line('income')}" fill="none" stroke="#a597fc" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><path d="${line('expenses')}" fill="none" stroke="#5dd6ad" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`;
  for(let i=0;i<history.length;i++){const d=history[i]; const label=new Date(d.month+'-01T12:00:00').toLocaleString('en-US',{month:'short'});svg+=`<text x="${x(i)}" y="${H-5}" text-anchor="middle">${label}</text>`;
    svg+=`<circle cx="${x(i)}" cy="${y(d.income)}" r="3.5" fill="#a597fc"><title>${label} income: ${currency(d.income)}</title></circle><circle cx="${x(i)}" cy="${y(d.expenses)}" r="3" fill="#5dd6ad"><title>${label} expenses: ${currency(d.expenses)}</title></circle>`;}
  svg+='</svg>';container.innerHTML=svg;
}
function drawDonut(categories,total){
  setMoney('#spend-donut-label',total);
  const donut=$('#spend-donut'),legend=$('#spend-legend');
  if(!categories.length || !total){donut.style.background='#2b3545';legend.innerHTML='<p class="empty-muted">Start adding expenses to see where your money goes.</p>';return;}
  const selected=categories.slice(0,6), other=categories.slice(6).reduce((s,x)=>s+x.amount,0);
  if(other)selected.push({category:'Other categories',amount:other});
  let pos=0;const slices=[];
  legend.innerHTML=selected.map((item,i)=>{const percent=item.amount/total*100,from=pos;pos+=percent;slices.push(`${palette[i]} ${from.toFixed(2)}% ${pos.toFixed(2)}%`);
    return `<div class="legend-row"><span class="legend-name" title="${escapeHTML(item.category)}"><i data-legend-color="${i}"></i>${escapeHTML(item.category)}</span><strong>${Math.round(percent)}%</strong></div>`;
  }).join('');$$('[data-legend-color]',legend).forEach(el=>el.style.backgroundColor=palette[Number(el.dataset.legendColor)]);donut.style.background=`conic-gradient(${slices.join(',')})`;
}
function renderClients(clients){
  const el=$('#clients-list');
  if(!clients.length){el.innerHTML='<p class="empty-muted">Your client earnings and outstanding invoices will show up here.</p>';return;}
  el.innerHTML=clients.slice(0,4).map(c=>`<div class="client-row"><div class="client-left"><div class="client-avatar">${escapeHTML(c.client[0].toUpperCase())}</div><div><strong title="${escapeHTML(c.client)}">${escapeHTML(c.client)}</strong><small>${currency(c.received)} received · lifetime</small></div></div><div class="client-right"><strong>${currency(c.invoiced)}</strong><small>${currency(c.outstanding)} pending</small></div></div>`).join('');
}
function renderRecent(rows){
  const el=$('#recent-list');
  if(!rows.length){el.innerHTML='<div class="empty-state"><span>⇅</span><h3>No activity yet</h3><p>Add your first income or expense to see it here.</p></div>';return;}
  el.innerHTML=rows.map(t=>`<div class="recent-row"><div class="recent-left"><div class="recent-icon ${t.kind}">${t.kind==='income'?'↙':'↗'}</div><div class="recent-name"><strong title="${escapeHTML(t.title)}">${escapeHTML(t.title)}</strong><small>${escapeHTML(t.category)} · ${escapeHTML(t.date)}</small></div></div><div class="recent-sum ${t.kind}">${t.kind==='income'?'+':'−'}${currency(t.received_pkr)}</div></div>`).join('');
}
function renderTransactions(){
  const search=$('#tx-search').value.trim().toLowerCase();
  const rows=state.tx.filter(t=>(state.filter==='all'||t.kind===state.filter)&&(!search||[t.title,t.category,t.client,t.project,t.notes].join(' ').toLowerCase().includes(search)));
  $('#transaction-count').textContent=rows.length;
  const tbody=$('#transactions-body');
  if(!rows.length){tbody.innerHTML='<tr><td colspan="7"><div class="empty-state"><span>⇅</span><h3>No transactions found</h3><p>Add an entry or try a different month or filter.</p></div></td></tr>';return;}
  tbody.innerHTML=rows.map(t=>`<tr><td><div class="tx-name"><div class="tx-type-icon ${t.kind}">${t.kind==='income'?'↙':'↗'}</div><div><strong title="${escapeHTML(t.title)}">${escapeHTML(t.title)}</strong><small>${t.kind==='income'?'Income':'Expense'}</small></div></div></td><td>${escapeHTML(t.client||t.category)}</td><td>${escapeHTML(t.date)}</td><td class="tx-amount">${escapeHTML(t.currency)} ${Number(t.amount).toLocaleString('en-PK')}</td><td class="tx-amount ${t.kind}">${currency(t.received_pkr)}</td><td><span class="status-pill ${t.status}">${t.kind==='expense'?'Spent':t.status}</span></td><td><div class="row-actions"><button data-edit-tx="${t.id}" title="Edit">✎</button><button data-delete-tx="${t.id}" title="Delete">✕</button></div></td></tr>`).join('');
}
function renderBudgets(){
  const categories=[...new Set([...expenseCategories,...state.budgets.map(x=>x.category),...state.summary.by_category.map(x=>x.category)])];
  const spend=Object.fromEntries(state.summary.by_category.map(x=>[x.category,x.amount]));
  const limit=Object.fromEntries(state.budgets.map(x=>[x.category,x.limit]));
  setMoney('#budgets-page-total',state.summary.budget);
  $('#budget-categories').innerHTML=categories.map((cat,i)=>{
    const used=spend[cat]||0,allowed=limit[cat]||0,percent=allowed?used/allowed*100:0;
    return `<div class="budget-category-card"><div class="budget-category-head"><strong>${escapeHTML(cat)}</strong><small>${allowed ? `${Math.round(percent)}% used`:'No limit set'}</small></div><div class="progress-track"><div class="progress-fill ${percent>100?'over':''}" data-budget-progress="${Math.min(100,percent)}"></div></div><div class="budget-category-bottom"><span class="budget-spent">${currency(used)} spent</span><form class="budget-edit" data-budget-category="${escapeHTML(cat)}"><span>Rs</span><input aria-label="${escapeHTML(cat)} budget in PKR" type="number" min="0" step="0.01" value="${allowed}" required><button type="submit" title="Save this category">Save</button></form></div></div>`;
  }).join('');
  $$('[data-budget-progress]').forEach(el=>el.style.width=el.dataset.budgetProgress+'%');
}
function renderGoals(){
  const el=$('#goals-grid');
  if(!state.goals.length){el.innerHTML='<div class="panel empty-state"><span>◎</span><h3>What are you saving for?</h3><p>Create a goal such as a laptop, emergency fund or a future trip.</p><button class="button primary" id="goal-empty-add" >+ Add your first goal</button></div>';return;}
  el.innerHTML=state.goals.map(g=>{const saved=Number(g.saved),target=Number(g.target),pct=target>0?saved/target*100:0;
    return `<div class="goal-card"><div class="goal-card-top"><div class="goal-icon">◎</div><div class="row-actions"><button data-edit-goal="${g.id}" title="Edit">✎</button><button data-delete-goal="${g.id}" title="Delete">✕</button></div></div><h3>${escapeHTML(g.name)}</h3><p class="muted">${g.due_month ? `Target: ${escapeHTML(g.due_month)}` : 'No deadline set'}</p><div class="goal-figures"><strong>${currency(saved)}</strong><span>/ ${currency(target)}</span></div><div class="progress-track"><div class="progress-fill" data-goal-progress="${Math.min(100,pct)}"></div></div><div class="goal-bottom"><span>${Math.round(pct)}% complete</span><span>${currency(Math.max(0,target-saved))} to go</span></div></div>`;
  }).join('');
  $$('[data-goal-progress]').forEach(el=>el.style.width=el.dataset.goalProgress+'%');
}
function openModal(id){$('#'+id).classList.remove('hidden');const field=$('#'+id+' input:not([type="hidden"])');field?.focus();}
function closeModal(id){$('#'+id).classList.add('hidden');}
function setKind(kind){
  $('#tx-form [name="kind"]').value=kind;
  $$('[data-set-kind]').forEach(b=>b.classList.toggle('selected',b.dataset.setKind===kind));
  $('#freelance-fields').classList.toggle('hidden',kind!=='income');
  const cat=$('#tx-category');const selected=cat.value;
  cat.innerHTML=(kind==='income'?incomeCategories:expenseCategories).map(name=>`<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join('');
  if([...cat.options].some(o=>o.value===selected))cat.value=selected;
}
function syncRate(){const currency=$('#tx-form [name="currency"]').value,isUSD=currency==='USD';$('#rate-group').classList.toggle('hidden',!isUSD);const rate=$('#tx-form [name="rate"]');rate.required=isUSD; if(!isUSD)rate.value='1';else if(rate.value==='1')rate.value='';}
function addTransaction(kind='income'){
  const form=$('#tx-form');form.reset();form.elements.tx_id.value='';form.elements.date.value=today();form.elements.received_date.value=today();form.elements.currency.value='PKR';form.elements.rate.value='1';form.elements.received.value='';setKind(kind);syncRate();$('#tx-modal-heading').textContent='Add transaction';$('#tx-submit').textContent='Save transaction →';openModal('tx-modal');
}
function editTransaction(id){
  const tx=state.tx.find(t=>t.id===id);if(!tx)return;
  addTransaction(tx.kind);const form=$('#tx-form');
  form.elements.tx_id.value=tx.id;form.elements.title.value=tx.title;form.elements.date.value=tx.date;form.elements.category.value=tx.category;
  if(form.elements.category.value!==tx.category){form.elements.category.insertAdjacentHTML('beforeend',`<option value="${escapeHTML(tx.category)}">${escapeHTML(tx.category)}</option>`);form.elements.category.value=tx.category;}
  form.elements.amount.value=tx.amount;form.elements.currency.value=tx.currency;form.elements.rate.value=tx.rate;
  form.elements.client.value=tx.client;form.elements.project.value=tx.project;form.elements.received.value=tx.received;form.elements.received_date.value=tx.received_date; form.elements.notes.value=tx.notes;
  syncRate();$('#tx-modal-heading').textContent='Edit transaction';$('#tx-submit').textContent='Save changes →';
}
function openGoal(goal){
  const f=$('#goal-form');f.reset();f.elements.goal_id.value=goal?.id||'';f.elements.name.value=goal?.name||'';f.elements.target.value=goal?.target||'';f.elements.saved.value=goal?.saved||'0';f.elements.due_month.value=goal?.due_month||'';
  $('#goal-modal-heading').textContent=goal?'Edit savings goal':'New savings goal';openModal('goal-modal');
}
async function download(url,filename){try{const r=await fetch(url,{credentials:'same-origin'});if(!r.ok)throw Error('Download failed. Sign in again.');const blob=await r.blob();const href=URL.createObjectURL(blob);const a=document.createElement('a');a.href=href;a.download=filename;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(href);toast('File downloaded successfully.');}catch(e){toast(e.message,true);}}

$('#login-form').addEventListener('submit', async e=>{
  e.preventDefault();setAuthError('');const f=e.currentTarget,submit=$('button[type="submit"]',f);submit.disabled=true;
  try{const out=await api('/api/login',{method:'POST',body:{email:f.elements.email.value,password:f.elements.password.value}});state.csrf=out.csrf;await bootstrap();}catch(err){setAuthError(err.message);}finally{submit.disabled=false;}
});
$('#setup-form').addEventListener('submit',async e=>{
  e.preventDefault();setAuthError('');const f=e.currentTarget,submit=$('button[type="submit"]',f);submit.disabled=true;
  try{await api('/api/setup',{method:'POST',body:{email:f.elements.email.value,password:f.elements.password.value,setup_key:f.elements.setup_key.value}});f.reset();showAuth(false);toast('Owner account created. Sign in now.');}catch(err){setAuthError(err.message);}finally{submit.disabled=false;}
});
$('#logout-btn').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'});}catch(e){}state.csrf='';await bootstrap();});
$$('[data-page]').forEach(el=>el.addEventListener('click',()=>setPage(el.dataset.page)));
$$('[data-goto]').forEach(el=>el.addEventListener('click',()=>setPage(el.dataset.goto)));
$('#menu-toggle').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
$('#month-input').addEventListener('change',e=>{if(e.target.value&&/^\d{4}-(0[1-9]|1[0-2])$/.test(e.target.value)){state.month=e.target.value;refresh();}});
function shiftMonth(diff){const [y,m]=state.month.split('-').map(Number);const date=new Date(Date.UTC(y,m-1+diff,1));const year=date.getUTCFullYear();if(year<2020||year>2100)return;state.month=`${year}-${String(date.getUTCMonth()+1).padStart(2,'0')}`;$('#month-input').value=state.month;refresh();}
$('#month-prev').addEventListener('click',()=>shiftMonth(-1));$('#month-next').addEventListener('click',()=>shiftMonth(1));
$('#top-add').addEventListener('click',()=>addTransaction());$$('[data-add]').forEach(b=>b.addEventListener('click',()=>addTransaction()));
$('#tx-search').addEventListener('input',renderTransactions);
$$('#tx-filters button').forEach(el=>el.addEventListener('click',()=>{state.filter=el.dataset.kind;$$('#tx-filters button').forEach(x=>x.classList.toggle('selected',x===el));renderTransactions();}));
$$('[data-set-kind]').forEach(b=>b.addEventListener('click',()=>setKind(b.dataset.setKind)));
$('#tx-form [name="currency"]').addEventListener('change',syncRate);
$('#tx-form').addEventListener('submit',async e=>{
  e.preventDefault();const f=e.currentTarget, data=Object.fromEntries(new FormData(f));
  const id=data.tx_id;delete data.tx_id;
  if(data.kind==='income' && !data.received) data.received=data.amount;
  if(data.currency==='PKR')data.rate='1';
  const btn=$('#tx-submit');btn.disabled=true;
  try{await api(id?`/api/transactions/${id}`:'/api/transactions',{method:id?'PUT':'POST',body:data});closeModal('tx-modal');toast('Transaction saved.');await refresh();}
  catch(err){toast(err.message,true);}finally{btn.disabled=false;}
});
$('#transactions-body').addEventListener('click',async e=>{
  const edit=e.target.closest('[data-edit-tx]'),del=e.target.closest('[data-delete-tx]');
  if(edit)editTransaction(Number(edit.dataset.editTx));
  if(del && confirm('Delete this transaction permanently?')){try{await api(`/api/transactions/${del.dataset.deleteTx}`,{method:'DELETE'});toast('Transaction deleted.');await refresh();}catch(err){toast(err.message,true);}}
});
$('#budget-categories').addEventListener('submit',async e=>{
  const f=e.target.closest('[data-budget-category]');if(!f)return;e.preventDefault();const btn=$('button',f);btn.disabled=true;
  try{await api('/api/budgets',{method:'PUT',body:{month:state.month,category:f.dataset.budgetCategory,limit:$('input',f).value}});toast('Monthly budget saved.');await refresh();}catch(err){toast(err.message,true);}finally{btn.disabled=false;}
});
$('#add-goal').addEventListener('click',()=>openGoal(null));
$('#goals-grid').addEventListener('click',async e=>{
  if(e.target.id==='goal-empty-add'){openGoal(null);return;}
  const edit=e.target.closest('[data-edit-goal]'),del=e.target.closest('[data-delete-goal]');
  if(edit)openGoal(state.goals.find(x=>x.id===Number(edit.dataset.editGoal)));
  if(del && confirm('Delete this savings goal permanently?')){try{await api(`/api/goals/${del.dataset.deleteGoal}`,{method:'DELETE'});toast('Savings goal removed.');await refresh();}catch(err){toast(err.message,true);}}
});
$('#goal-form').addEventListener('submit',async e=>{
  e.preventDefault();const f=e.currentTarget,data=Object.fromEntries(new FormData(f)),id=data.goal_id;delete data.goal_id;const btn=$('button[type="submit"]',f);btn.disabled=true;
  try{await api(id?`/api/goals/${id}`:'/api/goals',{method:id?'PUT':'POST',body:data});closeModal('goal-modal');toast('Savings goal saved.');await refresh();}catch(err){toast(err.message,true);}finally{btn.disabled=false;}
});
$$('[data-close]').forEach(b=>b.addEventListener('click',()=>closeModal(b.dataset.close)));
$$('.modal-layer').forEach(layer=>layer.addEventListener('click',e=>{if(e.target===layer)closeModal(layer.id);}));
document.addEventListener('keydown',e=>{if(e.key==='Escape')$$('.modal-layer:not(.hidden)').forEach(layer=>closeModal(layer.id));});
$('#export-csv').addEventListener('click',()=>download('/api/export.csv','finance-transactions.csv'));
$('#export-backup').addEventListener('click',()=>download('/api/backup.json','finance-backup.json'));
bootstrap();
