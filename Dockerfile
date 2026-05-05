# Use a lightweight Python image
FROM python:3.11-slim

# Install system dependencies and the Docker CLI
RUN apt-get update && apt-get install -y \
    curl \
    docker.io \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install gunicorn for production serving
RUN pip install gunicorn

# Copy the rest of your application code
COPY . .

# Expose the port your app runs on
EXPOSE 5000

# Run the application using gunicorn
# Adjust 'app:app' if your Flask instance has a different name (e.g., main:app)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--worker-class", "gevent", "--timeout", "120", "app:app"]