"use strict";
/* Portable Prism tables. Published data never supplies CSS declarations or code. */
(() => {
  const states = new Map(), instances = new WeakMap();
  const colors = /^#[0-9a-f]{6}$/i, ids = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
  const badges = new Set(["neutral", "info", "success", "warning", "error", "working", "waiting", "offline"]);
  const integer = (value, low, high) => Number.isInteger(value) && value >= low && value <= high;
  const object = value => value && typeof value === "object" && !Array.isArray(value);
  const element = (tag, text, name) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (name) node.className = name; return node; };
  const button = (text, action) => { const node = element("button", text); node.type = "button"; node.addEventListener("click", action); return node; };
  const display = value => value === null ? "" : value === true ? "Yes" : value === false ? "No" : String(value);
  function border(value) {
    const result = {};
    if (!object(value)) return result;
    if (typeof value.color === "string" && colors.test(value.color)) result.color = value.color;
    if (typeof value.width === "number" && Number.isFinite(value.width) && value.width >= 0 && value.width <= 8) result.width = value.width;
    if (["solid", "dashed", "dotted", "none"].includes(value.style)) result.style = value.style;
    return result;
  }
  function cellStyle(value) {
    const result = {};
    if (!object(value)) return result;
    for (const name of ["background", "text_color"]) if (typeof value[name] === "string" && colors.test(value[name])) result[name] = value[name];
    if (integer(value.font_size, 10, 28)) result.font_size = value.font_size;
    if (integer(value.padding, 0, 16)) result.padding = value.padding;
    for (const name of ["bold", "wrap"]) if (typeof value[name] === "boolean") result[name] = value[name];
    if (["left", "center", "right"].includes(value.align)) result.align = value.align;
    if (badges.has(value.badge)) result.badge = value.badge;
    if (typeof value.tooltip === "string" && value.tooltip.length <= 1000 && !value.tooltip.includes("\0")) result.tooltip = value.tooltip;
    if (Object.keys(border(value.border)).length) result.border = border(value.border);
    return result;
  }
  function options(card) {
    const raw = object(card.table_style) ? card.table_style : {}, count = card.columns.length;
    const result = { density: ["compact", "comfortable"].includes(raw.density) ? raw.density : "comfortable",
      striped: raw.striped === true, grid: ["all", "horizontal", "vertical", "none"].includes(raw.grid) ? raw.grid : "all",
      border: border(raw.border), header: cellStyle(raw.header), columns: [], rows: [], cells: [],
      sortable: raw.sortable === true, filterable: raw.filterable === true, copyable: raw.copyable === true };
    const seenColumns = new Set(), seenRows = new Set(), seenCells = new Set();
    for (const item of Array.isArray(raw.columns) ? raw.columns.slice(0, count) : []) {
      if (!object(item) || !integer(item.column, 0, count - 1) || seenColumns.has(item.column)) continue;
      seenColumns.add(item.column); const style = { column: item.column, ...cellStyle(item) };
      if (integer(item.width, 80, 800)) style.width = item.width;
      result.columns.push(style);
    }
    for (const item of Array.isArray(raw.rows) ? raw.rows.slice(0, card.rows.length) : []) {
      if (!object(item) || !integer(item.row, 0, card.rows.length - 1) || seenRows.has(item.row)) continue;
      seenRows.add(item.row); result.rows.push({ row: item.row, ...cellStyle(item) });
    }
    for (const item of Array.isArray(raw.cells) ? raw.cells.slice(0, card.rows.length * count) : []) {
      if (!object(item) || !integer(item.row, 0, card.rows.length - 1) || !integer(item.column, 0, count - 1)) continue;
      const key = item.row + ":" + item.column; if (seenCells.has(key)) continue;
      seenCells.add(key); result.cells.push({ row: item.row, column: item.column, ...cellStyle(item) });
    }
    return result;
  }
  function merged(...styles) {
    const result = {};
    for (const style of styles) if (style) {
      const combinedBorder = { ...(result.border || {}), ...(style.border || {}) };
      Object.assign(result, style); if (Object.keys(combinedBorder).length) result.border = combinedBorder;
    }
    return result;
  }
  function applyStyle(cell, style, grid, density) {
    cell.style.padding = (style.padding ?? ({ compact: 4, comfortable: 8 }[density])) + "px";
    if (style.background) cell.style.backgroundColor = style.background;
    if (style.text_color) cell.style.color = style.text_color;
    if (style.font_size) cell.style.fontSize = style.font_size + "px";
    if (typeof style.bold === "boolean") cell.style.fontWeight = style.bold ? "700" : "400";
    if (style.align) cell.style.textAlign = style.align;
    cell.style.whiteSpace = style.wrap === false ? "nowrap" : "normal";
    cell.style.overflowWrap = style.wrap === false ? "normal" : "anywhere";
    if (style.wrap === false) { cell.style.overflow = "hidden"; cell.style.textOverflow = "ellipsis"; }
    const effective = style.border || {}, explicit = Object.keys(effective).length > 0;
    cell.style.border = "none";
    const line = `${effective.width ?? 1}px ${effective.style || "solid"} ${effective.color || "var(--line)"}`;
    if (explicit) cell.style.border = line;
    else {
      if (["all", "horizontal"].includes(grid)) cell.style.borderBottom = "1px solid var(--line)";
      if (["all", "vertical"].includes(grid)) cell.style.borderRight = "1px solid var(--line)";
    }
    if (style.tooltip) cell.title = style.tooltip;
  }
  function columnKeys(columns) { const seen = new Map(); return columns.map(value => { const n = (seen.get(value) || 0) + 1; seen.set(value, n); return JSON.stringify(value) + ":" + n; }); }
  function rowKeys(card) {
    if (Array.isArray(card.row_ids) && card.row_ids.length === card.rows.length && card.row_ids.every(value => typeof value === "string" && ids.test(value)) && new Set(card.row_ids).size === card.rows.length) return [...card.row_ids];
    // Exact canonical row content is collision-free locally. Explicit row_ids
    // preserve identity even when a publisher changes a row's cell values.
    const seen = new Map();
    return card.rows.map(row => { const value = JSON.stringify(row); const n = (seen.get(value) || 0) + 1; seen.set(value, n); return "row:" + value + ":" + n; });
  }
  function restored(key, available) {
    let value;
    try { value = JSON.parse(localStorage.getItem("prism.table." + key)); } catch { /* Optional persistence. */ }
    if (!object(value)) value = {};
    const selected = new Set();
    if (available.size && Array.isArray(value.selected)) for (const identity of value.selected) {
      if (typeof identity === "string" && available.has(identity)) selected.add(identity);
      if (selected.size === available.size) break;
    }
    return { filter: typeof value.filter === "string" ? value.filter.slice(0, 1000) : "", sort: object(value.sort) ? value.sort : null,
      selected,
      anchor: typeof value.anchor === "string" ? value.anchor : null, focus: typeof value.focus === "string" ? value.focus : null,
      widths: object(value.widths) ? value.widths : {}, scrollTop: Number.isFinite(value.scrollTop) ? Math.max(0, value.scrollTop) : 0,
      scrollLeft: Number.isFinite(value.scrollLeft) ? Math.max(0, value.scrollLeft) : 0 };
  }
  function save(key, state) {
    try { localStorage.setItem("prism.table." + key, JSON.stringify({ ...state, selected: [...state.selected] })); } catch { /* State still survives board refresh in memory. */ }
  }
  function tsv(rows) {
    const quote = value => { const text = display(value); return /[\t\r\n"]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text; };
    return rows.map(row => row.map(quote).join("\t")).join("\n");
  }
  function textEditor(target) { return target instanceof Element && Boolean(target.closest("textarea,[contenteditable=true],input:not([type=checkbox])")); }
  function render(card, config = {}) {
    const root = element("section", undefined, "prism-table");
    if (!Array.isArray(card.columns) || !card.columns.length || !card.columns.every(value => typeof value === "string") || !Array.isArray(card.rows) || !card.rows.every(row => Array.isArray(row) && row.length === card.columns.length && row.every(value => value === null || ["string", "boolean"].includes(typeof value) || typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e15))) {
      root.append(element("p", "This table contains unsupported data.", "hint")); return root;
    }
    const key = typeof config.key === "string" ? config.key : String(card.id || "table");
    const settings = options(card), keys = rowKeys(card), columns = columnKeys(card.columns);
    const available = new Set(keys);
    const state = states.get(key) || restored(key, available); states.set(key, state);
    if (states.size > 200) states.delete(states.keys().next().value);
    state.selected = new Set([...state.selected].filter(value => available.has(value)));
    if (!available.has(state.anchor)) state.anchor = null;
    if (!available.has(state.focus)) state.focus = null;
    if (!settings.sortable || !state.sort || !columns.includes(state.sort.column) || !["asc", "desc"].includes(state.sort.direction)) state.sort = null;
    if (!settings.filterable) state.filter = "";
    state.widths = Object.fromEntries(Object.entries(state.widths).filter(([name, value]) => columns.includes(name) && integer(value, 80, 800)));
    const persist = () => save(key, state), selected = settings.copyable;
    let visible = [], rowNodes = new Map(), scrollTimer;
    const toolbar = element("div", undefined, "table-toolbar");
    const viewport = element("div", undefined, "table-viewport"); viewport.tabIndex = 0; viewport.setAttribute("role", "region"); viewport.setAttribute("aria-label", card.title + " table");
    const table = element("table", undefined, "rich-table"); table.dataset.grid = settings.grid; table.classList.toggle("striped", settings.striped);
    table.append(element("caption", card.title, "sr-only"));
    const colgroup = element("colgroup"), colNodes = [];
    if (selected) { const col = element("col"); col.style.width = "44px"; colgroup.append(col); }
    card.columns.forEach((title, index) => { const col = element("col"); col.style.width = (state.widths[columns[index]] || settings.columns.find(item => item.column === index)?.width || 180) + "px"; colNodes.push(col); colgroup.append(col); });
    const tableWidth = () => { table.style.width = colNodes.reduce((sum, col) => sum + parseInt(col.style.width), selected ? 44 : 0) + "px"; };
    tableWidth();
    table.append(colgroup); const head = element("thead"), headerRow = element("tr"), body = element("tbody");
    const status = element("span", "", "table-selection-count"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const fallback = element("textarea"); fallback.className = "table-copy-fallback"; fallback.readOnly = true; fallback.hidden = true; fallback.setAttribute("aria-label", "Selected rows as tab-separated text");
    const selectionSummary = () => { const count = visible.filter(row => state.selected.has(row.key)).length; status.textContent = `${count} of ${visible.length} visible rows selected` + (state.selected.size > count ? ` · ${state.selected.size} total` : ""); return count; };
    const selectedRows = () => visible.filter(row => state.selected.has(row.key)).map(row => row.values);
    const updateSelection = () => {
      for (const [identity, node] of rowNodes) {
        node.setAttribute("aria-selected", String(state.selected.has(identity))); node.tabIndex = identity === state.focus ? 0 : -1;
        const check = node.querySelector("input[type=checkbox]"); if (check) check.checked = state.selected.has(identity);
      }
      const count = selectionSummary(); if (copy) copy.disabled = count === 0;
      if (allCheck) { allCheck.checked = visible.length > 0 && count === visible.length; allCheck.indeterminate = count > 0 && count < visible.length; }
      persist();
    };
    const selectRow = (record, event, checkbox = false) => {
      const extend = event.shiftKey, additive = event.ctrlKey || event.metaKey;
      const anchor = visible.findIndex(row => row.key === state.anchor), index = visible.findIndex(row => row.key === record.key);
      if (extend && anchor >= 0) {
        if (!additive) state.selected.clear();
        for (const row of visible.slice(Math.min(anchor, index), Math.max(anchor, index) + 1)) state.selected.add(row.key);
      } else if (additive || checkbox) {
        if (state.selected.has(record.key)) state.selected.delete(record.key); else state.selected.add(record.key);
        state.anchor = record.key;
      } else { state.selected = new Set([record.key]); state.anchor = record.key; }
      state.focus = record.key; updateSelection();
    };
    let copy = null, allCheck = null;
    if (settings.filterable) {
      const label = element("label", "Filter rows"), input = element("input"); input.type = "search"; input.maxLength = 1000; input.value = state.filter; input.placeholder = "Search this table";
      input.addEventListener("input", () => { state.filter = input.value; state.scrollTop = 0; renderRows(); }); label.append(input); toolbar.append(label);
    }
    if (selected) {
      copy = button("Copy selected rows", async () => {
        const text = tsv(selectedRows()); if (!selectedRows().length) return;
        try { if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable"); await navigator.clipboard.writeText(text); status.textContent = "Selected rows copied."; }
        catch { fallback.value = text; fallback.hidden = false; fallback.focus(); fallback.select(); status.textContent = "Select and copy the rows below."; }
      }); copy.disabled = true; toolbar.append(copy, status);
      const cell = element("th"); cell.scope = "col"; cell.className = "table-check"; allCheck = element("input"); allCheck.type = "checkbox"; allCheck.setAttribute("aria-label", "Select all visible rows");
      allCheck.addEventListener("change", () => { for (const row of visible) if (allCheck.checked) state.selected.add(row.key); else state.selected.delete(row.key); updateSelection(); });
      cell.append(allCheck); headerRow.append(cell);
    }
    function resizeColumn(index, width) { const value = Math.max(80, Math.min(800, Math.round(width))); state.widths[columns[index]] = value; colNodes[index].style.width = value + "px"; tableWidth(); const handle = headerRow.querySelector(`[data-resize-column="${index}"]`); handle?.setAttribute("aria-valuenow", String(value)); persist(); }
    card.columns.forEach((title, index) => {
      const cell = element("th"); cell.scope = "col"; cell.dataset.sourceColumn = index;
      const columnStyle = settings.columns.find(item => item.column === index);
      const style = merged({ border: settings.border }, settings.header, columnStyle);
      applyStyle(cell, style, settings.grid, settings.density);
      if (settings.sortable) {
        const sort = button(title, () => {
          state.sort = { column: columns[index], direction: state.sort?.column === columns[index] && state.sort.direction === "asc" ? "desc" : "asc" };
          state.scrollTop = 0; renderRows();
        }); sort.className = "table-sort"; sort.setAttribute("aria-label", "Sort by " + title); cell.append(sort);
      } else cell.append(element("span", title, "table-column-label"));
      if (style.badge) { const label = cell.firstChild; label.classList.add("table-badge"); label.dataset.state = style.badge; }
      const handle = element("span", undefined, "table-column-resize"); handle.tabIndex = 0; handle.dataset.resizeColumn = index; handle.setAttribute("role", "separator"); handle.setAttribute("aria-orientation", "vertical"); handle.setAttribute("aria-label", "Resize " + title + " column"); handle.setAttribute("aria-valuemin", "80"); handle.setAttribute("aria-valuemax", "800"); handle.setAttribute("aria-valuenow", String(parseInt(colNodes[index].style.width)));
      handle.addEventListener("pointerdown", event => {
        if (event.button !== 0) return; event.preventDefault(); event.stopPropagation(); handle.setPointerCapture(event.pointerId);
        const start = parseInt(colNodes[index].style.width), x = event.clientX;
        const move = next => resizeColumn(index, start + next.clientX - x);
        const end = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", end); handle.removeEventListener("pointercancel", end); };
        handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", end, { once: true }); handle.addEventListener("pointercancel", end, { once: true });
      });
      handle.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.stopPropagation(); event.preventDefault(); resizeColumn(index, event.key === "Home" ? 80 : event.key === "End" ? 800 : parseInt(colNodes[index].style.width) + (event.key === "ArrowLeft" ? -10 : 10));
      }); cell.append(handle); headerRow.append(cell);
    });
    head.append(headerRow); table.append(head, body); viewport.append(table);
    function renderRows() {
      const query = state.filter.toLocaleLowerCase();
      visible = card.rows.map((values, index) => ({ values, index, key: keys[index] })).filter(row => !query || row.values.some(value => display(value).toLocaleLowerCase().includes(query)));
      if (state.sort) {
        const column = columns.indexOf(state.sort.column), direction = state.sort.direction === "asc" ? 1 : -1;
        visible.sort((a, b) => {
          const left = a.values[column], right = b.values[column];
          const order = typeof left === "number" && typeof right === "number" ? left - right : display(left).toLocaleLowerCase().localeCompare(display(right).toLocaleLowerCase());
          return order === 0 ? a.index - b.index : order * direction;
        });
      }
      if (!visible.some(row => row.key === state.focus)) state.focus = visible[0]?.key || null;
      headerRow.querySelectorAll("th[data-source-column]").forEach(cell => {
        const index = Number(cell.dataset.sourceColumn), active = state.sort?.column === columns[index];
        if (settings.sortable) { cell.setAttribute("aria-sort", active ? (state.sort.direction === "asc" ? "ascending" : "descending") : "none"); cell.querySelector("button").textContent = card.columns[index] + (active ? state.sort.direction === "asc" ? " ↑" : " ↓" : ""); }
      });
      body.replaceChildren(); rowNodes = new Map();
      for (const record of visible) {
        const row = element("tr"); row.dataset.sourceRow = record.index; rowNodes.set(record.key, row);
        row.tabIndex = record.key === state.focus ? 0 : -1; row.setAttribute("aria-label", "Row " + (record.index + 1));
        if (selected) {
          const cell = element("td"), check = element("input"); cell.className = "table-check"; check.type = "checkbox"; check.tabIndex = -1; check.setAttribute("aria-label", "Select row " + (record.index + 1));
          check.addEventListener("click", event => { event.stopPropagation(); selectRow(record, event, true); row.focus({ preventScroll: true }); }); cell.append(check); row.append(cell);
        }
        row.addEventListener("click", event => { if (!event.target.closest("button,input,textarea,select,a")) { selectRow(record, event); row.focus({ preventScroll: true }); } });
        row.addEventListener("focus", () => { state.focus = record.key; updateSelection(); });
        record.values.forEach((value, index) => {
          const cell = element("td"); cell.dataset.sourceColumn = index;
          const style = merged({ border: settings.border }, settings.columns.find(item => item.column === index), settings.rows.find(item => item.row === record.index), settings.cells.find(item => item.row === record.index && item.column === index));
          applyStyle(cell, style, settings.grid, settings.density);
          if (style.wrap === false && !("tooltip" in style)) cell.title = display(value);
          const label = element("span", display(value), style.badge ? "table-badge" : "table-value");
          if (style.badge) label.dataset.state = style.badge;
          cell.append(label); row.append(cell);
        }); body.append(row);
      }
      if (!visible.length) { const row = element("tr"), cell = element("td", card.rows.length ? "No matching rows." : "No rows.", "table-empty"); cell.colSpan = card.columns.length + (selected ? 1 : 0); row.append(cell); body.append(row); }
      updateSelection(); viewport.scrollTop = state.scrollTop; viewport.scrollLeft = state.scrollLeft;
    }
    viewport.addEventListener("keydown", event => {
      if (textEditor(event.target)) return;
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && event.key.toLowerCase() === "a") { event.preventDefault(); state.selected = new Set(visible.map(row => row.key)); updateSelection(); return; }
      if (!visible.length) return;
      const index = Math.max(0, visible.findIndex(row => row.key === state.focus));
      if (event.key === " ") { event.preventDefault(); selectRow(visible[index], event, true); return; }
      if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) || event.target.closest("[role=separator]")) return;
      event.preventDefault(); const next = event.key === "Home" ? 0 : event.key === "End" ? visible.length - 1 : Math.max(0, Math.min(visible.length - 1, index + (event.key === "ArrowUp" ? -1 : 1)));
      if (!modifier || event.shiftKey) selectRow(visible[next], event);
      state.focus = visible[next].key; updateSelection(); rowNodes.get(state.focus)?.focus({ preventScroll: true }); rowNodes.get(state.focus)?.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    viewport.addEventListener("copy", event => { if (!selected || textEditor(event.target) || !selectedRows().length || !event.clipboardData) return; event.clipboardData.setData("text/plain", tsv(selectedRows())); event.preventDefault(); status.textContent = "Selected rows copied."; });
    viewport.addEventListener("scroll", () => { state.scrollTop = viewport.scrollTop; state.scrollLeft = viewport.scrollLeft; clearTimeout(scrollTimer); scrollTimer = setTimeout(persist, 120); });
    if (toolbar.childNodes.length) root.append(toolbar); root.append(viewport, fallback);
    instances.set(root, { state, persist, viewport, capture() { clearTimeout(scrollTimer); state.scrollTop = viewport.scrollTop; state.scrollLeft = viewport.scrollLeft; persist(); } });
    renderRows(); requestAnimationFrame(() => { if (root.isConnected) { viewport.scrollTop = state.scrollTop; viewport.scrollLeft = state.scrollLeft; } });
    return root;
  }
  function capture(container) { container?.querySelectorAll(".prism-table").forEach(node => instances.get(node)?.capture()); }
  window.PrismTables = Object.freeze({ render, capture, options, cellStyle, rowKeys, tsv });
})();
