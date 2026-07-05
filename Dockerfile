FROM python:3.12-slim

WORKDIR /app

# La aplicación usa solo la librería estándar de Python:
# Sin dependencias que instalar.
COPY src/ ./src/
COPY data/ ./data/

ENV DATA_DIR=/app/data \
    DB_PATH=/app/output/revalidation.db

RUN mkdir -p /app/output

CMD ["python", "src/main.py"]
