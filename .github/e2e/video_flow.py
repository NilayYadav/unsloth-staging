import json, os, re, sys, time
from playwright.sync_api import sync_playwright

browser_name, base, out, conn_name, conn_url, model_id, media, prompt, tag = sys.argv[1:10]
password = os.environ["E2E_STUDIO_PW"]
boot_pw_file = os.environ.get("E2E_BOOT_PW_FILE", "")
os.makedirs(out, exist_ok = True)
log = []
short = model_id.split("/")[-1]


def shot(page, name):
    try:
        page.screenshot(path = f"{out}/{tag}-{name}.png", timeout = 60000)
    except Exception as e:
        log.append(f"screenshot {name} failed: {e}")


def note(msg):
    print(f"[flow {tag}] {msg}", flush = True)
    log.append(msg)


def login(page):
    page.goto(base, timeout = 180000)
    deadline = time.time() + 180
    while time.time() < deadline:
        if page.locator("#new-password").is_visible():
            if page.locator("#current-password").is_visible():
                page.fill("#current-password", open(boot_pw_file).read().strip())
            page.fill("#new-password", password)
            page.fill("#confirm-password", password)
            shot(page, "0-setup-account")
            page.locator('button[type="submit"]').click()
            note("set up the account through the Setup form")
            break
        if page.locator("#password").is_visible():
            page.fill("#password", password)
            shot(page, "0-login")
            page.locator('button[type="submit"]').click()
            note("logged in through the Login form")
            break
        if "/chat" in page.url:
            note("already on /chat")
            break
        page.wait_for_timeout(1000)
    page.wait_for_url("**/chat**", timeout = 180000)
    page.wait_for_timeout(5000)


def add_connection(page):
    page.get_by_role("button", name = "Settings").first.click()
    page.wait_for_timeout(2000)
    page.get_by_text("Connections", exact = True).first.click()
    page.wait_for_timeout(2000)
    page.get_by_role("button", name = "Add connection").first.click()
    page.wait_for_timeout(1500)
    page.locator("#provider-preset").click()
    page.wait_for_timeout(800)
    page.get_by_role("option", name = "Custom").first.click()
    page.wait_for_timeout(1000)
    page.fill("#provider-custom-name", conn_name)
    page.fill("#provider-base-url", conn_url)
    page.fill("#provider-manual-models", model_id)
    shot(page, "1-connection-form")
    page.get_by_role("button", name = "Add connection").last.click()
    page.wait_for_timeout(4000)
    shot(page, "2-connection-added")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)


def pick_model(page):
    page.locator(
        "[aria-haspopup]", has_text = re.compile(r"Select model|" + re.escape(short[:12]), re.I)
    ).first.click()
    page.wait_for_timeout(2500)
    page.get_by_text("Connected", exact = True).first.click()
    page.wait_for_timeout(2000)
    search = page.get_by_placeholder(re.compile("Search", re.I)).first
    search.fill(short)
    page.wait_for_timeout(2500)
    shot(page, "3-picker")
    page.get_by_text(short, exact = False).last.click()
    page.wait_for_timeout(3000)
    shot(page, "4-model-selected")


def toasts(page):
    try:
        return page.locator("[data-sonner-toast]").all_inner_texts()
    except Exception as e:
        return [f"<toast read failed {e}>"]


with sync_playwright() as p:
    if browser_name == "msedge":
        b = p.chromium.launch(channel = "msedge")
    else:
        b = getattr(p, browser_name).launch()
    ctx = b.new_context(viewport = {"width": 1440, "height": 1000})
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and log.append(f"console error: {m.text[:300]}"))
    page.on("pageerror", lambda e: log.append(f"pageerror: {str(e)[:300]}"))

    def on_request(r):
        if r.method == "POST" and ("/chat/completions" in r.url or "/api/inference" in r.url):
            try:
                body = r.post_data_json or {}
            except Exception:
                body = {}
            vb = body.get("video_base64") if isinstance(body, dict) else None
            parts = []
            for m in body.get("messages", []) if isinstance(body, dict) else []:
                c = m.get("content")
                if isinstance(c, list):
                    parts += [q.get("type") for q in c if isinstance(q, dict)]
            log.append(
                f"browser POST {r.url.split('?')[0]} provider_type={body.get('provider_type')} "
                f"video_base64_chars={len(vb) if vb else 0} part_types={parts}"
            )

    page.on("request", on_request)
    page.on(
        "response",
        lambda r: r.status >= 400
        and "/api/" in r.url
        and log.append(f"HTTP {r.status} {r.url.split('?')[0]}"),
    )
    login(page)
    add_connection(page)
    pick_model(page)
    page.get_by_role("button", name = "Tools and attachments").click()
    page.wait_for_timeout(1500)
    item = page.locator(
        "[role=menuitem]", has_text = re.compile("file|upload|attach|photo", re.I)
    ).first
    with page.expect_file_chooser() as fc:
        item.click()
    fc.value.set_files(media)
    page.wait_for_timeout(3500)
    note(f"toasts after attach: {toasts(page)}")
    shot(page, "5-attached")
    page.get_by_placeholder(re.compile(".")).first.fill(prompt)
    page.get_by_role("button", name = "Send message").click()
    page.wait_for_timeout(4000)
    note(f"toasts after send: {toasts(page)}")
    shot(page, "6-sent")
    start = time.time()
    while time.time() - start < 900:
        page.wait_for_timeout(4000)
        if page.locator("button[aria-label*='Stop']").count() == 0:
            break
    page.wait_for_timeout(4000)
    note(f"toasts after reply: {toasts(page)}")
    shot(page, "7-reply")
    body = page.inner_text("main") if page.locator("main").count() else page.inner_text("body")
    open(f"{out}/{tag}-page.txt", "w", encoding = "utf-8").write(body)
    open(f"{out}/{tag}-log.txt", "w", encoding = "utf-8").write("\n".join(log))
    print("\n".join(log))
    print("---- page tail ----\n" + body[-2000:], flush = True)
    b.close()
