# Sabi for a container host (Render). Settings come from the host's environment, not .env.
FROM python:3.12-slim

# ffmpeg: voice notes to WAV for Muse Voice Transcribe. DejaVu: the ₦ sign on flyers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
# Only the dependencies: the code runs from /app, not as an installed package.
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
        > requirements.txt \
    && pip install --no-cache-dir -r requirements.txt
# Sabi's voice (Meta MMS text-to-speech), pinned and checksummed
COPY scripts/get_voice.py ./scripts/get_voice.py
RUN python scripts/get_voice.py
COPY app ./app
COPY seed ./seed
COPY web ./web

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
