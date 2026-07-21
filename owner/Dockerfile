FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/owner-server.txt requirements/development.txt /app/requirements/
RUN pip install --no-cache-dir -r requirements/development.txt

COPY owner/ /app/owner/
WORKDIR /app/owner

ENV FLASK_APP=app:create_app
ENV OWNER_ENV=production

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:create_app()"]
