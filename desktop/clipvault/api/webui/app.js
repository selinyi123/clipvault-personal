"use strict";
const $ = (s) => document.querySelector(s);
let tab = "history";
let searchRefreshTimer = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
const clipPath = (id) => `/api/clips/${encodeURIComponent(id)}`;
const memoryPath = (id) => `/api/memory/${encodeURIComponent(id)}`;
const jpatch = (id, body) => api(clipPath(id), {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function fmtTime(iso) { return iso ? iso.replace("T", " ").replace("Z", "") : ""; }

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

function setChildren(parent, children) {
  parent.textContent = "";
  parent.append(...children);
}

function pill(text, extraClass) {
  return node("span", extraClass ? `pill ${extraClass}` : "pill", text);
}

function button(label, dataset, className) {
  const b = node("button", className, label);
  Object.entries(dataset || {}).forEach(([key, value]) => {
    b.dataset[key] = String(value);
  });
  return b;
}

// Mirror of core/actions.recommend() for the primary promote chip (S011).
const PROMOTE = {
  command: ["保存为常用命令", "command"], prompt: ["归档为 Prompt", "prompt"],
  url: ["保存链接到词库", "path"], path: ["保存路径到词库", "path"],
  code: ["加入代码片段", "phrase"], error_log: ["加入词库", "phrase"], text: ["加入词库", "phrase"],
};

function clipCard(c) {
  const secret = c.is_secret;
  const article = node("article", `card${secret ? " secret" : ""}`);
  article.dataset.id = String(c.id ?? "");

  const meta = node("div", "meta");
  meta.append(pill(c.content_type, "type"));
  if (secret && c.secret_level) meta.append(pill(c.secret_level, "secret"));
  if (secret && Array.isArray(c.secret_reasons) && c.secret_reasons.length) {
    meta.append(pill(c.secret_reasons.join(","), "secret"));
  }
  if (c.times_seen > 1) meta.append(pill(`×${c.times_seen}`));
  meta.append(node("span", "time", fmtTime(c.last_seen_at)));

  const content = node("pre", "content", c.content);
  content.dataset.content = "";

  const actions = node("div", "actions");
  if (secret) {
    actions.append(
      button("释放为非密钥", { release: c.id }, "release"),
      button("删除", { del: c.id }),
    );
  } else {
    const promote = PROMOTE[c.content_type] || PROMOTE.text;
    actions.append(
      button("📌 固定", { pin: c.id, v: !c.pinned }, c.pinned ? "on" : ""),
      button("★ 收藏", { fav: c.id, v: !c.favorite }, c.favorite ? "on" : ""),
      button(promote[0], { promote: c.id, kind: promote[1] }),
      button("复制", { copy: c.id }),
      button("删除", { del: c.id }),
    );
  }

  article.append(meta, content, actions);
  return article;
}

function memCard(m) {
  const article = node("article", "card");
  article.dataset.id = String(m.id ?? "");

  const meta = node("div", "meta");
  meta.append(pill(m.kind, "type"));
  if (m.pinned) meta.append(pill("📌"));
  meta.append(pill(`用 ${m.use_count}`), pill(m.source), node("span", "time", fmtTime(m.last_used_at)));

  const content = node("pre", "content", m.text);
  content.dataset.content = "";

  const actions = node("div", "actions");
  actions.append(button("复制", { memcopy: m.id }), button("删除", { memdel: m.id }));

  article.append(meta, content, actions);
  return article;
}

async function refresh() {
  const status = await api("/api/status");
  const sync = status.sync || {};
  $("#status").textContent =
    (status.version ? `v${status.version} · ` : "")
    + `共 ${status.clips_total} 条 · 隔离 ${status.quarantined} · 待备份 ${status.backup_pending}`
    + (status.last_backup_at ? ` · 最近备份 ${fmtTime(status.last_backup_at)}` : "")
    + (sync.paired_devices
        ? ` · 已配对 ${sync.paired_devices} 台`
          + (sync.last_peer_sync_at ? `（最近同步 ${fmtTime(sync.last_peer_sync_at)}）` : "")
        : "");

  if (tab === "memory") {
    const data = await api("/api/memory");
    const items = data.memory || [];
    setChildren($("#list"), items.map(memCard));
    $("#empty").hidden = items.length > 0;
    return;
  }
  const params = new URLSearchParams();
  if (tab === "quarantine") params.set("secret", "1");
  const q = $("#q").value.trim();
  if (q) params.set("q", q);
  const t = $("#type").value;
  if (t) params.set("type", t);
  const data = await api("/api/clips?" + params.toString());
  const clips = data.clips || [];
  setChildren($("#list"), clips.map(clipCard));
  $("#empty").hidden = clips.length > 0;
}

// Paired-device management. Loopback-only on the server; a 解绑 revokes the
// device's sync token immediately (lost/compromised-device recovery).
async function renderDevices() {
  const d = await api("/api/peers");
  const peers = d.peers || [];
  const el = $("#devices");
  el.hidden = false;
  if (!peers.length) {
    setChildren(el, [node("div", "devices-title", "暂无已配对设备")]);
    return;
  }
  const rows = [node("div", "devices-title", "已配对设备")];
  peers.forEach((p) => {
    const row = node("div", "device");
    row.append(
      node("span", "", p.device_name || p.device_id),
      node("span", "time", p.last_seen_at ? `最近同步 ${fmtTime(p.last_seen_at)}` : "未同步"),
      button("解绑", { unpair: p.device_id }),
    );
    rows.push(row);
  });
  setChildren(el, rows);
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.tab) {
    tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $("#clip-toolbar").hidden = tab !== "history";
    $("#mem-toolbar").hidden = tab !== "memory";
    return refresh();
  }
  if (b.dataset.pin) { await jpatch(b.dataset.pin, { pinned: b.dataset.v === "true" }); refresh(); }
  else if (b.dataset.fav) { await jpatch(b.dataset.fav, { favorite: b.dataset.v === "true" }); refresh(); }
  else if (b.dataset.del) { await jpatch(b.dataset.del, { deleted: true }); refresh(); }
  else if (b.dataset.release) { await api(`${clipPath(b.dataset.release)}/release`, { method: "POST" }); refresh(); }
  else if (b.dataset.promote) {
    await api(`${clipPath(b.dataset.promote)}/promote`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: b.dataset.kind }),
    });
    b.textContent = "已加入"; b.classList.add("on");
  }
  else if (b.dataset.memdel) { await api(memoryPath(b.dataset.memdel), { method: "DELETE" }); refresh(); }
  else if (b.dataset.copy || b.dataset.memcopy) {
    const pre = b.closest(".card").querySelector("[data-content]");
    navigator.clipboard.writeText(pre.textContent).then(() => { b.textContent = "已复制"; });
  }
  else if (b.id === "mem-add") {
    const text = $("#mem-text").value.trim();
    if (!text) return;
    const response = await fetch("/api/memory", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: $("#mem-kind").value, text }),
    });
    const result = await response.json();
    if (!response.ok) {
      $("#status").textContent = result.error?.message || "添加词条失败";
      $("#mem-text").focus();
      return;
    }
    $("#mem-text").value = ""; refresh();
  }
  else if (b.dataset.unpair) {
    if (!confirm("解绑该设备？其同步令牌将立即失效。")) return;
    await api(`/api/peers/${encodeURIComponent(b.dataset.unpair)}`, { method: "DELETE" });
    renderDevices(); refresh();
  }
});

$("#pair-btn").addEventListener("click", async () => {
  const r = await api("/api/pair/code");
  const banner = $("#pair-banner");
  banner.hidden = false;
  let text = `配对码：${r.code}（${Math.round(r.ttl_seconds / 60)} 分钟内有效，在 Android 端输入）`;
  if (r.lan_reachable === false && r.hint) text += `\n⚠️ ${r.hint}`;
  banner.textContent = text;
  renderDevices();
});

$("#q").addEventListener("input", () => {
  clearTimeout(searchRefreshTimer);
  searchRefreshTimer = setTimeout(refresh, 200);
});
$("#type").addEventListener("change", refresh);
refresh();
setInterval(() => { if (tab !== "memory") refresh(); }, 5000);
