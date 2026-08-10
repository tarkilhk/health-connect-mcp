FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY health_hub/ ./health_hub/

RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "-m", "health_hub"]
