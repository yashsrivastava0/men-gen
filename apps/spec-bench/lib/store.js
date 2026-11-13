/* Remembered choices (last run, mock profile, theme) in localStorage. It may be missing, full or blocked (private
   windows, blocked site data): every read and write is wrapped, and the page works without it. */

export function createStore(getStorage, prefix) {
  const p = prefix || "specbench.";
  function storage() {
    try {
      return getStorage();
    } catch (e) {
      return null;
    }
  }
  return {
    get(key, fallback) {
      try {
        const s = storage();
        if (!s) return fallback;
        const raw = s.getItem(p + key);
        return raw === null || raw === undefined ? fallback : JSON.parse(raw);
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try {
        const s = storage();
        if (!s) return false;
        s.setItem(p + key, JSON.stringify(value));
        return true;
      } catch (e) {
        return false;
      }
    },
    remove(key) {
      try {
        const s = storage();
        if (s) s.removeItem(p + key);
      } catch (e) {
        /* nothing to do */
      }
    },
  };
}
