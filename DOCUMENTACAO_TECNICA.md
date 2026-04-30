# Google Reviews Web — Documentação Técnica

## Objetivo

Ferramenta web para extrair avaliações do Google Maps de qualquer empresa e gerar um relatório Excel formatado, com métricas, gráficos e separação de avaliações negativas. O usuário acessa via browser, cola a URL do Google Maps, e baixa o `.xlsx` pronto.

---

## Arquitetura Geral

```
Browser (index.html + app.js + style.css)
        │
        │ HTTP/REST (JSON)
        ▼
FastAPI Backend (main.py)
        │
        ├── scraper.py   ← Playwright headless (extração do Maps)
        └── excel.py     ← openpyxl (geração do relatório)
```

### Stack
| Camada | Tecnologia |
|--------|-----------|
| Backend | FastAPI + Uvicorn |
| Scraping | Playwright (Chromium headless) + playwright-stealth |
| Relatório | openpyxl |
| Autenticação | Google Sign-In (OAuth2 ID Token) |
| Frontend | HTML/CSS/JS vanilla (sem framework) |

---

## Fluxo de Funcionamento

### 1. Autenticação
- O usuário faz login com Google Sign-In no frontend
- O ID Token JWT é enviado no header `Authorization: Bearer <token>` em todas as chamadas
- O backend valida o token via `google-auth` (`id_token.verify_oauth2_token`)
- O `GOOGLE_CLIENT_ID` fica no `.env` do servidor

### 2. Sessão do Google Maps (cookies)
- O Google Maps exige login para exibir a aba "Avaliações"
- Um script separado (`salvar_login.py`, do projeto original) abre o browser, faz login interativo e salva os cookies em `google_cookies.json`
- O scraper injeta esses cookies no contexto do Playwright antes de navegar

### 3. Preview (rápido, ~10s)
- `POST /api/preview` com `{ url }`
- Abre o Maps, espera carregar, extrai: nome, endereço, segmento, telefone, nota média, total de avaliações
- Não percorre reviews — só lê o cabeçalho da página
- Usado para confirmar antes de iniciar a extração completa

### 4. Extração completa (assíncrona)
- `POST /api/extract` com `{ url, empresa }` → retorna `{ job_id }`
- Job roda em background com `BackgroundTasks` do FastAPI
- Frontend faz polling em `GET /api/status/{job_id}` a cada 3s
- Progresso reportado em tempo real via callback

### 5. Download
- `GET /api/download/{job_id}` → retorna o arquivo `.xlsx`
- Arquivo salvo em `output/<NomeDaEmpresa>/<NomeDaEmpresa>_Relatorio_Avaliacoes_YYYYMMDD.xlsx`

---

## Scraping — Desafios Técnicos

### Problema 1: Windows + Playwright + FastAPI
O Uvicorn usa `SelectorEventLoop` no Windows, que **não suporta subprocessos** — mas o Playwright precisa lançar o Chromium (subprocess).

**Solução:** cada chamada ao Playwright roda em uma thread separada com `ProactorEventLoop` próprio:
```python
def _run_in_proactor(coro):
    loop = asyncio.ProactorEventLoop()  # Windows only
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

async def _run_playwright(coro):
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run_in_proactor, coro)
```

### Problema 2: Detecção de bot pelo Google
O Playwright puro é detectado pelo Google e bloqueia o acesso.

**Solução:** `playwright-stealth` — aplica patches no contexto do browser para remover fingerprints de automação (`navigator.webdriver`, etc).

### Problema 3: DOM virtualizado (infinite scroll)
O Google Maps **não mantém todos os reviews no DOM**. À medida que o usuário scrolla, reviews antigos são removidos do DOM e novos são adicionados — sempre mantendo ~16 elementos visíveis.

**Solução:** scroll contínuo com deduplicação por ID:
```python
seen_ids: set[str] = set()
while True:
    all_cards = page.locator("div[data-review-id][jsaction]").all()
    for card in all_cards:
        rid = await card.get_attribute("data-review-id")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            reviews.append(await _extract_card(card))
    # scroll...
```

### Problema 4: Dois elementos DOM por review
Cada review no Maps tem **dois elementos** com o mesmo `data-review-id`:
- `div[data-review-id][jsaction]` — container externo (o card real)
- `button[data-review-id][data-href]` — botão interno de link

Se usar `div[data-review-id]` (genérico), o resultado é 16 elementos para 8 reviews. O scroll usando `.last` aterrissa no botão interno, não no último card real → scroll insuficiente → reviews não carregam.

**Solução:** usar `div[data-review-id][jsaction]` para pegar **apenas** os containers externos.

### Problema 5: Stuck detection
O contador de "parado" não pode usar o count atual do DOM (sempre ~16 por virtualização). Tem que contar IDs únicos acumulados:
```python
if len(seen_ids) == last_seen:
    stuck += 1
    if stuck >= 12:  # ~24s sem novos reviews = fim
        break
else:
    stuck = 0
last_seen = len(seen_ids)
```

### Problema 6: URLs de contexto de busca
Quando o usuário pesquisa uma empresa e clica nela, a URL contém parâmetros de contexto de busca (`!2m1!1s<query>`) que mostram um painel dividido com lista de empresas — o seletor de reviews pode pegar reviews de outra empresa.

**Solução:** clicar na aba "Avaliações" explicitamente após carregar a página:
```python
await page.locator("[role=tab]").filter(has_text=re.compile(r"Avalia|Review", re.I)).first.click()
```

### Problema 7: Nome de empresa com caracteres inválidos no Windows
Empresas como `Leadlovers | Automação` têm `|` no nome → erro `[WinError 123]` ao criar diretório.

**Solução:** sanitização antes de usar o nome no filesystem:
```python
safe_name = re.sub(r'[\\/:*?"<>|]', "_", empresa_name).strip()[:60]
```

---

## Estrutura de Arquivos

```
google-reviews-web/
├── main.py              # FastAPI app, rotas, jobs, auth
├── scraper.py           # Playwright: preview_company + extract_reviews
├── excel.py             # openpyxl: gerar_excel (3 abas + gráficos)
├── requirements.txt     # Dependências Python
├── .gitignore           # Exclui output/, cookies, cache
├── static/
│   ├── index.html       # UI única (SPA simples)
│   ├── app.js           # Toda a lógica frontend
│   └── style.css        # Estilos
├── output/              # Relatórios gerados (gitignored)
│   └── <Empresa>/
│       └── <Empresa>_Relatorio_Avaliacoes_YYYYMMDD.xlsx
└── debug_reviews.py     # Script diagnóstico (não é parte do app)
```

---

## Relatório Excel — Estrutura

| Aba | Conteúdo |
|-----|---------|
| **Resumo** | Métricas (total, média, com/sem comentário, com resposta) + gráfico barras por estrelas + gráfico com/sem comentário |
| **Avaliações** | Todas as avaliações: #, Nome, Nota ★, Data, Texto, Resposta do proprietário |
| **Negativos (1-2★)** | Apenas avaliações ruins em destaque (fundo laranja/amarelo) |

**Convenções visuais:**
- Header azul escuro `#1F4E79`
- Avaliações negativas fundo `#FCE4D6` (salmão)
- Linhas zebradas `#EBF3FB`
- Estrelas coloridas por nota (verde escuro=5★ até vermelho=1★)

---

## Configuração e Deploy

### Requisitos
```bash
pip install -r requirements.txt
playwright install chromium
```

### Variáveis de ambiente (`.env`)
```
GOOGLE_CLIENT_ID=<seu-client-id>.apps.googleusercontent.com
COOKIES_FILE=../google-reviews-scraper/google_cookies.json
OUTPUT_DIR=./output
```

### Iniciar servidor
```bash
python -m uvicorn main:app --reload --port 8000
```

### Configurar sessão Google Maps
Executar uma vez (do projeto `google-reviews-scraper`):
```bash
python salvar_login.py
```

---

## Melhorias Planejadas

- [ ] Suporte a múltiplas URLs em batch
- [ ] Histórico de extrações (listar jobs anteriores)
- [ ] Filtros no Excel por data / nota mínima
- [ ] Análise de sentimento com Claude API (por review)
- [ ] Envio automático do relatório por e-mail
- [ ] Cache de resultados para evitar re-scraping da mesma empresa no mesmo dia
- [ ] Suporte a idiomas além de pt-BR

---

*Desenvolvido em março de 2026*
