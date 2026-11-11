/* Virtual-list math for long lists (stage 2 draws the findings with it): given the scroll position, which rows are
   on screen. Pure. Two forms: a fixed row height, and measured row heights (prefix sums + binary search). */

/** Fixed height rows. Returns {start, end (exclusive), padTop, padBottom, total} in rows and pixels. */
export function fixedWindow(o) {
  const count = Math.max(0, o.count | 0);
  const h = Math.max(1, o.itemH);
  const over = o.overscan === undefined ? 4 : o.overscan;
  const total = count * h;
  const top = Math.max(0, Math.min(o.scrollTop || 0, Math.max(0, total - 1)));
  const first = Math.floor(top / h);
  const visible = Math.ceil((o.viewportH || 0) / h) + 1;
  const start = Math.max(0, first - over);
  const end = Math.min(count, first + visible + over);
  return { start: start, end: end, padTop: start * h, padBottom: Math.max(0, (count - end) * h), total: total };
}

/** Prefix sums of row heights: offsets[i] is the top of row i, offsets[n] the total height. */
export function buildOffsets(heights) {
  const offsets = new Array(heights.length + 1);
  offsets[0] = 0;
  for (let i = 0; i < heights.length; i++) offsets[i + 1] = offsets[i] + Math.max(0, heights[i]);
  return offsets;
}
/** The row that contains pixel y (binary search over the offsets). */
export function rowAt(offsets, y) {
  const n = offsets.length - 1;
  if (n <= 0) return 0;
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= y) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
/** Measured heights. Same result shape as fixedWindow. */
export function offsetWindow(offsets, scrollTop, viewportH, overscan) {
  const n = offsets.length - 1;
  const over = overscan === undefined ? 4 : overscan;
  const total = offsets[n] || 0;
  if (n <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0, total: 0 };
  const top = Math.max(0, Math.min(scrollTop || 0, Math.max(0, total - 1)));
  const first = rowAt(offsets, top);
  const last = rowAt(offsets, Math.min(total - 1, top + Math.max(0, (viewportH || 0) - 1)));
  const start = Math.max(0, first - over);
  const end = Math.min(n, last + 1 + over);
  return { start: start, end: end, padTop: offsets[start], padBottom: Math.max(0, total - offsets[end]), total: total };
}
/** The scrollTop that brings row i to the top of the viewport. */
export function scrollTopFor(offsets, i) {
  const n = offsets.length - 1;
  return offsets[Math.max(0, Math.min(n, i))] || 0;
}
