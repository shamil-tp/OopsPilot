FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --no-create-home opspilot
USER opspilot

EXPOSE 8000

# Migrations are not applied on start: the Supabase database is shared by the whole team, so
# schema changes are run deliberately (`alembic upgrade head`).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
