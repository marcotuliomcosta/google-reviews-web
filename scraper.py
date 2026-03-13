"""
Playwright scraping: preview rápido e extração completa de reviews.
"""
import asyncio
import json
import re
from pathlib import Path
from typing import Callable, Awaitable

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SCROLL_PAUSE = 2000


def _load_cookies(cookies_file: Path) -> list[dict]:
    if cookies_file.exists():
        with open(cookies_file, encoding="utf-8") as f:
            return json.load(f)
    return []


def _normalize_maps_url(url: str) -> str:
    """
    Converte URL de contexto de busca do Google Maps para URL direta da empresa.
    Remove parâmetros de pesquisa (!2m1!1s<query>) que causam split-view com
    múltiplas empresas na lista, dificultando o scraping dos reviews.
    """
    # Extrai place name do path
    name_m = re.search(r'/maps/place/([^/@]+)', url)
    # Extrai place ID (formato hexadecimal)
    id_m = re.search(r'!1s(0x[a-f0-9]+:0x[a-f0-9]+)', url)
    # Extrai coordenadas do negócio (não do centro do mapa)
    coord_m = re.search(r'!8m2!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    # Extrai 16s path (identificador do Google Business)
    path16_m = re.search(r'(!16s[^!?&\s]+)', url)

    if not (id_m and coord_m):
        return url  # Não consegue normalizar, usa original

    place_name = name_m.group(1) if name_m else 'place'
    place_id = id_m.group(1)
    lat = coord_m.group(1)
    lng = coord_m.group(2)
    path16 = path16_m.group(1) if path16_m else ''

    # Garante que !9m1!1b1 (aba de avaliações) está presente
    clean_data = f"!4m6!3m5!1s{place_id}!8m2!3d{lat}!4d{lng}!9m1!1b1{path16}"
    clean_url = f"https://www.google.com/maps/place/{place_name}/@{lat},{lng},17z/data={clean_data}"
    return clean_url


async def search_companies(query: str, cookies_file: Path) -> list[dict]:
    """
    Busca empresas no Google Maps pelo nome e retorna os top resultados.
    """
    import urllib.parse
    cookies = _load_cookies(cookies_file)
    search_url = f"https://www.google.com/maps/search/{urllib.parse.quote(query)}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        if cookies:
            await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)

        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Aguarda os resultados aparecerem (mais confiável que timeout fixo)
        try:
            await page.wait_for_selector('.Nv2PK, [role="article"]', timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        results = await page.evaluate("""() => {
            const items = [];
            const seen = new Set();
            const containers = document.querySelectorAll('.Nv2PK, [role="article"]');

            for (const container of containers) {
                const link = container.querySelector('a[href*="/maps/place/"]');
                if (!link) continue;
                const url = link.href;
                if (!url || seen.has(url)) continue;
                seen.add(url);

                // Nome — múltiplas estratégias em ordem de confiabilidade
                let name = "";
                // 1: aria-label no link principal
                const aria = link.getAttribute('aria-label');
                if (aria && aria.length > 1 && aria.length < 120) name = aria.trim();
                // 2: elemento com role heading
                if (!name) {
                    const hEl = container.querySelector('[role="heading"]');
                    if (hEl) name = hEl.innerText.trim();
                }
                // 3: classes conhecidas
                if (!name) {
                    const nEl = container.querySelector('.qBF1Pd, .fontHeadlineSmall, .NrDZNb, .OSrXXb');
                    if (nEl) name = nEl.innerText.trim();
                }
                // 4: primeiro span/div de texto razoável no container
                if (!name) {
                    for (const el of container.querySelectorAll('span, div')) {
                        if (el.children.length > 0) continue;
                        const t = el.innerText.trim();
                        if (t.length > 2 && t.length < 100 && !/^[\\d,.★·]+$/.test(t)) {
                            name = t; break;
                        }
                    }
                }
                if (!name) continue;

                // Endereço + cidade — coleta folhas de texto e monta endereço completo
                const leafTexts = [];
                for (const el of container.querySelectorAll('span, div')) {
                    if (el.children.length > 0) continue;
                    const t = el.innerText.trim().replace(/^·\\s*/, '');
                    if (t.length > 3 && t !== name && !/^[\\d,.★]+$/.test(t) && !/avalia/i.test(t) && !/aberto|fechado/i.test(t) && !/^\\d+\\s*(km|m)$/.test(t)) {
                        leafTexts.push(t);
                    }
                }
                // Linha com número de rua (endereço) + linha com cidade/estado
                const streetLine = leafTexts.find(t => /\\d/.test(t) && t.length > 6) || "";
                const cityLine = leafTexts.find(t => !t.includes(streetLine) && /,\\s*[A-Z]/.test(t) && !/^\\d/.test(t)) || leafTexts.find(t => t !== streetLine && t.length > 3) || "";
                const address = [streetLine, cityLine].filter(Boolean).join(' — ');

                // Rating
                let rating = "";
                const rEl = container.querySelector('.MW4etd');
                if (rEl) rating = rEl.innerText.trim();

                items.push({ name, address, rating, url });
                if (items.length >= 7) break;
            }
            return items;
        }""")

        await browser.close()

    return results


async def preview_company(url: str, cookies_file: Path) -> dict:
    """
    Scrape rápido: retorna nome, endereço, qtd avaliações, nota média.
    Leva ~8-12 segundos. Não percorre os reviews.
    """
    cookies = _load_cookies(cookies_file)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        if cookies:
            await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(8000)

        # Nome da empresa via título da aba
        name = ""
        try:
            title = await page.title()
            name = re.sub(r"\s*[–-]\s*Google Maps$", "", title).strip()
        except Exception:
            pass

        # Nota média — busca span/div com texto no formato "4,5" ou "4.5"
        rating = ""
        try:
            rating_raw = await page.evaluate("""() => {
                const all = document.querySelectorAll("span, div");
                for (const e of all) {
                    const t = e.innerText.trim();
                    if (/^[1-5][,.]\\d$/.test(t)) return t;
                }
                return "";
            }""")
            if rating_raw:
                rating = rating_raw.replace(",", ".")
        except Exception:
            pass

        # Contagem total de avaliações — primeiro elemento com "X avaliações"
        review_count = 0
        try:
            total_text = await page.evaluate("""() => {
                const all = document.querySelectorAll("span, div, button");
                for (const e of all) {
                    const t = e.innerText.trim();
                    if (/^\\d+\\s*avalia/i.test(t)) return t;
                }
                return "";
            }""")
            if total_text:
                m = re.search(r"(\d+)", total_text)
                if m:
                    review_count = int(m.group(1))
        except Exception:
            pass

        # Clica em "Visão geral" para obter endereço
        address = ""
        try:
            await page.locator("[role=tab]").filter(has_text="Visão geral").first.click(timeout=4000)
            await page.wait_for_timeout(3000)
        except Exception:
            try:
                await page.locator("[role=tab]").filter(has_text="Overview").first.click(timeout=3000)
                await page.wait_for_timeout(3000)
            except Exception:
                pass

        try:
            btn = page.locator("button[data-item-id='address']")
            aria = await btn.first.get_attribute("aria-label", timeout=4000) or ""
            address = re.sub(r"^[Ee]ndere[çc]o:\s*", "", aria).strip()
            if not address:
                address = await btn.first.inner_text(timeout=3000)
                address = address.strip()
        except Exception:
            pass

        # Segmento / categoria
        segment = ""
        try:
            segment = await page.evaluate("""() => {
                const btns = document.querySelectorAll("button.DkEaL, span.DkEaL, button[jsaction*='category']");
                for (const b of btns) {
                    const t = b.innerText.trim();
                    if (t && t.length < 80) return t;
                }
                return "";
            }""")
        except Exception:
            pass

        # Telefone
        phone = ""
        try:
            phone_raw = await page.evaluate("""() => {
                const all = document.querySelectorAll("[aria-label]");
                for (const e of all) {
                    const a = e.getAttribute("aria-label") || "";
                    if (/Telefone:/i.test(a) || /Phone:/i.test(a)) return a;
                }
                const btns = document.querySelectorAll("button[data-item-id]");
                for (const b of btns) {
                    if ((b.getAttribute("data-item-id") || "").startsWith("phone")) {
                        return b.getAttribute("aria-label") || b.innerText.trim();
                    }
                }
                return "";
            }""")
            phone = re.sub(r"^Telefone:\s*", "", phone_raw, flags=re.I).strip()
        except Exception:
            pass

        await browser.close()

    return {
        "name": name or "Nome não encontrado",
        "address": address or "Endereço não encontrado",
        "segment": segment or "",
        "phone": phone or "",
        "review_count": review_count,
        "rating": rating or "—",
    }


async def extract_reviews(
    url: str,
    cookies_file: Path,
    progress_callback: Callable[[int, int, str], Awaitable[None]],
) -> list[dict]:
    """
    Extração completa: scroll até o fim, extrai todos os reviews.
    Chama progress_callback(loaded, total, message) a cada ciclo.
    """
    cookies = _load_cookies(cookies_file)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        if cookies:
            await ctx.add_cookies(cookies)

        page = await ctx.new_page()
        await Stealth().apply_stealth_async(page)

        await progress_callback(0, 0, "Abrindo página...")
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(5000)

        # Se a URL é de contexto de busca (tem múltiplas empresas), clica na aba Avaliações
        # para garantir que estamos no painel correto da empresa selecionada
        try:
            await page.locator("[role=tab]").filter(has_text=re.compile(r"Avalia|Review", re.I)).first.click(timeout=4000)
            await page.wait_for_timeout(3000)
        except Exception:
            pass

        count = await page.locator("div[data-review-id][jsaction]").count()
        if count == 0:
            await browser.close()
            raise RuntimeError(
                "Nenhum review encontrado. Verifique a URL ou se o login está válido."
            )

        # Ordenar por mais recentes
        await progress_callback(count, 0, "Ordenando por mais recentes...")
        try:
            sort = page.locator(
                "button[aria-label*='Classificar'], button[aria-label*='Sort']"
            )
            if await sort.count() == 0:
                import re as _re
                sort = page.get_by_role(
                    "button", name=_re.compile(r"Classificar|Sort", _re.I)
                )
            await sort.first.click(timeout=5000)
            await page.wait_for_timeout(800)
            await page.get_by_text(
                re.compile(r"Mais recentes|Newest", re.I)
            ).first.click(timeout=3000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        # Localiza o container de reviews para mouse wheel scroll
        feed_loc = None
        for sel in ['div[role="feed"]', '.m6QErb.DxyBCb', '.m6QErb.XiKgde', '.m6QErb']:
            loc = page.locator(sel)
            if await loc.count() > 0:
                feed_loc = loc.first
                break

        # Extrai um card individual
        async def _extract_card(card) -> dict | None:
            try:
                try:
                    await card.get_by_role(
                        "button", name=re.compile(r"^mais$|^more$", re.I)
                    ).first.click(timeout=300)
                    await page.wait_for_timeout(80)
                except Exception:
                    pass

                name = ""
                for sel in ["div[class*='d4r55']", "span[class*='Vpc5Fe']", ".TSUbDb"]:
                    try:
                        t = await card.locator(sel).first.inner_text(timeout=400)
                        if t.strip():
                            name = t.strip()
                            break
                    except Exception:
                        pass

                stars = 0
                try:
                    aria = await card.locator("span[role='img']").first.get_attribute(
                        "aria-label", timeout=400
                    )
                    m = re.search(r"(\d)", aria or "")
                    if m:
                        stars = int(m.group(1))
                except Exception:
                    pass

                date_text = ""
                for sel in ["span[class*='rsqaWe']", "span[class*='xRkPPb']", ".dehysf"]:
                    try:
                        t = await card.locator(sel).first.inner_text(timeout=400)
                        if t.strip():
                            date_text = t.strip()
                            break
                    except Exception:
                        pass

                text = ""
                for sel in ["span[class*='wiI7pd']", "div[class*='MyEned']", ".review-full-text"]:
                    try:
                        t = await card.locator(sel).first.inner_text(timeout=400)
                        if t.strip():
                            text = t.strip()
                            break
                    except Exception:
                        pass

                reply = ""
                try:
                    t = await card.locator("div[class*='CDe7pd']").first.inner_text(timeout=400)
                    reply = re.sub(r"^Resposta do propriet.rio\s*", "", t, flags=re.I).strip()
                except Exception:
                    pass

                return {"nome": name, "nota": stars, "data": date_text, "review": text, "resposta_dono": reply}
            except Exception:
                pass
            return None

        # Scroll + extração simultânea (evita virtualização do DOM)
        seen_ids: set[str] = set()
        reviews = []
        last_seen = 0
        stuck = 0

        # Selector que pega apenas os containers externos dos reviews (não os botões internos)
        REVIEW_SEL = "div[data-review-id][jsaction]"

        while True:
            # Extrai todos os cards visíveis antes de scrollar
            all_cards = await page.locator(REVIEW_SEL).all()
            for card in all_cards:
                rid = await card.get_attribute("data-review-id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    data = await _extract_card(card)
                    if data:
                        reviews.append(data)

            await progress_callback(len(seen_ids), 0, f"Carregando reviews... {len(seen_ids)} extraídos")

            # Stuck detection: baseado em IDs novos encontrados, não no count do DOM
            if len(seen_ids) == last_seen:
                stuck += 1
                if stuck >= 12:
                    break
            else:
                stuck = 0
            last_seen = len(seen_ids)

            # Scroll: rola o último card para a view para forçar lazy loading
            try:
                last_card = page.locator(REVIEW_SEL).last
                await last_card.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            # Mouse wheel como fallback no container do feed
            if feed_loc:
                try:
                    box = await feed_loc.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + box["height"] / 2
                        await page.mouse.move(cx, cy)
                        await page.mouse.wheel(0, 3000)
                except Exception:
                    pass

            await page.wait_for_timeout(SCROLL_PAUSE)

        total = len(reviews)
        await progress_callback(total, total, f"Extraindo {total} reviews...")

        await browser.close()

    return reviews
