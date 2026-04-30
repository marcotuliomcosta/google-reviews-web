import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

async def debug():
    cookies = json.loads(Path("../google-reviews-scraper/google_cookies.json").read_text())
    url = "https://www.google.com/maps/place/Ip%C3%AA+Digital/@-18.9422419,-48.301604,17z/data=!4m8!3m7!1s0x94a444bc6ef51ba7:0xb9286a51de9ca136!8m2!3d-18.9422419!4d-48.2990291!9m1!1b1!16s%2Fg%2F11bwflsk9z?entry=ttu"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="pt-BR", viewport={"width": 1280, "height": 900}, user_agent=USER_AGENT)
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(8000)

        # Clica em Visão geral
        try:
            await page.locator("[role=tab]").filter(has_text="Visão geral").first.click(timeout=4000)
            await page.wait_for_timeout(3000)
        except:
            pass

        # Segmento / tipo de negócio
        segment = await page.evaluate("""() => {
            const btns = document.querySelectorAll("button[jsaction]");
            for (const b of btns) {
                const t = b.innerText.trim();
                if (t && t.length < 60 && !t.match(/^[0-9]/) && !t.match(/Rota|Salvar|Próximo|Enviar|Compartilhar/i)) {
                    const parent = b.closest('[data-section-id]');
                    if (!parent) continue;
                }
            }
            // Tenta via span com classe de subtítulo
            const spans = document.querySelectorAll("span.DkEaL, span[class*='Io6YTe'], button[jsaction*='category']");
            return Array.from(spans).map(s => s.innerText.trim()).filter(t => t && t.length < 80).slice(0, 5);
        }""")
        print("SEGMENT candidates:", segment)

        # Telefone
        phone = await page.evaluate("""() => {
            const btns = document.querySelectorAll("button[data-item-id]");
            for (const b of btns) {
                const id = b.getAttribute("data-item-id") || "";
                if (id.startsWith("phone")) {
                    return b.getAttribute("aria-label") || b.innerText.trim();
                }
            }
            // Fallback: aria-label com padrão de telefone
            const all = document.querySelectorAll("[aria-label]");
            for (const e of all) {
                const a = e.getAttribute("aria-label") || "";
                if (/Telefone:/i.test(a) || /Phone:/i.test(a)) return a;
            }
            return "";
        }""")
        print("PHONE:", phone)

        # Segmento via data-item-id
        cat = await page.evaluate("""() => {
            const btns = document.querySelectorAll("button[data-item-id*='cat'], button[jsaction*='category'], a[jsaction*='category']");
            return Array.from(btns).map(b => b.innerText.trim()).filter(t => t).slice(0, 3);
        }""")
        print("CATEGORY buttons:", cat)

        # Tenta pegar subtítulo (segmento) via texto abaixo do nome
        subtitle = await page.evaluate("""() => {
            const h1 = document.querySelector("h1");
            if (h1) {
                const next = h1.nextElementSibling;
                if (next) return next.innerText.trim();
            }
            // Tenta span.DkEaL que é classe comum do segmento no Maps
            const el = document.querySelector("span.DkEaL");
            if (el) return el.innerText.trim();
            // Tenta button com categoria
            const btns = document.querySelectorAll("button.DkEaL");
            if (btns.length) return btns[0].innerText.trim();
            return "";
        }""")
        print("SUBTITLE/SEGMENT:", subtitle)

        await browser.close()

asyncio.run(debug())
