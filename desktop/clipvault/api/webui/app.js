"use strict";
const $ = (s) => document.querySelector(s);
let tab = "history";

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

function esc(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function fmtTime(iso) {
  if (!iso) return "";
  return iso.replace("T", " ").replace("Z", "");
}

function card(c) {
  const secret = c.is_secret;
  const actions = secret
    ? `<button class="release" data-release="${c.id}">释放为非密钥</button>
       <button data-del="${c.id}">删除</button>`
    : `<button class="${c.pinned ? "on" : ""}" data-pin="${c.id}" data-v="${!c.pinned}">📌 固定</button>
       <button class="${c.favorite ? "on" : ""}" data-fav="${c.id}" data-v="${!c.favorite}">★ 收藏</button>
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

async function refresh() {
  const status = await api("/api/status");
  $("#status").textContent =
    `共 ${status.clips_total} 条 · 隔离 ${status.quarantined} · 待备份 ${status.backup_pending}`
    + (status.last_backup_at ? ` · 最近备份 ${fmtTime(status.last_backup_at)}` : "");

  const params = new URLSearchParams();
  if (tab === "quarantine") params.set("secret", "1");
  const q = $("#q").value.trim();
  if (q) params.set("q", q);
  const t = $("#type").value;
  if (t) params.set("type", t);
  const data = await api("/api/clips?" + params.toString());
  const clips = data.clips || [];
  $("#list").innerHTML = clips.map(card).join("");
  $("#empty").hidden = clips.length > 0;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.tab) {
    tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $(".toolbar").style.display = tab === "quarantine" ? "none" : "flex";
    return refresh();
  }
  const patch = (id, body) => api(`/api/clips/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (b.dataset.pin) { await patch(b.dataset.pin, { pinned: b.dataset.v === "true" }); refresh(); }
  else if (b.dataset.fav) { await patch(b.dataset.fav, { favorite: b.dataset.v === "true" }); refresh(); }
  else if (b.dataset.del) { await patch(b.dataset.del, { deleted: true }); refresh(); }
  else if (b.dataset.release) {
    await api(`/api/clips/${b.dataset.release}/release`, { method: "POST" }); refresh();
  } else if (b.dataset.copy) {
    const pre = b.closest(".card").querySelector("[data-content]");
    navigator.clipboard.writeText(pre.textContent).then(() => { b.textContent = "已复制"; });
  }
});

$("#q").addEventListener("input", () => { clearTimeout(window._t); window._t = setTimeout(refresh, 200); });
$("#type").addEventListener("change", refresh);
refresh();
setInterval(refresh, 5000);
