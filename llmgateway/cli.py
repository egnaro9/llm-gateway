"""CLI: ``python -m llmgateway.cli serve`` (uvicorn) or ``... demo`` (offline).

``demo`` drives the gateway with the in-process test client so you can see the
full request -> cache-hit -> metrics story without starting a server.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List


def _demo() -> int:
    from fastapi.testclient import TestClient

    from .app import create_app

    client = TestClient(create_app())
    h = {"Authorization": "Bearer dev-key"}
    body = {"model": "mock-1", "messages": [{"role": "user", "content": "hello gateway"}]}

    r1 = client.post("/v1/chat/completions", json=body, headers=h).json()
    r2 = client.post("/v1/chat/completions", json=body, headers=h).json()  # cache hit
    print("first call :", r1["choices"][0]["message"]["content"])
    print("           :", f"cached={r1['cached']} cost=${r1['cost_usd']}")
    print("second call:", f"cached={r2['cached']} (served from cache)")
    print("\nmetrics:")
    print(json.dumps(client.get("/metrics").json(), indent=2))
    return 0


def _serve(host: str, port: int) -> int:  # pragma: no cover - needs a server
    import uvicorn

    uvicorn.run("llmgateway.app:app", host=host, port=port, reload=False)
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="llmgateway")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the gateway with uvicorn")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("demo", help="drive the gateway offline and print metrics")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args.host, args.port)
    if args.cmd == "demo":
        return _demo()
    p.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
