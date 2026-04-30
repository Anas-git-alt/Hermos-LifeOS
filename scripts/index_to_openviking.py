#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

try:
    import openviking as ov
except Exception as e:
    print("ERROR: Could not import openviking.", file=sys.stderr)
    print("Activate the OpenViking venv first:", file=sys.stderr)
    print("  source ~/.local/share/openviking-venv/bin/activate", file=sys.stderr)
    print(f"Original import error: {e}", file=sys.stderr)
    sys.exit(1)


def make_client():
    url = os.environ.get("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    api_key = os.environ.get("OPENVIKING_API_KEY") or None
    timeout = float(os.environ.get("OPENVIKING_TIMEOUT", "300"))

    client = ov.SyncHTTPClient(url=url, api_key=api_key, timeout=timeout)

    init = getattr(client, "initialize", None)
    if callable(init):
        init()

    return client


def add_resource(client, path, target, reason, timeout):
    kwargs = {
        "path": path,
        "reason": reason,
        "wait": True,
        "timeout": timeout,
    }

    if target:
        # Newer docs use target=, API table also mentions to= in some places.
        # Try target first, then to if this SDK version expects that.
        try:
            return client.add_resource(
                **kwargs,
                target=target,
                build_index=True,
                summarize=True,
            )
        except TypeError:
            return client.add_resource(
                **kwargs,
                to=target,
            )

    return client.add_resource(**kwargs)


def main():
    parser = argparse.ArgumentParser(
        description="Index a local file or directory into OpenViking."
    )
    parser.add_argument("path", help="Local file or directory path")
    parser.add_argument("--target", default=None, help="viking://resources/... target URI")
    parser.add_argument("--reason", default="Life OS indexed resource")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    source_path = Path(args.path).expanduser().resolve()

    if not source_path.exists():
        print(f"ERROR: Path does not exist: {source_path}", file=sys.stderr)
        return 2

    if args.target and not args.target.startswith("viking://resources/"):
        print("ERROR: target must start with viking://resources/", file=sys.stderr)
        return 2

    client = make_client()

    try:
        result = add_resource(
            client=client,
            path=str(source_path),
            target=args.target,
            reason=args.reason,
            timeout=args.timeout,
        )
        print(result)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
