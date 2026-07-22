FROM python:3.12-slim

WORKDIR /app
COPY . /app
# .[sql] = runtime deps plus the SQLAlchemy/Alembic persistence layer, and no
# test tooling. The in-memory default still works without a database.
RUN python -m pip install --no-cache-dir -e ".[sql]"

EXPOSE 8000
# No credentials or connection strings are baked into the image. A key set with
# `ENV` here would ship in a layer to everyone who pulls the image; the app also
# fails safe (an unset GATEWAY_API_KEYS accepts only the code-default dev-key,
# which is meant for the offline demo, not a deploy). Supply GATEWAY_API_KEYS and
# optionally GATEWAY_DATABASE_URL at run time via compose or the platform secrets.
CMD ["uvicorn", "llmgateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
