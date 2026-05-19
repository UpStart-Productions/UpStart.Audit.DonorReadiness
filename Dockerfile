# Donor Readiness Audit — Application Container
#
# Build:  docker build -t donor-audit .
# Local:  docker run --rm --entrypoint python -e ANTHROPIC_API_KEY=sk-ant-... donor-audit main.py https://example-nonprofit.org
# Lambda: entrypoint runs awslambdaric with handler main.lambda_handler

FROM --platform=linux/arm64 python:3.11-slim

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

# Install Playwright and its Chromium browser to a fixed path
# (Lambda runs as a different user than the build user, so ~/.cache won't work)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium && chmod -R 755 /ms-playwright

# Copy application code
COPY app/ .

# Reports output directory
RUN mkdir -p /app/reports

# Lambda runtime interface — CMD specifies the handler (module.function)
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["main.lambda_handler"]
