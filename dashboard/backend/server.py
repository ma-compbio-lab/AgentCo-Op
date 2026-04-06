from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from dashboard.backend.routes import runs, topology, logs, config, skills

app = FastAPI(title="AgentCo-Op Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(topology.router)
app.include_router(logs.router)
app.include_router(config.router)
app.include_router(skills.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat")
async def chat_endpoint(payload: dict = Body(...)):
    task_description = payload.get("task", "")
    if not task_description:
        return {"error": "No task provided"}
    return {
        "summary": f"Chat execution is not yet connected to the workflow engine. Task received: {task_description}",
        "status": "placeholder",
    }


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="AgentCo-Op Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--runs-dir", nargs="*", default=["runs", "runs-test", "runs-final", "runs-opt", "runs-clean"])
    args = parser.parse_args()

    run_dirs = [Path(d).resolve() for d in args.runs_dir]
    runs.set_run_dirs(run_dirs)

    # Serve frontend build if it exists
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
