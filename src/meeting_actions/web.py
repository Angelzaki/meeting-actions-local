from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from meeting_actions.pipeline import Pipeline, list_meeting_files
from meeting_actions.report import build_data, render_markdown

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MAX_CHARS = 200_000


class AnalyzeBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    source: str = "pegado"
    save: bool = True
    link_kb: bool = True


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    pipe = pipeline or Pipeline()
    app = FastAPI(title="meeting-actions-local", version="0.1.0")

    def _samples() -> list[str]:
        return [p.name for p in list_meeting_files(pipe.config.meetings_dir)]

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "samples": _samples(),
                "kb_docs": pipe.kb.docs if pipe.kb else [],
                "history": pipe.store.history(8),
            },
        )

    @app.get("/api/health")
    def health():
        return {"ok": True, "kb_docs": pipe.kb_docs, "samples": len(_samples())}

    def _payload(result, markdown: bool = True):
        as_of = date.today()
        data = build_data([result], as_of)
        out = result.to_dict(as_of)
        out["board"] = {k: v for k, v in data["board"].items()}
        out["as_of"] = data["as_of"]
        if markdown:
            out["markdown"] = render_markdown([result], as_of)
        return out

    @app.post("/api/analyze")
    def analyze(body: AnalyzeBody):
        result = pipe.analyze_text(
            body.text, source=body.source or "pegado", record=body.save, link_kb=body.link_kb
        )
        return JSONResponse(_payload(result))

    @app.get("/api/samples")
    def samples():
        return {"samples": _samples()}

    @app.post("/api/samples/{name}")
    def analyze_sample(name: str, save: bool = True):
        if name not in _samples():
            raise HTTPException(404, "acta no encontrada")
        path = pipe.config.meetings_dir / name
        return JSONResponse(_payload(pipe.analyze_file(path, record=save)))

    @app.get("/api/samples/{name}/text", response_class=PlainTextResponse)
    def sample_text(name: str):
        if name not in _samples():
            raise HTTPException(404, "acta no encontrada")
        return (pipe.config.meetings_dir / name).read_text(encoding="utf-8")

    @app.get("/api/history")
    def history(limit: int = 20, source: str | None = None):
        return {"runs": pipe.store.history(min(limit, 200), source), "owners": pipe.store.owner_load()}

    @app.get("/api/runs/{run_id}")
    def run(run_id: int):
        data = pipe.store.get_run(run_id)
        if data is None:
            raise HTTPException(404, "corrida no encontrada")
        return data

    @app.get("/api/audit")
    def audit(limit: int = 30):
        return {"events": pipe.store.audit_events(min(limit, 200))}

    return app


def app_factory() -> FastAPI:
    return create_app()
