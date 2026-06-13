"use strict";
const $ = (s) => document.querySelector(s);
let tab = "history";

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}
const jpatch = (id, body) => api(`/api/clips/${id}`, {
  method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function esc(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function fmtTime(iso) { return iso ? iso.replace("T", " ").replace("Z", "") : ""; }

// Mirror of core/actions.recommend() for the primary promote chip (S011).
const PROMOTE = {
  command: ["保存为常用命令", "command"], prompt: ["归档为 Prompt", "prompt"],
  url: ["保存链接到词库", "path"], path: ["保存路径到词库", "path"],
  code: ["加入代码片段", "phrase"], error_log: ["加入词库", "phrase"], text: ["加入词库", "phrase"],
};

function clipCard(c) {
  const secret = c.is_secret;
  const actions = secret
    ? `<button class="release" data-release="${c.id}">释放为非密钥</button>
       <button data-del="${c.id}">删除</button>`
    : `<button class="${c.pinned ? "on" : ""}" data-pin="${c.id}" data-v="${!c.pinned}">📌 固定</button>
       <button class="${c.favorite ? "on" : ""}" data-fav="${c.id}" data-v="${!c.favorite}">★ 收藏</button>
       <button data-promote="${c.id}" data-kind="${(PROMOTE[c.content_type]||PROMOTE.text)[1]}">${(PROMOTE[c.content_type]||PROMOTE.text)[0]}</button>
       <button data-copy="${c.id}">复制</button>
       <button data-del="${c.id}">删除</button>`;
  const reasons = secret && c.secret_reasons.length
    ? `<span class="pill secret">${esc(c.secret_reasons.join(","))}</span>` : "";
  return `<article class="card ${secret ? "secret" : ""}" data-id="${c.id}">
    <div class="meta">
      <span class="pill type">${c.content_type}</span>
      ${secret ? `<span class="pill secret">${c.secret_level}</span>` : ""}
      ${reasons}
      ${c.times_seen > 1 ? `<span class="pill">×${c.times_seen}</span>` : ""}
      <span class="time">${fmtTime(c.last_seen_at)}</span>
    </div>
    <pre class="content" data-content>${esc(c.content)}</pre>
    <div class="actions">${actions}</div>
  </article>`;
}

function memCard(m) {
  return `<article class="card" data-id="${m.id}">
    <div class="meta">
      <span class="pill type">${m.kind}</span>
      ${m.pinned ? `<span class="pill">📌</span>` : ""}
      <span class="pill">用 ${m.use_count}</span>
      <span class="pill">${m.source}</span>
      <span class="time">${fmtTime(m.last_used_at)}</span>
    </div>
    <pre class="content" data-content>${esc(m.text)}</pre>
    <div class="actions">
      <button data-memcopy="${m.id}">复制</button>
      <button data-memdel="${m.id}">删除</button>
    </div>
  </article>`;
}

async function refresh() {
  const status = await api("/api/status");
  $("#status").textContent =
    `共 ${status.clips_total} 条 · 隔离 ${status.quarantined} · 待备份 ${status.backup_pending}`
    + (status.last_backup_at ? ` · 最近备份 ${fmtTime(status.last_backup_at)}` : "");

  if (tab === "memory") {
    const data = await api("/api/memory");
    const items = data.memory || [];
    $("#list").innerHTML = items.map(memCard).join("");
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
  $("#list").innerHTML = clips.map(clipCard).join("");
  $("#empty").hidden = clips.length > 0;
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
  else if (b.dataset.release) { await api(`/api/clips/${b.dataset.release}/release`, { method: "POST" }); refresh(); }
  else if (b.dataset.promote) {
    await api(`/api/clips/${b.dataset.promote}/promote`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: b.dataset.kind }),
    });
    b.textContent = "已加入"; b.classList.add("on");
  }
  else if (b.dataset.memdel) { await api(`/api/memory/${b.dataset.memdel}`, { method: "DELETE" }); refresh(); }
  else if (b.dataset.copy || b.dataset.memcopy) {
    const pre = b.closest(".card").querySelector("[data-content]");
    navigator.clipboard.writeText(pre.textContent).then(() => { b.textContent = "已复制"; });
  }
  else if (b.id === "mem-add") {
    const text = $("#mem-text").value.trim();
    if (!text) return;
    await api("/api/memory", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: $("#mem-kind").value, text }),
    });
    $("#mem-text").value = ""; refresh();
  }
});

$("#pair-btn").addEventListener("click", async () => {
  const r = await api("/api/pair/code");
  const banner = $("#pair-banner");
  banner.hidden = false;
  banner.textContent = `配对码：${r.code}（${Math.round(r.ttl_seconds / 60)} 分钟内有效，在 Android 端输入）`;
});

$("#q").addEventListener("input", () => { clearTimeout(window._t); window._t = setTimeout(refresh, 200); });
$("#type").addEventListener("change", refresh);
refresh();
setInterval(() => { if (tab !== "memory") refresh(); }, 5000);
