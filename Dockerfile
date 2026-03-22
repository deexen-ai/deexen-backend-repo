# apps/backend/Dockerfile
FROM python:3.10-slim

WORKDIR /app

# Upgrade pip first for better download handling
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 300 --retries 10 -r requirements.txt

COPY . .

EXPOSE 8000

# Assuming entrypoint is app.main:app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
