#!/usr/bin/env python3
"""End-to-end: a real socket, a real IMAP conversation, a real subprocess.

The daemon is started the way the plugin starts it and its stdout is read the
way Service.qml reads it. Mail is then delivered into the fake server mid-IDLE,
which is the one path — connect, baseline, block, wake, fetch, classify, emit —
that the unit tests cannot cover.

    python3 tests/test_integration.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_imap import FakeIMAP  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "bin", "herald-daemon")

OTP_MAIL = b"""From: Google <no-reply@accounts.google.com>
Subject: Your Google verification code
Message-ID: <otp-1@google.com>
Date: Wed, 16 Sep 2026 12:00:00 +0000
Content-Type: text/plain; charset=utf-8

Your verification code is 281940. It expires in 10 minutes.
"""

TXN_MAIL = b"""From: HDFC Bank <alerts@hdfcbank.net>
Subject: Transaction alert
Message-ID: <txn-1@hdfcbank.net>
Date: Wed, 16 Sep 2026 12:05:00 +0000
Content-Type: text/plain; charset=utf-8

Your card ending 4821 was debited Rs.1,249.00 at Blue Tokai Coffee.
"""

STYLED_MAIL = b"""From: HDFC Bank <alerts@hdfcbank.net>
Subject: Transaction alert
Message-ID: <styled-1@hdfcbank.net>
Date: Wed, 16 Sep 2026 12:07:00 +0000
Content-Type: text/html; charset=utf-8

<html><head><style type="text/css">
@import url('https://fonts.example/x.css');
body,table,td,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
.wrapper{width:100%!important;max-width:600px;margin:0 auto;background:#ffffff}
.btn a{display:inline-block;padding:12px 24px;border-radius:4px;background:#0a7c3f}
@media screen and (max-width:600px){.stack{display:block!important;width:100%!important}}
</style>
<!--[if mso]><style>.fallback{font-family:Arial,sans-serif!important}</style><![endif]-->
</head><body style="margin:0;padding:0;background:#f4f4f4">
<table class="wrapper"><tr><td style="padding:24px;font-family:Helvetica;color:#111">
<p>Your card ending 4821 was debited Rs.1,249.00 at BLUE TOKAI COFFEE.</p>
</td></tr></table>
<style>.footer{color:#999;font-size:11px}</style>
<div class="footer">Automated message.</div>
</body></html>
"""

NEWSLETTER = b"""From: Deals <newsletter@shop.example>
Subject: 40% off everything this weekend
Message-ID: <news-1@shop.example>
Date: Wed, 16 Sep 2026 12:06:00 +0000
Content-Type: text/plain; charset=utf-8

Our biggest sale of the year is here. Shop now.
"""


class _EchoLLM(BaseHTTPRequestHandler):
    """Answers with whatever the local heuristics already guessed.

    The point of this test is the IMAP path, so the model is made boring on
    purpose: it agrees with the hint, which keeps the assertions about
    plumbing rather than about anybody's prompt.
    """

    prompts = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = payload["messages"][-1]["content"]
        _EchoLLM.prompts.append(prompt)
        guess = "other"
        for line in prompt.splitlines():
            if line.startswith("Local guess"):
                guess = line.split(":", 1)[1].strip().split(" ")[0]
        code = "281940" if guess == "otp" else ""
        body = json.dumps({"choices": [{"message": {"content": json.dumps({
            "category": guess, "title": "Test", "headline": "headline",
            "detail": "detail", "code": code, "amount": "Rs.1,249.00" if guess == "transaction" else "",
            "merchant": "Blue Tokai Coffee" if guess == "transaction" else "",
            "account_hint": "", "expires_minutes": 10 if guess == "otp" else 0,
            "confidence": 0.9})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DaemonHarness:
    """Starts the daemon and collects its JSON lines off a reader thread."""

    def __init__(self, config_path, home, extra_args=()):
        env = dict(os.environ)
        env.update({
            "HOME": home,
            "HERALD_IMAP_PASSWORD": "hunter2",
            "HERALD_API_KEY": "sk-test",
            "PYTHONUNBUFFERED": "1",
        })
        self.proc = subprocess.Popen(
            [sys.executable, DAEMON, "--config", config_path, *extra_args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        self.lines = []
        self.lock = threading.Condition()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for raw in self.proc.stdout:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            with self.lock:
                self.lines.append(row)
                self.lock.notify_all()

    def wait_for(self, predicate, timeout=15):
        with self.lock:
            deadline = self.lock.wait_for(
                lambda: any(predicate(r) for r in self.lines), timeout=timeout)
        if not deadline:
            raise AssertionError(
                "timed out waiting; saw:\n" + "\n".join(json.dumps(r) for r in self.lines)
                + "\nstderr: " + self.stderr())
        with self.lock:
            return [r for r in self.lines if predicate(r)]

    def stderr(self):
        if self.proc.poll() is None:
            return "(still running)"
        return self.proc.stderr.read()

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def is_status(state):
    return lambda r: r.get("t") == "status" and r.get("state") == state


def is_event(**fields):
    return lambda r: r.get("t") == "event" and all(r.get(k) == v for k, v in fields.items())


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.imap = FakeIMAP(messages=[])
        self.llm = HTTPServer(("127.0.0.1", 0), _EchoLLM)
        threading.Thread(target=self.llm.serve_forever, daemon=True).start()
        self.harness = None

    def tearDown(self):
        if self.harness:
            self.harness.stop()
        self.imap.stop()
        self.llm.shutdown()
        self.tmp.cleanup()

    def write_config(self, **scan):
        config = {
            "version": 1,
            "imap": {"host": "127.0.0.1", "port": self.imap.port,
                     "username": "watcher@example.com", "mailbox": "INBOX",
                     "ssl": False, "starttls": False},
            "llm": {"enabled": True, "model": "test",
                    "baseUrl": f"http://127.0.0.1:{self.llm.server_port}/v1",
                    "timeoutSeconds": 10},
            "scan": {"backfill": 0, "maxPerCycle": 12, "pollSeconds": 30, **scan},
        }
        path = os.path.join(self.home, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        return path

    def test_mail_arriving_during_idle_becomes_an_event(self):
        self.imap.messages = [NEWSLETTER]     # already there: must not be announced
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        # Not just "watching": the watcher takes its baseline before it parks
        # in IDLE, and a message delivered inside that window is mail that was
        # already there, which backfill 0 deliberately ignores.
        self.assertTrue(self.imap.wait_until_idle(), "watcher never reached IDLE")

        self.imap.deliver(OTP_MAIL)
        [event] = self.harness.wait_for(is_event(category="otp"))

        self.assertEqual(event["code"], "281940")
        self.assertEqual(event["uid"], 2)
        self.assertEqual(event["subject"], "Your Google verification code")
        self.assertIn("accounts.google.com", event["senderAddress"])
        self.assertEqual(event["source"], "llm")
        # backfill 0: the message that predates the watcher stays unannounced.
        self.assertEqual([r for r in self.harness.lines if r.get("t") == "event"], [event])

    def test_two_deliveries_are_both_picked_up_and_not_repeated(self):
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        self.assertTrue(self.imap.wait_until_idle())

        self.imap.deliver(OTP_MAIL)
        self.harness.wait_for(is_event(category="otp"))
        self.imap.deliver(TXN_MAIL)
        [txn] = self.harness.wait_for(is_event(category="transaction"))

        self.assertEqual(txn["uid"], 2)
        self.assertEqual(txn["amount"], "Rs.1,249.00")
        events = [r for r in self.harness.lines if r.get("t") == "event"]
        self.assertEqual(len(events), 2, "a UID must not be classified twice")

    def test_the_denylist_is_applied_before_the_model_is_called(self):
        config = self.write_config(senderDenylist=["newsletter@"])
        self.harness = DaemonHarness(config, self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        self.assertTrue(self.imap.wait_until_idle())

        self.imap.deliver(NEWSLETTER)
        self.harness.wait_for(lambda r: r.get("t") == "log" and "denylist" in r.get("msg", ""))

        self.imap.deliver(OTP_MAIL)
        self.harness.wait_for(is_event(category="otp"))
        events = [r for r in self.harness.lines if r.get("t") == "event"]
        self.assertEqual(len(events), 1)

    def test_events_survive_a_restart_through_the_history_file(self):
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        self.assertTrue(self.imap.wait_until_idle())
        self.imap.deliver(OTP_MAIL)
        self.harness.wait_for(is_event(category="otp"))
        self.harness.stop()

        history = os.path.join(self.home, ".local/state/omarchy/herald/events.jsonl")
        rows = [json.loads(l) for l in open(history, encoding="utf-8") if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "281940")
        # A live code sits in this file; it must not be world-readable.
        self.assertEqual(os.stat(history).st_mode & 0o077, 0)

        # A fresh watcher resumes from the recorded UID rather than replaying.
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        self.assertTrue(self.imap.wait_until_idle())
        self.imap.deliver(TXN_MAIL)
        self.harness.wait_for(is_event(category="transaction"))
        self.assertEqual(
            len([r for r in self.harness.lines if r.get("t") == "event"]), 1,
            "the already-classified message must not be announced again")

    def test_backfill_classifies_what_is_already_there(self):
        self.imap.messages = [NEWSLETTER, OTP_MAIL]
        self.harness = DaemonHarness(self.write_config(backfill=2), self.home, ["--dry-run"])
        self.harness.wait_for(is_event(category="otp"))
        events = self.harness.wait_for(lambda r: r.get("t") == "event")
        self.assertEqual(len(events), 2)

    def test_once_mode_classifies_and_exits(self):
        self.imap.messages = [NEWSLETTER, OTP_MAIL]
        self.harness = DaemonHarness(self.write_config(), self.home, ["--once", "1", "--dry-run"])
        self.harness.wait_for(is_event(category="otp"))
        self.assertEqual(self.harness.proc.wait(timeout=15), 0)

    def test_a_rejected_login_stops_instead_of_hammering_the_server(self):
        # Retrying a password the server keeps refusing is how an account gets
        # locked, so the watcher gives up and says so rather than looping.
        self.imap.password = "different"
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(
            lambda r: r.get("t") == "status" and "not retrying" in r.get("detail", ""),
            timeout=40)
        self.assertEqual(self.harness.proc.wait(timeout=15), 4)

        attempts = [r for r in self.harness.lines
                    if r.get("t") == "status" and r.get("state") == "connecting"]
        self.assertEqual(len(attempts), 3, "should stop at MAX_AUTH_FAILURES")

    def test_no_stylesheet_reaches_the_model(self):
        _EchoLLM.prompts.clear()
        self.harness = DaemonHarness(self.write_config(), self.home, ["--dry-run"])
        self.harness.wait_for(is_status("watching"))
        self.assertTrue(self.imap.wait_until_idle())

        self.imap.deliver(STYLED_MAIL)
        self.harness.wait_for(lambda r: r.get("t") == "event")

        self.assertTrue(_EchoLLM.prompts, "the model was never asked")
        prompt = _EchoLLM.prompts[-1]

        # The words survive.
        self.assertIn("4821", prompt)
        self.assertIn("Rs.1,249.00", prompt)
        self.assertIn("BLUE TOKAI COFFEE", prompt)

        # The stylesheet does not — element, comment-hidden block, at-rules,
        # inline attributes, and the digits they drag along.
        for token in ("!important", "@media", "@import", "max-width", "webkit",
                      "border-radius", "#ffffff", "#0a7c3f", "font-family",
                      "Helvetica", "color:#999", "mso", "600px", "<style"):
            self.assertNotIn(token, prompt, f"leaked {token!r} to the model")

    def test_missing_credentials_park_the_watcher_rather_than_retry(self):
        config = json.load(open(self.write_config(), encoding="utf-8"))
        config["imap"]["host"] = ""
        path = os.path.join(self.home, "config.json")
        json.dump(config, open(path, "w", encoding="utf-8"))

        self.harness = DaemonHarness(path, self.home, ["--dry-run"])
        [status] = self.harness.wait_for(is_status("unconfigured"))
        self.assertIn("required", status["detail"])
        self.assertEqual(self.harness.proc.wait(timeout=15), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
