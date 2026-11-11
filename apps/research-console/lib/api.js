/* The one place the page talks to the app. Same origin only. The dev token comes from GET /api/session and lives in
   memory (never in storage); it is sent as "Authorization: Bearer <token>" with "X-Tenant-Id: demo". Failures become
   an ApiError with a plain-language `problem` (see safe.problemText). */
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
  let token = null;
  let tenant = "demo";

  async function session() {
    let res;
    try {
      res = await fetchFn(base + "/api/session", { headers: { Accept: "application/json" }, cache: "no-store" });
    } catch (e) {
      throw new ApiError(0, null);
    }
    const body = await readJson(res);
    if (!res.ok || !body || !body.token) throw new ApiError(res.status, body);
    token = body.token;
    tenant = body.tenant_id || "demo";
    return body;
  }
  async function readJson(res) {
    const type = res.headers && res.headers.get ? res.headers.get("content-type") || "" : "application/json";
    if (type.indexOf("json") === -1) return null;
    try {
      return await res.json();
    } catch (e) {
      return null;
    }
  }
  function headers(extra) {
    return Object.assign({ Authorization: "Bearer " + token, "X-Tenant-Id": tenant, Accept: "application/json" }, extra || {});
  }
  async function send(method, path, o) {
    const opt = o || {};
    if (!token) await session();
    for (let attempt = 0; attempt < 2; attempt++) {
      const init = { method: method, headers: headers(opt.headers), signal: opt.signal, cache: "no-store" };
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
      if (res.status === 401 && attempt === 0) {
        await session(); // the app was restarted with a new token: ask again once
        continue;
      }
      if (!res.ok) throw new ApiError(res.status, await readJson(res));
      return res;
    }
    throw new ApiError(401, null);
  }
  return {
    session: session,
    /** GET a JSON path. */
    async get(path, o) {
      return readJson(await send("GET", path, o));
    },
    async put(path, json, o) {
      return readJson(await send("PUT", path, Object.assign({ json: json === undefined ? {} : json }, o || {})));
    },
    /** GET a text body (the Markdown report). */
    async text(path, o) {
      return (await send("GET", path, o)).text();
    },
    async post(path, json, o) {
      return readJson(await send("POST", path, Object.assign({ json: json === undefined ? {} : json }, o || {})));
    },
    /** POST the raw JSON text of a file (the upload). */
    async postRaw(path, raw) {
      return readJson(await send("POST", path, { raw: raw }));
    },
    async blob(path) {
      return (await send("GET", path)).blob();
    },
    /** Open the event stream of a run after a sequence number: the raw Response (its body is read by the caller). */
    stream(runId, afterSeq, signal) {
      return send("GET", "/v1/research/runs/" + encodeURIComponent(runId) + "/events", {
        signal: signal,
        headers: { Accept: "text/event-stream", "Last-Event-ID": String(afterSeq || 0) },
      });
    },
    /** A GET with no sign-in that may fail quietly (the test-fake probe). */
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
