"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const keys = { draft: "visor.browser.draft", pending: "visor.browser.pending", selected: "visor.browser.selected", message: "visor.browser.message" };
  const strip = value => value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, "");
  const read = (key, fallback = null) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const save = (key, value, required = false) => {
    try { const encoded = JSON.stringify(value); localStorage.setItem(key, encoded); if (localStorage.getItem(key) !== encoded) throw new Error("Storage verification failed"); return true; }
    catch { if (required) throw new Error("This browser cannot safely save the retry. Enable local storage before sending."); return false; }
  };
  const fragment = new URLSearchParams(location.hash.slice(1));
  let token = fragment.get("token");
  try { if (token) sessionStorage.setItem("prism.token", token); else token = sessionStorage.getItem("prism.token"); } catch { /* A fresh fragment can authenticate this page. */ }
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const validPending = item => item && typeof item === "object" && Object.keys(item).sort().join() === "body,recipient,request_id" && typeof item.recipient === "string" && item.recipient.length > 0 && typeof item.body === "string" && item.body.length > 0 && item.body.length <= 16000 && uuid.test(item.request_id);
  const restored = read(keys.pending);
  const savedDraft = read(keys.draft, {});
  const state = { sessions: [], messages: [], pairs: [], selected: read(keys.selected, ""), message: read(keys.message, ""),
    pending: validPending(restored) ? restored : null, draft: { recipient: typeof savedDraft.recipient === "string" ? savedDraft.recipient : "", body: typeof savedDraft.body === "string" ? savedDraft.body : "" },
    sending: false, refreshing: false, locked: false, invalidPending: Boolean(restored && !validPending(restored)), receiptGeneration: 0, snapshot: null };
  function node(tag, text, className) { const item = document.createElement(tag); if (text !== undefined) item.textContent = String(text); if (className) item.className = className; return item; }
  function notice(text = "", error = false) { $("notice").textContent = text; $("notice").classList.toggle("error", error); }
  function friendly(identity, fallback = "Session") { return identity === "operator" ? "You" : state.sessions.find(item => item.id === identity)?.name || fallback; }
  function stamp(value) { const date = new Date(typeof value === "number" ? value * 1000 : value); return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
  const stateLabel = value => ({ delivery_unknown: "Delivery uncertain", completed: "Reported complete", read: "Acknowledged", acknowledged: "Acknowledged", queued: "Queued", sending: "Sending", delivered: "Delivered", held: "Held", failed: "Failed", deleted: "Deleted" }[value] || String(value || "Unknown"));
  function badge(value) { const item = node("span", stateLabel(value), "status"); item.dataset.state = value; return item; }
  async function api(path, body) {
    if (!token || state.locked) throw new Error("Reopen the private browser link from VISOR to connect.");
    const timeout = new AbortController(); const timer = setTimeout(() => timeout.abort(), 15000);
    try {
      const response = await fetch(path, { cache: "no-store", credentials: "omit", signal: timeout.signal,
        headers: { Authorization: "Bearer " + token, ...(body ? { "Content-Type": "application/json" } : {}) },
        ...(body ? { method: "POST", body: JSON.stringify(body) } : {}) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Private browser request unavailable.");
      return result;
    } finally { clearTimeout(timer); }
  }
  function requestID() {
    const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  function renderSessions() {
    const filter = $("search").value.toLocaleLowerCase(); $("sessions").replaceChildren();
    const sessions = state.sessions.filter(item => [item.name, item.provider, item.project].some(value => String(value || "").toLocaleLowerCase().includes(filter)));
    $("session-count").textContent = `${sessions.length}`;
    for (const session of sessions) {
      const entry = node("div"); entry.setAttribute("role", "listitem");
      const button = node("button", undefined, "session"); button.type = "button"; button.dataset.session = session.id; button.setAttribute("aria-pressed", session.id === state.selected ? "true" : "false");
      const top = node("span", undefined, "session-top"); top.append(node("span", session.name || "Unnamed session", "session-name"), badge(session.status)); button.append(top);
      button.append(node("span", session.provider || "Provider", "session-meta"));
      if (session.project) { const project = node("span", session.project, "session-project"); project.title = session.project; button.append(project); }
      button.addEventListener("click", () => choose(session.id)); entry.append(button); $("sessions").append(entry);
    }
    if (!sessions.length) $("sessions").append(node("p", state.sessions.length ? "No matching sessions." : "No sessions available.", "empty"));
    $("agent-status").textContent = state.snapshot?.agent_enabled ? "Agent messaging enabled" : "Agent messaging paused";
    $("pairs").replaceChildren();
    for (const pair of state.pairs) $("pairs").append(node("p", `${pair.a_name || friendly(pair.a)} ↔ ${pair.b_name || friendly(pair.b)}`, "pair"));
    if (!state.pairs.length) $("pairs").append(node("p", "No session connections.", "pair"));
  }
  function choose(identity) {
    state.selected = identity; save(keys.selected, identity);
    if (!state.pending) { state.draft.recipient = identity; save(keys.draft, state.draft); }
    renderSessions(); renderMessages(true); renderComposer();
  }
  function renderMessages(scroll = false) {
    const session = state.sessions.find(item => item.id === state.selected);
    $("session-heading").textContent = session?.name || "Choose a session";
    $("session-description").textContent = session ? [session.provider, session.description].filter(Boolean).join(" · ") : "";
    const panel = $("messages"); const nearBottom = panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 40;
    const oldScroll = panel.scrollTop; panel.replaceChildren(); const filter = $("message-filter").value;
    const messages = state.messages.filter(item => state.selected && (filter === "inbox" ? item.recipient === state.selected : filter === "outbox" ? item.sender === state.selected : item.sender === state.selected || item.recipient === state.selected)).sort((a,b) => Number(a.created) - Number(b.created));
    for (const message of messages) {
      const operator = message.origin === "operator" && message.sender === "operator";
      const article = node("article", undefined, "message" + (operator ? " operator" : "") + (state.message === message.id ? " selected" : "")); article.dataset.message = message.id;
      const header = node("div", undefined, "message-heading");
      const route = node("div"); route.append(node("div", `${message.sender_name || friendly(message.sender)} → ${message.recipient_name || friendly(message.recipient)}`, "message-route"));
      const when = node("time", stamp(message.created)); route.append(when); header.append(route, badge(message.state)); article.append(header);
      const body = String(message.body || ""); article.append(node("p", body.length > 500 ? body.slice(0, 500) + "…" : body, "message-body"));
      if (body.length > 500) { const details = node("details"); details.append(node("summary", "Full message"), node("pre", body, "exact-body")); article.append(details); }
      if (operator) { const actions = node("div", undefined, "message-actions"); actions.append(node("span", message.kind === "wrap_up" ? "Wrap-up request" : "From you", "muted")); const inspect = node("button", "View receipt & reply"); inspect.type = "button"; inspect.addEventListener("click", () => selectMessage(message.id)); actions.append(inspect); article.append(actions); }
      panel.append(article);
    }
    if (!messages.length) panel.append(node("p", state.selected ? "No recent messages in this view." : "Choose a session to review recent messages.", "empty"));
    panel.scrollTop = scroll || nearBottom ? panel.scrollHeight : oldScroll;
  }
  function renderComposer() {
    const pending = state.pending; const recipient = pending?.recipient || state.selected;
    const value = pending ? pending.body : state.draft.body;
    if ($("message-body").value !== value) $("message-body").value = value;
    $("message-body").disabled = Boolean(pending) || state.locked || !recipient;
    $("compose-recipient").textContent = recipient ? "To: " + friendly(recipient, "Saved recipient") : "Select a session first.";
    $("character-count").textContent = `${value.length} / 16000`;
    $("pending-note").hidden = !pending; $("clear-retry").hidden = !pending; $("clear-retry").disabled = state.sending;
    $("send").textContent = state.sending ? "Confirming…" : pending ? "Retry same message" : "Send message";
    $("send").disabled = state.locked || state.sending || state.invalidPending || !recipient || (!pending && !strip(state.draft.body));
    $("compose-status").textContent = pending ? "Exact retry saved on this device." : "Drafts stay in this browser.";
  }
  async function selectMessage(identity) {
    state.message = identity; save(keys.message, identity); renderMessages();
    await refreshReceipt();
    if (!state.locked && innerWidth <= 1100) document.querySelector(".receipt-panel").scrollIntoView({ block: "start" });
  }
  async function refreshReceipt() {
    if (!state.message || state.locked) return;
    const identity = state.message; const generation = ++state.receiptGeneration;
    try {
      const message = await api("/api/visor/message/" + encodeURIComponent(identity));
      if (state.locked || identity !== state.message || generation !== state.receiptGeneration) return;
      if (message.id !== identity || message.origin !== "operator" || message.sender !== "operator") throw new Error("The exact message receipt could not be confirmed.");
      renderReceipt(message);
    } catch (error) { if (!state.locked && identity === state.message) $("receipt").replaceChildren(node("p", error.message, "empty")); }
  }
  function renderReceipt(message) {
    const target = $("receipt"); target.replaceChildren();
    target.append(node("p", "To " + (message.recipient_name || friendly(message.recipient)), "receipt-meta"), node("p", stamp(message.created), "receipt-meta"), badge(message.state));
    const stages = node("dl", undefined, "receipt-stages");
    const delivered = ["delivered", "read", "acknowledged", "completed"].includes(message.state);
    for (const [label,value] of [["Delivery", delivered ? "Delivered" : stateLabel(message.state)], ["Read", ["read", "acknowledged"].includes(message.state) ? "Acknowledged" : message.state === "completed" ? "Not separately recorded" : "Not recorded"], ["Complete", message.state === "completed" ? "Reported complete" : "Not recorded"]]) { const row = node("div"); row.append(node("dt", label), node("dd", value)); stages.append(row); }
    target.append(stages, node("h3", "Your exact message"), node("pre", message.body, "exact-body"));
    const reply = node("section", undefined, "receipt-block"); reply.id = "reply";
    reply.append(node("h3", message.reply ? "Reply from " + (message.reply.sender_name || friendly(message.reply.sender)) : "Reply"));
    if (message.reply) reply.append(node("pre", message.reply.body, "exact-body"), node("p", stamp(message.reply.created), "receipt-meta"));
    else reply.append(node("p", "No reply recorded for this exact message.", "hint"));
    reply.append(node("p", "Reply, read acknowledgment and completion are separate.", "hint")); target.append(reply);
    const details = node("details", undefined, "receipt-reference"); details.append(node("summary", "Message reference"), node("code", message.id));
    const copy = node("button", "Copy reference"); copy.type = "button"; copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(message.id); copy.textContent = "Copied"; } catch { details.open = true; copy.textContent = "Select the reference above to copy"; } }); details.append(copy); target.append(details);
  }
  async function refresh() {
    if (state.refreshing || state.locked || !token) return; state.refreshing = true;
    try {
      const snapshot = await api("/api/visor"); if (state.locked) return;
      state.snapshot = snapshot; state.sessions = Array.isArray(snapshot.sessions) ? snapshot.sessions : []; state.messages = Array.isArray(snapshot.messages) ? snapshot.messages : []; state.pairs = Array.isArray(snapshot.pairs) ? snapshot.pairs : [];
      if (!state.sessions.some(item => item.id === state.selected)) state.selected = state.sessions.find(item => item.id === state.draft.recipient)?.id || state.sessions[0]?.id || "";
      $("version").textContent = snapshot.version ? snapshot.version : ""; $("connection").textContent = "Connected · private";
      renderSessions(); renderMessages(); renderComposer(); await refreshReceipt();
    } catch (error) { if (!state.locked) { $("connection").textContent = "Connection unavailable"; notice(error.message, true); } }
    finally { state.refreshing = false; }
  }
  async function send(event) {
    event.preventDefault(); if (state.sending || state.locked || state.invalidPending) return;
    try {
      const existing = read(keys.pending);
      if (validPending(existing)) state.pending = existing;
      if (!state.pending) {
        const body = strip(state.draft.body); if (!state.selected || !body || body.length > 16000) throw new Error("Choose a session and write 1–16000 characters.");
        state.pending = { recipient: state.selected, body, request_id: requestID() };
      }
      const request = { ...state.pending }; save(keys.pending, request, true);
      state.sending = true; renderComposer(); notice();
      const receipt = await api("/api/visor/send", request);
      if (state.locked) return;
      if (receipt.request_id !== request.request_id || !uuid.test(receipt.id) || receipt.sender !== "operator" || receipt.origin !== "operator" || receipt.recipient !== request.recipient || receipt.body !== request.body) throw new Error("The exact send receipt is unconfirmed. Retry the saved message.");
      if (JSON.stringify(read(keys.pending)) === JSON.stringify(request)) localStorage.removeItem(keys.pending);
      state.pending = validPending(read(keys.pending)) ? read(keys.pending) : null;
      if (state.draft.recipient === request.recipient && strip(state.draft.body) === request.body) { state.draft.body = ""; save(keys.draft, state.draft); }
      state.message = receipt.id; save(keys.message, receipt.id); notice("Message accepted · delivery, read and completion are tracked separately.");
      renderReceipt(receipt); await refresh();
    } catch (error) { if (!state.locked) notice(state.pending ? "Send not confirmed. Your exact message is saved for retry. " + error.message : error.message, true); }
    finally { state.sending = false; if (!state.locked) renderComposer(); }
  }
  let clearing = null;
  $("clear-retry").addEventListener("click", () => { if (!state.pending || state.sending) return; clearing = { ...state.pending }; $("clear-dialog").returnValue = "cancel"; $("clear-dialog").showModal(); $("clear-dialog").querySelector('[value="cancel"]').focus(); });
  $("clear-dialog").addEventListener("close", () => {
    if ($("clear-dialog").returnValue !== "clear" || !clearing || state.sending) return;
    if (JSON.stringify(read(keys.pending)) !== JSON.stringify(clearing)) { notice("The saved retry changed in another tab. Review it first.", true); return; }
    try { localStorage.removeItem(keys.pending); } catch { notice("The saved retry could not be cleared.", true); return; }
    state.pending = null; renderComposer(); notice("Local retry cleared. Previously accepted messages remain recorded.");
  });
  $("message-body").addEventListener("input", () => { if (state.pending) return; state.draft = { recipient: state.selected, body: $("message-body").value }; save(keys.draft, state.draft); renderComposer(); });
  $("composer").addEventListener("submit", send); $("search").addEventListener("input", renderSessions); $("message-filter").addEventListener("change", () => renderMessages(true)); $("refresh").addEventListener("click", refresh);
  $("lock").addEventListener("click", () => { state.locked = true; token = null; state.receiptGeneration++; try { sessionStorage.removeItem("prism.token"); } catch { /* Session storage may be unavailable. */ } state.sessions = []; state.messages = []; state.pairs = []; state.snapshot = null; $("sessions").replaceChildren(); $("pairs").replaceChildren(); $("messages").replaceChildren(); $("receipt").replaceChildren(); $("session-heading").textContent = "Private view locked"; $("session-description").textContent = ""; $("message-body").value = ""; $("message-body").disabled = true; $("send").disabled = true; $("clear-retry").hidden = true; $("pending-note").hidden = true; $("compose-recipient").textContent = ""; $("connection").textContent = "Locked"; notice("Reopen the private link from VISOR. Saved drafts and retries remain on this device."); });
  window.addEventListener("storage", event => { if (event.key === keys.pending && !state.locked) { const pending = read(keys.pending); state.pending = validPending(pending) ? pending : null; renderComposer(); } });
  if (!token) { $("connection").textContent = "Not connected"; notice("Open this optional private view from VISOR to connect."); }
  if (restored && !validPending(restored)) notice("A saved retry could not be validated. It was kept on this device; no message was sent.", true);
  renderComposer(); refresh(); setInterval(refresh, 5000);
})();
