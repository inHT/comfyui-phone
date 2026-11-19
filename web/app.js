const tokenKey = "comfy-phone-token";
const formKey = "comfy-phone-form";
const dbName = "comfy-phone";

const loginScreen = document.querySelector("#login-screen");
const app = document.querySelector("#app");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const loginStatus = document.querySelector("#login-status");
const linkStatus = document.querySelector("#link-status");
const formError = document.querySelector("#form-error");
const generateButton = document.querySelector("#generate");
const generateBar = document.querySelector("#generate-bar");
const resultBox = document.querySelector("#result");
const progressText = document.querySelector("#progress-text");
const progressFill = document.querySelector("#progress-fill");
const resultImage = document.querySelector("#result-image");
const gallery = document.querySelector("#gallery");
const galleryEmpty = document.querySelector("#gallery-empty");
const viewer = document.querySelector("#viewer");

let options = null;
let aspectId = "1:1 (Square)";
let loraRows = [];
let seedRandom = true;
let activeJob = null;
let pollTimer = 0;
let viewingId = null;
const galleryUrls = [];

function authHeader() {
  const token = localStorage.getItem(tokenKey);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function api(path, opts = {}) {
  const headers = { ...authHeader(), ...(opts.headers || {}) };
  let body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const response = await fetch(path, { method: opts.method || "GET", headers, body });
  if (response.status === 401 && path !== "/api/login") {
    localStorage.removeItem(tokenKey);
    showLogin();
    throw new Error("请重新登录");
  }
  if (!response.ok) {
    let detail = "请求失败";
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response.blob();
}

function showError(node, message) {
  node.hidden = !message;
  node.textContent = message || "";
}

function showLogin() {
  app.hidden = true;
  loginScreen.hidden = false;
}

function showApp() {
  loginScreen.hidden = true;
  app.hidden = false;
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("shots", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function withStore(mode, run) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction("shots", mode);
    const request = run(tx.objectStore("shots"));
    tx.oncomplete = () => {
      db.close();
      resolve(request.result);
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error || new Error("图库读取失败"));
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error || new Error("图库写入中断"));
    };
  }));
}

function saveShot(shot) {
  return withStore("readwrite", (store) => store.put(shot));
}

function readShot(id) {
  return withStore("readonly", (store) => store.get(id));
}

function allShots() {
  return withStore("readonly", (store) => store.getAll());
}

function deleteShot(id) {
  return withStore("readwrite", (store) => store.delete(id));
}

function loraLabel(name) {
  const parts = name.split(/[\\/]/);
  return parts[parts.length - 1];
}

function loraGroup(name) {
  const parts = name.split(/[\\/]/);
  if (parts.length < 2) return "其他";
  return parts.slice(0, -1).join(" / ");
}

function fillSelect(select, values, current, labeler = (value) => value) {
  const groups = new Map();
  values.forEach((value) => {
    const group = labeler.group ? labeler.group(value) : "";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(value);
  });
  select.replaceChildren();
  groups.forEach((items, group) => {
    const parent = group ? document.createElement("optgroup") : select;
    if (group) {
      parent.label = group;
      select.append(parent);
    }
    items.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labeler.item ? labeler.item(value) : labeler(value);
      parent.append(option);
    });
  });
  if (current && values.includes(current)) select.value = current;
}

function savedForm() {
  try {
    return JSON.parse(localStorage.getItem(formKey) || "{}");
  } catch {
    return {};
  }
}

function rememberForm() {
  localStorage.setItem(formKey, JSON.stringify({
    unet: document.querySelector("#unet").value,
    prompt: document.querySelector("#prompt").value,
    aspectId,
    sampler: document.querySelector("#sampler").value,
    scheduler: document.querySelector("#scheduler").value,
    steps: document.querySelector("#steps").value,
    cfg: document.querySelector("#cfg").value,
    loras: loraRows,
    seedRandom,
    seed: document.querySelector("#seed").value,
  }));
}

function setSwitch(button, on, onText, offText) {
  button.setAttribute("aria-checked", on ? "true" : "false");
  button.querySelector(".switch-text").textContent = on ? onText : offText;
}

function renderAspects() {
  const box = document.querySelector("#aspects");
  box.replaceChildren();
  options.aspects.forEach((aspect) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", aspect.id === aspectId ? "true" : "false");
    if (aspect.id === aspectId) button.classList.add("is-on");
    const title = document.createElement("strong");
    title.textContent = aspect.label;
    const size = document.createElement("span");
    size.textContent = `${aspect.width} × ${aspect.height}`;
    button.append(title, size);
    button.addEventListener("click", () => {
      aspectId = aspect.id;
      renderAspects();
      rememberForm();
    });
    box.append(button);
  });
}

function blankLora() {
  return {
    enabled: true,
    name: options.loras[0] || "",
    modelWeight: 0.8,
    clipWeight: 0.8,
  };
}

function renderLoras() {
  const list = document.querySelector("#lora-list");
  list.replaceChildren();
  loraRows.forEach((row, index) => list.append(loraCard(row, index)));
  const add = document.querySelector("#add-lora");
  add.disabled = loraRows.length >= 9;
  add.textContent = loraRows.length >= 9 ? "已满 9 个" : "添加";
}

function weightControl(row, key, label) {
  const wrap = document.createElement("div");
  wrap.className = "slider-row";
  const caption = document.createElement("label");
  const out = document.createElement("output");
  out.textContent = Number(row[key]).toFixed(2);
  caption.append(document.createTextNode(`${label} `), out);
  const range = document.createElement("input");
  range.type = "range";
  range.min = "-4";
  range.max = "4";
  range.step = "0.01";
  range.value = String(Math.min(4, Math.max(-4, Number(row[key]))));
  range.disabled = !row.enabled;
  range.setAttribute("aria-label", label);
  range.addEventListener("input", () => {
    row[key] = Number(range.value);
    out.textContent = row[key].toFixed(2);
    rememberForm();
  });
  wrap.append(caption, range);
  return wrap;
}

function loraCard(row, index) {
  const card = document.createElement("div");
  card.className = row.enabled ? "lora-row" : "lora-row is-off";
  const head = document.createElement("div");
  head.className = "lora-row-head";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "switch";
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", row.enabled ? "true" : "false");
  const knob = document.createElement("span");
  knob.className = "switch-knob";
  const text = document.createElement("span");
  text.className = "switch-text";
  text.textContent = row.enabled ? "开启" : "关闭";
  toggle.append(knob, text);
  toggle.addEventListener("click", () => {
    row.enabled = !row.enabled;
    renderLoras();
    rememberForm();
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "text-button";
  remove.textContent = "移除";
  remove.addEventListener("click", () => {
    loraRows.splice(index, 1);
    renderLoras();
    rememberForm();
  });
  head.append(toggle, remove);
  const select = document.createElement("select");
  select.disabled = !row.enabled;
  select.setAttribute("aria-label", `LoRA ${index + 1}`);
  fillSelect(select, options.loras, row.name, { group: loraGroup, item: loraLabel });
  row.name = select.value;
  select.addEventListener("change", () => {
    row.name = select.value;
    rememberForm();
  });
  card.append(
    head,
    select,
    weightControl(row, "modelWeight", "模型权重"),
    weightControl(row, "clipWeight", "CLIP 权重"),
  );
  return card;
}

function applySeedState() {
  setSwitch(document.querySelector("#seed-random"), seedRandom, "随机", "固定");
  document.querySelector("#seed").disabled = seedRandom;
}

function bindSliders() {
  const steps = document.querySelector("#steps");
  const cfg = document.querySelector("#cfg");
  const paint = () => {
    document.querySelector("#steps-out").textContent = steps.value;
    document.querySelector("#cfg-out").textContent = Number(cfg.value).toFixed(1);
  };
  [steps, cfg].forEach((input) => input.addEventListener("input", () => {
    paint();
    rememberForm();
  }));
  paint();
}

function fillForm() {
  const saved = savedForm();
  fillSelect(document.querySelector("#unet"), options.unets, saved.unet || options.unets[0]);
  fillSelect(document.querySelector("#sampler"), options.samplers, saved.sampler || options.defaults.sampler_name);
  fillSelect(document.querySelector("#scheduler"), options.schedulers, saved.scheduler || options.defaults.scheduler);
  aspectId = saved.aspectId || options.defaults.aspect_ratio;
  document.querySelector("#steps").value = saved.steps || options.defaults.steps;
  document.querySelector("#cfg").value = saved.cfg || options.defaults.cfg;
  document.querySelector("#prompt").value = saved.prompt || "";
  document.querySelector("#seed").value = saved.seed || "";
  if (Array.isArray(saved.loras)) {
    loraRows = saved.loras.slice(0, 9).map((row) => ({
      enabled: Boolean(row.enabled),
      name: row.name || options.loras[0] || "",
      modelWeight: Number(row.modelWeight ?? 0.8),
      clipWeight: Number(row.clipWeight ?? 0.8),
    }));
  } else if (saved.loraName) {
    loraRows = [{
      enabled: Boolean(saved.loraOn),
      name: saved.loraName,
      modelWeight: Number(saved.loraWeight || 0.8),
      clipWeight: Number(saved.loraWeight || 0.8),
    }];
  } else {
    loraRows = [];
  }
  seedRandom = saved.seedRandom !== false;
  renderLoras();
  applySeedState();
  bindSliders();
  renderAspects();
}

async function refreshLink() {
  try {
    const health = await api("/api/health");
    const text = health.comfy ? "电脑在线" : "ComfyUI 没开";
    linkStatus.textContent = text;
    linkStatus.classList.toggle("is-on", health.comfy);
    loginStatus.textContent = health.comfy ? "电脑上的 ComfyUI 已连接" : "网关在线，但 ComfyUI 没开";
  } catch {
    linkStatus.textContent = "网关离线";
    linkStatus.classList.remove("is-on");
    loginStatus.textContent = "网关没开";
  }
}

function showCreate(on) {
  document.querySelector("#view-create").hidden = !on;
  document.querySelector("#view-gallery").hidden = on;
  generateBar.hidden = !on;
  document.querySelector("#tab-create").classList.toggle("is-on", on);
  document.querySelector("#tab-gallery").classList.toggle("is-on", !on);
}

async function paintGallery() {
  const shots = (await allShots()).sort((a, b) => b.createdAt - a.createdAt);
  galleryUrls.splice(0).forEach((url) => URL.revokeObjectURL(url));
  gallery.replaceChildren();
  galleryEmpty.hidden = shots.length > 0;
  shots.forEach((shot) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "shot";
    button.setAttribute("aria-label", shot.params.prompt || "查看这张图");
    const image = document.createElement("img");
    image.alt = shot.params.prompt || "出图";
    const url = URL.createObjectURL(shot.blob);
    galleryUrls.push(url);
    image.src = url;
    button.append(image);
    button.addEventListener("click", () => openViewer(shot.id));
    gallery.append(button);
  });
}

async function openViewer(id) {
  const shot = await readShot(id);
  if (!shot) return;
  viewingId = id;
  document.querySelector("#viewer-image").src = URL.createObjectURL(shot.blob);
  document.querySelector("#viewer-prompt").textContent = shot.params.prompt || "";
  const params = shot.params;
  const used = Array.isArray(params.loras) ? params.loras.filter((item) => item.enabled !== false && item.name) : [];
  const lora = used.length
    ? used.map((item) => `${loraLabel(item.name)} 模型 ${Number(item.model_weight).toFixed(2)} / CLIP ${Number(item.clip_weight).toFixed(2)}`).join("；")
    : (params.lora_enabled ? `${loraLabel(params.lora_name)} · ${params.lora_weight}` : "未使用");
  document.querySelector("#viewer-meta").textContent =
    `${params.aspect_label || ""} ${params.width}×${params.height} · ${params.steps} 步 · CFG ${params.cfg} · ${params.sampler_name} / ${params.scheduler} · 种子 ${params.seed} · LoRA ${lora}`;
  viewer.showModal();
}

async function keepShot(job, blob) {
  await saveShot({
    id: job.id,
    createdAt: job.created_at * 1000,
    params: job.params,
    blob,
  });
}

async function syncShots() {
  const jobs = await api("/api/jobs");
  for (const job of jobs) {
    if (job.status !== "done") continue;
    if (await readShot(job.id)) continue;
    const blob = await api(`/api/jobs/${job.id}/image`);
    await keepShot(job, blob);
  }
  await paintGallery();
}

function paintProgress(job) {
  resultBox.hidden = false;
  const percent = Math.round((job.progress || 0) * 100);
  progressFill.style.width = `${percent}%`;
  if (job.status === "queued") progressText.textContent = "已交给电脑，排队中";
  else if (job.status === "running") progressText.textContent = `采样 ${job.step || 0} / ${job.step_max || job.params.steps}`;
  else if (job.status === "error") progressText.textContent = job.error || "出图失败";
  else progressText.textContent = "完成，已收入图库";
}

async function finishJob(job) {
  const blob = await api(`/api/jobs/${job.id}/image`);
  await keepShot(job, blob);
  resultImage.alt = job.params.prompt;
  resultImage.src = URL.createObjectURL(blob);
  resultImage.hidden = false;
  paintProgress(job);
  await paintGallery();
}

async function pollJob(id) {
  window.clearTimeout(pollTimer);
  try {
    const job = await api(`/api/jobs/${id}`);
    activeJob = job;
    paintProgress(job);
    if (job.status === "done") {
      generateButton.disabled = false;
      generateButton.textContent = "开始出图";
      await finishJob(job);
      return;
    }
    if (job.status === "error") {
      generateButton.disabled = false;
      generateButton.textContent = "开始出图";
      showError(formError, job.error || "出图失败");
      return;
    }
  } catch (error) {
    showError(formError, error.message);
    generateButton.disabled = false;
    generateButton.textContent = "开始出图";
    return;
  }
  pollTimer = window.setTimeout(() => pollJob(id), 1000);
}

async function generate() {
  showError(formError, "");
  const prompt = document.querySelector("#prompt").value.trim();
  if (!prompt) {
    showError(formError, "先写提示词");
    return;
  }
  if (loraRows.some((row) => row.enabled && !row.name)) {
    showError(formError, "有开启的 LoRA 还没选择文件");
    return;
  }
  rememberForm();
  generateButton.disabled = true;
  generateButton.textContent = "电脑出图中";
  resultImage.removeAttribute("src");
  resultImage.hidden = true;
  try {
    const seedValue = document.querySelector("#seed").value;
    const job = await api("/api/generate", {
      method: "POST",
      json: {
        prompt,
        unet: document.querySelector("#unet").value,
        aspect_ratio: aspectId,
        steps: Number(document.querySelector("#steps").value),
        cfg: Number(document.querySelector("#cfg").value),
        sampler_name: document.querySelector("#sampler").value,
        scheduler: document.querySelector("#scheduler").value,
        seed: seedRandom ? null : Number(seedValue || 0),
        loras: loraRows.map((row) => ({
          enabled: row.enabled,
          name: row.name,
          model_weight: Number(row.modelWeight),
          clip_weight: Number(row.clipWeight),
        })),
      },
    });
    paintProgress(job);
    pollJob(job.id);
  } catch (error) {
    generateButton.disabled = false;
    generateButton.textContent = "开始出图";
    showError(formError, error.message);
  }
}

function resumeRunning(jobs) {
  const running = jobs.find((job) => job.status === "queued" || job.status === "running");
  if (!running) return;
  generateButton.disabled = true;
  generateButton.textContent = "电脑出图中";
  pollJob(running.id);
}

document.querySelector("#add-lora").addEventListener("click", () => {
  if (loraRows.length >= 9) return;
  loraRows.push(blankLora());
  renderLoras();
  rememberForm();
});

document.querySelector("#seed-random").addEventListener("click", () => {
  seedRandom = !seedRandom;
  applySeedState();
  rememberForm();
});

document.querySelector("#reset-sample").addEventListener("click", () => {
  document.querySelector("#steps").value = options.defaults.steps;
  document.querySelector("#cfg").value = options.defaults.cfg;
  document.querySelector("#sampler").value = options.defaults.sampler_name;
  document.querySelector("#scheduler").value = options.defaults.scheduler;
  aspectId = options.defaults.aspect_ratio;
  document.querySelector("#steps").dispatchEvent(new Event("input"));
  renderAspects();
  rememberForm();
});

["#unet", "#prompt", "#sampler", "#scheduler", "#seed"].forEach((selector) => {
  document.querySelector(selector).addEventListener("change", rememberForm);
  document.querySelector(selector).addEventListener("input", rememberForm);
});

document.querySelector("#tab-create").addEventListener("click", () => showCreate(true));
document.querySelector("#tab-gallery").addEventListener("click", async () => {
  showCreate(false);
  try {
    await syncShots();
  } catch (error) {
    showError(formError, error.message);
    await paintGallery();
  }
});

document.querySelector("#sync").addEventListener("click", async () => {
  try {
    await syncShots();
  } catch (error) {
    galleryEmpty.hidden = false;
    galleryEmpty.textContent = error.message;
  }
});

document.querySelector("#viewer-close").addEventListener("click", () => viewer.close());
document.querySelector("#viewer-delete").addEventListener("click", async () => {
  if (!viewingId) return;
  await deleteShot(viewingId);
  viewer.close();
  await paintGallery();
});

generateButton.addEventListener("click", generate);

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError(loginError, "");
  const button = document.querySelector("#login-button");
  button.disabled = true;
  try {
    const result = await api("/api/login", {
      method: "POST",
      json: { password: document.querySelector("#password").value },
    });
    localStorage.setItem(tokenKey, result.token);
    await boot();
  } catch (error) {
    showError(loginError, error.message);
  } finally {
    button.disabled = false;
  }
});

async function boot() {
  if (!localStorage.getItem(tokenKey)) {
    showLogin();
    refreshLink();
    return;
  }
  try {
    options = await api("/api/options");
  } catch (error) {
    if (!localStorage.getItem(tokenKey)) return;
    showLogin();
    showError(loginError, error.message);
    return;
  }
  showApp();
  fillForm();
  refreshLink();
  await paintGallery();
  try {
    const jobs = await api("/api/jobs");
    resumeRunning(jobs);
  } catch {
    /* 图库仍可用本地记录 */
  }
}

setInterval(refreshLink, 8000);
boot();
