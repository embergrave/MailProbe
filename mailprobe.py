"""Send an existing MIME file. Run from your config folder: python mailprobe.py [--debug]."""

import argparse
import configparser
from contextlib import closing
from email import policy
from email.errors import MessageError
from email.parser import BytesHeaderParser, BytesParser
from email.utils import parseaddr
import logging
from pathlib import Path
import smtplib
import ssl


LOG = logging.getLogger("mailprobe")
TIMEOUT = 30


class MailProbeError(Exception):
    """An explanation safe to show without including secrets or message contents."""


def load_config():
    config = configparser.ConfigParser(interpolation=None)
    try:
        with Path("config.ini").open(encoding="utf-8-sig") as file:
            config.read_file(file)
        smtp = config["smtp"]
        host, username, password = (smtp[name] for name in ("host", "username", "password"))
        port = smtp.getint("port", fallback=587)
        defaults = dict(config["message"]) if config.has_section("message") else {}
    except (OSError, UnicodeError, configparser.Error, KeyError, ValueError):
        # Parser exceptions may contain entire config lines, including passwords.
        raise MailProbeError("Cannot load config.ini. Check [smtp], required settings, and port.") from None
    host, port, username, password = validate_smtp(host, port, username, password)
    if any("\n" in value or "\r" in value for value in defaults.values()):
        raise MailProbeError("Each configuration value must occupy one line.")
    LOG.info("Configuration loaded: config.ini (current working directory).")
    return host, port, username, password, defaults


def validate_smtp(host, port, username, password):
    """Validate configured settings or a desktop session's overrides."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise MailProbeError("SMTP port must be a whole number from 1 to 65535.") from None
    if not all((host, username, password)) or not 1 <= port <= 65535:
        raise MailProbeError("SMTP host, username, and password are required; port must be 1-65535.")
    if any("\n" in value or "\r" in value for value in (host, username, password)):
        raise MailProbeError("Each SMTP setting must occupy one line.")
    if any(char.isspace() for char in host) or any(char in host for char in "/@"):
        raise MailProbeError("SMTP host must be a hostname, without a URL, username, or path.")
    if not username.isascii() or not password.isascii():
        raise MailProbeError("This SMTP client requires ASCII username/password values.")
    return host, port, username, password


def prompt(label, default=""):
    shown_default = f" [{ascii(default)}]" if default else ""
    return input(f"{label}{shown_default}: ").strip() or default


def envelope_address(value):
    name, address = parseaddr(value)
    if (not value or not value.isascii() or name or address != value or "@" not in value
            or any(char.isspace() or not char.isprintable() for char in value)):
        raise MailProbeError("Use bare ASCII envelope addresses, such as sender@example.com.")
    return value


def envelope_recipients(entered):
    if not entered.strip():
        raise MailProbeError("Enter at least one recipient for this send.")
    return [envelope_address(address.strip()) for address in entered.split(",")]


def load_message(path):
    try:
        raw = path.read_bytes()
    except OSError:
        raise MailProbeError("Cannot read the message file. Check its path and access permissions.") from None
    LOG.info("Message loaded.")
    return validate_message(raw)


def validate_message(raw):
    """Check structure without reconstructing headers or MIME parts."""
    if not raw.strip():
        raise MailProbeError("MIME structure: the message file is empty.")
    # SMTP expects CRLF. Reject other line endings instead of silently changing bytes.
    if b"\n" in raw.replace(b"\r\n", b"") or b"\r" in raw.replace(b"\r\n", b""):
        raise MailProbeError("MIME structure: use CRLF line endings; the file was not changed.")
    if b"\r\n\r\n" not in raw:
        raise MailProbeError("MIME structure: missing blank line between headers and body.")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not message.keys():
            raise MailProbeError("MIME structure: no message headers were found.")
        part_count = 0
        for part_count, part in enumerate(message.walk(), start=1):
            if part.defects:
                # Defect names are safe; defect descriptions can contain message data.
                problem = type(part.defects[0]).__name__
                raise MailProbeError(f"MIME structure: part {part_count} has {problem}.")
            if part.get_content_maintype() == "multipart" and not part.is_multipart():
                raise MailProbeError(f"MIME structure: part {part_count} has no multipart children.")
    except (MessageError, ValueError, RecursionError):
        raise MailProbeError("MIME structure: the message could not be parsed.") from None
    LOG.info("Basic MIME structural preflight passed.")
    LOG.debug("Checked %d MIME part(s), %d bytes. This is not exhaustive RFC validation.", part_count, len(raw))
    return raw


def prepare_gui_message(raw, entered_to, from_override=None):
    """Set GUI addresses in memory; keep unrelated headers and body bytes intact."""
    if any(char in entered_to for char in "\r\n") or (
            from_override is not None and any(char in from_override for char in "\r\n")):
        raise MailProbeError("From and To must each occupy one line.")
    recipients = envelope_recipients(entered_to)
    headers, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise MailProbeError("MIME structure: missing blank line between headers and body.")
    if from_override is None:
        parsed = BytesHeaderParser(policy=policy.default).parsebytes(headers + separator)
        from_headers = parsed.get_all("From", [])
        if len(from_headers) != 1:
            raise MailProbeError("MIME must have exactly one From header, or enter a From override.")
        origin = from_headers[0]
        if origin.defects or len(origin.addresses) != 1:
            raise MailProbeError("MIME From must contain one valid address, or enter a From override.")
        sender = envelope_address(origin.addresses[0].addr_spec)
    else:
        sender = envelope_address(from_override.strip())

    replacements = {b"to": b"To: " + ", ".join(recipients).encode("ascii")}
    if from_override is not None:
        replacements[b"from"] = b"From: " + sender.encode("ascii")
    # Group continuations with their header so folded old values cannot survive.
    blocks = []
    for line in headers.split(b"\r\n"):
        if line.startswith((b" ", b"\t")) and blocks:
            blocks[-1] += b"\r\n" + line
        else:
            blocks.append(line)
    outgoing = []
    replaced = set()
    for block in blocks:
        name = block.split(b":", 1)[0].lower()
        if name in replacements:
            if name not in replaced:
                outgoing.append(replacements[name])
                replaced.add(name)
        else:
            outgoing.append(block)
    for name, value in replacements.items():
        if name not in replaced:
            outgoing.append(value)
    result = b"\r\n".join(outgoing) + separator + body
    validate_message(result)
    LOG.info("Outgoing From and To prepared; source file unchanged.")
    return sender, recipients, result


def send_raw(host, port, username, password, sender, recipients, raw):
    context = ssl.create_default_context()
    LOG.info("Connecting to SMTP server.")
    LOG.debug("Network operation timeout: %d seconds.", TIMEOUT)
    # Local close avoids a failed QUIT obscuring a successful message acceptance.
    with closing(smtplib.SMTP(host, port, timeout=TIMEOUT)) as smtp:
        if smtp.ehlo()[0] != 250:
            raise MailProbeError("SMTP server rejected EHLO; nothing was sent.")
        if not smtp.has_extn("starttls"):
            raise MailProbeError("SMTP server did not offer STARTTLS; nothing was sent.")
        LOG.info("Starting TLS and verifying the server certificate.")
        smtp.starttls(context=context)
        if smtp.ehlo()[0] != 250:
            raise MailProbeError("SMTP server rejected EHLO after TLS; nothing was sent.")
        LOG.info("TLS verified.")
        LOG.info("Authenticating with SMTP server.")
        smtp.login(username, password)
        LOG.info("SMTP authentication succeeded.")
        LOG.info("Sending message.")
        try:
            # sendmail(bytes) keeps headers intact; SMTP still applies normal DATA framing.
            refused = smtp.sendmail(sender, recipients, raw)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise
        except (OSError, smtplib.SMTPException):
            LOG.error("Sending was interrupted; SMTP acceptance is uncertain. Check before sending again.")
            raise
    for recipient, (code, _response) in refused.items():
        LOG.error("Recipient %s rejected (SMTP %d).", ascii(recipient), code)
    LOG.info("SMTP server accepted the message for %d recipient(s). Inbox delivery is not guaranteed.",
             len(recipients) - len(refused))
    return 1 if refused else 0


def safe_error(error):
    """Share useful errors with both interfaces without printing server/config data."""
    if isinstance(error, MailProbeError):
        return str(error)
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return f"SMTP authentication rejected (SMTP {error.smtp_code})."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP rejected all envelope recipients; no message was accepted."
    if isinstance(error, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return f"SMTP rejected the envelope sender or message (SMTP {error.smtp_code})."
    if isinstance(error, ssl.SSLCertVerificationError):
        return "TLS certificate/hostname verification failed; nothing was sent."
    return f"Operation failed ({type(error).__name__}). Check the current stage and connection/settings."


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--debug", action="store_true", help="show extra sanitized diagnostics")
    args = arguments.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="[%(levelname)s] %(message)s")
    try:
        host, port, username, password, defaults = load_config()
        print(f"SMTP: {ascii(host)}:{port} (STARTTLS required)")
        filename = prompt("Message file", defaults.get("default_file", ""))
        if not filename:
            raise MailProbeError("A message file is required.")
        path = Path(filename).expanduser().resolve()
        sender = envelope_address(prompt("Envelope sender", defaults.get("default_envelope_sender", "")))
        # Never use configured recipients or MIME To/Cc/Bcc headers for delivery.
        entered = input("Envelope recipients (required, comma-separated): ")
        recipients = envelope_recipients(entered)
        raw = load_message(path)
        print("\nReady to send")
        print(f"SMTP: {ascii(host)}:{port} (STARTTLS required)")
        print(f"Message file: {ascii(str(path))}")
        print(f"Envelope sender: {sender}")
        print(f"Envelope recipients: {', '.join(recipients)}")
        print("MIME checks are basic. SES may rewrite headers; exact delivered bytes are not guaranteed.")
        if input("Type SEND to confirm: ") != "SEND":
            LOG.info("Cancelled; no message sent.")
            return 0
        return send_raw(host, port, username, password, sender, recipients, raw)
    except (MailProbeError, OSError, smtplib.SMTPException, UnicodeError, ValueError) as error:
        LOG.error("%s", safe_error(error))
    except (EOFError, KeyboardInterrupt):
        LOG.warning("Stopped. If sending had begun, check acceptance before trying again.")
    LOG.info("No automatic retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
