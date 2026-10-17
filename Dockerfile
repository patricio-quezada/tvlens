# The image Fly.io runs. Python matches the dev box; requires-python is >=3.12.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Requirements before the code, so a code change reuses the installed layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic needs settings to load, and settings refuse to load without a
# key when DEBUG is off. This key exists only for the build step; the running
# app gets the real one from Fly's secrets.
RUN DEBUG=False SECRET_KEY=build-only-not-a-secret python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["./deploy/start.sh"]
