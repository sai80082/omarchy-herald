#!/usr/bin/env python3
"""Unit tests for herald-daemon's pure parts.

Everything here runs without a mail server. The LLM test stands up a real
HTTP server on localhost so the request shape — the URL, the auth header, the
json_schema block, and the fallback when a server rejects it — is exercised
against something that can actually reject it.

    python3 tests/test_daemon.py
"""

import email.message
import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_loader(
    "herald_daemon",
    importlib.machinery.SourceFileLoader("herald_daemon", os.path.join(ROOT, "bin", "herald-daemon")),
)
herald = importlib.util.module_from_spec(spec)
spec.loader.exec_module(herald)


def meta(subject, sender="Google <no-reply@accounts.google.com>"):
    return {
        "uid": 1, "subject": subject, "from_display": sender,
        "from_name": sender.split(" <")[0], "from_address": sender.split("<")[-1].strip(">"),
        "from_domain": "accounts.google.com", "date": "", "message_id": "<x@y>",
    }


class TestHeuristics(unittest.TestCase):
    def test_otp_is_found_and_the_code_extracted(self):
        v = herald.heuristic_verdict(
            meta("Your verification code"),
            "Your one-time code is 281940. It expires in 10 minutes.")
        self.assertEqual(v["category"], "otp")
        self.assertEqual(v["code"], "281940")

    def test_a_bare_number_is_not_an_otp(self):
        # No OTP vocabulary anywhere: 4 digits alone must not become a code.
        v = herald.heuristic_verdict(meta("Team offsite"), "See you at 1430 in room 220.")
        self.assertEqual(v["category"], "other")
        self.assertEqual(v["code"], "")

    def test_transaction_captures_the_amount(self):
        v = herald.heuristic_verdict(
            meta("Transaction alert", "HDFC <alerts@hdfcbank.net>"),
            "Your card ending 4821 was debited Rs.1,249.00 at Blue Tokai.")
        self.assertEqual(v["category"], "transaction")
        self.assertIn("1,249.00", v["amount"])
        self.assertEqual(v["account_hint"], "••4821")

    def test_security_alert(self):
        v = herald.heuristic_verdict(
            meta("Security alert", "GitHub <noreply@github.com>"),
            "We noticed a new sign-in to your account from Firefox.")
        self.assertEqual(v["category"], "security")

    def test_otp_wins_over_transaction_when_both_read(self):
        # Bank OTPs mention both a code and an amount; the code is the thing
        # the user needs in the next thirty seconds.
        v = herald.heuristic_verdict(
            meta("OTP for your payment", "HDFC <alerts@hdfcbank.net>"),
            "Use OTP 553201 to authorise the payment of Rs.4,500 to Zomato.")
        self.assertEqual(v["category"], "otp")
        self.assertEqual(v["code"], "553201")


class TestBodyExtraction(unittest.TestCase):
    def test_html_only_mail_is_reduced_to_its_words(self):
        msg = email.message.EmailMessage()
        msg["Subject"] = "Code"
        msg.set_content(
            "<html><head><style>p{color:red}</style></head>"
            "<body><table><tr><td>Your code is <b>481920</b></td></tr></table>"
            "<script>track()</script></body></html>",
            subtype="html")
        text = herald.message_body(msg, 4000)
        self.assertIn("481920", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("track()", text)

    def test_plain_text_is_preferred_over_the_html_alternative(self):
        msg = email.message.EmailMessage()
        msg.set_content("PLAIN VERSION with code 111222")
        msg.add_alternative("<p>HTML VERSION</p>", subtype="html")
        text = herald.message_body(msg, 4000)
        self.assertIn("PLAIN VERSION", text)
        self.assertNotIn("HTML VERSION", text)

    def test_a_browser_stub_yields_to_the_html_part(self):
        # "View this email in your browser" is not the message; the code is.
        msg = email.message.EmailMessage()
        msg.set_content("View this email in your browser")
        msg.add_alternative("<p>Your security code is 774411</p>", subtype="html")
        self.assertIn("774411", herald.message_body(msg, 4000))

    def test_body_is_truncated_to_the_configured_ceiling(self):
        msg = email.message.EmailMessage()
        msg.set_content("x" * 9000)
        self.assertEqual(len(herald.message_body(msg, 500)), 500)


class TestCssRemoval(unittest.TestCase):
    """No stylesheet reaches the model, by any of the routes one can arrive."""

    def test_style_and_script_elements_are_dropped(self):
        text = herald.html_to_text(
            "<style>.a{color:red}</style><script>track()</script><p>Code 4471</p>")
        self.assertIn("4471", text)
        self.assertNotIn("color:red", text)
        self.assertNotIn("track()", text)

    def test_outlook_conditional_stylesheets_are_dropped(self):
        # Whole stylesheets hide inside <!--[if mso]> blocks.
        text = herald.html_to_text(
            "<!--[if mso]><style>.f{font-family:Arial!important}</style><![endif]-->"
            "<p>Code 4471</p>")
        self.assertIn("4471", text)
        self.assertNotIn("font-family", text)
        self.assertNotIn("mso", text)

    def test_the_parser_fallback_does_not_leak_the_stylesheet(self):
        # Tag-stripping alone keeps everything between the tags, so the style
        # element has to be gone before the fallback ever runs.
        cleaned = herald.strip_style_elements(
            "<style>.a{color:red;font-size:11px}</style><p>Code 4471</p>")
        self.assertNotIn("font-size", herald.re.sub(r"<[^>]+>", " ", cleaned))

    def test_an_unclosed_style_does_not_swallow_the_message(self):
        # HTMLParser reads everything after an unclosed <style> as stylesheet
        # and emits nothing, which loses the code rather than leaking it.
        text = herald.html_to_text(
            "<html><body><style>.a{color:red}<p>Your code is 998877</p></body></html>")
        self.assertIn("998877", text)
        self.assertNotIn("color:red", text)

    def test_css_pasted_into_the_text_plain_part_is_stripped(self):
        msg = email.message.EmailMessage()
        msg.set_content(
            "body{margin:0;padding:0}\n"
            ".btn{background:#0a0;border-radius:4px;color:#fff}\n"
            "@media screen and (max-width:600px){.btn{width:100%}}\n"
            "Your security code is 774411. It expires in 5 minutes.\n")
        text = herald.message_body(msg, 4000)
        self.assertEqual(text, "Your security code is 774411. It expires in 5 minutes.")

    def test_at_rules_of_both_shapes_are_removed(self):
        self.assertEqual(
            herald.strip_css_text("@import url('x.css');@charset \"utf-8\";Code 4471").strip(),
            "Code 4471")
        self.assertEqual(
            herald.strip_css_text(
                "@media (prefers-color-scheme: dark){body{background:#111}} Code 4471").strip(),
            "Code 4471")

    def test_selectors_of_every_shape_go_with_their_block(self):
        for css in (
            '.a,\n.b,\ntd[class="x"] {display:none;width:100%}',
            "body,table,td,a{-webkit-text-size-adjust:100%}",
            "a:hover, .btn > span {color:red}",
            "#header .logo img {border:0}",
            "* {box-sizing:border-box}",
        ):
            self.assertEqual(herald.strip_css_text(css + "Code 4471").strip(), "Code 4471",
                             f"leftover from: {css}")

    def test_prose_containing_braces_survives(self):
        for line in (
            "Your order {ref 8842} shipped. Use code 4471 today.",
            "The template renders {{name}} for each row.",
            'JSON attached: {"amount": 1249, "currency": "INR"}',
            "Meeting {Tue 10:00} confirmed.",
        ):
            self.assertEqual(herald.strip_css_text(line), line)

    def test_a_declaration_block_loses_its_selector_but_not_the_sentence_before(self):
        text = herald.strip_css_text("Your code is 281940.\n.footer{color:#999;font-size:11px}")
        self.assertIn("Your code is 281940.", text)
        self.assertNotIn("footer", text)
        self.assertNotIn("#999", text)

    def test_a_real_bank_alert_arrives_as_its_words(self):
        html = (
            "<html><head><style>"
            "@import url('https://fonts.example/x.css');"
            "body,table,td,a{-webkit-text-size-adjust:100%}"
            ".wrapper{width:100%!important;max-width:600px}"
            "@media screen and (max-width:600px){.stack{display:block!important}}"
            "</style></head><body style='margin:0'>"
            "<table class='wrapper'><tr><td style='padding:24px;color:#111'>"
            "<p>Your card ending <b>4821</b> was debited <b>Rs.1,249.00</b> at BLUE TOKAI.</p>"
            "</td></tr></table></body></html>")
        msg = email.message.EmailMessage()
        msg.set_content(html, subtype="html")
        text = herald.message_body(msg, 4000)

        self.assertIn("4821", text)
        self.assertIn("Rs.1,249.00", text)
        self.assertIn("BLUE TOKAI", text)
        for token in ("!important", "@media", "@import", "max-width", "webkit", "#111"):
            self.assertNotIn(token, text)
        # The words, and essentially nothing else.
        self.assertLess(len(text), len(html) / 4)


class TestVerdictNormalisation(unittest.TestCase):
    def setUp(self):
        self.hint = herald.heuristic_verdict(meta("hello"), "nothing here")

    def test_spaces_are_stripped_from_a_returned_code(self):
        v = herald.normalize_verdict(
            {"category": "otp", "code": "281 940", "confidence": 0.9}, self.hint)
        self.assertEqual(v["code"], "281940")

    def test_an_otp_verdict_with_no_code_is_demoted(self):
        # The model sometimes labels "someone signed in" as an OTP. Without a
        # code there is nothing to copy, so it must not render as one.
        v = herald.normalize_verdict({"category": "otp", "code": ""}, self.hint)
        self.assertNotEqual(v["category"], "otp")

    def test_an_unknown_category_falls_back_to_the_local_guess(self):
        v = herald.normalize_verdict({"category": "invoice-ish"}, self.hint)
        self.assertEqual(v["category"], self.hint["category"])

    def test_confidence_is_clamped(self):
        self.assertEqual(
            herald.normalize_verdict({"category": "other", "confidence": 7}, self.hint)["confidence"], 1.0)
        self.assertEqual(
            herald.normalize_verdict({"category": "other", "confidence": "nope"}, self.hint)["confidence"], 0.5)


class TestNotificationText(unittest.TestCase):
    def test_a_long_code_is_grouped_for_reading(self):
        headline, body = herald.notification_lines(
            {"category": "otp", "title": "Google", "code": "281940",
             "detail": "Sign-in", "expires_minutes": 10})
        self.assertIn("281 940", headline)
        self.assertIn("10m left", body)
        self.assertIn("click to copy", body)

    def test_a_four_digit_code_is_left_alone(self):
        headline, _ = herald.notification_lines(
            {"category": "otp", "title": "Stripe", "code": "4471", "detail": ""})
        self.assertIn("4471", headline)
        self.assertNotIn(" ", headline.split("·")[-1].strip())

    def test_a_transaction_leads_with_the_money(self):
        headline, body = herald.notification_lines(
            {"category": "transaction", "title": "HDFC", "amount": "₹1,249.00",
             "merchant": "Blue Tokai", "detail": "UPI", "account_hint": "••4821"})
        self.assertTrue(headline.startswith("₹1,249.00"))
        self.assertIn("Blue Tokai", headline)
        self.assertIn("4821", body)


class _MockLLM(BaseHTTPRequestHandler):
    """Rejects json_schema the way a local server does, accepts json_object."""

    requests = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _MockLLM.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                  "payload": payload})
        if payload.get("response_format", {}).get("type") == "json_schema":
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"json_schema is not supported"}')
            return
        body = json.dumps({"choices": [{"message": {"content": json.dumps({
            "category": "otp", "title": "Google", "headline": "Google code",
            "detail": "Sign-in", "code": "281940", "amount": "", "merchant": "",
            "account_hint": "", "expires_minutes": 10, "confidence": 0.95})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class TestClassify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _MockLLM)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def config(self, **llm):
        base = json.loads(json.dumps(herald.DEFAULTS))
        base["llm"].update({"baseUrl": self.base, "model": "test-model"})
        base["llm"].update(llm)
        return base

    def test_it_falls_back_from_json_schema_to_json_object(self):
        _MockLLM.requests.clear()
        os.environ["HERALD_API_KEY"] = "sk-test"
        hint = herald.heuristic_verdict(meta("code"), "no code here")
        verdict = herald.classify(self.config(), meta("code"), "Your code is 281940", hint)

        self.assertEqual(verdict["category"], "otp")
        self.assertEqual(verdict["code"], "281940")
        self.assertEqual(verdict["source"], "llm")

        self.assertEqual(len(_MockLLM.requests), 2)
        first, second = _MockLLM.requests
        self.assertTrue(first["path"].endswith("/v1/chat/completions"))
        self.assertEqual(first["auth"], "Bearer sk-test")
        self.assertEqual(first["payload"]["response_format"]["type"], "json_schema")
        self.assertEqual(second["payload"]["response_format"]["type"], "json_object")
        self.assertEqual(first["payload"]["temperature"], 0)

    def test_an_unreachable_endpoint_leaves_the_local_verdict_standing(self):
        os.environ.pop("HERALD_API_KEY", None)
        hint = herald.heuristic_verdict(
            meta("code"), "Your one-time code is 999888.")
        config = self.config()
        config["llm"]["baseUrl"] = "http://127.0.0.1:1/v1"
        config["llm"]["timeoutSeconds"] = 1

        verdict = herald.classify(config, meta("code"), "Your one-time code is 999888.", hint)
        self.assertEqual(verdict["category"], "otp")
        self.assertEqual(verdict["code"], "999888")
        self.assertEqual(verdict["source"], "heuristic")
        self.assertIn("llm_error", verdict)

    def test_no_token_cap_is_sent_unless_configured(self):
        _MockLLM.requests.clear()
        hint = herald.heuristic_verdict(meta("x"), "y")
        herald.classify(self.config(), meta("x"), "y", hint)
        self.assertNotIn("max_tokens", _MockLLM.requests[0]["payload"])

        _MockLLM.requests.clear()
        herald.classify(self.config(maxTokens=256), meta("x"), "y", hint)
        self.assertEqual(_MockLLM.requests[0]["payload"]["max_tokens"], 256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
