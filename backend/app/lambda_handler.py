"""AWS Lambda entry point: API Gateway HTTP API → Mangum → FastAPI (ASGI)."""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
