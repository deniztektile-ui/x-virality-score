#!/usr/bin/env python3
"""Read-only, sampled Four.meme launch feed. Python 3.8+, standard library only."""
import argparse
import getpass
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "board" / "bnb" / "data.json"
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
QUERY = '''{
  EVM(dataset: realtime, network: bsc) {
    Events(
      where: {
        Transaction: {To: {is: "0x5c952063c7fc8610ffdb798152d69f0b9550762b"}}
        Log: {Signature: {Name: {is: "TokenCreate"}}}
      }
      limit: {count: 100}
      orderBy: {descending: Block_Time}
    ) {
      Arguments {
        Name
        Value {
          ... on EVM_ABI_Address_Value_Arg { address }
          ... on EVM_ABI_String_Value_Arg { string }
          ... on EVM_ABI_Integer_Value_Arg { integer }
          ... on EVM_ABI_BigInt_Value_Arg { bigInteger }
        }
      }
      Transaction { Hash }
    }
  }
}'''


class FeedError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_events(payload):
    # Do not print remote error messages: these may contain request details.
    if not isinstance(payload, dict) or payload.get("errors"):
        raise FeedError("Bitquery rejected the query. Check API access and quota in the IDE.")
    try:
        events = payload["data"]["EVM"]["Events"]
    except (KeyError, TypeError):
        raise FeedError("Unexpected Bitquery response structure.")
    if not isinstance(events, list):
        raise FeedError("Invalid event list.")
    rows = {}
    for event in events:
        try:
            args = {a["Name"]: next(iter(a["Value"].values()), None)
                    for a in event["Arguments"] if a.get("Value")}
            token, creator = args.get("token", ""), args.get("creator", "")
            if not ADDRESS.fullmatch(token) or not ADDRESS.fullmatch(creator):
                raise ValueError()
            launch_time = datetime.fromtimestamp(int(args["launchTime"]), timezone.utc).isoformat()
            rows[token.lower()] = {
                "token": token.lower(), "creator": creator.lower(),
                "name": str(args.get("name", ""))[:200],
                "symbol": str(args.get("symbol", ""))[:80],
                "launch_time": launch_time,
            }
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, OSError):
            raise FeedError("Unrecognized TokenCreate event; feed not updated.")
    return list(rows.values())


def fetch_events(token):
    request = urllib.request.Request(
        "https://streaming.bitquery.io/graphql",
        data=json.dumps({"query": QUERY}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    # Never follow a redirect with the authentication header.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise FeedError("Response too large.")
        return parse_events(json.loads(raw))
    except urllib.error.HTTPError as error:
        labels = {401: "API token expired or invalid", 403: "API access denied", 429: "API quota or rate limit reached"}
        raise FeedError(labels.get(error.code, "Bitquery HTTP error") + " (" + str(error.code) + ").")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FeedError("Network request failed. Check your connection.")
    except (ValueError, UnicodeError):
        raise FeedError("Bitquery returned invalid JSON.")


def write_snapshot(path, snapshot):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".data-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update(token, path=OUTPUT, fetcher=fetch_events):
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                previous = {}
        except (ValueError, OSError):
            pass
    try:
        rows = fetcher(token)
        current = {"status": "ok", "last_attempt": utc_now(), "last_success": utc_now(),
                   "source": "Bitquery / Four.meme", "network": "BNB Chain",
                   "sample_limit": 100, "rows": rows}
        write_snapshot(path, current)
        print("Updated: " + str(len(rows)) + " recent launches. No trades executed.", flush=True)
        return True
    except FeedError as error:
        previous.update(status="error", error=str(error), last_attempt=utc_now())
        write_snapshot(path, previous)
        print("Feed error: " + str(error), file=sys.stderr, flush=True)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Poll every 5 minutes; uses API quota")
    args = parser.parse_args()
    token = os.environ.get("BITQUERY_TOKEN", "").strip()
    if not token:
        if not sys.stdin.isatty():
            parser.error("Run interactively to enter a Bitquery API token.")
        token = getpass.getpass("Bitquery API token (hidden; not saved): ").strip()
    if not token or any(c.isspace() for c in token):
        parser.error("API token is empty or contains whitespace.")
    try:
        while True:
            success = update(token)
            # Stop on failure; do not repeatedly spend quota or retry invalid credentials.
            if not args.watch or not success:
                return 0 if success else 1
            time.sleep(300)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
