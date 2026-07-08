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
