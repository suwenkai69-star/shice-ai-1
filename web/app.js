(() => {
  const API = window.ShiceAPI;
  const $ = (s, root=document) => root.querySelector(s);
  const $$ = (s, root=document) => [...root.querySelectorAll(s)];
  const state = { page:"today", store:null, uploadId:null, chat:[] };
  const titles = {
    today:["TODAY","今天"], upload:["IMPORT","上传"], actions:["ACTIONS","待办"],
    data:["DATA","数据"], chat:["ASK SHICE AI","问食策AI"], profile:["PROFILE","我的"]
  };
  const categoryLabels = {
    MILK_TEA:"奶茶饮品",COFFEE:"咖啡",FAST_FOOD_SNACK:"快餐小吃",CHINESE_DINING:"中式正餐",
    HOTPOT:"火锅",BBQ:"烧烤",BAKERY_DESSERT:"烘焙甜品",BAR_LEISURE:"酒吧/休闲娱乐",OTHER_FOOD:"其他餐饮"
  };

  function escapeHtml(v){ return String(v ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
  function money(v){ if(v===null || v===undefined || v==="") return "—"; const n=Number(v); return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN",{minimumFractionDigits:0,maximumFractionDigits:2})}` : "—"; }
  function num(v){ const n=Number(v); return Number.isFinite(n) ? n : 0; }
  function dateISO(){ return new Date().toLocaleDateString("sv-SE", {timeZone:"Asia/Shanghai"}); }
  function dateCN(){ return new Intl.DateTimeFormat("zh-CN",{month:"long",day:"numeric",weekday:"short",timeZone:"Asia/Shanghai"}).format(new Date()); }
  function toast(msg, error=false){ const el=$("#toast"); el.textContent=msg; el.className=`toast show${error?" error":""}`; clearTimeout(toast.t); toast.t=setTimeout(()=>el.className="toast",2600); }
  function setLoading(){ $("#pageContent").innerHTML=`<div class="grid-2"><div class="card"><div class="skeleton"></div><div class="skeleton" style="margin-top:16px;height:70px"></div></div><div class="card"><div class="skeleton"></div><div class="skeleton" style="margin-top:16px;height:70px"></div></div></div>`; }
  function detailMessage(err){ if(typeof err?.payload?.detail === "object") return err.payload.detail.message || JSON.stringify(err.payload.detail); return err?.message || "请求失败"; }

  function showView(name){
    ["loginView","onboardingView","appView"].forEach(id=>$("#"+id).classList.add("hidden"));
    $("#"+name).classList.remove("hidden");
  }

  async function bootstrap(){
    $("#todayDate").textContent=dateCN();
    if(!API.getToken()){ showView("loginView"); return; }
    try{
      const data = await API.request("/store");
      state.store=data.store; enterApp();
    }catch(err){
      if(err.status===404){ showView("onboardingView"); }
      else { API.clearToken(); showView("loginView"); }
    }
  }

  async function login(e){
    e.preventDefault();
    const btn=e.submitter; btn.disabled=true; btn.textContent="正在进入…";
    try{
      const result=await API.request("/auth/web",{method:"POST",body:{password:$("#loginPassword").value}});
      API.setToken(result.token); state.store=result.store || null;
      $("#loginPassword").value="";
      if(result.has_store) enterApp(); else showView("onboardingView");
    }catch(err){ toast(detailMessage(err),true); }
    finally{ btn.disabled=false; btn.textContent="进入我的门店"; }
  }

  async function createStore(e){
    e.preventDefault(); const btn=e.submitter; btn.disabled=true;
    try{
      const result=await API.request("/store",{method:"POST",body:{name:$("#storeName").value.trim(),category_code:$("#storeCategory").value}});
      state.store=result.store; enterApp(); toast("门店已建立");
    }catch(err){ toast(detailMessage(err),true); }
    finally{ btn.disabled=false; }
  }

  function enterApp(){ showView("appView"); $("#sidebarStoreName").textContent=state.store?.name || "我的门店"; navigate("today"); }

  function syncNav(page){
    $$('[data-page]').forEach(b=>b.classList.toggle("active",b.dataset.page===page));
    const [eyebrow,title]=titles[page]; $("#pageEyebrow").textContent=eyebrow; $("#pageTitle").textContent=title;
  }

  async function navigate(page){
    state.page=page; syncNav(page); setLoading();
    try{
      if(page==="today") await renderToday();
      else if(page==="upload") renderUpload();
      else if(page==="actions") await renderActions();
      else if(page==="data") await renderData();
      else if(page==="chat") renderChat();
      else if(page==="profile") await renderProfile();
    }catch(err){
      if(err.status===401){ API.clearToken(); showView("loginView"); return; }
      $("#pageContent").innerHTML=errorCard(detailMessage(err));
    }
  }

  function errorCard(message){ return `<div class="empty"><strong>这部分暂时没加载出来</strong><p>${escapeHtml(message)}</p><button class="btn btn-secondary" onclick="window.ShiceApp.refresh()">重新加载</button></div>`; }
  function emptyCard(title,body,button="去上传数据",page="upload"){ return `<div class="empty"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(body)}</p><button class="btn btn-secondary" data-jump="${page}">${button}</button></div>`; }

  function profitText(p){
    if(!p || p.status==="INSUFFICIENT_DATA" || (p.profit_low==null && p.profit_high==null)) return "暂时算不准";
    if(p.profit_low===p.profit_high) return money(p.profit_low);
    return `${money(p.profit_low)} ～ ${money(p.profit_high)}`;
  }
  function confidenceLabel(v){ return ({HIGH:"高",MEDIUM:"中",LOW:"低"}[v] || v || "待补数据"); }
  function completenessLabel(v){ return ({COMPLETE:"已完整",DECLARED_COMPLETE:"已确认完整",PARTIAL:"部分数据",EMPTY:"暂无数据"}[v] || v || "暂无数据"); }

  async function renderToday(){
    const data=await API.request(`/today?date=${dateISO()}`);
    const sales=data.sales||{}, p=data.profit||{}, ds=data.data_status||{}, issues=data.top_issues||[];
    const received=(ds.received_sources||[]).length, expected=(ds.expected_sources||[]).length, pct=expected?Math.round(received/expected*100):(ds.complete?100:0);
    const issueHtml=issues.length ? issues.map((x,i)=>`<div class="card issue-card ${String(x.severity).toLowerCase()==="high"?"high":""}"><div class="issue-title">${i+1}. ${escapeHtml(x.title||"需要关注")}</div><div class="issue-message">${escapeHtml(x.message||"")}</div><div class="issue-foot"><span class="tag">${escapeHtml(x.severity||"待判断")}</span><span class="tag">可信度 ${escapeHtml(x.confidence||"—")}</span></div></div>`).join("") : emptyCard("今天还没有明确异常","数据不足时我不会硬给结论。先把今天的营业数据补进来。", "去上传", "upload");
    const missing=(ds.missing_sources||[]).map(x=>`<span class="source-pill missing">缺 ${escapeHtml(x)}</span>`).join("");
    const receivedPills=(ds.received_sources||[]).map(x=>`<span class="source-pill">${escapeHtml(x)}</span>`).join("");
    $("#pageContent").innerHTML=`
      <div class="hero-grid">
        <div class="hero-card"><p class="eyebrow">今天到目前为止</p><div class="hero-number">${money(sales.amount)}</div><div class="hero-caption">营业额${sales.orders!=null?` · ${sales.orders} 单`:""}${sales.avg_order_value?` · 客单 ${money(sales.avg_order_value)}`:""}</div><div class="hero-status"><span class="pill">${escapeHtml(completenessLabel(ds.completeness_level))}</span>${data.reconciliation?.user_message?`<span class="pill">${escapeHtml(data.reconciliation.user_message)}</span>`:""}</div></div>
        <div class="card"><div class="card-head"><div><h3>今天大概赚多少</h3><p>明确区分实际与估算</p></div><span class="tag">可信度 ${escapeHtml(confidenceLabel(p.confidence_level))}</span></div><div class="metric-big">${profitText(p)}</div><div class="metric-sub">${p.status==="READY"?"按当前已录入收入与成本测算":"数据还不够，先不要把估算当实际利润"}</div>${p.missing_data?.length?`<div class="source-list">${p.missing_data.map(x=>`<span class="source-pill missing">缺 ${escapeHtml(x)}</span>`).join("")}</div>`:""}</div>
      </div>
      <section class="section"><div class="section-title"><h3>数据够不够</h3><p>${received}/${expected||0} 类已收到</p></div><div class="card"><div class="progress"><span style="width:${Math.max(0,Math.min(100,pct))}%"></span></div><div class="source-list">${receivedPills}${missing}</div>${!ds.complete&&expected?`<div style="margin-top:14px"><button class="btn btn-secondary" id="declareCompleteBtn">这些就是今天全部数据</button></div>`:""}</div></section>
      <section class="section"><div class="section-title"><h3>今天最值得关注的问题</h3><p>最多只看 3 个</p></div><div class="grid-3">${issueHtml}</div></section>
      <section class="section"><div class="section-title"><h3>今天最该做的事</h3><p>${data.pending_actions||0} 个待办</p></div><div id="todayActions"></div></section>`;
    bindJumps();
    $("#declareCompleteBtn")?.addEventListener("click", async()=>{ try{ await API.request(`/data-status/today/complete?business_date=${dateISO()}`,{method:"POST"}); toast("已按你的确认标记为完整"); renderToday(); }catch(e){toast(detailMessage(e),true);} });
    await renderActionList("#todayActions",3);
  }

  async function getActions(){ return (await API.request(`/actions?date=${dateISO()}`)).actions || []; }
  function actionCard(a,i){
    const steps=(a.steps||[]).map(s=>`<li>${escapeHtml(s)}</li>`).join("");
    return `<div class="card action-card"><div class="action-index">${i+1}</div><div><div class="action-title">${escapeHtml(a.title)}</div><div class="action-reason">${escapeHtml(a.reason||"")}</div>${steps?`<ol class="action-steps">${steps}</ol>`:""}${a.requires_user_confirmation?`<div class="source-list"><span class="source-pill missing">执行前需要你确认</span></div>`:""}</div><div class="action-buttons"><button class="btn btn-secondary" data-action-execute="${a.id}" data-confirm="${a.requires_user_confirmation?1:0}">标记已做</button><button class="btn btn-ghost" data-action-skip="${a.id}">跳过</button></div></div>`;
  }
  async function renderActionList(selector,limit){
    const root=$(selector); const actions=await getActions(); const shown=limit?actions.slice(0,limit):actions;
    root.innerHTML=shown.length?`<div class="stack gap-12">${shown.map(actionCard).join("")}</div>`:emptyCard("今天没有待办","当系统发现明确问题后，会把最值得做的事情放在这里。","去看今天","today");
    bindActionButtons(root); bindJumps(root);
  }
  function bindActionButtons(root=document){
    $$('[data-action-execute]',root).forEach(btn=>btn.addEventListener("click",async()=>{
      if(btn.dataset.confirm==="1" && !confirm("这是需要老板确认的动作。确认你已经决定执行？")) return;
      try{ await API.request(`/actions/${btn.dataset.actionExecute}/execute`,{method:"POST",body:{note:"Web老板版确认执行"}}); toast("已记录为执行"); navigate(state.page); }catch(e){toast(detailMessage(e),true);}
    }));
    $$('[data-action-skip]',root).forEach(btn=>btn.addEventListener("click",async()=>{
      if(!confirm("确认今天先跳过这条建议？")) return;
      try{ await API.request(`/actions/${btn.dataset.actionSkip}/skip`,{method:"POST"}); toast("已跳过"); navigate(state.page); }catch(e){toast(detailMessage(e),true);}
    }));
  }

  function renderUpload(){
    $("#pageContent").innerHTML=`<div class="upload-grid">
      <div class="drop-card"><div class="drop-icon">表</div><h3>上传平台表格</h3><p>直接上传从美团外卖、淘宝闪购等平台导出的 Excel / CSV。不会要求你先汇总、删列。</p><input id="fileUpload" class="file-input" type="file" accept=".xlsx,.xls,.csv,.json"/><button class="btn btn-primary" data-pick="fileUpload">选择表格</button></div>
      <div class="drop-card"><div class="drop-icon">图</div><h3>上传平台截图</h3><p>把你手机里看到的经营截图直接传上来。识别结果会先给你确认，不会直接入账。</p><input id="imageUpload" class="file-input" type="file" accept="image/*"/><button class="btn btn-secondary" data-pick="imageUpload">选择截图</button></div>
    </div>
    <section class="section"><div class="card"><div class="card-head"><div><h3>为什么一定先确认</h3><p>避免把识别错的数据算进经营结果</p></div></div><div class="status-box">上传 → 系统读取 → 你看到预览 → 确认 → 才进入经营分析。遇到重复日报时，也会先问你是否替换旧数据。</div></div></section>`;
    $$('[data-pick]').forEach(btn=>btn.addEventListener("click",()=>$("#"+btn.dataset.pick).click()));
    $("#fileUpload").addEventListener("change",e=>handleUpload(e.target.files[0],"file"));
    $("#imageUpload").addEventListener("change",e=>handleUpload(e.target.files[0],"image"));
  }

  async function handleUpload(file,type){
    if(!file) return; toast("正在读取这份数据…");
    try{
      const result=await API.upload(`/uploads/${type}`,file); state.uploadId=result.id;
      const detail=await API.request(`/uploads/${result.id}`);
      showUploadPreview(detail);
    }catch(err){ toast(detailMessage(err),true); }
  }
  function showUploadPreview(detail){
    const doc=detail.document||{}; const preview=detail.preview; const fields=detail.fields||[];
    let body=`文件：${doc.original_filename||"截图"}\n状态：${doc.status||"待确认"}\n`;
    if(preview) body+=`\n系统预览：\n${JSON.stringify(preview,null,2)}`;
    if(fields.length) body+=`\n识别字段：\n${fields.map(f=>`${f.field_code}: ${f.confirmed_value ?? f.extracted_value ?? "—"}${f.confidence?`（${f.confidence}）`:""}`).join("\n")}`;
    if(!preview&&!fields.length) body+="\n暂时没有可展示的字段，请确认文件是否为支持的数据格式。";
    $("#uploadPreview").textContent=body; $("#uploadDialog").showModal();
  }
  async function confirmUpload(){
    if(!state.uploadId)return;
    try{
      const result=await API.request(`/uploads/${state.uploadId}/confirm`,{method:"POST"});
      toast("数据已导入"); $("#uploadDialog").close(); state.uploadId=null; navigate("today");
    }catch(err){
      if(err.status===409 && confirm("检测到同一天可能已有数据。是否用这份数据更新之前的数据？")){
        try{ await API.request(`/uploads/${state.uploadId}/duplicate-decision`,{method:"POST",body:{decision:"REPLACE_EXISTING"}}); toast("已用新数据更新"); $("#uploadDialog").close(); state.uploadId=null; navigate("today"); }catch(e){toast(detailMessage(e),true);}
      }else toast(detailMessage(err),true);
    }
  }
  async function rejectUpload(){ if(!state.uploadId)return; try{await API.request(`/uploads/${state.uploadId}/reject`,{method:"POST"}); toast("这份数据没有导入"); $("#uploadDialog").close(); state.uploadId=null;}catch(e){toast(detailMessage(e),true);} }

  async function renderActions(){
    $("#pageContent").innerHTML=`<div class="section-title"><h3>今天的行动清单</h3><p>只保留值得你亲自处理的事</p></div><div id="actionList"></div>`;
    await renderActionList("#actionList");
  }

  async function renderData(){
    const [trendRes,profitRes,reconRes]=await Promise.all([
      API.request(`/trends?days=7&end_date=${dateISO()}`), API.request(`/profit/today?business_date=${dateISO()}`), API.request(`/reconciliation/today?date=${dateISO()}`)
    ]);
    const t=trendRes.trend||{}, p=profitRes.profit||{}, r=reconRes.reconciliation||{}; const rows=t.daily_sales||[];
    const max=Math.max(...rows.map(x=>num(x.amount ?? x.sales ?? x.value)),1);
    const bars=rows.length?rows.map(x=>{const value=num(x.amount ?? x.sales ?? x.value); const label=x.business_date||x.date||"";return `<div class="bar-row"><span>${escapeHtml(String(label).slice(5))}</span><div class="bar-track"><div class="bar-value" style="width:${Math.round(value/max*100)}%"></div></div><span class="bar-amount">${money(value)}</span></div>`}).join(""):emptyCard("还没有 7 天趋势","至少导入几天营业数据后，趋势才有意义。","去上传","upload");
    const reconClass=r.status==="MATCHED"?"good":(r.status?"warn":"");
    $("#pageContent").innerHTML=`<div class="grid-2"><div class="card"><div class="card-head"><div><h3>近 7 天营业趋势</h3><p>${t.observed_days||0} 天有数据 · ${t.missing_days||0} 天缺失</p></div></div><div class="bar-list">${bars}</div></div><div class="card"><div class="card-head"><div><h3>今天利润</h3><p>区间比单一数字更诚实</p></div><span class="tag">可信度 ${escapeHtml(confidenceLabel(p.confidence_level))}</span></div><div class="metric-big">${profitText(p)}</div><div class="metric-sub">营业额 ${money(p.revenue)}</div></div></div>
    <section class="section"><div class="card"><div class="card-head"><div><h3>今天对账</h3><p>平台销售、结算、到账是否对得上</p></div></div><div class="status-box ${reconClass}">${escapeHtml(r.user_message||"还没有足够的结算/到账证据可以对账。")}${r.difference!=null?`<br>差额：${money(r.difference)}`:""}</div></div></section>`;
    bindJumps();
  }

  function renderChat(){
    const messages=state.chat.length?state.chat.map(m=>`<div class="message ${m.role}">${escapeHtml(m.text)}</div>`).join(""):`<div class="message ai">你可以直接问我：\n“今天到底赚没赚钱？”\n“为什么营业额不错但利润还是低？”\n“我现在最该先处理哪个问题？”</div>`;
    $("#pageContent").innerHTML=`<div class="card chat-shell"><div id="chatMessages" class="chat-messages">${messages}</div><form id="chatForm" class="chat-compose"><textarea id="chatInput" placeholder="用你自己的话问，不需要懂财务术语"></textarea><button class="btn btn-primary">发送</button></form></div>`;
    $("#chatForm").addEventListener("submit",sendChat);
  }
  async function sendChat(e){
    e.preventDefault(); const q=$("#chatInput").value.trim(); if(!q)return;
    state.chat.push({role:"user",text:q}); renderChat(); const input=$("#chatInput"); if(input) input.value="";
    try{
      const result=await API.request("/ai/chat",{method:"POST",body:{question:q,date:dateISO()}});
      let text=result.answer || result.message || "我现在还没有足够信息判断。";
      if(result.status==="NEEDS_DATA" && result.missing_data?.length) text+=`\n\n还缺：${result.missing_data.join("、")}`;
      if(result.next_action?.label) text+=`\n下一步：${result.next_action.label}`;
      state.chat.push({role:"ai",text}); renderChat();
    }catch(err){
      const text=err.status===503?"AI文字服务尚未配置。你仍然可以使用今天、数据、上传和待办功能。":detailMessage(err);
      state.chat.push({role:"ai",text}); renderChat();
    }
  }

  async function renderProfile(){
    const [storeRes,profileRes]=await Promise.all([API.request("/store"),API.request("/profit/profile")]);
    state.store=storeRes.store; const p=profileRes.profile||{};
    $("#sidebarStoreName").textContent=state.store.name;
    $("#pageContent").innerHTML=`<div class="card"><div class="profile-summary"><div class="avatar">${escapeHtml(state.store.name.slice(0,1))}</div><div><h3 style="margin:0">${escapeHtml(state.store.name)}</h3><p class="muted tiny" style="margin:5px 0 0">${escapeHtml(categoryLabels[state.store.category_code]||state.store.store_type||"餐饮门店")}</p></div></div><div class="divider"></div><form id="profileForm" class="form-grid"><label class="field"><span>门店名称</span><input id="profileName" value="${escapeHtml(state.store.name)}"/></label><label class="field"><span>门店类型</span><select id="profileCategory">${Object.entries(categoryLabels).map(([v,l])=>`<option value="${v}" ${state.store.category_code===v?"selected":""}>${l}</option>`).join("")}</select></label><label class="field"><span>每月租金（可选）</span><input id="monthlyRent" inputmode="decimal" value="${escapeHtml(p.monthly_rent||"")}" placeholder="例如 12000"/></label><label class="field"><span>每月人工实际支出（可选）</span><input id="monthlyLabor" inputmode="decimal" value="${escapeHtml(p.monthly_labor_actual||"")}" placeholder="例如 26000"/></label><div><button class="btn btn-primary" type="submit">保存设置</button></div></form><div class="divider"></div><button class="btn btn-danger" id="profileLogout">退出当前账号</button></div>`;
    $("#profileForm").addEventListener("submit",saveProfile); $("#profileLogout").addEventListener("click",logout);
  }
  async function saveProfile(e){
    e.preventDefault();
    try{
      const store=await API.request("/store",{method:"PATCH",body:{name:$("#profileName").value.trim(),category_code:$("#profileCategory").value}}); state.store=store.store;
      const profile={}; const rent=$("#monthlyRent").value.trim(), labor=$("#monthlyLabor").value.trim();
      if(rent){profile.rent_mode="ACTUAL";profile.monthly_rent=rent;} if(labor){profile.labor_cost_mode="ACTUAL";profile.monthly_labor_actual=labor;}
      if(Object.keys(profile).length) await API.request("/profit/profile",{method:"PATCH",body:profile});
      $("#sidebarStoreName").textContent=state.store.name; toast("设置已保存");
    }catch(err){toast(detailMessage(err),true);}
  }

  function bindJumps(root=document){ $$('[data-jump]',root).forEach(b=>b.addEventListener("click",()=>navigate(b.dataset.jump))); }
  function logout(){ API.clearToken(); state.store=null; state.chat=[]; showView("loginView"); toast("已退出"); }

  $("#loginForm").addEventListener("submit",login);
  $("#storeForm").addEventListener("submit",createStore);
  $("#sideNav").addEventListener("click",e=>{const b=e.target.closest('[data-page]');if(b)navigate(b.dataset.page)});
  $("#mobileNav").addEventListener("click",e=>{const b=e.target.closest('[data-page]');if(b)navigate(b.dataset.page)});
  $("#logoutBtn").addEventListener("click",logout);
  $("#refreshBtn").addEventListener("click",()=>navigate(state.page));
  $("#closeUploadDialog").addEventListener("click",()=>$("#uploadDialog").close());
  $("#confirmUploadBtn").addEventListener("click",confirmUpload);
  $("#rejectUploadBtn").addEventListener("click",rejectUpload);
  window.ShiceApp={refresh:()=>navigate(state.page),navigate};
  bootstrap();
})();
