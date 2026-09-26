import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bnb_monitor import FeedError, parse_events, update


def event():
    values = {"creator": {"address": "0x" + "1" * 40},
              "token": {"address": "0x" + "2" * 40},
              "name": {"string": "<script>test</script>"},
              "symbol": {"string": "TEST"},
              "launchTime": {"bigInteger": "1790446211"}}
    return {"Arguments": [{"Name": k, "Value": v} for k, v in values.items()],
            "Transaction": {"Hash": "0x" + "3" * 64}}


class FeedTests(unittest.TestCase):
    def test_creator_comes_from_event_and_duplicates_collapse(self):
        row = event()
        rows = parse_events({"data": {"EVM": {"Events": [row, row]}}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["creator"], "0x" + "1" * 40)

    def test_graphql_partial_error_is_not_success(self):
        with self.assertRaises(FeedError):
            parse_events({"errors": [{"message": "secret"}], "data": {"EVM": {"Events": []}}})

    def test_missing_creator_cannot_be_guessed(self):
        row = event()
        row["Arguments"] = [a for a in row["Arguments"] if a["Name"] != "creator"]
        with self.assertRaises(FeedError):
            parse_events({"data": {"EVM": {"Events": [row]}}})

    def test_error_preserves_last_success_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with patch("builtins.print"):
                update("private-token", path, lambda token: [{"token": "original"}])
                old = json.loads(path.read_text())
                def fail(token):
                    raise FeedError("Network failure")
                self.assertFalse(update("private-token", path, fail))
            new = json.loads(path.read_text())
            self.assertEqual(new["rows"], old["rows"])
            self.assertEqual(new["last_success"], old["last_success"])
            self.assertEqual(new["status"], "error")
            self.assertNotIn("private-token", path.read_text())


if __name__ == "__main__":
    unittest.main()
