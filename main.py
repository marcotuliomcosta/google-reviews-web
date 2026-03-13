"""
Google Reviews Web — FastAPI backend
"""
import asyncio
import concurrent.futures
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
COOKIES_FILE     = Path(os.getenv("COOKIES_FILE", "./google_cookies.json"))
OUTPUT_DIR       = Path(os.getenv("OUTPUT_DIR", "./output"))
OUTPUT_DIR.mkdir(exist_ok=True)

# Carrega cookies de variável de ambiente (base64) se existir
_cookies_b64 = os.getenv("GOOGLE_COOKIES_B64", "")
if _cookies_b64 and not COOKIES_FILE.exists():
    import base64
    COOKIES_FILE.write_bytes(base64.b64decode(_cookies_b64))

jobs: dict[str, dict] = {}
_semaphore = asyncio.Semaphore(2)

app = FastAPI(title="Google Reviews Scraper")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Helper: roda coroutine Playwright em thread com ProactorEventLoop ─────────
def _run_in_proactor(coro):
    """
    Uvicorn usa SelectorEventLoop no Windows, que não suporta subprocessos.
    Playwright precisa lançar o browser (subprocess).
    Solução: rodar em thread separada com ProactorEventLoop próprio.
    """
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _run_playwright(coro):
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run_in_proactor, coro)


# ── Rotas estáticas ────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/api/config")
async def get_config():
    return {"google_client_id": GOOGLE_CLIENT_ID}


@app.get("/api/search")
async def search(q: str = "", authorization: str = Header(default="")):
    _require_auth(authorization)
    _check_server_session()
    if not q or len(q.strip()) < 2:
        return []
    try:
        from scraper import search_companies
        results = await asyncio.wait_for(
            _run_playwright(search_companies(q.strip(), COOKIES_FILE)),
            timeout=30,
        )
        return results
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout na busca.")
    except Exception as e:
        raise HTTPException(500, f"Erro na busca: {str(e)}")


# ── Autenticação Google Sign-In ───────────────────────────────────────────────
def _require_auth(authorization: str) -> dict:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID não configurado no .env")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Faça login com o Google para continuar.")

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as g_req
        return id_token.verify_oauth2_token(token, g_req.Request(), GOOGLE_CLIENT_ID)
    except Exception as e:
        print(f"[AUTH ERROR] {type(e).__name__}: {e}")
        raise HTTPException(401, f"Sessão expirada: {e}")


def _check_server_session():
    if not COOKIES_FILE.exists() or COOKIES_FILE.stat().st_size < 10:
        raise HTTPException(
            503,
            "Sessão Google Maps não configurada no servidor. "
            "Execute salvar_login.py para configurar."
        )


# ── Preview ────────────────────────────────────────────────────────────────────
class PreviewRequest(BaseModel):
    url: str


@app.post("/api/preview")
async def preview(body: PreviewRequest, authorization: str = Header(default="")):
    _require_auth(authorization)
    _check_server_session()

    if "google.com" not in body.url or "/maps" not in body.url:
        raise HTTPException(400, "URL inválida. Use uma URL do Google Maps.")

    try:
        from scraper import preview_company
        info = await asyncio.wait_for(
            _run_playwright(preview_company(body.url, COOKIES_FILE)),
            timeout=40,
        )
        return info
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout ao carregar a página do Google Maps.")
    except Exception as e:
        raise HTTPException(500, f"Erro: {str(e)}")


# ── Extração completa ─────────────────────────────────────────────────────────
class ExtractRequest(BaseModel):
    url: str
    empresa: str


@app.post("/api/extract")
async def start_extract(
    body: ExtractRequest,
    background_tasks: BackgroundTasks,
    authorization: str = Header(default=""),
):
    _require_auth(authorization)
    _check_server_session()

    if "google.com" not in body.url or "/maps" not in body.url:
        raise HTTPException(400, "URL inválida.")

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {
        "status": "queued", "progress": 0, "total": 0,
        "message": "Na fila...", "output_file": None, "error": None,
    }
    background_tasks.add_task(_run_job, job_id, body.url, body.empresa)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job não encontrado.")
    j = jobs[job_id]
    return {k: j[k] for k in ("status", "progress", "total", "message", "error")}


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job não encontrado.")
    j = jobs[job_id]
    if j["status"] != "done" or not j["output_file"]:
        raise HTTPException(400, "Relatório ainda não está pronto.")
    path = Path(j["output_file"])
    if not path.exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(
        str(path), filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Background job ────────────────────────────────────────────────────────────
async def _run_job(job_id: str, url: str, empresa: str):
    def sync_update(progress, total, message):
        jobs[job_id].update({"progress": progress, "total": total, "message": message, "status": "running"})

    async def update(progress, total, message):
        sync_update(progress, total, message)

    async with _semaphore:
        try:
            from scraper import extract_reviews
            from excel import gerar_excel

            reviews = await _run_playwright(extract_reviews(url, COOKIES_FILE, update))
            jobs[job_id].update({
                "progress": len(reviews), "total": len(reviews),
                "message": "Gerando relatório Excel...", "status": "running",
            })
            xlsx = gerar_excel(reviews, empresa, OUTPUT_DIR)

            jobs[job_id]["status"] = "done"
            jobs[job_id]["output_file"] = str(xlsx)
            jobs[job_id]["message"] = f"Concluído! {len(reviews)} reviews extraídos."

        except Exception as e:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(e)
            jobs[job_id]["message"] = f"Erro: {str(e)}"


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
