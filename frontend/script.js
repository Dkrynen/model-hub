let systemInfo = null;
let chatAbort = null;
let chatHistory = [];
let ollamaPoll = null;

function escHtml(s) {
  if (typeof s !== "string") return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function mdToHtml(text) {
  return escHtml(text)
    .replace(/```(\w*)\n([\s\S]*?)```/g, "<pre><code>$2</code></pre>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/^- (.+)$/gm, "&bull; $1<br>")
    .replace(/\n/g, "<br>");
}

function toast(msg, type) {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type || "info"}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3500);
}

function openModal(title, bodyHtml) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = bodyHtml;
  document.getElementById("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
}

function getRecsSortFn(key) {
  const fns = {
    score: (a, b) => b.score - a.score,
    vram: (a, b) => a.vram_gb - b.vram_gb,
    context: (a, b) => b.context - a.context,
    name: (a, b) => a.name.localeCompare(b.name),
  };
  return fns[key] || fns.score;
}

document.addEventListener("DOMContentLoaded", () => {
  initNav();
  checkOllama();
  loadDashboard();
  checkFirstRun();
  checkForUpdates();
  ollamaPoll = setInterval(checkOllama, 10000);
  setInterval(loadRunningModels, 15000);

  document.getElementById("chat-input").addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
  });
  document.getElementById("chat-model").addEventListener("change", selectChatModel);
  document.getElementById("sort-recs").addEventListener("change", sortRecommendations);
  document.getElementById("model-search").addEventListener("input", filterInstalledModels);
  document.getElementById("browse-search").addEventListener("input", debounce(loadBrowse, 300));
  document.getElementById("browse-capability").addEventListener("change", loadBrowse);
  document.getElementById("browse-sort").addEventListener("change", loadBrowse);

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });

  document.addEventListener("click", e => {
    const pullBtn = e.target.closest("[data-pull]");
    if (pullBtn) { pullModel(decodeURIComponent(pullBtn.dataset.pull)); return; }
    const detailBtn = e.target.closest("[data-details]");
    if (detailBtn) { showDetails(detailBtn.dataset.details); return; }
    const runBtn = e.target.closest("[data-run]");
    if (runBtn) { runModel(decodeURIComponent(runBtn.dataset.run)); return; }
    const delBtn = e.target.closest("[data-delete]");
    if (delBtn) { deleteModel(decodeURIComponent(delBtn.dataset.delete)); return; }
    const copyBtn = e.target.closest("[data-copy]");
    if (copyBtn) { copyText(copyBtn.dataset.copy); return; }
    const pageBtn = e.target.closest("[data-page-btn]");
    if (pageBtn) { browsePage(parseInt(pageBtn.dataset.pageBtn)); return; }
    const browseDetailBtn = e.target.closest("[data-browse-details]");
    if (browseDetailBtn) { showBrowseDetails(decodeURIComponent(browseDetailBtn.dataset.browseDetails)); return; }

    if (e.target.id === "modal-overlay" || e.target.closest("#modal-close")) closeModal();
    if (e.target.id === "btn-scan") runScan();
    if (e.target.id === "btn-recommend") runScanAndRecommend();
    if (e.target.id === "btn-refresh-models") { loadInstalledModels(); loadRunningModels(); }
    if (e.target.id === "btn-refresh-chat-models") loadChatModels();
    if (e.target.id === "btn-clear-chat") clearChat();
    if (e.target.id === "btn-export-csv") exportRecsCSV();
    if (e.target.id === "sidebar-toggle") toggleSidebar();
    if (e.target.id === "btn-manual-install" || e.target.id === "btn-manual-install-dl") {
      const input = document.getElementById(e.target.id === "btn-manual-install" ? "manual-model-input" : "manual-model-input-dl");
      const name = input.value.trim();
      if (name) { pullModel(name); input.value = ""; }
    }
  });
});

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

function initNav() {
  document.querySelectorAll(".sidebar nav a").forEach(a => {
    a.addEventListener("click", e => {
      e.preventDefault();
      const page = a.dataset.page;
      document.querySelectorAll(".sidebar nav a").forEach(x => x.classList.remove("active"));
      a.classList.add("active");
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      document.getElementById(`page-${page}`).classList.add("active");
      document.getElementById("sidebar").classList.remove("open");
      if (page === "models") { loadInstalledModels(); loadRunningModels(); }
      if (page === "dashboard") loadDashboard();
      if (page === "chat") loadChatModels();
      if (page === "browse") loadBrowse();
    });
  });
}

async function api(method, path, body) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json();
}

async function checkOllama() {
  const badge = document.getElementById("ollama-badge");
  try {
    const r = await api("GET", "/api/ollama/status");
    if (r.running) {
      badge.className = "badge-online";
      badge.textContent = `Ollama: ${r.version || "running"}`;
    } else {
      badge.className = "badge-offline";
      badge.textContent = "Ollama: not running";
    }
  } catch {
    badge.className = "badge-offline";
    badge.textContent = "Ollama: unreachable";
  }
}

async function checkFirstRun() {
  try {
    const r = await api("GET", "/api/ollama/check-install-detailed");
    if (!r.installed) {
      const dlPage = document.getElementById("page-downloads");
      dlPage.innerHTML = `
        <h1>First-Run Setup</h1>
        <div class="result-box">
          <h3>Ollama Not Found</h3>
          <p>Model Hub requires <strong>Ollama</strong> to download and run models.</p>
          <p style="margin:16px 0">
            <a href="${escHtml(r.download_url)}" target="_blank" class="btn" style="text-decoration:none">Download Ollama</a>
          </p>
          <p style="font-size:0.85rem;color:var(--muted)">Install Ollama, then restart Model Hub. You can still browse recommendations without it.</p>
        </div>
        <h2>Downloads</h2>
        <div id="downloads-list"><em>No downloads yet.</em></div>
      `;
    }
  } catch {}
}

async function checkForUpdates() {
  try {
    const v = await api("GET", "/api/system/version");
    document.getElementById("version-badge").textContent = `v${v.version}`;
    const update = await api("GET", `/api/system/check-update?current=${v.version}`);
    if (update.update_available) {
      const badge = document.getElementById("ollama-badge");
      badge.innerHTML = `<a href="${escHtml(update.download_url)}" target="_blank" style="color:var(--accent);text-decoration:none">Update v${update.latest_version}</a>`;
    }
  } catch {
    document.getElementById("version-badge").textContent = "";
  }
}

async function loadDashboard() {
  try {
    const info = await api("GET", "/api/scan");
    systemInfo = info;
    renderSystemCard(info);
  } catch { document.getElementById("card-system").querySelector(".card-body").textContent = "Failed to scan"; }

  try {
    const r = await api("GET", "/api/ollama/status");
    const body = document.getElementById("card-ollama").querySelector(".card-body");
    body.innerHTML = r.running
      ? `<span class="badge badge-gpu">Running</span> version ${escHtml(r.version || "?")}`
      : '<span class="badge badge-offload">Not running</span>';
  } catch { document.getElementById("card-ollama").querySelector(".card-body").textContent = "Error checking"; }

  try {
    const models = await api("GET", "/api/ollama/models");
    const card = document.getElementById("card-models").querySelector(".card-body");
    if (models.length === 0) { card.textContent = "No models installed"; }
    else { card.innerHTML = models.map(m => `<div>${escHtml(m.name)} <span class="badge badge-gpu">${m.size_gb} GB</span></div>`).join(""); }
  } catch { document.getElementById("card-models").querySelector(".card-body").textContent = "Ollama not running"; }

  loadRunningModels();
}

function renderSystemCard(info) {
  const sys = document.getElementById("card-system").querySelector(".card-body");
  sys.innerHTML = `
    <div><span class="label">OS:</span> <span class="value">${escHtml(info.os)}</span></div>
    <div><span class="label">CPU:</span> <span class="value">${escHtml(info.cpu)}</span></div>
    <div><span class="label">Cores:</span> <span class="value">${info.cores}</span></div>
    <div><span class="label">RAM:</span> <span class="value">${info.ram_gb} GB</span></div>
  `;
  const gpu = document.getElementById("card-gpu").querySelector(".card-body");
  if (info.gpus && info.gpus.length) {
    gpu.innerHTML = info.gpus.map(g =>
      `<div><span class="value">${escHtml(g.name)}</span> &mdash; ${g.vram_gb} GB <span class="label">(${escHtml(g.backend)})</span></div>`
    ).join("");
    loadQuickPicks(info.total_vram_gb || info.gpus[0].vram_gb);
  } else {
    gpu.textContent = "No GPU detected";
  }
}

async function loadQuickPicks(vram) {
  const div = document.getElementById("quick-picks");
  if (!vram || vram <= 0) { div.innerHTML = '<span class="empty-state">Scan your hardware first.</span>'; return; }
  try {
    const r = await api("GET", `/api/recommend?vram=${vram}&use_case=coding&top_k=3`);
    const recs = r.recommendations || [];
    if (!recs.length) { div.innerHTML = '<span class="empty-state">No recommendations</span>'; return; }
    div.innerHTML = recs.map((m, i) => {
      const tag = encodeURIComponent(m.model_id);
      return `<div class="result-box" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><strong>${i+1}.</strong> ${escHtml(m.name)} <span class="badge badge-${m.run_mode === 'gpu' ? 'gpu' : 'offload'}">${escHtml(m.quant)}</span></div>
          <button class="btn btn-sm" data-pull="${tag}">Install</button>
        </div>
        <div style="font-size:0.8rem;color:var(--muted);margin-top:4px">
          ${m.vram_gb} GB VRAM &middot; ${m.context} ctx &middot; Score ${m.score}
        </div>
      </div>`;
    }).join("");
  } catch { div.innerHTML = '<span class="empty-state">Error loading picks</span>'; }
}

let lastRecs = [];

async function runScan() {
  const div = document.getElementById("scan-result");
  div.innerHTML = '<div class="result-box"><div class="spinner"></div> Scanning...</div>';
  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${escHtml(info.os)}</td></tr>`;
  html += `<tr><td>CPU</td><td>${escHtml(info.cpu)}</td></tr>`;
  html += `<tr><td>Cores</td><td>${info.cores}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${escHtml(g.name)} &mdash; ${g.vram_gb} GB (${escHtml(g.backend)})</td></tr>`;
    });
  } else {
    html += `<tr><td>GPU</td><td>None detected</td></tr>`;
  }
  html += '</table></div>';
  div.innerHTML = html;
}

async function runScanAndRecommend() {
  document.getElementById("scan-result").innerHTML = '<div class="result-box"><div class="spinner"></div> Scanning...</div>';
  document.getElementById("recs-result").innerHTML = '';
  const vramOverride = parseFloat(document.getElementById("vram-override").value) || 0;
  const useCase = document.getElementById("use-case").value;
  const vram = vramOverride > 0 ? vramOverride : null;

  const info = await api("GET", "/api/scan");
  systemInfo = info;
  let html = '<div class="result-box"><h3>System</h3><table>';
  html += `<tr><td>OS</td><td>${escHtml(info.os)}</td></tr>`;
  html += `<tr><td>CPU</td><td>${escHtml(info.cpu)}</td></tr>`;
  html += `<tr><td>RAM</td><td>${info.ram_gb} GB</td></tr>`;
  if (info.gpus && info.gpus.length) {
    info.gpus.forEach(g => {
      html += `<tr><td>GPU</td><td>${escHtml(g.name)} &mdash; ${g.vram_gb} GB (${escHtml(g.backend)})</td></tr>`;
    });
  }
  html += '</table></div>';
  document.getElementById("scan-result").innerHTML = html;

  document.getElementById("recs-result").innerHTML = '<div class="result-box"><div class="spinner"></div> Generating recommendations...</div>';
  const effectiveVram = vram || info.total_vram_gb || (info.gpus && info.gpus.length ? info.gpus[0].vram_gb : 0);
  const r = await api("GET", `/api/recommend?vram=${effectiveVram}&use_case=${useCase}&top_k=50`);
  lastRecs = r.recommendations || [];

  if (!lastRecs.length) {
    document.getElementById("recs-result").innerHTML = '<div class="result-box"><span class="empty-state">No models fit your hardware. Try a lower VRAM override or different use case.</span></div>';
    return;
  }

  renderRecommendations();
}

function renderRecommendations() {
  const sortKey = document.getElementById("sort-recs").value;
  const sorted = [...lastRecs].sort(getRecsSortFn(sortKey));

  let rh = `<div class="result-box"><h3>Recommended Models (${lastRecs.length} found)</h3><table>`;
  rh += `<tr><th>#</th><th>Model</th><th>Quant</th><th>Score</th><th>VRAM</th><th>Context</th><th>Mode</th><th>Actions</th></tr>`;
  sorted.forEach((m, i) => {
    const modeClass = m.run_mode === "gpu" ? "badge-gpu" : "badge-offload";
    const modeLabel = m.run_mode === "gpu" ? "GPU" : "Offload";
    const tag = encodeURIComponent(m.model_id);
    const details = JSON.stringify(m).replace(/"/g, "&quot;");
    rh += `<tr>
      <td>${i+1}</td>
      <td><strong>${escHtml(m.name)}</strong></td>
      <td><span class="badge badge-gpu">${escHtml(m.quant)}</span></td>
      <td>${m.score}</td>
      <td>${m.vram_gb}</td>
      <td>${m.context}</td>
      <td><span class="badge ${modeClass}">${modeLabel}</span></td>
      <td class="model-actions">
        <button class="btn btn-sm" data-pull="${tag}">Install</button>
        <button class="btn btn-sm btn-secondary" data-details="${details}">Details</button>
      </td>
    </tr>`;
  });
  rh += '</table></div>';
  document.getElementById("recs-result").innerHTML = rh;
}

function sortRecommendations() {
  if (lastRecs.length) renderRecommendations();
}

function exportRecsCSV() {
  if (!lastRecs.length) { toast("No recommendations to export.", "error"); return; }
  const headers = ["Name", "Quant", "Score", "VRAM GB", "Context", "Mode", "Provider", "Params B"];
  const rows = lastRecs.map(m => [
    m.name, m.quant, m.score, m.vram_gb, m.context, m.run_mode, m.provider || "", m.params_b
  ]);
  const csv = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "model-recommendations.csv";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Exported recommendations as CSV.", "success");
}

function showDetails(raw) {
  const m = JSON.parse(raw);
  const bodyHtml = `
    <table>
      <tr><td>Provider</td><td>${escHtml(m.provider || "?")}</td></tr>
      <tr><td>Parameters</td><td>${m.params_b}B</td></tr>
      <tr><td>Quantization</td><td>${escHtml(m.quant)}</td></tr>
      <tr><td>VRAM needed</td><td>${m.vram_gb} GB</td></tr>
      <tr><td>Context window</td><td>${m.context} tokens</td></tr>
      <tr><td>Run mode</td><td>${escHtml(m.run_mode)}</td></tr>
      <tr><td>Quality score</td><td>${m.scores.quality}</td></tr>
      <tr><td>Speed score</td><td>${m.scores.speed}</td></tr>
      <tr><td>Fit score</td><td>${m.scores.fit}</td></tr>
      <tr><td>Context score</td><td>${m.scores.context}</td></tr>
      <tr><td>Ollama command</td><td><code>${escHtml(m.ollama_cmd)}</code></td></tr>
    </table>
    <div style="margin-top:12px"><button class="btn btn-sm btn-secondary" data-copy="${escHtml(m.ollama_cmd)}">Copy Command</button></div>
  `;
  openModal(escHtml(m.name), bodyHtml);
}

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => toast("Copied to clipboard!", "success")).catch(() => {});
}

async function pullModel(modelName) {
  if (!modelName) return;
  const dlDiv = document.getElementById("downloads-list");
  const id = `dl-${Date.now()}`;
  const el = document.createElement("div");
  el.className = "download-item";
  el.id = id;
  el.innerHTML = `<div class="title">Installing ${escHtml(modelName)}...</div>
    <div class="progress-bar"><div class="progress-fill" id="${id}-progress"></div></div>
    <div class="status" id="${id}-status">Starting download...</div>`;
  dlDiv.prepend(el);
  document.querySelector('[data-page="downloads"]').click();

  try {
    const resp = await fetch("/api/ollama/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelName }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const s = line.replace(/^data: /, "").trim();
        if (s === "[DONE]" || !s) continue;
        try {
          const p = JSON.parse(s);
          const progress = document.getElementById(`${id}-progress`);
          const status = document.getElementById(`${id}-status`);
          const title = el.querySelector(".title");
          if (p.error) {
            status.textContent = `Error: ${p.error}`;
            status.className = "status error";
            break;
          }
          if (p.status) status.textContent = p.status;
          if (p.completed && p.total) {
            const pct = Math.round((p.completed / p.total) * 100);
            if (progress) progress.style.width = `${Math.min(pct, 100)}%`;
            status.textContent = `${p.status || "Downloading..."} (${pct}%)`;
          }
          if (p.status === "success") {
            if (progress) progress.style.width = "100%";
            status.textContent = "Installed successfully!";
            status.className = "status done";
            if (title) title.textContent = `${modelName} &mdash; Installed`;
            toast(`Installed ${modelName}`, "success");
          }
        } catch {}
      }
    }
  } catch (err) {
    const status = document.getElementById(`${id}-status`);
    if (status) { status.textContent = `Failed: ${err.message}`; status.className = "status error"; }
    toast(`Download failed: ${err.message}`, "error");
  }
}

function filterInstalledModels() {
  const query = document.getElementById("model-search").value.toLowerCase();
  document.querySelectorAll("#installed-models table tr").forEach((tr, i) => {
    if (i === 0) return;
    tr.style.display = tr.textContent.toLowerCase().includes(query) ? "" : "none";
  });
}

async function loadInstalledModels() {
  const div = document.getElementById("installed-models");
  div.innerHTML = '<div class="spinner"></div>';
  try {
    const models = await api("GET", "/api/ollama/models");
    if (!models.length) {
      div.innerHTML = '<div class="result-box"><span class="empty-state">No models installed.</span><p style="margin-top:12px">Go to <strong>Scan &amp; Recommend</strong> to find and install models.</p></div>';
      return;
    }
    let html = '<div class="result-box"><table><tr><th>Model</th><th>Size</th><th>Modified</th><th>Actions</th></tr>';
    models.forEach(m => {
      const safeName = escHtml(m.name);
      html += `<tr>
        <td><strong>${safeName}</strong></td>
        <td>${m.size_gb} GB</td>
        <td>${new Date(m.modified).toLocaleDateString()}</td>
        <td class="model-actions">
          <button class="btn btn-sm btn-secondary" data-run="${encodeURIComponent(m.name)}">Run</button>
          <button class="btn btn-sm btn-danger" data-delete="${encodeURIComponent(m.name)}">Delete</button>
        </td>
      </tr>`;
    });
    html += '</table></div>';
    div.innerHTML = html;
    document.getElementById("model-search").value = "";
  } catch {
    div.innerHTML = '<div class="result-box"><span class="empty-state">Could not connect to Ollama.</span><p style="margin-top:12px">Make sure <a href="https://ollama.com/download" target="_blank" style="color:var(--accent)">Ollama</a> is installed and running.</p></div>';
  }
}

async function deleteModel(name) {
  if (!confirm(`Delete ${name}?`)) return;
  await api("POST", "/api/ollama/delete", { model: name });
  loadInstalledModels();
  toast(`Deleted ${name}`, "info");
}

function runModel(name) {
  const cmd = `ollama run ${name}`;
  copyText(cmd);
  toast(`Copied "${cmd}" to clipboard. Paste in your terminal to run!`, "info");
}

async function loadChatModels() {
  const sel = document.getElementById("chat-model");
  try {
    const models = await api("GET", "/api/ollama/models");
    sel.innerHTML = '<option value="">&mdash; Select a model &mdash;</option>';
    models.forEach(m => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = `${m.name} (${m.size_gb} GB)`;
      sel.appendChild(opt);
    });
  } catch {
    sel.innerHTML = '<option value="">Ollama not running</option>';
  }
}

function selectChatModel() {
  const sel = document.getElementById("chat-model");
  const hasModel = sel.value !== "";
  document.getElementById("chat-input").disabled = !hasModel;
  document.getElementById("chat-send").disabled = !hasModel;
  if (hasModel) document.getElementById("chat-input").focus();
}

function clearChat() {
  if (!chatHistory.length && !document.getElementById("chat-box").querySelector(".chat-msg")) return;
  if (!confirm("Clear the chat history?")) return;
  chatHistory = [];
  document.getElementById("chat-box").innerHTML = '<div class="chat-welcome">Select a model above and start chatting.</div>';
  toast("Chat cleared.", "info");
}

async function sendChat() {
  const model = document.getElementById("chat-model").value;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!model || !text) return;

  const box = document.getElementById("chat-box");
  input.value = "";
  chatHistory.push({ role: "user", content: text });
  appendChatMessage("user", text);
  const msgDiv = appendChatMessage("assistant", "Thinking...", true);

  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  sendBtn.style.display = "none";
  stopBtn.style.display = "inline-flex";

  try {
    const resp = await fetch("/api/ollama/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, messages: chatHistory }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullContent = "";

    chatAbort = reader;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const s = line.replace(/^data: /, "").trim();
        if (s === "[DONE]" || !s) continue;
        try {
          const p = JSON.parse(s);
          if (p.error) {
            msgDiv.querySelector(".bubble").textContent = `Error: ${p.error}`;
            msgDiv.classList.remove("thinking");
            continue;
          }
          if (p.message && p.message.content) {
            fullContent += p.message.content;
            msgDiv.querySelector(".bubble").innerHTML = mdToHtml(fullContent);
            box.scrollTop = box.scrollHeight;
          }
        } catch {}
      }
    }

    msgDiv.classList.remove("thinking");
    chatHistory.push({ role: "assistant", content: fullContent });

  } catch (err) {
    msgDiv.querySelector(".bubble").textContent = `Error: ${err.message}`;
    msgDiv.classList.remove("thinking");
  }

  sendBtn.style.display = "inline-flex";
  stopBtn.style.display = "none";
  chatAbort = null;
}

function stopChat() {
  if (chatAbort) {
    chatAbort.cancel();
    chatAbort = null;
  }
}

function appendChatMessage(role, text, thinking) {
  const box = document.getElementById("chat-box");
  const welcome = box.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  if (thinking) div.classList.add("thinking");

  const ts = new Date().toLocaleTimeString();
  const content = role === "user" ? escHtml(text) : (thinking ? escHtml(text) : mdToHtml(text));
  div.innerHTML = `<div class="label">${role === "user" ? "You" : "Assistant"}</div><div class="bubble">${content}</div><span class="timestamp">${ts}</span>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  return div;
}

let allBrowseModels = [];
let browsePageIdx = 0;
const BROWSE_PAGE_SIZE = 24;

async function loadBrowse() {
  const grid = document.getElementById("browse-grid");
  grid.innerHTML = '<div class="spinner"></div>';
  const q = document.getElementById("browse-search").value;
  const capability = document.getElementById("browse-capability").value;
  const sort = document.getElementById("browse-sort").value;
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (capability) params.set("capability", capability);
    params.set("sort", sort);
    const r = await api("GET", "/api/library/browse?" + params.toString());
    allBrowseModels = r.models || [];
    browsePageIdx = 0;
    document.getElementById("browse-count").textContent = `${allBrowseModels.length} models`;
    renderBrowsePage();
  } catch {
    grid.innerHTML = '<div class="result-box"><span class="empty-state">Failed to load model catalog.</span></div>';
  }
}

function renderBrowsePage() {
  const grid = document.getElementById("browse-grid");
  const start = browsePageIdx * BROWSE_PAGE_SIZE;
  const page = allBrowseModels.slice(start, start + BROWSE_PAGE_SIZE);
  if (!page.length) {
    grid.innerHTML = '<div class="result-box"><span class="empty-state">No models found.</span></div>';
    renderBrowsePagination();
    return;
  }
  grid.innerHTML = page.map(m => {
    const tag = encodeURIComponent(m.name);
    const caps = (m.capabilities || []).map(c => `<span class="badge-sm cap">${escHtml(c)}</span>`).join("");
    const sizes = (m.sizes || []).map(s => `<span class="badge-sm size">${escHtml(s)}</span>`).join("");
    return `<div class="browse-card">
      <h3>${escHtml(m.name)}</h3>
      <div class="desc">${escHtml(m.description || "No description available.")}</div>
      <div class="meta">
        <span>${escHtml(m.pulls)} pulls</span>
        ${caps ? ` ${caps}` : ""}
        ${sizes ? ` ${sizes}` : ""}
      </div>
      <div class="actions">
        <button class="btn btn-sm" data-pull="${tag}">Install</button>
        <button class="btn btn-sm btn-secondary" data-browse-details="${tag}">Tags</button>
      </div>
    </div>`;
  }).join("");
  renderBrowsePagination();
}

function renderBrowsePagination() {
  const total = allBrowseModels.length;
  const pages = Math.ceil(total / BROWSE_PAGE_SIZE);
  const container = document.getElementById("browse-pagination");
  if (pages <= 1) { container.innerHTML = ""; return; }
  let html = `<button class="page-btn" data-page-btn="${browsePageIdx - 1}" ${browsePageIdx === 0 ? "disabled" : ""}>&laquo; Prev</button>`;
  for (let i = Math.max(0, browsePageIdx - 3); i < Math.min(pages, browsePageIdx + 4); i++) {
    html += `<button class="page-btn${i === browsePageIdx ? " active" : ""}" data-page-btn="${i}">${i + 1}</button>`;
  }
  html += `<button class="page-btn" data-page-btn="${browsePageIdx + 1}" ${browsePageIdx >= pages - 1 ? "disabled" : ""}>Next &raquo;</button>`;
  container.innerHTML = html;
}

function browsePage(idx) {
  if (idx < 0 || idx >= Math.ceil(allBrowseModels.length / BROWSE_PAGE_SIZE)) return;
  browsePageIdx = idx;
  renderBrowsePage();
  document.getElementById("browse-grid").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function showBrowseDetails(name) {
  try {
    const r = await api("GET", `/api/library/tags?name=${encodeURIComponent(name)}`);
    if (r.error) { toast(`Error: ${r.error}`, "error"); return; }
    const tags = r.tags || [];
    if (!tags.length) {
      toast(`No tags found for ${name}`, "info");
      return;
    }
    const tagHtml = tags.map(t => {
      const fullTag = `${name}:${t}`;
      return `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
        <code style="font-size:0.85rem">${escHtml(fullTag)}</code>
        <button class="btn btn-sm" data-pull="${encodeURIComponent(fullTag)}">Install</button>
      </div>`;
    }).join("");
    openModal(`Tags: ${escHtml(name)}`, `
      <p style="margin-bottom:12px;color:var(--muted);font-size:0.85rem">${tags.length} available variants</p>
      ${tagHtml}
    `);
  } catch {
    toast("Failed to load tags.", "error");
  }
}

async function loadRunningModels() {
  const div = document.getElementById("running-models");
  const badge = document.getElementById("running-models-badge");
  try {
    const r = await api("GET", "/api/ollama/ps");
    if (!r.running || !r.models.length) {
      div.innerHTML = '<em>No models currently loaded.</em>';
      badge.textContent = "";
      return;
    }
    badge.textContent = `${r.models.length} running`;
    div.innerHTML = r.models.map(m =>
      `<div style="margin-bottom:4px">${escHtml(m.name)} <span class="badge badge-gpu">${m.size_gb} GB</span></div>`
    ).join("");
  } catch {
    div.innerHTML = '<em>Could not check.</em>';
    badge.textContent = "";
  }
}
