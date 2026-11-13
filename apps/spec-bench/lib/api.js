/* The one place the page talks to the app. Same origin only, no sign-in, no cookies. Failures become an ApiError with
   a plain-language `problem` (see safe.problemText). Errors from the app look like {error:{code, message, fix?}}. */
import { problemText } from "./safe.js";

export class ApiError extends Error {
  constructor(status, body) {
    const problem = problemText(status, body);
    super(problem.text);
    this.status = status;
    this.body = body;
    this.problem = problem;
  }
}

export function createApi(opts) {
  const fetchFn = (opts && opts.fetch) || ((...a) => fetch(...a));
  const base = (opts && opts.base) || "";

  async function readJson(res) {
    const type = res.headers && res.headers.get ? res.headers.get("content-type") || "" : "application/json";
    if (type.indexOf("json") === -1) return null;
    try {
      return await res.json();
    } catch (e) {
      return null;
    }
  }
  async function send(method, path, o) {
    const opt = o || {};
    const init = { method: method, headers: Object.assign({ Accept: "application/json" }, opt.headers || {}), signal: opt.signal, cache: "no-store" };
    if (opt.json !== undefined) {
      init.body = JSON.stringify(opt.json);
      init.headers["Content-Type"] = "application/json";
    } else if (opt.raw !== undefined) {
      init.body = opt.raw;
      init.headers["Content-Type"] = "application/json";
    }
    let res;
    try {
      res = await fetchFn(base + path, init);
    } catch (e) {
      if (opt.signal && opt.signal.aborted) throw e;
      throw new ApiError(0, null);
    }
    if (!res.ok) throw new ApiError(res.status, await readJson(res));
    return res;
  }
  const q = (params) => {
    const parts = [];
    for (const k of Object.keys(params || {})) {
      const v = params[k];
      if (v === undefined || v === null || v === "") continue;
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(String(v)));
    }
    return parts.length ? "?" + parts.join("&") : "";
  };
  const run = (id) => "/api/runs/" + encodeURIComponent(id);
  return {
    q: q,
    async get(path, o) {
      return readJson(await send("GET", path, o));
    },
    async post(path, json, o) {
      return readJson(await send("POST", path, Object.assign({ json: json === undefined ? {} : json }, o || {})));
    },
    /** POST the raw JSON text of a file (the upload). */
    async postRaw(path, raw) {
      return readJson(await send("POST", path, { raw: raw }));
    },
    /** GET a text body (one pack file). */
    async text(path, o) {
      return (await send("GET", path, o)).text();
    },
    runPath: run,
    /** Open the event stream of a run after a sequence number: the raw Response (its body is read by the caller). */
    stream(runId, afterSeq, signal) {
      return send("GET", run(runId) + "/events" + q({ after: afterSeq || 0 }), {
        signal: signal,
        headers: { Accept: "text/event-stream", "Last-Event-ID": String(afterSeq || 0) },
      });
    },
    /** A GET that may fail quietly (the health probe). */
    async probe(path) {
      try {
        const res = await fetchFn(base + path, { cache: "no-store" });
        return res.ok ? await readJson(res) : null;
      } catch (e) {
        return null;
      }
    },
  };
}
