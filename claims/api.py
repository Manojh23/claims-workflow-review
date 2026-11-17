import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import Field, ValidationError

from .auth import Auth
from .fixtures import BLOCKED, READY
from .schemas import Decision, Model, NewCase
from .store import Problem, Store

log = logging.getLogger("uvicorn.error")
bearer = HTTPBearer(auto_error=False, bearerFormat="JWT")
# OAuth2 mode consumes access tokens issued by an external IdP; this prototype never receives an IdP password.


class LocalToken(Model):
    role: Literal["operator", "reviewer"]
    access_key: Annotated[str, Field(max_length=160)]


class Revision(Model):
    version: Annotated[int, Field(ge=1)]
    packet: NewCase


def create_app(store=None, auth=None):
    @asynccontextmanager
    async def lifespan(api):
        api.state.store = store if store is not None else Store()
        api.state.auth = auth if auth is not None else Auth()
        yield

    app = FastAPI(title="Claims Workflow Review", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(Problem)
    async def error(_, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.middleware("http")
    async def request_metadata(request, call_next):
        start = perf_counter()
        rid = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        route = getattr(request.scope.get("route"), "name", "unknown")
        log.info("request id=%s method=%s route=%s status=%s duration_ms=%d",
                 rid, request.method, route, response.status_code, 1000*(perf_counter()-start))
        return response

    async def load(request: Request, schema):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 50_000:
                raise HTTPException(413, "Request exceeds the 50 KB prototype limit.")
            data.extend(chunk)
        try:
            return schema.model_validate_json(data)
        except ValidationError as exc:
            raise HTTPException(422, "Invalid packet or request fields.") from exc

    def actor(request: Request, token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if not token:
            raise HTTPException(401, "Bearer token required.", headers={"WWW-Authenticate": "Bearer"})
        return request.app.state.auth.verify(token.credentials)

    def operator(request: Request, token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        user = actor(request, token)
        if user.role != "operator":
            raise HTTPException(403, "An operator role is required.")
        return user

    def reviewer(request: Request, token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        user = actor(request, token)
        if user.role != "reviewer":
            raise HTTPException(403, "A reviewer role is required.")
        return user

    @app.get("/api/health")
    def health():
        with app.state.store.connection() as c:
            c.execute("SELECT id FROM cases LIMIT 1")
        return {"status": "ok"}

    @app.post("/api/demo/token")
    async def demo_token(request: Request):
        item = await load(request, LocalToken)
        return {"access_token": app.state.auth.issue(item.role, item.access_key), "token_type": "bearer"}

    @app.get("/api/demo/fixtures")
    def fixtures():
        if not app.state.auth.demo:
            raise HTTPException(404, "Fixtures are disabled.")
        return {"ready": READY, "blocked": BLOCKED}

    @app.post("/api/cases", status_code=202)
    async def intake(request: Request, user=Depends(operator),
                     key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=70)] = ""):
        from starlette.concurrency import run_in_threadpool
        if not key:
            raise HTTPException(422, "Provide an Idempotency-Key header.")
        item = await load(request, NewCase)
        return await run_in_threadpool(app.state.store.create, user, key, item.model_dump())

    @app.get("/api/cases")
    def queue(user=Depends(actor)):
        return {"cases": app.state.store.list_cases(user)}

    @app.get("/api/cases/{case_id}")
    def detail(case_id: UUID, user=Depends(actor)):
        return app.state.store.read(str(case_id), user)

    @app.post("/api/cases/{case_id}/revisions", status_code=202)
    async def revise(case_id: UUID, request: Request, user=Depends(operator)):
        from starlette.concurrency import run_in_threadpool
        item = await load(request, Revision)
        return await run_in_threadpool(app.state.store.revised, str(case_id), user, item.version, item.packet.model_dump())

    @app.post("/api/cases/{case_id}/decision")
    async def decide(case_id: UUID, request: Request, user=Depends(reviewer)):
        from starlette.concurrency import run_in_threadpool
        item = await load(request, Decision)
        return await run_in_threadpool(app.state.store.decide, str(case_id), user, item)

    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
