FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/scripts/start_api_with_migrations.sh

EXPOSE 8000

CMD ["/app/scripts/start_api_with_migrations.sh"]
