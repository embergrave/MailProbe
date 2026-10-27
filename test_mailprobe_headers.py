"""Offline checks for GUI address overrides; no configuration or network needed."""

from email import policy
from email.parser import BytesParser
import unittest

import mailprobe


RAW = (b'From: Example Sender <sender@example.com>\r\n'
       b'To: old@example.com\r\n'
       b'Subject: Test\r\n'
       b'Auto-Submitted: auto-generated\r\n'
       b'X-Custom-Test: keep  these spaces\r\n\tand this continuation\r\n'
       b'MIME-Version: 1.0\r\nContent-Type: text/plain\r\n\r\nTest body.\r\n')


class HeaderTests(unittest.TestCase):
    def prepare(self, raw=RAW, to="fresh@example.com", sender=None):
        return mailprobe.prepare_gui_message(raw, to, sender)

    def test_mime_from_default_and_required_to_replace_only_to(self):
        sender, recipients, outgoing = self.prepare()
        self.assertEqual(sender, "sender@example.com")
        self.assertEqual(recipients, ["fresh@example.com"])
        self.assertEqual(outgoing, RAW.replace(b"To: old@example.com", b"To: fresh@example.com"))

    def test_explicit_from_and_multiple_to_match_envelope(self):
        sender, recipients, outgoing = self.prepare(
            to=" one@example.com, two@example.com ", sender=" override@example.com ")
        parsed = BytesParser(policy=policy.default).parsebytes(outgoing)
        self.assertEqual(sender, "override@example.com")
        self.assertEqual(str(parsed["From"]), sender)
        self.assertEqual(recipients, ["one@example.com", "two@example.com"])
        self.assertEqual(str(parsed["To"]), ", ".join(recipients))
        self.assertEqual(outgoing.split(b"\r\n\r\n", 1)[1], RAW.split(b"\r\n\r\n", 1)[1])
        self.assertIn(b"Auto-Submitted: auto-generated\r\n", outgoing)
        self.assertIn(b"X-Custom-Test: keep  these spaces\r\n\tand this continuation\r\n", outgoing)

    def test_folded_duplicate_target_headers_become_single_headers(self):
        raw = (b"fRoM: first@example.com\r\nFrom: second@example.com\r\n"
               b"tO: old@example.com,\r\n\tother@example.com\r\nTO: third@example.com\r\n"
               b"X-Keep: exact\r\n\r\nbody\r\n")
        _, _, outgoing = self.prepare(raw, sender="override@example.com")
        self.assertEqual(outgoing, b"From: override@example.com\r\nTo: fresh@example.com\r\n"
                         b"X-Keep: exact\r\n\r\nbody\r\n")

    def test_missing_target_headers_are_added_with_explicit_from(self):
        raw = b"Subject: Test\r\n\r\nbody\r\n"
        _, _, outgoing = self.prepare(raw, sender="override@example.com")
        parsed = BytesParser(policy=policy.default).parsebytes(outgoing)
        self.assertEqual(parsed.get_all("From"), ["override@example.com"])
        self.assertEqual(parsed.get_all("To"), ["fresh@example.com"])

    def test_missing_to_is_added_while_mime_from_stays_byte_identical(self):
        raw = b"From: Sender\r\n\t<sender@example.com>\r\nSubject: Test\r\n\r\nbody\r\n"
        sender, _, outgoing = self.prepare(raw)
        self.assertEqual(sender, "sender@example.com")
        self.assertTrue(outgoing.startswith(raw.split(b"\r\n\r\n", 1)[0]))
        self.assertIn(b"\r\nTo: fresh@example.com\r\n\r\nbody\r\n", outgoing)

    def test_ambiguous_or_missing_mime_from_requires_override(self):
        for headers in (b"Subject: Test", b"From: a@example.com\r\nFrom: b@example.com",
                        b"From: a@example.com, b@example.com", b"From: invalid"):
            with self.subTest(headers=headers), self.assertRaises(mailprobe.MailProbeError):
                self.prepare(headers + b"\r\n\r\nbody\r\n")

    def test_encoded_display_name_is_preserved(self):
        raw = RAW.replace(b"Example Sender", b"=?utf-8?q?Ex=C3=A4mple?=")
        sender, _, outgoing = self.prepare(raw)
        self.assertEqual(sender, "sender@example.com")
        self.assertIn(raw.split(b"\r\n", 1)[0] + b"\r\n", outgoing)

    def test_recipients_never_fall_back_to_mime(self):
        for to in ("", "   ", "bad-address", "a@example.com,", "Name <a@example.com>"):
            with self.subTest(to=to), self.assertRaises(mailprobe.MailProbeError):
                self.prepare(to=to)

    def test_newlines_are_rejected_before_trimming(self):
        for value in ("a@example.com\r", "\na@example.com", "a@example.com\r\nBcc: b@example.com"):
            with self.subTest(value=value):
                with self.assertRaises(mailprobe.MailProbeError):
                    self.prepare(to=value)
                with self.assertRaises(mailprobe.MailProbeError):
                    self.prepare(sender=value)

    def test_blank_explicit_from_is_not_silently_replaced(self):
        with self.assertRaises(mailprobe.MailProbeError):
            self.prepare(sender=" ")

    def test_multipart_attachment_bytes_are_preserved(self):
        body = (b"--boundary\r\nContent-Type: text/plain\r\n\r\nplain body\r\n"
                b"--boundary\r\nContent-Type: application/octet-stream\r\n"
                b"Content-Disposition: attachment; filename=example.bin\r\n"
                b"Content-Transfer-Encoding: base64\r\n\r\nAAECA/8=\r\n--boundary--\r\n")
        raw = (b"From: sender@example.com\r\nTo: old@example.com\r\nMIME-Version: 1.0\r\n"
               b'Content-Type: multipart/mixed; boundary="boundary"\r\n\r\n' + body)
        _, _, outgoing = self.prepare(raw, sender="override@example.com")
        self.assertEqual(outgoing.split(b"\r\n\r\n", 1)[1], body)
        self.assertEqual(len(list(BytesParser(policy=policy.default).parsebytes(outgoing).walk())), 3)

    def test_outgoing_structure_is_checked(self):
        raw = b"From: sender@example.com\r\nContent-Type: multipart/mixed\r\n\r\nbody\r\n"
        with self.assertRaisesRegex(mailprobe.MailProbeError, "NoBoundaryInMultipartDefect"):
            self.prepare(raw)


if __name__ == "__main__":
    unittest.main()
