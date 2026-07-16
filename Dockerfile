FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000
# Start the gateway. Override GATEWAY_API_KEYS at run time.
ENV GATEWAY_API_KEYS=dev-key
CMD ["uvicorn", "llmgateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
