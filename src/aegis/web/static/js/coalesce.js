// Pure streaming-chunk coalescer — the browser mirror of
// aegis.render.coalesce_chunks. Consecutive AssistantText (or
// AssistantThinking) frames sharing the same message_id merge into one
// block with concatenated text; anything else starts a new block.
//
// A block record is { seq, event_type, message_id, text, event, truncated,
// handle }. `event` is the compact encoded dict; `truncated` flags a clipped
// body that expands on tap (fetched via get_event and rendered client-side).

const STREAMING = new Set(["AssistantText", "AssistantThinking"]);

export function coalesceInto(history, frame) {
  const eventType = frame.event_type;
  const ev = frame.event || {};
  const messageId = ev.message_id ?? null;
  const text = ev.text ?? "";

  if (STREAMING.has(eventType) && messageId !== null && history.length) {
    const last = history[history.length - 1];
    if (last.event_type === eventType && last.message_id === messageId) {
      last.text += text;
      last.seq = frame.seq;
      return { action: "update", index: history.length - 1 };
    }
  }

  // Route a subagent's event into its Task record's children (grouped view).
  // Task children carry parent_tool_use_id == the dispatching Task's id.
  const parentId = ev.parent_tool_use_id ?? null;
  if (parentId !== null) {
    for (let i = history.length - 1; i >= 0; i--) {
      const b = history[i];
      const bn = (b.event || {}).name;
      if (b.event_type === "ToolUse"
          && (bn === "Task" || bn === "Agent")
          && (b.event || {}).tool_call_id === parentId) {
        const kids = (b.children ||= []);
        // In-box tool pairing: fold a child result into its child use.
        if (eventType === "ToolResult" && ev.tool_call_id != null
            && kids.length) {
          const prev = kids[kids.length - 1];
          if (prev.event_type === "ToolUse"
              && (prev.event || {}).tool_call_id === ev.tool_call_id) {
            prev.result = ev;
            prev.resultSeq = frame.seq;
            prev.resultTruncated = frame.truncated ?? false;
            return { action: "update", index: i };
          }
        }
        kids.push({
          seq: frame.seq, event_type: eventType, event: ev,
          truncated: frame.truncated ?? false, handle: frame.handle,
        });
        return { action: "update", index: i };
      }
    }
  }

  // Fold a ToolResult into its matching ToolUse block by tool_call_id, so
  // parallel results land under their own call instead of piling up as
  // trailing blocks. Search backward for the still-unpaired use.
  if (eventType === "ToolResult" && ev.tool_call_id != null) {
    for (let i = history.length - 1; i >= 0; i--) {
      const b = history[i];
      if (b.event_type === "ToolUse"
          && (b.event || {}).tool_call_id === ev.tool_call_id) {
        b.result = ev;
        b.resultSeq = frame.seq;
        b.resultTruncated = frame.truncated ?? false;
        return { action: "update", index: i };
      }
    }
  }

  history.push({
    seq: frame.seq,
    event_type: eventType,
    message_id: messageId,
    text,
    event: ev,
    truncated: frame.truncated ?? false,
    handle: frame.handle,
  });
  return { action: "append", index: history.length - 1 };
}
