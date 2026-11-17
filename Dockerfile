FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --no-audit
COPY web/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home review && install -d -o review -g review /app/data
COPY claims claims
COPY --from=frontend /web/dist web/dist
USER review
EXPOSE 8000
CMD ["uvicorn","claims.api:app","--host","0.0.0.0","--port","8000","--no-access-log"]
