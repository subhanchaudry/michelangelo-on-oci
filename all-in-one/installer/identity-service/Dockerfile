FROM python@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe AS runtime-security
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --chmod=0644 requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes --no-deps --requirement /app/requirements.lock && pip check
COPY --chmod=0644 server.py /app/server.py
USER 1000:1000
EXPOSE 8080 8081
ENTRYPOINT ["python", "/app/server.py", "--config", "/etc/identity/config.json"]
