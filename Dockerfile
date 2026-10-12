# Signaling server only (no media passes through it). Needs nothing but websockets.
FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir "websockets>=14" \
    && useradd --create-home --uid 10001 p2pshare
COPY p2pshare ./p2pshare
USER p2pshare
ENV PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-m", "p2pshare", "server"]
