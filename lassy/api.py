"""Optional HTTP adapter for the same service used by native MCP."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .concurrency import BusyError
from .documents import DocumentRequest
from .service import open_service
from .types import QueryRequest


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > 8 * 1024 * 1024:
                from starlette.exceptions import HTTPException

                raise HTTPException(413, "Request body must be at most 8 MiB")
            return message

        await self.app(scope, limited_receive, send)


def create_app(service=None):
    @asynccontextmanager
    async def lifespan(application):
        if service is not None:
            application.state.service = service
            yield
        else:
            try:
                async with open_service() as runtime:
                    application.state.service = runtime
                    yield
            except Exception:
                logging.getLogger(__name__).critical(
                    "Lassy startup failed. Check explicit configuration and index migration; run lassy-models prepare for missing models."
                )
                raise RuntimeError(
                    "Lassy startup failed. Check explicit configuration, index migration, and prepared models."
                ) from None

    application = FastAPI(
        title="Lassy document retrieval", version="0.2.0", lifespan=lifespan
    )
    application.add_middleware(BodyLimitMiddleware)

    @application.get("/health")
    async def health(request: Request):
        try:
            return await request.app.state.service.status()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"detail": "Local document index is unavailable"},
            )

    @application.post("/query")
    async def query(body: QueryRequest, request: Request):
        return await request.app.state.service.search(body)

    @application.put("/documents/{collection}/{document_id}")
    async def put_document(
        collection: str, document_id: str, body: DocumentRequest, request: Request
    ):
        return await request.app.state.service.upsert(collection, document_id, body)

    @application.delete("/documents/{collection}/{document_id}")
    async def delete_document(collection: str, document_id: str, request: Request):
        return await request.app.state.service.delete(collection, document_id)

    @application.exception_handler(BusyError)
    async def busy(_request, _exc):
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Local inference or this document is busy. Retry shortly."
            },
            headers={"Retry-After": "30"},
        )

    async def invalid(_request, _exc):
        # Never echo document text, credentials, or Pydantic's input values.
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid query, document, filters, or input limits"},
        )

    for error in (RequestValidationError, ValidationError, ValueError):
        application.add_exception_handler(error, invalid)

    @application.exception_handler(Exception)
    async def unavailable(_request, _exc):
        logging.getLogger(__name__).error(
            "Document request failed (%s)", type(_exc).__name__
        )
        return JSONResponse(
            status_code=500, content={"detail": "Local document service is unavailable"}
        )

    return application


app = create_app()


def main():
    import uvicorn

    from .config import load_settings

    try:
        settings = load_settings()
    except Exception:
        raise SystemExit(
            "Lassy API configuration is invalid. Check its explicit environment settings."
        ) from None
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    uvicorn.run(
        app, host=settings.api_host, port=settings.api_port, log_level="critical"
    )


if __name__ == "__main__":
    main()
