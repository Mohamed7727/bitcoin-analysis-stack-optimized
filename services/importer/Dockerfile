FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy importer script
COPY importer.py .

# Create cache and state directories
RUN mkdir -p /app/cache /app/state

CMD ["python", "-u", "importer.py"]
