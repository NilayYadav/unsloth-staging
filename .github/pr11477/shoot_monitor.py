import asyncio, json, os, sys
from playwright.async_api import async_playwright

BASE = f"http://127.0.0.1:{os.environ['PORT']}"
LABEL = sys.argv[1]
OUT = sys.argv[2]
A, R = os.environ["TOKEN"], os.environ["REFRESH"]

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1366, "height": 900})
        await ctx.add_init_script(
            "try{localStorage.setItem('unsloth_auth_token',%s);localStorage.setItem('unsloth_refresh_token',%s);}catch(e){}"
            % (json.dumps(A), json.dumps(R)))
        page = await ctx.new_page()
        await page.goto(f"{BASE}/api-monitor", wait_until="domcontentloaded")
        await page.get_by_text("Model loaded").first.wait_for(state="visible", timeout=90_000)
        await page.wait_for_timeout(2000)
        loaded = await page.get_by_text("Model loaded", exact=True).count()
        loading = await page.get_by_text("Loading model", exact=True).count()
        await page.screenshot(path=OUT, full_page=False)
        print(json.dumps({"label": LABEL, "ui_model_loaded_rows": loaded, "ui_loading_rows": loading}))
        await b.close()

asyncio.run(main())
