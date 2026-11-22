FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOOP_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py index.html script.js styles.css ./
RUN adduser --disabled-password --gecos "" --uid 10001 loop \
    && chown -R loop:loop /app

USER loop
EXPOSE 8000
CMD ["python", "server.py"]
