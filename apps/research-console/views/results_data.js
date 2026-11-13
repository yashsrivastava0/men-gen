/* Shared loading for the results tabs. The server has no "items by id" call, so the tabs that point at items by id
   (Confirm, Norms, Gaps and conflicts) load the whole list once, 200 at a time, and keep it for the visit. */
export async function ensureAllItems(state) {
  if (state.itemsAll) return state.itemsAll;
  if (!state.itemsPromise) {
    const base = "/v1/research/runs/" + encodeURIComponent(state.runId) + "/review?limit=200&offset=";
    state.itemsPromise = (async () => {
      const first = await state.ctx.api.get(base + 0);
      const pages = [];
      for (let off = 200; off < first.total; off += 200) pages.push(state.ctx.api.get(base + off));
      const rest = await Promise.all(pages);
      const map = new Map();
      for (const r of [first].concat(rest)) for (const it of r.items) map.set(it.id, it);
      state.itemsAll = map;
      return map;
    })();
    state.itemsPromise.catch(() => {
      state.itemsPromise = null;
    });
  }
  return state.itemsPromise;
}
