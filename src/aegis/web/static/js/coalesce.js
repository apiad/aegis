// Pure streaming-chunk coalescer — the browser mirror of
// aegis.render.coalesce_chunks. Consecutive AssistantText (or
// AssistantThinking) frames sharing the same message_id merge into one
// block with concatenated text; anything else starts a new block.
//
// A block record is { seq, event_type, message_id, text, event, truncated,
// handle, html }. `event` is the compact encoded dict; `truncated` flags a
// clipped body that expands on tap; `html` is legacy (dropped in W2 Task 4).

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
      if (frame.html != null) last.html = frame.html;
      return { action: "update", index: history.length - 1 };
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
    html: frame.html ?? null,
  });
  return { action: "append", index: history.length - 1 };
}
