/* The live connection to one run (Spec Bench; a partial run also ends the stream, resume opens a new one): open the stream with Last-Event-ID, hand events to the screen, reconnect with
   back-off when it drops. Everything it needs from the outside is injected (open, sleep), so it runs under Node.

   open(afterSeq, signal) -> Promise<Response>   (the caller's fetch with the auth headers)
   onEvents([event])      events in arrival order (the screen de-duplicates by seq and batches its drawing)
   onState(name, info)    "connecting" | "live" | "retry" {seconds} | "ended" | "fatal" {error}
   The stream ends by itself after a terminal event (the server closes it): then "ended" is reported once. */
import { readFrames, eventOfFrame } from "./sse.js";
import { reconnectDelay } from "./format.js";

const TERMINAL = { "run.finished": true, "run.failed": true, "run.partial": true };

export function createRunFeed(opts) {
  let after = opts.afterSeq || 0;
  let stopped = false;
  let gen = 0; // each start() is a new generation: an old loop that wakes up late sees it is stale and quits
  let controller = null;
  const sleep = opts.sleep || ((s) => new Promise((r) => setTimeout(r, s * 1000)));
  const rand = opts.random || Math.random;

  async function loop(my) {
    const alive = () => !stopped && my === gen;
    let attempt = 0;
    while (alive()) {
      let terminal = false;
      controller = new AbortController();
      try {
        opts.onState("connecting", {});
        const res = await opts.open(after, controller.signal);
        opts.onState("live", {});
        await readFrames(res.body, (frame) => {
          const ev = eventOfFrame(frame);
          if (!ev || !alive()) return; // retry frames and keepalive comments
          attempt = 0;
          if (ev.seq > after) after = ev.seq;
          if (TERMINAL[ev.type]) terminal = true;
          opts.onEvents([ev]);
        });
        if (!alive()) return;
        if (terminal) {
          opts.onState("ended", {});
          return;
        }
        // the server closed an active run's stream: come back after a short pause
        const seconds = reconnectDelay(0, rand());
        opts.onState("retry", { seconds: seconds });
        await sleep(seconds);
      } catch (err) {
        if (!alive()) return;
        const status = err && typeof err.status === "number" ? err.status : 0;
        if (status === 404 || status === 403) {
          opts.onState("fatal", { error: err });
          return;
        }
        const seconds = reconnectDelay(attempt, rand());
        attempt += 1;
        opts.onState("retry", { seconds: seconds, error: err });
        await sleep(seconds);
      }
    }
  }

  return {
    start() {
      stopped = false;
      gen += 1;
      return loop(gen);
    },
    /** Stop reading (the connection is aborted; no more callbacks). */
    stop() {
      stopped = true;
      if (controller) controller.abort();
    },
    /** After a snapshot refetch: continue from this sequence number. */
    reset(seq) {
      after = seq || 0;
    },
    get lastSeq() {
      return after;
    },
  };
}
