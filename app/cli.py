from __future__ import annotations

import argparse
import json

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Literature Search Agent Phases A+B")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "docs": f"http://{args.host}:{args.port}/docs",
                "health": f"http://{args.host}:{args.port}/health",
            },
            ensure_ascii=False,
        )
    )
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
