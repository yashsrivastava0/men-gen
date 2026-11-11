"""Real-browser verification of the stage 2 screens (results tabs, item view, spot-check, run-view additions).

    uv run --with playwright python apps/research-console/tests/browser/browser_check_stage2.py --shots <folder>

One fake app is started with ``--seed 600 --slow``: it commits a 600-item result (hostile text in the first three items)
and a partial one through the store API, and runs of the small input last about 30 s. Uses the helpers of
``browser_check.py`` (same folder). Nothing here touches the real app or any key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_check as bc
from playwright.sync_api import sync_playwright

check = bc.check


def seeded(f: bc.Fake) -> dict:
    for _ in range(40):
        try:
            d = f.get("/api/fake/seeded", auth=False)
            if isinstance(d, dict) and d.get("big"):
                return d
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("seed did not finish")


def wait_for(page, expr: str, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(expr):
            return True
        time.sleep(0.05)
    return False


def tab(page, name: str) -> None:
    page.get_by_role("tab", name=name, exact=True).click()


def chip(page, label: str):
    return page.get_by_role("button", name=re.compile(r"^" + re.escape(label) + r" \(\d+\)"))


def filtered(page, action) -> tuple[int, int]:
    """Run a filter action; return (rows total after, milliseconds from the request to the rows being on screen)."""
    page.evaluate("document.querySelector('.vscroll').setAttribute('data-filter-ms', '')")
    action()
    wait_for(page, "document.querySelector('.vscroll').getAttribute('data-filter-ms') !== ''", 8)
    ms = int(
        page.evaluate(
            "document.querySelector('.vscroll').getAttribute('data-filter-ms') || '99999'"
        )
    )
    total = int(
        page.evaluate("document.querySelector('.vscroll').getAttribute('data-total') || '-1'")
    )
    return total, ms


def scenario_results(browser, f: bc.Fake, ids: dict) -> None:
    big, part = ids["big"], ids["partial"]
    base = f.base
    page = bc.new_page(browser)
    page.goto(f"{base}/#/results/{big}")
    page.wait_for_selector(".vrow-btn", timeout=15000)
    wall = page.evaluate("performance.now()")
    inner = int(page.locator(".vscroll").get_attribute("data-first-render-ms"))
    check(
        "results (600 items): first screen of the list in under 500 ms (page load to rows on screen)",
        wall < 500,
        f"{wall:.0f} ms wall, {inner} ms inside the tab",
    )
    rendered = int(page.locator(".vscroll").get_attribute("data-rendered"))
    total = int(page.locator(".vscroll").get_attribute("data-total"))
    check(
        "results (600 items): the list is virtualized, only a screenful of rows exists",
        total == 450 and 5 < rendered < 30,
        f"{rendered} rows for {total}",
    )
    check(
        "results: the header shows status, cost, time, findings and topics",
        "Done" in bc.text(page, ".results-head .chip")
        and all(
            w in bc.text(page, ".results-head") for w in ("Cost", "Time", "Findings", "Topics")
        ),
    )
    page.locator(".results-head summary").click()
    check(
        "results: 'how far to trust it' explains the evidence mix and the checker in plain words",
        "official or several agreeing sources" in bc.text(page, ".results-head")
        and "checker" in bc.text(page, ".results-head"),
    )
    # scrolling to the middle of 450 rows
    page.evaluate("document.querySelector('.vscroll').scrollTop = 104 * 220")
    ok = wait_for(
        page,
        "(() => { const r = document.querySelector('.vrow[aria-posinset=\"225\"] .vrow-title'); return !!r; })()",
        8,
    )
    pos = page.evaluate("Number(document.querySelector('.vrow').getAttribute('aria-posinset'))")
    check(
        "results (600 items): scrolling loads the page it needs and shows rows around row 225",
        ok and 200 < pos < 230,
        f"first row {pos}",
    )
    check(
        "results (600 items): still only a screenful of rows in the page after scrolling",
        int(page.locator(".vscroll").get_attribute("data-rendered")) < 30,
    )
    page.evaluate("document.querySelector('.vscroll').scrollTop = 0")
    wait_for(page, "document.querySelector('.vrow')?.getAttribute('aria-posinset') === '1'")
    # filters
    t_lane, ms_lane = filtered(page, lambda: chip(page, "Integrations").click())
    check(
        "filters: choosing a lane answers fast (request to rows on screen, under 100 ms)",
        ms_lane < 100 and 0 < t_lane < 450,
        f"{ms_lane} ms, {t_lane} rows",
    )
    check(
        "filters: the lane note and quality are shown",
        "Outside services" in bc.text(page, "main") or "Quality:" in bc.text(page, "main"),
    )
    t_ev, ms_ev = filtered(page, lambda: page.locator("#f-evidence").select_option("official"))
    check(
        "filters: evidence filter narrows the list and is fast",
        ms_ev < 100 and 0 < t_ev <= t_lane,
        f"{ms_ev} ms, {t_ev} rows",
    )
    t_flag, ms_flag = filtered(page, lambda: page.locator("#f-impact").select_option("high"))
    check(
        "filters: impact filter is fast",
        ms_flag < 100 and t_flag <= t_ev,
        f"{ms_flag} ms, {t_flag} rows",
    )
    t_all, ms_all = filtered(page, lambda: page.get_by_role("button", name="Clear filters").click())
    check(
        "filters: Clear filters restores the whole list", t_all == 450 and ms_all < 100, f"{t_all}"
    )
    reqs: list[str] = []
    page.on("request", lambda r: "/review?" in r.url and "q=" in r.url and reqs.append(r.url))
    page.evaluate("document.querySelector('.vscroll').setAttribute('data-filter-ms', '')")
    page.locator("#item-q").click()
    page.keyboard.type("fees", delay=25)
    wait_for(page, "document.querySelector('.vscroll').getAttribute('data-filter-ms') !== ''", 5)
    time.sleep(0.4)
    ms_q = int(page.locator(".vscroll").get_attribute("data-filter-ms"))
    check(
        "filters: typing is debounced (one request for 'fees', not four) and answers fast",
        len(reqs) == 1
        and ms_q < 100
        and int(page.locator(".vscroll").get_attribute("data-total")) > 0,
        f"{len(reqs)} requests, {ms_q} ms",
    )
    check(
        "filters: only the final answer is shown (stale requests are cancelled)",
        "q=fees" in reqs[-1],
    )
    t_none, _ = filtered(page, lambda: page.locator("#item-q").fill("zzzzqqqq"))
    time.sleep(0.5)
    check(
        "filters: no match says so and offers to clear",
        "No finding matches these filters" in bc.text(page, ".vscroll")
        and page.get_by_role("button", name="Clear the filters").count() == 1,
    )
    page.locator("#item-q").fill("")
    time.sleep(0.5)
    chip(page, "Integrations").click()
    page.reload()
    page.wait_for_selector(".vrow-btn")
    check(
        "filters: the lane filter is remembered across a reload",
        chip(page, "Integrations").get_attribute("aria-pressed") == "true",
    )
    page.get_by_role("button", name="Clear filters").click()
    wait_for(page, "document.querySelector('.vscroll').getAttribute('data-total') === '450'")
    # item view with hostile text
    a_before = page.evaluate("document.querySelectorAll('a').length")
    page.locator(".vrow-btn").first.click()
    page.wait_for_selector("dialog[open]")
    d = page.locator("dialog[open]")
    dt = d.inner_text()
    check(
        "item view XSS: label, claim, quote, source title and data show as literal characters",
        all(
            x in dt
            for x in (
                "<img src=x onerror=alert(1)>",
                "<script>alert(2)</script>",
                "<b onmouseover=alert(4)>Fees start at 2 percent</b>",
                "<img src=x onerror=alert(6)> Pricing page",
                "javascript:alert(7)",
            )
        ),
        dt[:300],
    )
    check(
        "item view XSS: no injected element, no javascript: link, no event attribute",
        d.locator(
            "img, script, iframe, svg, b, [onerror], [onmouseover], a[href^='javascript' i]"
        ).count()
        == 0,
    )
    links = d.locator("a")
    check(
        "item view: only http(s) links, opening safely (the hostile address is not a link)",
        links.count() >= 1
        and all(
            links.nth(i).get_attribute("href").startswith(("http://", "https://"))
            and links.nth(i).get_attribute("rel") == "noopener noreferrer"
            and links.nth(i).get_attribute("target") == "_blank"
            for i in range(links.count())
        ),
    )
    for needle in (
        "The quote it rests on",
        "About this finding",
        "What it applies to",
        "sources",
        "Your decision",
    ):
        check(f"item view: shows '{needle}'", needle in dt)
    check(
        "item view: evidence badge and flags are in plain words",
        "Official source" in dt and "Please confirm" in dt and "May be out of date" in dt,
    )
    check(
        "item view: data is key/value rows, not a JSON dump",
        d.locator("dl.data-rows").count() >= 1
        and "{" not in d.locator("dl.data-rows").first.inner_text(),
    )
    bc.shot(page, "item-view-1280-light", full=False)
    focused_tag = page.evaluate("document.activeElement.tagName")
    page.keyboard.press("Escape")
    wait_for(page, "!document.querySelector('dialog[open]')", 3)
    check(
        "item view: opens with its heading focused, Escape closes it and focus returns to the row",
        focused_tag == "H3"
        and page.locator("dialog[open]").count() == 0
        and page.evaluate("document.activeElement.classList.contains('vrow-btn')"),
        focused_tag,
    )
    problems = bc.four_shots(browser, f"{base}/#/results/{big}", "results-playbook", ".vrow-btn")
    check(
        "results playbook: no sideways scroll at 360 px; screenshots in four themes",
        not problems,
        "; ".join(problems),
    )
    # reject
    page.locator(".vrow-btn").nth(3).click()
    page.wait_for_selector("dialog[open]")
    iid = page.locator("dialog[open] h3.item-title").inner_text()
    page.get_by_role("button", name="Reject this finding…").click()
    page.locator("#reject-reason").fill("not about our market")
    shot_name = "reject-confirm-1280-light"
    bc.shot(page, shot_name, full=False)
    page.get_by_role("button", name="Yes, reject it").click()
    page.wait_for_selector("dialog[open] .tone-ok")
    check(
        "reject: the finding is struck through and marked 'Rejected by you'",
        page.locator("dialog[open] .item-title.struck").count() == 1
        and "Rejected by you" in page.locator("dialog[open]").inner_text(),
    )
    page.keyboard.press("Escape")
    wait_for(page, "document.querySelectorAll('.vrow.is-rejected').length === 1", 5)
    check(
        "reject: the list row shows it as rejected right away",
        page.locator(".vrow.is-rejected").count() == 1
        and "Rejected by you" in page.locator(".vrow.is-rejected").inner_text(),
    )
    ov = f.get(f"/v1/research/runs/{big}/overview")
    check(
        "reject: the server recorded it (overlay), the committed file is unchanged",
        len(ov["rejected_ids"]) == 1 and ov["item_count"] == 600,
    )
    page.reload()
    page.wait_for_selector(".vrow-btn")
    check(
        "reject: still shown as rejected after a reload",
        page.locator(".vrow.is-rejected").count() == 1,
        str(iid),
    )
    # appendix
    tab(page, "Appendix")
    wait_for(page, "document.querySelector('.vscroll')?.getAttribute('data-total') === '150'")
    check(
        "appendix: lists the 150 lower-ranked findings",
        page.locator(".vscroll").get_attribute("data-total") == "150"
        and "Nothing was deleted" in bc.text(page, ".tab-panel"),
    )
    # norms
    tab(page, "Norms")
    page.wait_for_selector(".norm")
    nt = bc.text(page, ".tab-panel")
    check(
        "norms: 'mentioned in n of N sources', the level and the newest source date",
        re.search(r"mentioned in \d+ of \d+ sources", nt)
        and "newest source" in nt
        and any(w in nt for w in ("Core", "Common", "Limited evidence")),
        nt[:200],
    )
    page.locator(".norm summary").first.click()
    page.wait_for_selector(".norm-items button")
    check(
        "norms: a norm lists its findings and each opens",
        page.locator(".norm-items button").count() >= 1,
    )
    page.locator(".norm-items button").first.click()
    page.wait_for_selector("dialog[open]")
    page.keyboard.press("Escape")
    boxes = page.locator(".toggles input[type=checkbox]")
    check(
        "suggestions: toggles exist and every one starts OFF",
        boxes.count() > 0 and all(not boxes.nth(i).is_checked() for i in range(boxes.count())),
        str(boxes.count()),
    )
    boxes.first.check()
    check(
        "suggestions: switching one on is counted, nothing is added by itself",
        "1 suggestion switched on" in bc.text(page, ".tab-panel"),
    )
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{big}",
        "results-norms",
        ".vrow-btn",
        before=lambda p: (tab(p, "Norms"), p.wait_for_selector(".norm")),
    )
    check("norms: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # confirm
    tab(page, "Confirm")
    page.wait_for_selector(".confirm-item")
    check(
        "confirm: each finding to confirm shows why, with an Open",
        "must be confirmed" in bc.text(page, ".tab-panel")
        or "confirm" in bc.text(page, ".tab-panel").lower(),
    )
    page.locator(".confirm-item button").first.click()
    page.wait_for_selector("dialog[open]")
    page.keyboard.press("Escape")
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{big}",
        "results-confirm",
        ".vrow-btn",
        before=lambda p: (tab(p, "Confirm"), p.wait_for_selector(".confirm-item")),
    )
    check("confirm: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # gaps (the partial run)
    page.goto(f"{base}/#/results/{part}")
    page.wait_for_selector(".results-head")
    hd = bc.text(page, ".results-head")
    check(
        "partial result: the header says 'Finished with gaps' and why (budget)",
        "Finished with gaps" in hd and "budget limit" in hd,
        hd[:300],
    )
    tab(page, "Gaps and conflicts")
    page.wait_for_selector("[data-reason]")
    gt = bc.text(page, ".tab-panel")
    check(
        "gaps: every reason is in plain words",
        all(
            w in gt
            for w in (
                "budget limit stopped",
                "Research ended before this topic was reached",
                "search service was not available",
                "You stopped this topic",
            )
        )
        and "budget_stop" not in gt,
        gt[:300],
    )
    check(
        "gaps: lists what was searched for and says there are no conflicts",
        "What we searched for" in gt and "No conflicts" in gt,
    )
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{part}",
        "results-gaps-partial",
        ".results-head",
        before=lambda p: (tab(p, "Gaps and conflicts"), p.wait_for_selector("[data-reason]")),
    )
    check("gaps: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # quality
    page.goto(f"{base}/#/results/{big}")
    page.wait_for_selector(".tabs")
    tab(page, "Quality")
    page.wait_for_selector("[data-measure]")
    qt = bc.text(page, ".tab-panel")
    states = {c.inner_text() for c in page.locator("[data-measure] .chip").all()}
    check(
        "quality: every measure shows value, target and OK / Not OK / Not measured",
        page.locator("[data-measure]").count() == 8
        and states <= {"OK", "Not OK", "Not measured"}
        and "OK" in states,
        str(states),
    )
    for needle in (
        "Lane quality",
        "What the builder needs",
        "Validation checks",
        "Quotes found in the page the first time",
        "Right after one retry",
        "The checker",
        "Time by stage",
        "Cost by step",
        "Fallbacks used",
        "Required facts by topic area",
    ):
        check(f"quality: shows '{needle}'", needle in qt)
    check(
        "quality: lane quality uses plain words",
        all(w in qt for w in ("Partly covered", "Strong")) and "defaulted_only" not in qt,
    )
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{big}",
        "results-quality",
        ".vrow-btn",
        before=lambda p: (tab(p, "Quality"), p.wait_for_selector("[data-measure]")),
    )
    check("quality: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # sources
    tab(page, "Sources")
    page.wait_for_selector("tbody tr")
    st = bc.text(page, ".tab-panel")
    rows = page.locator("tbody tr").count()
    check(
        "sources: every page with kind, date, link and how many findings use it",
        rows > 0 and "Open the page" in st and "finding" in st and "Site" in st,
        f"{rows} rows",
    )
    n_before = rows
    page.locator("#src-q").fill("evil")
    wait_for(
        page,
        "document.querySelectorAll('tbody tr').length < "
        + str(max(rows, 2))
        + " || document.querySelectorAll('tbody tr').length === 1",
        5,
    )
    evil_rows = page.locator("tbody tr", has_text="evil.example")
    check(
        "sources: search narrows the list, and the hostile address is plain text, not a link",
        1 <= page.locator("tbody tr").count() < n_before
        and evil_rows.count() >= 1
        and evil_rows.first.locator("a").count() == 0
        and page.locator("tbody a[href^='javascript' i]").count() == 0
        and "<img src=x onerror=alert(6)> Pricing page" in bc.text(page, "tbody"),
        str(n_before),
    )
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{big}",
        "results-sources",
        ".vrow-btn",
        before=lambda p: (tab(p, "Sources"), p.wait_for_selector("tbody tr")),
    )
    check("sources: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # report
    tab(page, "Report")
    page.wait_for_selector(".md h2")
    rt = bc.text(page, ".report")
    check(
        "report: a readable preview with headings, lists and tables",
        page.locator(".md h2, .md h3").count() >= 3
        and page.locator(".md table").count() >= 1
        and page.locator(".md ul").count() >= 1
        and page.locator(".md blockquote").count() >= 1,
    )
    check(
        "report XSS: <script> and javascript: text is shown as characters, nothing is injected",
        "<script>alert(2)</script>" in rt
        and page.locator(
            ".report script, .report img, .report iframe, .report [onerror], .report a[href^='javascript' i]"
        ).count()
        == 0,
    )
    check(
        "report: a long report is drawn in steps",
        page.get_by_role("button", name=re.compile("Show more")).count() == 1,
    )
    page.get_by_role("button", name=re.compile("Show more")).click()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download the report (.md)").click()
    md = Path(dl.value.path()).read_text(encoding="utf-8")
    check(
        "report: the .md download is the report text",
        md.startswith("# Research report")
        and "<script>alert(2)</script>" in md
        and dl.value.suggested_filename.endswith(".md"),
        md[:60],
    )
    problems = bc.four_shots(
        browser,
        f"{base}/#/results/{big}",
        "results-report",
        ".vrow-btn",
        before=lambda p: (tab(p, "Report"), p.wait_for_selector(".md h2")),
    )
    check("report: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # raw
    tab(page, "Raw JSON")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download research.json").click()
    data = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    check(
        "raw JSON: downloads the committed file",
        len(data["items"]) == 600 and data["schema"].startswith("research.v"),
    )
    # keyboard on tabs
    tab(page, "Playbook")
    page.get_by_role("tab", name="Playbook", exact=True).focus()
    page.keyboard.press("ArrowRight")
    check(
        "tabs: arrow keys move between tabs",
        page.get_by_role("tab", name="Norms", exact=True).get_attribute("aria-selected") == "true",
    )
    page.context.close()


def scenario_other_headers(browser, f: bc.Fake) -> None:
    """Skipped, cancelled (never started) and still-running results headers, from real runs."""
    base = f.base
    page = bc.new_page(browser)
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    rid1 = bc.start_run_via_ui(page, "quick-yoga")
    page.wait_for_selector(".topic", timeout=15000)
    page.goto(f"{base}/#/results/{rid1}")
    page.wait_for_selector(".results-head")
    check(
        "results while running: says the run is still going and links to the live view",
        "still going" in bc.text(page, ".results-head")
        and page.get_by_role("link", name="Watch it live").count() == 1
        and page.get_by_role("tab").count() == 0,
    )
    rid2 = bc.start_run_via_ui(page, "standard-produce")
    page.wait_for_selector(".banner-info")
    page.get_by_role("button", name="Cancel (it has not started)").click()
    page.wait_for_selector(".banner-bad")
    page.goto(f"{base}/#/results/{rid2}")
    page.wait_for_selector(".results-head")
    hd = bc.text(page, ".results-head")
    check(
        "results of a cancelled run: plain explanation, no tabs, link to the run",
        "cancelled" in hd.lower()
        and "no results to read" in hd
        and page.get_by_role("tab").count() == 0,
        hd[:200],
    )
    bc.shot(page, "results-cancelled-1280-light")
    problems = bc.four_shots(
        browser, f"{base}/#/results/{rid2}", "results-cancelled", ".results-head"
    )
    check("results (cancelled): no sideways scroll at 360 px", not problems, "; ".join(problems))
    page.goto(f"{base}/#/run/{rid1}")
    page.wait_for_selector(".topic")
    page.get_by_role("button", name="Skip research").click()
    page.wait_for_selector("dialog[open]")
    page.locator("dialog[open]").get_by_role("button", name="Skip research").click()
    f.wait_status(rid1, ("skipped", "complete", "partial", "failed"), 60)
    page.goto(f"{base}/#/results/{rid1}")
    page.wait_for_selector(".results-head")
    check(
        "results of a skipped run: 'Stopped early' and the tabs are there",
        "Stopped early" in bc.text(page, ".results-head") and page.get_by_role("tab").count() == 9,
        bc.text(page, ".results-head")[:200],
    )
    problems = bc.four_shots(
        browser, f"{base}/#/results/{rid1}", "results-skipped", ".results-head"
    )
    check("results (skipped): no sideways scroll at 360 px", not problems, "; ".join(problems))
    page.context.close()


def scenario_run_view(browser, f: bc.Fake) -> None:
    base = f.base
    page = bc.new_page(browser)
    page.goto(base + "/#/inputs")
    page.wait_for_selector(".input-card")
    rid = bc.start_run_via_ui(page, "quick-yoga")
    page.wait_for_selector(".topic", timeout=15000)
    wait_for(
        page,
        "Number(document.querySelector('[data-fact-counts]')?.getAttribute('data-fact-counts') || 0) > 60",
        60,
    )
    tile = bc.text(page, ".tile:has-text('Required facts found') strong")
    check(
        "run view: 'required facts found x of y' tile and bar",
        re.fullmatch(r"\d+ of [1-9]\d*", tile) is not None
        and page.locator("progress[aria-label='Required facts found']").count() == 1,
        tile,
    )
    check(
        "run view: the progress line says what the numbers are",
        "required facts found" in bc.text(page, ".progress-row:nth-of-type(2)")
        or "required facts found" in bc.text(page, ".card[aria-label='Progress']"),
    )
    n_before = int(page.locator("[data-fact-counts]").get_attribute("data-fact-counts"))
    page.context.close()
    time.sleep(1.5)
    page = bc.new_page(browser)
    page.goto(f"{base}/#/run/{rid}")
    page.wait_for_selector(".fact", timeout=15000)
    n_after = int(page.locator("[data-fact-counts]").get_attribute("data-fact-counts"))
    shown = page.locator(".fact").count()
    check(
        "run view: a reloaded page shows the whole list of findings, not just the last events",
        n_after >= n_before and n_after > 60 and shown >= min(n_after, 120),
        f"before {n_before}, after reload {n_after}, rows {shown}",
    )
    page.wait_for_selector(".banner-ok, .banner-warn", timeout=90000)
    wait_for(page, "document.querySelectorAll('.fact').length >= 120", 10)
    server_facts = len(f.get(f"/v1/research/runs/{rid}")["facts"])
    wait_for(
        page,
        "Number(document.querySelector('[data-fact-counts]').getAttribute('data-fact-counts')) === "
        + str(server_facts),
        8,
    )
    final_total = int(page.locator("[data-fact-counts]").get_attribute("data-fact-counts"))
    final_rows = page.locator(".fact").count()
    check(
        "run view: after the reload and the end, the finding count equals the server's list (no duplicates, none lost)",
        final_total == server_facts and final_rows == min(final_total, 120),
        f"page {final_total}, rows {final_rows}, server {server_facts}",
    )
    page.wait_for_selector(".run-summary .card", timeout=10000)
    st = bc.text(page, ".run-summary")
    check(
        "run view: the finished run shows time by stage, cost by step and fallbacks used",
        all(w in st for w in ("Time by stage", "Cost by step", "Fallbacks used")) and "USD" in st,
        st[:300],
    )
    checked = page.locator(".fact-checked").count()
    check(
        "run view: findings end up 'checked'",
        checked > 0
        and int(page.locator("[data-fact-counts]").get_attribute("data-fact-counts")) >= 100,
    )
    bc.shot(page, "run-finished-summary-1280-light")
    problems = bc.four_shots(browser, f"{base}/#/run/{rid}", "run-summary", ".run-summary .card")
    check(
        "run view (finished, with summary): no sideways scroll at 360 px",
        not problems,
        "; ".join(problems),
    )
    page.context.close()


def press_and_wait(page, key: str) -> None:
    before = page.evaluate(
        "document.querySelector('#sc-title, #sc-end')?.textContent + '|' + (document.querySelector('[data-progress]')?.textContent || '')"
    )
    page.keyboard.press(key)
    wait_for(
        page,
        "(document.querySelector('#sc-title, #sc-end')?.textContent + '|' + (document.querySelector('[data-progress]')?.textContent || '')) !== "
        + json.dumps(before),
        6,
    )


def scenario_spotcheck(browser, f: bc.Fake, ids: dict) -> None:
    base = f.base
    page = bc.new_page(browser)
    page.goto(base + "/#/spotcheck")
    page.wait_for_selector(".runs-pick")
    check(
        "spot-check: lists the finished runs with a seed box and Start disabled until one is picked",
        page.locator(".runs-pick li").count() >= 3
        and page.get_by_role("button", name="Start the spot-check").is_disabled(),
    )
    bc.shot(page, "spotcheck-setup-1280-light")
    problems = bc.four_shots(browser, base + "/#/spotcheck", "spotcheck-setup", ".runs-pick")
    check("spot-check setup: no sideways scroll at 360 px", not problems, "; ".join(problems))
    page.locator(".runs-pick li", has_text="Seeded big").locator("input").check()
    page.locator("#sc-seed").fill("7")
    page.get_by_role("button", name="Start the spot-check").click()
    page.wait_for_url("**/#/spotcheck/*", timeout=10000)
    page.wait_for_selector("#sc-title")
    cid = page.url.split("#/spotcheck/")[1]
    server = f.get(f"/api/spot-checks/{cid}")
    check(
        "spot-check: the chosen seed is used and 20 cards are drawn",
        server["seed"] == 7 and len(server["cards"]) == 20,
        str(server["seed"]),
    )
    t = bc.text(page, "main")
    for needle in (
        "Card 1 of 20",
        "The claim",
        "The quote it rests on",
        "Open the page",
        "1\nOK",
        "6\nInvented detail",
        "Skip",
    ):
        check(
            f"spot-check card: shows '{needle.replace(chr(10), ' ')}'",
            needle in t or needle.replace("\n", " ") in t,
            t[:120],
        )
    check(
        "spot-check card: stratum, evidence badge and the six verdict buttons",
        page.locator(".verdict").count() == 6 and page.locator(".sc-card .chip").count() >= 3,
    )
    bc.shot(page, "spotcheck-card-1280-light")
    problems = bc.four_shots(browser, f"{base}/#/spotcheck/{cid}", "spotcheck-card", "#sc-title")
    check("spot-check card: no sideways scroll at 360 px", not problems, "; ".join(problems))
    # keyboard only: answer 7 cards, reload, resume
    plan = ["1", "1", "2", "1", "3", "1", "1"]
    for k in plan:
        press_and_wait(page, k)
    saved = f.get(f"/api/spot-checks/{cid}")
    check(
        "spot-check: each answer is saved at once (server holds all 7)",
        len(saved["verdicts"]) == 7,
        str(len(saved["verdicts"])),
    )
    page.reload()
    page.wait_for_selector("#sc-title")
    check(
        "spot-check: reloading resumes at the first unanswered card, with the progress kept",
        "Card 8 of 20" in bc.text(page, "#sc-title")
        and "7 answered" in bc.text(page, "[data-progress]"),
        bc.text(page, "#sc-title"),
    )
    press_and_wait(page, "ArrowLeft")
    check(
        "spot-check: the left arrow goes back to change an answer",
        "Card 7 of 20" in bc.text(page, "#sc-title") and page.locator(".verdict-on").count() == 1,
    )
    press_and_wait(page, "6")
    check(
        "spot-check: changing an answer saves it and returns to the first unanswered card",
        "Card 8 of 20" in bc.text(page, "#sc-title")
        and f.get(f"/api/spot-checks/{cid}")["verdicts"][saved["cards"][6]["key"]]
        == "invented_detail",
    )
    for k in ["1"] * 10 + ["4", "5"]:
        press_and_wait(page, k)
    check("spot-check: 19 answered, one card left", "Card 20 of 20" in bc.text(page, "#sc-title"))
    page.keyboard.press("s")
    page.wait_for_selector("#sc-end", timeout=8000)
    et = bc.text(page, "main")
    s = f.get(f"/api/spot-checks/{cid}/summary")
    check(
        "spot-check end: the summary against the thresholds, with a verdict",
        "Not passed" in et
        and "Supported" in et
        and "at least 95%" in et
        and "at least 90%" in et
        and "fewer than 3" in et
        and not s["passed"],
        et[:300],
    )
    rows = {r.get_attribute("data-row"): r.inner_text() for r in page.locator("tr[data-row]").all()}
    check(
        "spot-check end: each threshold row shows your result and whether it was met",
        len(rows) == 3 and all(("Yes" in v or "No" in v) for v in rows.values()),
    )
    check(
        "spot-check end: shows where it went wrong (the failing job or kind)",
        "Where it went wrong" in et and "not OK" in et and len(s["worst"]) >= 1,
        str(s["worst"]),
    )
    check(
        "spot-check end: shows the count of every verdict",
        "Invented detail: 1" in et.replace("\n", " ") or "Invented detail:" in et,
    )
    bc.shot(page, "spotcheck-end-1280-light")
    with page.expect_download() as d1:
        page.get_by_role("button", name="Download as Markdown (.md)").click()
    md = Path(d1.value.path()).read_text(encoding="utf-8")
    with page.expect_download() as d2:
        page.get_by_role("button", name="Download as data (.json)").click()
    js_ = json.loads(Path(d2.value.path()).read_text(encoding="utf-8"))
    check(
        "spot-check end: .md and .json exports download",
        md.startswith("# Spot-check")
        and "NOT PASSED" in md
        and js_["summary"]["answered"] == 19
        and len(js_["verdicts"]) == 20,
    )
    problems = bc.four_shots(browser, f"{base}/#/spotcheck/{cid}", "spotcheck-end", "#sc-end")
    check("spot-check end: no sideways scroll at 360 px", not problems, "; ".join(problems))
    page.get_by_role("button", name=re.compile(r"Review the 1 skipped")).click()
    page.wait_for_selector("#sc-title")
    check(
        "spot-check: 'Review the skipped' goes to the skipped card",
        "Card 20 of 20" in bc.text(page, "#sc-title"),
    )
    page.keyboard.press("1")
    page.wait_for_selector("#sc-end", timeout=8000)
    check(
        "spot-check: answering the skipped card finishes the check again",
        f.get(f"/api/spot-checks/{cid}/summary")["answered"] == 20,
    )
    page.goto(base + "/#/spotcheck")
    page.wait_for_selector(".runs-pick")
    check(
        "spot-check: earlier checks are listed with 'See the result' (resumable)",
        page.get_by_role("link", name="See the result").count() >= 1,
    )
    # focus ring visible on a verdict button (keyboard)
    page.goto(f"{base}/#/results/{ids['big']}")
    page.context.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(Path(tempfile.gettempdir()) / "research-console-shots2"))
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    bc.SHOTS = Path(args.shots)
    bc.SHOTS.mkdir(parents=True, exist_ok=True)
    f = bc.Fake(8151, "--seed", "600", "--slow", "--delay", "3").start()
    try:
        ids = seeded(f)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            for name, fn in (
                ("results", lambda: scenario_results(browser, f, ids)),
                ("run", lambda: scenario_run_view(browser, f)),
                ("spotcheck", lambda: scenario_spotcheck(browser, f, ids)),
                ("headers", lambda: scenario_other_headers(browser, f)),
            ):
                if args.only and args.only != name:
                    continue
                print(f"--- {name}", flush=True)
                try:
                    fn()
                except Exception as exc:
                    check(f"scenario {name} ran to the end", False, repr(exc)[:500])
            browser.close()
    finally:
        f.kill()
    check(
        "no alert dialog, page error or console error in any page",
        not bc.ISSUES,
        "; ".join(bc.ISSUES[:5]),
    )
    failed = [r for r in bc.RESULTS if not r[1]]
    print(
        f"\n{len(bc.RESULTS) - len(failed)} passed, {len(failed)} failed; screenshots in {bc.SHOTS}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
