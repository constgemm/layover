# Layover — pure-stdlib Python, so the image is tiny and has nothing to pip-install.
# Pinned base tag for reproducibility (bump deliberately, per the homelab upgrade rule).
FROM python:3.12-slim-bookworm

# tzdata only: lets the stdlib scheduler fire at the right *local* time, DST-aware.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    LAYOVER_OUT=/data

WORKDIR /app

# Copy only the code (no secrets, no output — see .dockerignore).
COPY layover.py flightparse.py airdata.py airtrail.py llm.py notify.py populate.py scheduler.py ./

# Run unprivileged; /data (state.json watermark + candidates) is a persisted volume.
RUN useradd --system --uid 10001 --home-dir /app layover \
    && mkdir -p /data \
    && chown -R layover:layover /data
USER layover

VOLUME ["/data"]

# PID-1-friendly loop: schedule weekly, run populate (digest only), never write.
ENTRYPOINT ["python3", "scheduler.py"]
