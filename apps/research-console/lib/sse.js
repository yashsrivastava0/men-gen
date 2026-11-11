/* Server-sent events over fetch (EventSource cannot send the Authorization header). Pure except readFrames, which
   takes a stream reader. Frames: "id: N", "event: type", "data: {json}", blank line; ": keepalive" comments are
   ignored; the first frame is "retry: 3000". */

/** Split a buffer into complete frames; the unfinished tail comes back as `rest`.
    A frame is {id, event, data, retry}; `data` is null for frames that carry none. */
export function parseSseFrames(buffer) {
  const text = String(buffer || "").replace(/\r\n/g, "\n");
  const parts = text.split("\n\n");
  const rest = parts.pop();
  const frames = [];
  for (const block of parts) {
    const frame = { id: null, event: null, data: null, retry: null };
    const data = [];
    for (const line of block.split("\n")) {
      if (!line || line.charAt(0) === ":") continue;
      const at = line.indexOf(":");
      const field = at === -1 ? line : line.slice(0, at);
      const value = at === -1 ? "" : line.slice(at + 1).replace(/^ /, "");
      if (field === "data") data.push(value);
      else if (field === "id") frame.id = value;
      else if (field === "event") frame.event = value;
      else if (field === "retry") frame.retry = parseInt(value, 10) || null;
    }
    if (data.length) frame.data = data.join("\n");
    if (frame.data !== null || frame.retry !== null || frame.id !== null) frames.push(frame);
  }
  return { frames: frames, rest: rest };
}

/** "ok" (the next event), "dup" (already seen) or "gap" (an event is missing: refetch the snapshot). */
export function checkSeq(lastSeq, seq) {
  if (typeof seq !== "number") return "dup";
  const last = lastSeq || 0;
  if (seq <= last) return "dup";
  return seq === last + 1 ? "ok" : "gap";
}

/** One frame -> an event object, or null (keepalive, retry, broken JSON). */
export function eventOfFrame(frame) {
  if (!frame || frame.data === null) return null;
  try {
    const ev = JSON.parse(frame.data);
    return ev && typeof ev === "object" && typeof ev.seq === "number" && typeof ev.type === "string" ? ev : null;
  } catch (e) {
    return null;
  }
}

/** Read a fetch body (a ReadableStream) to its end, calling onFrame for every complete frame. */
export async function readFrames(body, onFrame) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.rest;
      for (const f of parsed.frames) onFrame(f);
    }
  } finally {
    try {
      reader.releaseLock();
    } catch (e) {
      /* already released */
    }
  }
}
