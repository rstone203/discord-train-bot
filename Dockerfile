# Discord Bot Deployment
FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY pyproject.toml uv.lock ./
RUN pip install -e .

# Copy application code.
# Note: .dockerignore excludes backups/ and *.backup / *.bak / *.dump / *.sql.gz
# so operational backup data is never included in the image even if accidentally
# present in the build context.
COPY . .

# Expose port
EXPOSE 5000

# Run the application
CMD ["python", "main.py"]
