"""One command: python -m app   (then open the printed address)."""
import argparse
import os

from .server import serve
from .service import NudgeService, StateError


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app", description="Nudge: simulated voice shopping demo")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="loopback only (127.0.0.1, localhost or ::1)")
    ap.add_argument("--data-dir", default=".nudge-data", help="where receipts, checkpoint and saved state live")
    ap.add_argument("--reset-demo", action="store_true",
                    help="DEMO ONLY: delete the receipts and saved state in --data-dir and start over")
    args = ap.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
    path = os.path.join(args.data_dir, "receipts.jsonl")
    if args.reset_demo:   # explicit and demo only: remove the files first so a damaged state can be cleared
        cp = os.path.join(args.data_dir, "checkpoint", "receipts.jsonl.checkpoint.json")
        for f in (path, path + ".state.json", path + ".checkpoint.json", cp):
            if os.path.exists(f):
                os.remove(f)
    try:
        svc = NudgeService(path)
    except StateError as e:
        raise SystemExit(f"Cannot start: {e}\nTo start over on purpose, run again with --reset-demo.")
    try:
        httpd = serve(args.host, args.port, svc)
    except ValueError as e:
        raise SystemExit(str(e))
    print(f"Nudge simulation running at http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    print("Restored from saved state." if svc.restored else "Started a fresh demo profile.")
    print("Simulation only: mock payments, sample products, no external store or payment calls.")
    print(f"Host token for an MCP client (can propose, cannot approve): {httpd.nudge_agent_token}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
