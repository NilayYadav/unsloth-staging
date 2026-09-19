# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
import argparse, json, pathlib
from playwright.sync_api import sync_playwright

p = argparse.ArgumentParser()
p.add_argument("--url", required = True)
p.add_argument("--out", required = True)
p.add_argument("--side", choices = ["before", "after"], required = True)
a = p.parse_args()
out = pathlib.Path(a.out)
out.mkdir(parents = True, exist_ok = True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    context = browser.new_context(
        viewport = {"width": 1360, "height": 850}, permissions = ["clipboard-read", "clipboard-write"]
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(a.url + "/review-evidence.html", wait_until = "domcontentloaded", timeout = 120000)
    page.wait_for_function("window.__ready === true", timeout = 120000)
    page.get_by_text("Pears.", exact = True).wait_for(timeout = 120000)
    page.get_by_role("button", name = "Previous", exact = True).click()
    page.get_by_text("Apples.", exact = True).wait_for()
    assert page.evaluate("window.__branch()") == ["u1", "a1"]
    page.get_by_role("button", name = "Copy Markdown", exact = True).click()
    page.wait_for_function("window.__facts.copied !== undefined")
    copied = page.evaluate("navigator.clipboard.readText()")
    with page.expect_download() as d:
        page.get_by_role("button", name = "Download Markdown", exact = True).click()
    download = d.value
    download.save_as(out / "conversation.md")
    markdown = (out / "conversation.md").read_text(encoding = "utf-8")
    with page.expect_download() as d:
        page.get_by_role("button", name = "Download CSV", exact = True).click()
    d.value.save_as(out / "conversation.csv")
    csv = (out / "conversation.csv").read_text(encoding = "utf-8")
    page.get_by_role("button", name = "Save project source", exact = True).click()
    page.wait_for_function("window.__facts.uploads.length === 1")
    facts = page.evaluate("window.__facts")
    source = facts["uploads"][0]["body"]
    assert copied == markdown == source, (copied, markdown, source)
    assert "Apples." in markdown
    assert ("Pears." in markdown) == (a.side == "before")
    assert "Apples." in csv and "Pears." in csv
    assert not errors, errors
    facts.update(
        side = a.side,
        branch = ["u1", "a1"],
        copied = copied,
        download = markdown,
        source = source,
        csv = csv,
        markdown_assistant_sections = markdown.count("## Assistant"),
        csv_assistant_rows = sum(
            line.split(",")[0].strip(chr(34)) == "assistant" for line in csv.splitlines()
        ),
        browser = browser.version,
        viewport = {"width": 1360, "height": 850},
        errors = errors,
    )
    (out / "facts.json").write_text(json.dumps(facts, indent = 2), encoding = "utf-8")
    page.screenshot(path = str(out / "browser.png"))
    print(
        json.dumps(
            {
                k: facts[k]
                for k in (
                    "side",
                    "branch",
                    "markdown_assistant_sections",
                    "csv_assistant_rows",
                    "copied",
                    "source",
                )
            }
        )
    )
    browser.close()
