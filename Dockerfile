# Donor Readiness Audit — Application Container
#
# Build:  docker build -t donor-audit .
# Run:    docker run --rm -e ANTHROPIC_API_KEY=sk-ant-... donor-audit https://example-nonprofit.org

FROM python:3.11-slim

# Install system dependencies for Playwright/Chromium
RUN apt-get update && apt-get install -y \
    # Chromium runtime deps
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its Chromium browser
RUN playwright install chromium

# Copy application code
COPY app/ .

# Reports output directory
RUN mkdir -p /app/reports

ENTRYPOINT ["python", "main.py"]
