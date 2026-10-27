"""Offline GUI checks with fake settings and transport; never load personal config."""

from pathlib import Path
import socket
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox
import unittest
from unittest.mock import patch

import mailprobe as core
import mailprobe_gui as gui


RAW = b"From: sender@example.com\r\nTo: old@example.com\r\n\r\nTest body.\r\n"


class SessionSendTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(folder.cleanup)
        self.message = Path(folder.name) / "test.eml"
        self.message.write_bytes(RAW)
        self.enterContext(patch.object(core, "load_config", return_value=(
            "smtp.example.com", 587, "FAKE_USERNAME", "FAKE_PASSWORD",
            {"default_file": str(self.message), "default_envelope_sender": "cli-only@example.com"})))
        self.enterContext(patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")))
        self.popup = self.enterContext(patch.object(messagebox, "askyesno", side_effect=AssertionError("No send popup")))
        self.transport = self.enterContext(patch.object(core, "send_raw", return_value=0))
        self.open_app()
        self.addCleanup(self.close_app)

    def open_app(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.MailProbeApp(self.root)

    def close_app(self):
        self.app.busy = False
        self.app.close()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail("Offline GUI operation did not complete")

    def send(self):
        self.app.send_button.invoke()
        self.wait_until(lambda: not self.app.busy)
        self.popup.assert_not_called()

    def set_from(self, value="override@example.com"):
        field = self.app.fields["sender"]
        field.check.invoke()
        field.value.set(value)

    def test_send_button_validates_then_sends_without_popup(self):
        self.assertEqual(self.app.send_button.cget("text"), "Send")
        self.app.recipients.set("to@example.com")
        self.send()
        self.transport.assert_called_once()
        args = self.transport.call_args.args
        self.assertEqual(args[4:6], ("sender@example.com", ["to@example.com"]))
        self.assertEqual(args[6], RAW.replace(b"To: old@example.com", b"To: to@example.com"))
        self.assertEqual(self.message.read_bytes(), RAW)
        self.assertIn("unconfirmed", self.app.status.get())

    def test_explicit_from_and_to_stay_for_multiple_sends(self):
        self.set_from()
        self.app.recipients.set("one@example.com, two@example.com")
        for _ in range(2):
            self.send()
            self.assertEqual(self.app.fields["sender"].value.get(), "override@example.com")
            self.assertFalse(self.app.fields["sender"].use_config.get())
            self.assertEqual(self.app.recipients.get(), "one@example.com, two@example.com")
            self.assertEqual(self.transport.call_args.args[4:6],
                             ("override@example.com", ["one@example.com", "two@example.com"]))
        self.assertEqual(self.transport.call_count, 2)

    def test_use_mime_still_reads_source_on_each_send(self):
        self.app.recipients.set("to@example.com")
        self.send()
        self.assertEqual(self.app.fields["sender"].value.get(), "sender@example.com")
        self.message.write_bytes(RAW.replace(b"sender@example.com", b"changed@example.com"))
        self.send()
        self.assertEqual(self.transport.call_args.args[4], "changed@example.com")
        self.assertTrue(self.app.fields["sender"].use_config.get())
        self.assertEqual(self.app.recipients.get(), "to@example.com")

    def test_reopening_app_resets_session_addresses(self):
        self.set_from()
        self.app.recipients.set("to@example.com")
        self.send()
        self.close_app()
        self.open_app()
        self.assertEqual(self.app.recipients.get(), "")
        self.assertEqual(self.app.fields["sender"].value.get(), "")
        self.assertTrue(self.app.fields["sender"].use_config.get())

    def test_validation_errors_do_not_send_or_clear_fields(self):
        self.set_from()
        for recipient in ("", "invalid", "to@example.com\r\nBcc: extra@example.com"):
            self.app.recipients.set(recipient)
            self.send()
            self.assertEqual(self.app.recipients.get(), recipient)
            self.assertEqual(self.app.fields["sender"].value.get(), "override@example.com")
        self.app.recipients.set("to@example.com")
        self.message.write_bytes(b"Content-Type: multipart/mixed\r\n\r\nbody\r\n")
        self.send()
        self.assertIn("NoBoundaryInMultipartDefect", self.app.status.get())
        self.transport.assert_not_called()

    def test_transport_error_is_sanitized_and_addresses_remain(self):
        self.set_from()
        self.app.recipients.set("to@example.com")
        self.transport.side_effect = OSError("PRIVATE_SERVER_RESPONSE FAKE_PASSWORD")
        self.send()
        self.transport.assert_called_once()
        self.assertEqual(self.app.recipients.get(), "to@example.com")
        self.assertEqual(self.app.fields["sender"].value.get(), "override@example.com")
        output = self.app.log.get("1.0", "end") + self.app.status.get()
        self.assertIn("OSError", output)
        self.assertNotIn("PRIVATE_SERVER_RESPONSE", output)
        self.assertNotIn("FAKE_PASSWORD", output)

    def test_partial_result_preserves_addresses_and_reports_rejection(self):
        self.transport.return_value = 1
        self.app.recipients.set("to@example.com")
        self.send()
        self.assertIn("rejected", self.app.status.get())
        self.assertEqual(self.app.recipients.get(), "to@example.com")

    def test_busy_send_stays_responsive_and_blocks_duplicate_clicks(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def slow_send(*_):
            release.wait(2)
            return 0
        self.transport.side_effect = slow_send
        self.app.recipients.set("to@example.com")
        self.app.send_button.invoke()
        self.wait_until(lambda: self.transport.called)
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.send_button.instate(["disabled"]))
        self.assertTrue(self.app.progress.winfo_manager())
        for _ in range(20):
            self.app.send_button.invoke()
            self.app.start_send()
        self.app.close()
        self.assertTrue(self.root.winfo_exists())
        release.set()
        self.wait_until(lambda: not self.app.busy)
        self.transport.assert_called_once()
        self.assertEqual(self.app.recipients.get(), "to@example.com")


if __name__ == "__main__":
    unittest.main()
