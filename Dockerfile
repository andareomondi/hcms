FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Python dependencies first, so this layer is cached between code changes.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY . .

# Build-time only: collectstatic imports settings, which requires these variables.
# They are placeholders, never used at runtime, and not stored in the image.
RUN DJANGO_SECRET_KEY=build-only DB_NAME=build DB_USER=build DB_PASSWORD=build \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media /app/private_exports \
    && chown -R appuser:appuser /app/media /app/private_exports
USER appuser

EXPOSE 8000

# Migrations and seeding are separate steps, run once per deployment:
#   docker run --rm <image> python manage.py migrate
#   docker run --rm <image> python seed_db.py
CMD ["gunicorn", "hospital_system.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", \
     "--access-logfile", "-"]
