FROM python:3.11-slim
RUN apt-get update && apt-get install -y build-essential gcc python3-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Dodajemy biblioteki Google API oraz psutil
RUN pip install --no-cache-dir \
    nicegui \
    httpx \
    sqlalchemy \
    psutil \
    pytest \
    google-api-python-client \
    google-auth-httplib2 \
    google-auth-oauthlib
COPY app.py .
CMD ["python", "app.py"]