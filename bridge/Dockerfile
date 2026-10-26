FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git openssh-client docker.io bash nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 bridge
RUN mkdir -p /home/bridge/.claude && chown bridge:bridge /home/bridge/.claude
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge.py ./
COPY backends/ ./backends/
USER bridge
ENV MC_WORKSPACE=/projects
CMD ["python", "bridge.py", "--game-dir", "/game", "--listen", "0.0.0.0"]
