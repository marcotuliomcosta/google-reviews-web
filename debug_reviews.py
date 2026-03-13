"""
Diagnóstico: carrega a URL da Leadlovers e imprime o que está no DOM.
Roda standalone (não precisa do servidor).
"""
import asyncio, json, re, sys
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

COOKIES_FILE = Path("../google-reviews-scraper/google_cookies.json")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# URL original com contexto de busca
URL = "https://www.google.com.br/maps/place/Leadlovers+%7C+Automa%C3%A7%C3%A3o+de+Marketing,+Email+e+WhatsApp/@-25.5335008,-49.349019,12z/data=!4m12!1m2!2m1!1sleadlovers!3m8!1s0x94dce2668fbad7e1:0x5d4d88e014a86323!8m2!3d-25.5335008!4d-49.1965837!9m1!1b1!15sCgpsZWFkbG92ZXJzkgEQc29mdHdhcmVfY29tcGFueeABAA!16s%2Fg%2F11fs2pqwv3?entry=ttu&g_ep=EgoyMDI2MDMxMS4wIKXMDSoASAFQAw%3D%3D"

async def main():
    cookies = json.loads(COOKIES_FILE.read_text()) if COOKIES_FILE.exists() else []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        if cookies:
            await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)

        print("Abrindo URL...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(5000)

        # Clica na aba Avaliações
        try:
            await page.locator("[role=tab]").filter(has_text=re.compile(r"Avalia|Review", re.I)).first.click(timeout=4000)
            print("Clicou em Avaliações")
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"Não achou aba Avaliações: {e}")

        # Conta containers
        count0 = await page.locator("div[data-review-id]").count()
        print(f"\n=== ANTES DE QUALQUER SCROLL: {count0} cards ===")

        # Inspeciona os primeiros 3 cards
        cards = await page.locator("div[data-review-id]").all()
        for i, card in enumerate(cards[:3]):
            rid = await card.get_attribute("data-review-id")
            # Tenta pegar nome
            name = ""
            for sel in ["div[class*='d4r55']", "span[class*='Vpc5Fe']"]:
                try:
                    t = await card.locator(sel).first.inner_text(timeout=500)
                    if t.strip(): name = t.strip(); break
                except: pass
            # Tenta pegar estrelas
            stars = 0
            try:
                aria = await card.locator("span[role='img']").first.get_attribute("aria-label", timeout=500)
                m = re.search(r"(\d)", aria or "")
                if m: stars = int(m.group(1))
            except: pass
            # HTML do card
            html_snippet = await card.inner_html(timeout=1000)
            print(f"\nCard {i+1}: id={rid}, nome={name!r}, stars={stars}")
            print(f"  HTML preview: {html_snippet[:200]!r}")

        # Verifica qual container está disponível para scroll
        print("\n=== CONTAINERS DE SCROLL ===")
        for sel in ['div[role="feed"]', '.m6QErb.DxyBCb', '.m6QErb.XiKgde', '.m6QErb']:
            loc = page.locator(sel)
            c = await loc.count()
            if c > 0:
                box = await loc.first.bounding_box()
                print(f"  {sel}: {c} encontrado(s), bbox={box}")
            else:
                print(f"  {sel}: 0")

        # Testa scroll via scroll_into_view_if_needed do último card
        print("\n=== TENTANDO SCROLL (5x) ===")
        for i in range(5):
            try:
                last = page.locator("div[data-review-id]").last
                await last.scroll_into_view_if_needed(timeout=2000)
            except Exception as e:
                print(f"  scroll_into_view falhou: {e}")

            # Também mouse wheel
            feed = page.locator('div[role="feed"]')
            if await feed.count() > 0:
                box = await feed.first.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    await page.mouse.wheel(0, 3000)

            await page.wait_for_timeout(2000)
            count = await page.locator("div[data-review-id]").count()
            print(f"  Após scroll {i+1}: {count} cards no DOM")

        # Contagem final
        final_ids = set()
        for card in await page.locator("div[data-review-id]").all():
            rid = await card.get_attribute("data-review-id")
            if rid: final_ids.add(rid)
        print(f"\n=== FINAL: {len(final_ids)} IDs únicos ===")

        await browser.close()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
asyncio.run(main())
