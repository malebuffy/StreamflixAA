FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY config.default.json .
COPY logo.png .

# Create default config.json from template if not mounted
RUN cp config.default.json config.json

# Default media directories inside the container
RUN mkdir -p /media/movies /media/tvshows

EXPOSE 8642

CMD ["python", "server.py"]
