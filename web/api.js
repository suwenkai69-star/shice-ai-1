(() => {
  const BASE = "/api/mini/v1";
  const TOKEN_KEY = "shice_web_token";

  function getToken(){ return localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(token){ token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); }
  function clearToken(){ localStorage.removeItem(TOKEN_KEY); }

  async function request(path, options = {}){
    const headers = new Headers(options.headers || {});
    const token = getToken();
    if(token) headers.set("Authorization", `Bearer ${token}`);
    let body = options.body;
    if(body && !(body instanceof FormData) && typeof body !== "string"){
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(body);
    }
    const response = await fetch(`${BASE}${path}`, {...options, headers, body});
    let payload = null;
    const text = await response.text();
    if(text){
      try { payload = JSON.parse(text); } catch { payload = {detail:text}; }
    }
    if(!response.ok){
      const err = new Error(typeof payload?.detail === "string" ? payload.detail : (payload?.message || `请求失败（${response.status}）`));
      err.status = response.status;
      err.payload = payload;
      if(response.status === 401 && path !== "/auth/demo") clearToken();
      throw err;
    }
    return payload;
  }

  async function upload(path, file){
    const form = new FormData();
    form.append("file", file);
    return request(path, {method:"POST", body:form});
  }

  window.ShiceAPI = { BASE, getToken, setToken, clearToken, request, upload };
})();
