"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const storage = {
    get(key, fallback = null) { try { const v = localStorage.getItem("prism." + key); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem("prism." + key, JSON.stringify(value)); } catch { /* Storage may be disabled. */ } },
    remove(key) { try { localStorage.removeItem("prism." + key); } catch { /* Optional browser drafts. */ } }
  };
  const fragment = new URLSearchParams(location.hash.slice(1));
  let token = fragment.get("token");
  try {
    if (token) sessionStorage.setItem("prism.token", token);
    else token = sessionStorage.getItem("prism.token");
  } catch { /* A fragment token also works without browser persistence. */ }
  if (location.hash) history.replaceState(null, "", location.pathname);
  const state = { boards: [], board: null, selected: "", mode: storage.get("view", "read"),
    theme: storage.get("theme", "slate"), generation: 0, urls: [], timers: [],
    refreshing: false, editing: false, layoutUndo: [], pendingRequests: {}, locked: false };
  const svgNS = "http://www.w3.org/2000/svg";
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function svg(tag, attrs = {}, text) {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function button(text, callback, className) {
    const node = el("button", text, className); node.type = "button";
    node.addEventListener("click", callback); return node;
  }
  function notice(text = "", error = false) { $("notice").textContent = text; $("notice").classList.toggle("error", error); }
  function requestID() {
    const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
    return "web-" + Array.from(bytes, n => n.toString(16).padStart(2, "0")).join("");
  }
  async function api(path, body, binary = false) {
    const response = await fetch(path, { cache: "no-store", credentials: "omit",
      headers: { Authorization: "Bearer " + token, ...(body ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { method: "POST", body: JSON.stringify(body) } : {}) });
    if (!response.ok) {
      let message = "The private Prism connection is unavailable.";
      try { message = (await response.json()).error || message; } catch { /* Non-JSON network error. */ }
      const error = new Error(message); error.status = response.status; throw error;
    }
    return binary ? response.blob() : response.json();
  }
  async function action(name, params) {
    return api("/api/action", { action: name, params: { ...params, request_id: params.request_id || requestID() } });
  }
  function pendingKey(name, params) { return "pending." + name + "." + params.id + "." + params.card_id; }
  function pendingRequest(name, params) { const key = pendingKey(name, params); return state.pendingRequests[key] || storage.get(key); }
  async function retainedAction(name, params) {
    const key = pendingKey(name, params);
    const pending = pendingRequest(name, params) || { action: name, params: { ...params, request_id: requestID() } };
    state.pendingRequests[key] = pending; storage.set(key, pending);
    const clear = () => { delete state.pendingRequests[key]; storage.remove(key); };
    const receipt = await api("/api/action", pending);
    if (receipt.request_id !== pending.params.request_id || receipt.accepted === false || !receipt.id) throw new Error("The receipt is uncertain. Retry the pending request.");
    clear(); return receipt;
  }
  async function attempt(callback) {
    try { await callback(); } catch (error) { notice(error.message, true); }
  }
  function cleanup() {
    window.PrismTables?.capture($("board"));
    state.generation++;
    state.urls.forEach(url => URL.revokeObjectURL(url)); state.urls = [];
    state.timers.forEach(clearInterval); state.timers = []; state.editing = false;
  }
  function empty(message, show = false) {
    cleanup(); $("board").replaceChildren();
    const node = el("div", message, "empty");
    if (show) node.append(button("Show selected Prism", () => attempt(() => choose(state.selected, true))));
    $("board").append(node); $("heading").textContent = state.board?.title || "Prism";
    $("updated").textContent = ""; $("connections").replaceChildren();
  }
  function number(value) { return typeof value === "number" ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value) : String(value ?? ""); }
  function stamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", { month: "2-digit", day: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true }).formatToParts(date).map(p => [p.type, p.value]));
    return `${parts.month}/${parts.day}/${parts.year}  ${parts.hour}:${parts.minute} ${parts.dayPeriod.toLowerCase()}`;
  }
  function formattedHTML(value) {
    // Defense in depth: HTML from Python is already sanitized. Preserve only
    // formatting elements and text in a fresh document; never copy attributes.
    const source = new DOMParser().parseFromString(value, "text/html");
    const allowed = new Set("P BR HR H1 H2 H3 H4 B STRONG I EM U UL OL LI TABLE THEAD TBODY TR TD TH PRE CODE BLOCKQUOTE SPAN DIV SMALL SUB SUP".split(" "));
    const result = document.createDocumentFragment();
    const copy = (from, into) => {
      for (const node of from.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) into.append(document.createTextNode(node.textContent));
        else if (node.nodeType === Node.ELEMENT_NODE && allowed.has(node.tagName)) {
          const child = el(node.tagName.toLowerCase()); copy(node, child); into.append(child);
        }
      }
    };
    copy(source.body, result); return result;
  }
  function sanitizedHTML(value) { const holder = el("div"); holder.append(formattedHTML(value)); return holder.innerHTML; }
  function names(board) {
    const raw = board.member_names || {};
    const result = Array.isArray(raw) ? raw.filter(x => typeof x === "string") : Object.values(raw).filter(x => typeof x === "string");
    if (board.owner_name) result.unshift(board.owner_name);
    return [...new Set(result)];
  }
  function renderPicker() {
    const picker = $("board-picker"); picker.replaceChildren();
    if (!state.boards.length) picker.append(new Option("No Prism boards", ""));
    for (const board of state.boards) picker.append(new Option(board.title + (board.view_state?.operator_dismissed ? " · hidden" : ""), board.id));
    picker.value = state.selected;
    const index = state.boards.findIndex(board => board.id === state.selected);
    $("position").textContent = index >= 0 ? `${index + 1} / ${state.boards.length}` : "";
    $("previous").disabled = $("next").disabled = state.boards.length < 2;
    $("hide-board").disabled = !state.board;
  }
  async function refresh(explicit = false) {
    if (!token || state.locked || state.refreshing) return;
    state.refreshing = true;
    try {
      const boards = await api("/api/boards");
      if (state.locked) return;
      state.boards = boards;
      if (!state.selected || !boards.some(board => board.id === state.selected)) {
        state.selected = boards.find(board => !board.view_state?.operator_dismissed)?.id || boards[0]?.id || "";
        state.board = null;
      }
      renderPicker();
      if (!state.selected) { state.board = null; empty("No Prism boards have been shared yet."); return; }
      const current = boards.find(board => board.id === state.selected);
      if (current.view_state?.operator_dismissed) {
        state.board = current; empty("This Prism is hidden. Show it when you are ready.", true); return;
      }
      if (state.editing && !explicit) {
        if (current.revision !== state.board?.revision) notice("A newer revision is available. Your draft is kept; choose Refresh to review the changes.");
        return;
      }
      if (explicit || !state.board?.document || state.board.revision !== current.revision || state.board.view_seq !== current.view_seq) {
        const board = await api("/api/boards/" + encodeURIComponent(state.selected));
        if (state.locked) return;
        state.board = board; renderBoard();
        await presentation("visible");
      }
      if (!state.locked) $("footer-status").textContent = "Connected · Private operator view";
    } finally { state.refreshing = false; }
  }
  async function presentation(presentationState) {
    const board = state.board;
    if (board?.document && board.view_seq > 0) await action("activity_view", { id: board.id, state: presentationState, revision: board.revision, view_seq: board.view_seq });
  }
  async function choose(identity, explicit = true) {
    if (!identity) return;
    if (state.board?.document && state.selected !== identity) await presentation("hidden");
    if (explicit) await action("activity_control", { id: identity, action: "open" });
    if (state.selected !== identity) state.layoutUndo = [];
    state.selected = identity; state.board = null; notice(); await refresh(true);
  }
  async function advance(direction) {
    const index = state.boards.findIndex(board => board.id === state.selected);
    const board = state.boards[(index + direction + state.boards.length) % state.boards.length];
    if (board) await choose(board.id);
  }
  function renderBoard() {
    cleanup(); const board = state.board; const doc = board.document;
    $("heading").textContent = doc.title; document.title = doc.title + " · Prism";
    const updated = stamp(doc.updated_at || board.updated);
    $("updated").textContent = updated ? "– Updated: " + updated : "";
    $("connections").replaceChildren();
    const connected = names(board);
    if (connected.length) for (const name of connected) $("connections").append(el("span", name));
    else $("connections").append(el("span", "Connected session"));
    $("connections").title = connected.join(" · ");
    $("board").replaceChildren(); $("board").classList.toggle("canvas-mode", state.mode === "canvas");
    const hidden = storage.get("hidden." + board.id, []);
    for (const card of doc.cards) if (!hidden.includes(card.id)) $("board").append(renderCard(card));
    if (!$("board").children.length) $("board").append(el("div", "No visible cards. Use Reset layout to restore hidden cards.", "empty"));
    applyLayout(); renderPicker();
  }
  function renderCard(card) {
    const article = el("article", undefined, "prism-card" + (card.depth ? " depth" : ""));
    if (state.theme !== "contrast" && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(card.color || "")) {
      const color = card.color.length === 9 ? "#" + card.color.slice(3) + card.color.slice(1, 3) : card.color;
      article.style.setProperty("--accent", color);
    }
    article.dataset.card = card.id; article.setAttribute("aria-label", card.title);
    const heading = el("div", undefined, "card-heading");
    const move = button("⠿", () => {}, "move-handle"); move.setAttribute("aria-label", "Move " + card.title); move.title = "Drag to move; arrow keys move 10 pixels";
    heading.append(move, el("h2", card.title)); article.append(heading);
    if (typeof card.help === "string" && card.help.trim()) {
      const help = el("div", card.help.slice(0, 1000), "card-help-panel"); help.hidden = true;
      help.id = "help-" + card.id; help.setAttribute("role", "note");
      const helpButton = button("?", () => { help.hidden = !help.hidden; helpButton.setAttribute("aria-expanded", String(!help.hidden)); }, "card-help-button");
      helpButton.setAttribute("aria-label", "Help for " + card.title); helpButton.setAttribute("aria-expanded", "false"); helpButton.setAttribute("aria-controls", help.id);
      help.addEventListener("keydown", event => { if (event.key === "Escape") { help.hidden = true; helpButton.setAttribute("aria-expanded", "false"); helpButton.focus(); } });
      helpButton.addEventListener("keydown", event => { if (event.key === "Escape") { help.hidden = true; helpButton.setAttribute("aria-expanded", "false"); } });
      heading.append(helpButton); article.append(help);
    }
    const author = state.board.document.contributor_labels?.[card.id];
    if (author) article.append(el("div", author, "card-author"));
    const dataStatus = card.data_status;
    if (dataStatus && ["loading", "empty", "error", "stale"].includes(dataStatus.state)) {
      const line = el("div", undefined, "card-data-status state-" + dataStatus.state);
      const labels = { loading: "Loading", empty: "No data", error: "Data error", stale: "Data may be out of date" };
      line.append(el("strong", labels[dataStatus.state]));
      if (typeof dataStatus.message === "string" && dataStatus.message) line.append(document.createTextNode(" · " + dataStatus.message.slice(0, 400)));
      article.append(line);
    }
    const content = el("div", undefined, "card-content"); renderContent(card, content); article.append(content);
    if (card.caption) article.append(el("p", card.caption, "caption"));
    const direct = directActions(card); if (direct.childNodes.length) article.append(direct);
    article.append(cardOptions(card));
    const resize = button("↘", () => {}, "resize-handle"); resize.setAttribute("aria-label", "Resize " + card.title); resize.title = "Drag to resize; arrow keys resize 10 pixels";
    article.append(resize); geometryHandle(move, article, false); geometryHandle(resize, article, true);
    return article;
  }
  function detail(content, card) { if (card.detail) content.append(el("div", card.detail, "gadget-detail")); }
  function bigValue(content, value, unit) { const line = el("div", undefined, "big-value"); line.append(document.createTextNode(number(value))); if (unit) line.append(el("span", unit, "unit")); content.append(line); }
  function renderContent(card, content) {
    const kind = card.kind;
    if (kind === "text") content.append(formattedHTML(card.html));
    else if (kind === "image") {
      const generation = state.generation; const image = el("img"); image.alt = card.caption || card.title;
      const message = el("p", "Loading image…", "muted"); content.append(message);
      api("/api/asset?" + new URLSearchParams({ board: state.board.id, path: card.path }), null, true).then(blob => {
        if (generation !== state.generation) return;
        const url = URL.createObjectURL(blob); state.urls.push(url); image.src = url; content.replaceChildren(image);
      }).catch(() => { if (generation === state.generation) message.textContent = "Image is unavailable. Refresh to try again."; });
    } else if (kind === "gauge") {
      const low = card.min ?? 0, high = card.max ?? 100, ratio = (card.value - low) / (high - low), angle = Math.PI * (1 - ratio);
      const drawing = svg("svg", { viewBox: "0 0 240 168", class: "gauge", role: "img", "aria-label": `${card.title}: ${number(card.value)} ${card.unit || ""}, range ${number(low)} to ${number(high)}` });
      drawing.append(svg("path", { d: "M 25 120 A 95 95 0 0 1 215 120", class: "gauge-track", pathLength: 100 }));
      drawing.append(svg("path", { d: "M 25 120 A 95 95 0 0 1 215 120", class: "gauge-value", pathLength: 100, "stroke-dasharray": `${ratio * 100} 100` }));
      drawing.append(svg("line", { x1: 120, y1: 120, x2: 120 + 72 * Math.cos(angle), y2: 120 - 72 * Math.sin(angle), class: "gauge-needle" }));
      drawing.append(svg("circle", { cx: 120, cy: 120, r: 6, fill: "var(--complement)" }));
      drawing.append(svg("text", { x: 120, y: 160, "text-anchor": "middle", class: "value-label" }, number(card.value) + (card.unit ? " " + card.unit : "")));
      drawing.append(svg("text", { x: 20, y: 145, class: "range-label" }, number(low)), svg("text", { x: 220, y: 145, "text-anchor": "end", class: "range-label" }, number(high)));
      content.append(drawing); detail(content, card);
    } else if (kind === "meter" || kind === "progress") {
      const low = card.min ?? 0, high = card.max ?? 100, ratio = (card.value - low) / (high - low);
      content.append(el("div", number(card.value) + (card.unit ? " " + card.unit : ""), "meter-value"));
      const track = el("div", undefined, "meter-track" + (card.orientation === "vertical" ? " meter-vertical" : ""));
      track.setAttribute("role", kind === "progress" ? "progressbar" : "meter"); track.setAttribute("aria-label", card.title);
      track.setAttribute("aria-valuemin", low); track.setAttribute("aria-valuemax", high); track.setAttribute("aria-valuenow", card.value);
      const fill = el("div", undefined, "meter-fill"); fill.style[card.orientation === "vertical" ? "height" : "width"] = ratio * 100 + "%"; track.append(fill); content.append(track); detail(content, card);
    } else if (kind === "indicator" || kind === "status") {
      const line = el("div", undefined, "status-line state-" + (card.state || "neutral"));
      line.append(el("span", undefined, "status-dot"));
      if (card.symbol) line.append(el("span", card.symbol, "status-symbol"));
      line.append(el("span", card.text || card.state || "Ready", "status-text")); content.append(line); detail(content, card);
    } else if (kind === "counter" || kind === "stat") { bigValue(content, card.value ?? 0, card.unit); detail(content, card); }
    else if (kind === "timer") {
      const value = el("div", undefined, "big-value"), published = Date.parse(state.board.document.updated_at || ""), started = Number.isFinite(published) ? published : Date.now();
      const update = () => { const elapsed = card.running ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
        const seconds = Math.max(0, Math.floor((card.seconds || 0) + (card.mode === "countdown" ? -elapsed : elapsed)));
        value.textContent = `${Math.floor(seconds / 3600).toString().padStart(2, "0")}:${Math.floor(seconds / 60 % 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`; };
      update(); content.append(value, el("div", card.running ? (card.mode === "countdown" ? "Counting down" : "Running") : "Paused", "gadget-detail"));
      if (card.running) state.timers.push(setInterval(update, 1000)); detail(content, card);
    } else if (kind === "sparkline") {
      const values = card.values || []; if (!values.length) { content.append(el("p", "No observations yet", "muted")); return; }
      bigValue(content, values[values.length - 1], card.unit);
      const low = Math.min(...values), range = Math.max(...values) - low || 1;
      const drawing = svg("svg", { viewBox: "0 0 300 100", class: "sparkline", role: "img", "aria-label": `${card.label || card.title}: ${values.map(number).join(", ")} ${card.unit || ""}` });
      drawing.append(svg("polyline", { points: values.map((value, index) => `${6 + index / Math.max(values.length - 1, 1) * 288},${92 - (value - low) / range * 80}`).join(" ") }));
      content.append(drawing); if (card.label) content.append(el("div", card.label, "gadget-detail"));
    } else if (kind === "table") {
      if (window.PrismTables) content.append(window.PrismTables.render(card, { key: state.board.id + "/" + card.id }));
      else content.append(el("p", "Table controls are unavailable. Refresh this view.", "muted"));
    } else if (kind === "arrow") {
      const arrows = { left: "←", right: "→", up: "↑", down: "↓" }; const line = el("div", undefined, "arrow-gadget");
      const arrow = el("span", arrows[card.direction || "right"], "arrow-symbol"); arrow.setAttribute("aria-label", card.direction || "right");
      line.append(arrow); if (card.text) line.append(el("span", card.text)); content.append(line);
    } else if (kind === "chart") {
      const max = Math.max(1, ...card.values.map(Math.abs)), rows = el("div", undefined, "chart-bars");
      card.values.forEach((value, index) => { const row = el("div", undefined, "chart-row"), bar = el("div", undefined, "chart-bar"); bar.style.width = Math.abs(value) / max * 100 + "%"; row.append(el("span", card.labels[index]), bar, el("span", number(value) + (card.unit || ""))); rows.append(row); }); content.append(rows);
    } else if (kind === "question") content.append(el("p", card.prompt, "question-prompt"));
    else if (kind === "sketch") {
      const drawing = svg("svg", { viewBox: `0 0 ${card.canvas[0]} ${card.canvas[1]}`, class: "sketch", role: "img", "aria-label": card.caption || card.title });
      for (const stroke of card.strokes) drawing.append(svg("polyline", { points: stroke.points.map(point => point.join(",")).join(" "), fill: "none", stroke: stroke.color, "stroke-width": stroke.width, "stroke-linecap": "round", "stroke-linejoin": "round" }));
      content.append(drawing);
    } else if (kind === "diagram") {
      const nodes = card.nodes || [], width = Math.max(200, ...nodes.map(node => node.x + node.w + 20)), height = Math.max(100, ...nodes.map(node => node.y + node.h + 20));
      const drawing = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "diagram", role: "img", "aria-label": card.title + ": " + nodes.map(node => node.label).join(", ") });
      for (const edge of card.edges || []) {
        const from = nodes.find(node => node.id === edge.from), to = nodes.find(node => node.id === edge.to);
        if (from && to) { drawing.append(svg("line", { x1: from.x + from.w / 2, y1: from.y + from.h / 2, x2: to.x + to.w / 2, y2: to.y + to.h / 2 })); if (edge.label) drawing.append(svg("text", { x: (from.x + from.w / 2 + to.x + to.w / 2) / 2, y: (from.y + from.h / 2 + to.y + to.h / 2) / 2 - 5, "text-anchor": "middle" }, edge.label)); }
      }
      for (const node of nodes) { drawing.append(svg("rect", { x: node.x, y: node.y, width: node.w, height: node.h, rx: 8 })); drawing.append(svg("text", { x: node.x + node.w / 2, y: node.y + node.h / 2 + 5, "text-anchor": "middle" }, node.label)); } content.append(drawing);
    } else if (["action", "toggle", "slider", "select"].includes(kind)) {
      if (card.text || card.detail) content.append(el("p", card.text || card.detail, "muted"));
      else content.append(el("p", "Choose an action below.", "muted"));
    } else content.append(el("p", "This card needs a newer Prism version.", "muted"));
  }
  async function emit(card, actionID, value, sender) {
    sender.closest(".direct-actions")?.querySelectorAll("button,input,select").forEach(control => { control.disabled = true; });
    const boardID = state.board.id;
    try { await retainedAction("activity_event", { id: boardID, card_id: card.id, revision: state.board.revision, action_id: actionID, value }); notice("Intent sent to the connected session."); }
    finally { sender.disabled = false; const row = sender.closest(".direct-actions"); if (row?.isConnected && state.board?.id === boardID) row.replaceWith(directActions(card)); }
  }
  function directActions(card) {
    const row = el("div", undefined, "direct-actions");
    const send = (label, actionID, getValue) => { const item = button(label, () => attempt(() => emit(card, actionID, getValue(), item)), "primary"); row.append(item); return item; };
    if (card.kind === "action") send(card.label || "Send", card.action_id, () => null);
    if (card.kind === "toggle") {
      const input = el("input"); input.type = "checkbox"; input.checked = card.value || false;
      const label = el("label", card.label || "Enabled"); label.prepend(input); row.append(label);
      send("Send change", card.action_id, () => input.checked);
    }
    if (card.kind === "slider") {
      const group = el("div", undefined, "slider-control"), input = el("input"), output = el("output", number(card.value ?? card.min ?? 0));
      input.type = "range"; input.min = card.min ?? 0; input.max = card.max ?? 100; input.step = card.step ?? 1; input.value = card.value ?? card.min ?? 0;
      const label = el("label", card.label || card.title); input.id = "input-" + card.id; label.htmlFor = input.id;
      input.addEventListener("input", () => { output.textContent = number(Number(input.value)); });
      group.append(label, input, output); row.append(group); send("Send value", card.action_id, () => Number(input.value));
    }
    if (card.kind === "select") {
      const input = el("select"); for (const choice of card.choices) input.append(new Option(choice, choice)); input.value = card.value ?? card.choices[0];
      const label = el("label", card.label || "Choose"); label.append(input); row.append(label); send("Send choice", card.action_id, () => input.value);
    }
    for (const item of card.actions || []) send(item.label, item.id, () => null);
    const pending = pendingRequest("activity_event", { id: state.board.id, card_id: card.id });
    if (pending) {
      row.querySelectorAll("button,input,select").forEach(control => { control.disabled = true; });
      row.append(el("p", "Delivery is uncertain. Retry sends the same intent.", "draft-state"));
      const retry = button("Retry pending intent", () => attempt(async () => {
        retry.disabled = true;
        try { await retainedAction("activity_event", pending.params); notice("Intent receipt confirmed."); }
        finally { if (row.isConnected) row.replaceWith(directActions(card)); }
      }), "primary"); row.append(retry);
    }
    return row;
  }
  function cardOptions(card) {
    const options = el("details", undefined, "card-options"); options.append(el("summary", "Card actions"));
    options.addEventListener("toggle", () => {
      state.editing = [...document.querySelectorAll('.card-options[open] .edit-area')].length > 0;
    });
    const controls = el("div", undefined, "toolbar");
    const holder = el("div");
    if (card.data_status && typeof card.data_status === "object") {
      const metadata = el("p", undefined, "hint data-metadata"), status = card.data_status;
      if (typeof status.source === "string" && status.source) metadata.append(el("span", "Reported source: " + status.source.slice(0, 160)));
      if (typeof status.observed_at === "string" && stamp(status.observed_at)) metadata.append(el("span", "Observed: " + stamp(status.observed_at)));
      if (metadata.childNodes.length) options.append(metadata);
    }
    if (card.kind === "text") controls.append(button("Edit text", () => editor(card, holder, false)));
    if (card.kind === "question") controls.append(button("Reply", () => editor(card, holder, true)));
    const draftKey = "draft." + state.board.id + "." + card.id;
    const draft = storage.get(draftKey);
    if (draft) controls.append(el("span", "Saved browser draft", "draft-state"));
    controls.append(button("Hide card", () => {
      const key = "hidden." + state.board.id; storage.set(key, [...storage.get(key, []), card.id]); renderBoard();
    }));
    options.append(controls, holder); return options;
  }
  function editor(card, holder, reply) {
    if (holder.children.length) { holder.querySelector("[contenteditable],textarea,select")?.focus(); return; }
    state.editing = true;
    const board = state.board, key = "draft." + board.id + "." + card.id, draft = storage.get(key);
    const operation = reply ? "activity_reply" : "activity_edit";
    const existingRequest = pendingRequest(operation, { id: board.id, card_id: card.id });
    let baseRevision = draft?.revision || board.revision;
    const area = el("div", undefined, "edit-area"), label = el("label", reply ? "Your reply" : "Your text");
    let input;
    if (reply && card.choices?.length) { input = el("select"); for (const choice of card.choices) input.append(new Option(choice, choice)); input.value = draft?.content || card.choices[0]; label.append(input); }
    else if (reply) { input = el("textarea"); input.maxLength = 10000; input.value = draft?.content || ""; label.append(input); }
    else { input = el("div"); input.contentEditable = "true"; input.setAttribute("role", "textbox"); input.setAttribute("aria-multiline", "true"); input.setAttribute("aria-label", "Your text"); input.append(formattedHTML(draft?.content ?? card.html)); }
    const hint = el("p", "Draft stays in this browser until you send it.", "draft-state"), toolbar = el("div", undefined, "toolbar");
    const value = () => reply ? input.value : sanitizedHTML(input.innerHTML);
    const save = button(existingRequest ? "Retry pending save" : (reply ? "Send reply" : "Save text"), () => attempt(async () => {
      save.disabled = true;
      try {
        await retainedAction(operation, { id: board.id, card_id: card.id, revision: baseRevision, [reply ? "answer" : "content"]: value() });
        storage.remove(key); holder.replaceChildren(); state.editing = false; notice(reply ? "Reply sent to the connected session." : "Text saved."); await refresh(true);
      } finally {
        save.disabled = false;
        if (pendingRequest(operation, { id: board.id, card_id: card.id })) { save.textContent = "Retry pending save"; input.contentEditable = "false"; input.disabled = true; hint.textContent = "Delivery is uncertain. Retry uses your original text and request."; }
      }
    }), "primary");
    const keep = button("Use draft with this revision", () => { baseRevision = board.revision; save.disabled = false; hint.textContent = "Review your draft, then send it when ready."; keep.remove(); storage.set(key, { revision: baseRevision, content: value() }); });
    if (baseRevision !== board.revision && !existingRequest) { hint.textContent = "This draft is from an earlier revision. Review the current card before using it."; save.disabled = true; toolbar.append(keep); }
    if (existingRequest) { input.contentEditable = "false"; input.disabled = true; hint.textContent = "Delivery is uncertain. Retry uses your original text and request."; }
    toolbar.append(save, button(existingRequest ? "Close editor" : "Discard draft", () => { if (!pendingRequest(operation, { id: board.id, card_id: card.id })) storage.remove(key); holder.replaceChildren(); state.editing = false; }));
    input.addEventListener("input", () => { storage.set(key, { revision: baseRevision, content: value() }); hint.textContent = baseRevision === board.revision ? "Draft saved in this browser." : "Earlier revision draft saved. Review the current card before sending."; });
    area.append(label); if (!reply) area.append(input); area.append(hint, toolbar); holder.append(area); input.focus();
  }
  function layout() { return storage.get("layout." + state.board.id, {}); }
  function saveLayout(value, undo = true) { if (undo) { state.layoutUndo.push(layout()); if (state.layoutUndo.length > 30) state.layoutUndo.shift(); } storage.set("layout." + state.board.id, value); }
  function applyLayout() {
    if (!state.board?.document) return;
    const positions = layout(); let bottom = 0;
    for (const [index, card] of state.board.document.cards.entries()) {
      const node = [...$("board").children].find(child => child.dataset.card === card.id); if (!node) continue;
      const stored = positions[card.id];
      const geometry = (Array.isArray(stored) && stored.length === 4 && stored.every(Number.isFinite) ? stored : null) || card.geometry || [20 + index % 3 * 360, 20 + Math.floor(index / 3) * 340, 340, 320];
      const [x, y, width, height] = geometry;
      node.style.left = Math.max(0, x) + "px"; node.style.top = Math.max(0, y) + "px";
      node.style.width = Math.max(280, width) + "px";
      const reserved = [...node.children].filter(child => !child.classList.contains("card-content")).reduce((total, child) => total + child.offsetHeight, 0);
      const safeHeight = Math.max(160, height, reserved + (node.querySelector(".prism-table") ? 230 : 65));
      node.style.height = safeHeight + "px";
      node.style.setProperty("--card-height", safeHeight + "px"); bottom = Math.max(bottom, y + safeHeight + 30);
      if (state.mode !== "canvas") { node.style.width = ""; node.style.height = ""; }
    }
    $("board").style.height = state.mode === "canvas" && matchMedia("(min-width:900px)").matches ? bottom + "px" : "";
  }
  function currentGeometry(article) { return [parseInt(article.style.left) || 0, parseInt(article.style.top) || 0, article.offsetWidth, article.offsetHeight]; }
  function geometryHandle(handle, article, resize) {
    const change = (start, dx, dy) => {
      const result = [...start];
      if (resize) { result[2] = Math.max(280, Math.round((start[2] + dx) / 10) * 10); result[3] = Math.max(160, Math.round((start[3] + dy) / 10) * 10); }
      else { result[0] = Math.max(0, Math.round((start[0] + dx) / 10) * 10); result[1] = Math.max(0, Math.round((start[1] + dy) / 10) * 10); }
      return result;
    };
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || state.mode !== "canvas") return;
      const start = currentGeometry(article), initialX = event.clientX, initialY = event.clientY, before = layout();
      handle.setPointerCapture(event.pointerId); event.preventDefault(); let latest = start;
      const move = next => { latest = change(start, next.clientX - initialX, next.clientY - initialY); const positions = layout(); positions[article.dataset.card] = latest; storage.set("layout." + state.board.id, positions); applyLayout(); };
      const end = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", end); handle.removeEventListener("pointercancel", end); state.layoutUndo.push(before); if (state.layoutUndo.length > 30) state.layoutUndo.shift(); };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", end, { once: true }); handle.addEventListener("pointercancel", end, { once: true });
    });
    handle.addEventListener("keydown", event => {
      const directions = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] };
      if (!directions[event.key] || state.mode !== "canvas") return;
      event.preventDefault(); const positions = layout(); positions[article.dataset.card] = change(currentGeometry(article), ...directions[event.key]); saveLayout(positions); applyLayout();
    });
  }
  $("previous").title = "Previous Prism (Alt+Shift+Left)"; $("next").title = "Next Prism (Alt+Shift+Right)";
  $("previous").addEventListener("click", () => attempt(() => advance(-1)));
  $("next").addEventListener("click", () => attempt(() => advance(1)));
  $("board-picker").addEventListener("change", event => attempt(() => choose(event.target.value)));
  $("refresh").addEventListener("click", () => attempt(() => refresh(true)));
  $("hide-board").addEventListener("click", () => attempt(async () => { if (state.board) { await action("activity_control", { id: state.board.id, action: "close" }); await refresh(true); notice("Prism hidden. Show it explicitly when needed."); } }));
  $("show-board").addEventListener("click", () => attempt(() => choose(state.selected, true)));
  $("show-all").addEventListener("click", () => attempt(async () => { for (const board of state.boards) await action("activity_control", { id: board.id, action: "open" }); await refresh(true); notice("All Prisms are available. Use the arrows to browse."); }));
  $("view").value = state.mode;
  $("view").addEventListener("change", event => { state.mode = event.target.value; storage.set("view", state.mode); if (state.board?.document) { $("board").classList.toggle("canvas-mode", state.mode === "canvas"); applyLayout(); } });
  $("theme").value = state.theme; document.body.dataset.theme = state.theme;
  $("theme").addEventListener("change", event => { state.theme = event.target.value; document.body.dataset.theme = state.theme; storage.set("theme", state.theme); });
  $("undo-layout").addEventListener("click", () => { if (!state.board) return; const previous = state.layoutUndo.pop(); if (previous) { storage.set("layout." + state.board.id, previous); applyLayout(); } });
  $("reset-layout").addEventListener("click", () => { if (!state.board?.document) return; saveLayout({}); storage.remove("hidden." + state.board.id); renderBoard(); notice("Card layout restored."); });
  $("lock").addEventListener("click", () => { token = null; state.locked = true; try { sessionStorage.removeItem("prism.token"); } catch { /* Optional persistence. */ } state.board = null; state.boards = []; state.selected = ""; notice(); renderPicker(); empty("Prism is locked. Open its private browser link from Visor to reconnect."); $("footer-status").textContent = "Locked"; });
  document.addEventListener("keydown", event => { if (event.altKey && event.shiftKey && !event.ctrlKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); attempt(() => advance(event.key === "ArrowLeft" ? -1 : 1)); } });
  document.addEventListener("visibilitychange", () => { if (token && !state.locked) attempt(async () => { if (document.hidden) await presentation("hidden"); else { await refresh(); if (state.board?.document) await presentation("visible"); } }); });
  window.addEventListener("resize", applyLayout);
  if (!token) { state.locked = true; empty("Open the private browser link from Visor to connect."); $("footer-status").textContent = "Locked"; }
  else attempt(() => refresh(true));
  setInterval(() => { if (!document.hidden) attempt(() => refresh()); }, 3000);
})();
