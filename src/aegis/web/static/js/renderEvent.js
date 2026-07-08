// Per-kind event → HTML-string renderer. Browser mirror of
// aegis.render_html.render_event_html (+ aegis.render_shared helpers),
// reading the compact `event` dict. Returns "" for kinds with no visible
// block. Wrapped by app.js's nodeFromHtml.
import { renderMarkdown } from "./markdown.js";

const KIND_ICON = {
  read: "📖", edit: "✏️", execute: "⌬", search: "🔎", think: "✻",
  fetch: "🌐", move: "➡️", delete: "🗑", switch_mode: "🔄", other: "⏺",
};
const PLAN_GLYPH = { completed: "●", in_progress: "◐", pending: "○" };

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function pathhint(ev) {
  const locs = ev.locations || [];
  if (locs.length) {
    const [path, line] = locs[0];
    const tail = path ? path.split("/").pop() : "";
    return line != null ? `${tail}:${line}` : tail;
  }
  return ev.summary || "";
}

function diffWindow(oldText, newText, maxLines = 6) {
  const o = oldText ? oldText.split("\n") : [];
  const n = newText ? newText.split("\n") : [];
  let head = 0;
  while (head < o.length && head < n.length && o[head] === n[head]) head++;
  let tail = 0;
  while (tail < o.length - head && tail < n.length - head
         && o[o.length - 1 - tail] === n[n.length - 1 - tail]) tail++;
  const removed = o.slice(head, o.length - tail);
  const added = n.slice(head, n.length - tail);
  const shownRemoved = [], shownAdded = [];
  let budget = maxLines;
  for (const l of removed) { if (budget <= 0) break; shownRemoved.push(l); budget--; }
  for (const l of added) { if (budget <= 0) break; shownAdded.push(l); budget--; }
  const elided = (removed.length + added.length)
    - (shownRemoved.length + shownAdded.length);
  return { shownRemoved, shownAdded, elided };
}

function fmtCost(usd) {
  const cents = usd * 100;
  if (cents < 1) return `${Math.round(cents * 10) / 10}¢`;
  if (usd < 1) return `${Math.floor(cents)}¢`;
  return `$${usd.toFixed(2)}`;
}

function resultParts(ev) {
  const secs = (ev.duration_ms || 0) / 1000;
  const parts = [`done in ${secs.toFixed(1)}s`];
  if (ev.cost_usd != null && ev.cost_usd > 0) parts.push(fmtCost(ev.cost_usd));
  if (ev.stop_reason && ev.stop_reason !== "end_turn") parts.push(ev.stop_reason);
  return parts;
}

export function expandControl(rec, label) {
  return `<span class="expand" data-handle="${esc(rec.handle)}" `
    + `data-seq="${rec.seq}">${esc(label)}</span>`;
}

function diffHtml(diff) {
  const { shownRemoved, shownAdded, elided } = diffWindow(diff.old, diff.new);
  const rows = [`<div class="diff-head">┌ ${esc(diff.path)}</div>`];
  for (const l of shownRemoved) rows.push(`<div class="diff-row removed">- ${esc(l)}</div>`);
  for (const l of shownAdded) rows.push(`<div class="diff-row added">+ ${esc(l)}</div>`);
  if (elided > 0) {
    const s = elided !== 1 ? "s" : "";
    rows.push(`<div class="diff-more">… ${elided} more line${s}</div>`);
  }
  return `<div class="tool-result diff">${rows.join("")}</div>`;
}

function toolResultHtml(ev, { handle, seq, truncated }) {
  if (ev.diff && !ev.is_error) return diffHtml(ev.diff);
  const raw = ev.text || "";
  let first = raw.trim() ? raw.split("\n")[0] : "";
  if (first.length > 100) first = first.slice(0, 100) + "…";
  const cls = ev.is_error ? "error" : "ok";
  const ctl = truncated ? " " + expandControl({ handle, seq }, "⋯") : "";
  return `<div class="tool-result ${cls}">└ `
    + `<span class="status">${cls}</span> ${esc(first)}${ctl}</div>`;
}

function planHtml(ev) {
  const entries = ev.entries || [];
  if (!entries.length) return '<div class="agent-plan muted">📋 (no plan)</div>';
  const done = entries.filter((e) => e.status === "completed").length;
  const rows = [`<div class="plan-head">📋 Plan — ${done}/${entries.length} done</div>`];
  for (const e of entries) {
    const glyph = PLAN_GLYPH[e.status] || "○";
    const prio = (e.priority === "high" || e.priority === "low") ? ` ${e.priority}` : "";
    rows.push(`<div class="plan-row ${e.status}${prio}">`
      + `<span class="glyph">${glyph}</span> ${esc(e.content)}</div>`);
  }
  return `<div class="agent-plan">${rows.join("")}</div>`;
}

export function renderEvent(rec) {
  const ev = rec.event || {};
  const t = rec.event_type;

  if (t === "AssistantText") {
    const text = (ev.text || "").trim();
    if (!text) return "";
    return `<div class="assistant-text">${renderMarkdown(ev.text)}</div>`;
  }
  if (t === "AssistantThinking") {
    const body = (ev.text || "").trim();
    if (!body) {
      const ctl = rec.truncated ? " " + expandControl(rec, "expand") : "";
      return `<div class="thinking muted">✻ Thinking…${ctl}</div>`;
    }
    return `<div class="thinking muted"><em>✻ ${esc(body)}</em></div>`;
  }
  if (t === "ToolUse") {
    const icon = KIND_ICON[ev.kind || ""] || "⏺";
    const hint = pathhint(ev);
    const arg = (hint && hint !== ev.name)
      ? `<span class="tool-hint">(${esc(hint)})</span>` : "";
    const ctl = rec.truncated ? " " + expandControl(rec, "⋯") : "";
    const useHtml = `<div class="tool-use"><span class="icon">${icon}</span> `
      + `<span class="tool-name">${esc(ev.name)}</span>${arg}${ctl}</div>`;
    // A Task with routed children renders as a collapsible subagent box:
    // header (the Task call) + body (the subagent's events).
    if (rec.children && rec.children.length) {
      const n = rec.children.length;
      const header = `<div class="subagent-header">🤖 `
        + `<span class="tool-name">${esc(ev.summary || ev.name)}</span> `
        + `<span class="sa-count">· ${n} events</span></div>`;
      const body = rec.children.map((c) => renderEvent(c)).join("");
      return `<div class="subagent" data-collapsed>${header}`
        + `<div class="subagent-body">${body}</div></div>`;
    }
    // A folded result (paired by tool_call_id in coalesceInto) renders
    // directly under its call so parallel results don't pile up.
    if (rec.result) {
      const resHtml = toolResultHtml(rec.result, {
        handle: rec.handle, seq: rec.resultSeq,
        truncated: rec.resultTruncated,
      });
      return `<div class="tool-call">${useHtml}${resHtml}</div>`;
    }
    return useHtml;
  }
  if (t === "ToolResult") {
    // Standalone result (no matching use found) — fold path is preferred.
    return toolResultHtml(ev, {
      handle: rec.handle, seq: rec.seq, truncated: rec.truncated,
    });
  }
  if (t === "AgentPlan") return planHtml(ev);
  if (t === "Result") {
    return `<div class="result-sep">── ${esc(resultParts(ev).join(" · "))} ──</div>`;
  }
  return "";
}
