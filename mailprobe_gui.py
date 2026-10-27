"""MailProbe desktop window. Run beside mailprobe.py from your config folder."""

import logging
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import mailprobe as core


class ConfigField(ttk.Frame):
    """A default value with a temporary override; nothing is written back."""

    def __init__(self, parent, label, configured, secret=False, default_label="Use config"):
        super().__init__(parent)
        self.configured = str(configured)
        self.value = tk.StringVar(value=self.configured)
        self.use_config = tk.BooleanVar(value=True)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=label, width=18).grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(self, textvariable=self.value, show="\u2022" if secret else "")
        self.entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        self.check = ttk.Checkbutton(self, text=default_label, variable=self.use_config, command=self.toggle)
        self.check.grid(row=0, column=2, sticky="w")
        self.toggle()

    def toggle(self):
        if self.use_config.get():
            self.value.set(self.configured)
        self.set_busy(False)

    def set_busy(self, busy):
        self.check.state(["disabled"] if busy else ["!disabled"])
        self.entry.state(["disabled"] if busy or self.use_config.get() else ["!disabled"])


class QueueLogHandler(logging.Handler):
    def __init__(self, events):
        super().__init__(logging.INFO)
        self.events = events
        self.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    def emit(self, record):
        self.events.put(("log", self.format(record)))


class MailProbeApp:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.handler = QueueLogHandler(self.events)
        self.previous_logging = (core.LOG.level, core.LOG.propagate)
        core.LOG.setLevel(logging.DEBUG)
        core.LOG.propagate = False
        core.LOG.addHandler(self.handler)
        try:
            host, port, username, password, defaults = core.load_config()
            initial_error = ""
        except core.MailProbeError as error:
            host, port, username, password, defaults = "", 587, "", "", {}
            initial_error = core.safe_error(error)

        root.title("MailProbe")
        root.minsize(760, 570)
        root.geometry("850x640")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", "{Segoe UI} 10")
        body = ttk.Frame(root, padding=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(6, weight=1)
        ttk.Label(body, text="MailProbe", font=("Segoe UI", 21, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Send an existing MIME file through verified SMTP.").grid(row=1, column=0, sticky="w", pady=(4, 18))

        message = ttk.LabelFrame(body, text="Message", padding=14)
        message.grid(row=2, column=0, sticky="ew")
        message.columnconfigure(0, weight=1)
        self.fields = {}
        self.fields["file"] = ConfigField(message, "MIME file", defaults.get("default_file", ""))
        self.fields["file"].grid(row=0, column=0, sticky="ew", pady=4)
        self.browse = ttk.Button(message, text="Browse\u2026", command=self.browse_file)
        self.browse.grid(row=0, column=1, padx=(12, 0))
        self.fields["sender"] = ConfigField(message, "From", "", default_label="Use MIME")
        self.fields["sender"].grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        self.fields["file"].value.trace_add("write", self.clear_mime_from)
        recipient_row = ttk.Frame(message)
        recipient_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        recipient_row.columnconfigure(1, weight=1)
        ttk.Label(recipient_row, text="To", width=18).grid(row=0, column=0, sticky="w")
        self.recipients = tk.StringVar()  # Always blank, even if a config has recipient keys.
        self.recipient_entry = ttk.Entry(recipient_row, textvariable=self.recipients)
        self.recipient_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(message, text="Use MIME reads From on review; uncheck to override. To is required (comma-separated).").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.settings_open = False
        # Enlarge only the disclosure artwork, keeping the button's label font.
        self.settings_carets = {}
        for opened in (False, True):
            caret = tk.PhotoImage(width=5, height=5)
            for y, width in enumerate((1, 2, 3, 2, 1)):
                for x in range(width):
                    pixel = (4 - y, x + 1) if opened else (x + 1, y)
                    caret.put("#1f2937", pixel)
            self.settings_carets[opened] = caret.zoom(2, 2)
        self.settings_toggle = ttk.Button(body, text="SMTP settings", image=self.settings_carets[False],
                                          compound="left", command=self.toggle_settings)
        self.settings_toggle.grid(row=3, column=0, sticky="w", pady=(14, 8))
        self.settings = ttk.LabelFrame(body, text="SMTP defaults and session overrides", padding=14)
        self.settings.grid(row=4, column=0, sticky="ew")
        self.settings.columnconfigure(0, weight=1)
        for row, (key, label, value) in enumerate((("host", "Endpoint", host), ("port", "Port", port),
                                                  ("username", "Username", username), ("password", "Password", password))):
            field = ConfigField(self.settings, label, value, secret=key == "password")
            field.grid(row=row, column=0, sticky="ew", pady=4)
            self.fields[key] = field
        helper = ttk.Frame(self.settings)
        helper.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(helper, text="SES region helper", width=18).grid(row=0, column=0, sticky="w")
        match = re.fullmatch(r"email-smtp\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?", host)
        self.region = tk.StringVar(value=match[1] if match else "")
        self.region_entry = ttk.Entry(helper, textvariable=self.region, width=18)
        self.region_entry.grid(row=0, column=1, sticky="w")
        self.region_button = ttk.Button(helper, text="Use SES endpoint", command=self.apply_region)
        self.region_button.grid(row=0, column=2, padx=(12, 0))
        ttk.Label(self.settings, text="Region only helps fill Endpoint. Endpoint is used for sending; overrides are not saved.").grid(row=5, column=0, sticky="w", pady=(8, 0))
        self.fields["host"].use_config.trace_add("write", lambda *_: self.update_region_state())
        self.settings.grid_remove()

        controls = ttk.Frame(body)
        controls.grid(row=5, column=0, sticky="ew", pady=(18, 12))
        controls.columnconfigure(0, weight=1)
        self.debug = tk.BooleanVar()
        ttk.Checkbutton(controls, text="Debug details (sanitized)", variable=self.debug, command=self.toggle_debug).grid(row=0, column=0, sticky="w")
        log_frame = ttk.LabelFrame(body, text="Status log", padding=10)
        log_frame.grid(row=6, column=0, sticky="nsew")
        self.log = scrolledtext.ScrolledText(log_frame, height=7, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Ready. To and final confirmation are required.")
        ttk.Label(body, textvariable=self.status).grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.progress = ttk.Progressbar(body, mode="indeterminate")
        self.progress.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        self.progress.grid_remove()
        # A local clam border lets this one button use color on Windows' native theme.
        style = ttk.Style(root)
        style.element_create("MailProbe.Primary.border", "from", "clam", "Button.border")
        style.layout("MailProbe.Primary.TButton", [("MailProbe.Primary.border", {"sticky": "nswe", "children": [
            ("Button.focus", {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})
            ]})
        ]})])
        style.configure("MailProbe.Primary.TButton", background="#1d4ed8", foreground="white",
                        font=("Segoe UI", 12, "bold"), padding=(28, 14), borderwidth=2,
                        bordercolor="#1d4ed8", lightcolor="#1d4ed8", darkcolor="#1d4ed8",
                        focuscolor="white", focusthickness=2)
        style.map("MailProbe.Primary.TButton", background=[("disabled", "#d1d5db"), ("pressed", "#163aa8"), ("active", "#2563eb")],
                  foreground=[("disabled", "#6b7280")], bordercolor=[("disabled", "#d1d5db"), ("focus", "#111827")])
        self.review_button = ttk.Button(body, text="Review & Send", style="MailProbe.Primary.TButton",
                                       width=24, command=self.review)
        self.review_button.grid(row=9, column=0, pady=(18, 0))
        self.update_region_state()
        if initial_error:
            core.LOG.error("%s Uncheck Use config to enter session settings.", initial_error)
            self.status.set("Configuration could not be loaded. Session overrides are available.")
        self.poll_id = root.after(80, self.poll)

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        self.root.minsize(760, 760 if self.settings_open else 570)
        if self.settings_open:
            self.settings.grid()
            self.root.geometry("850x900")
        else:
            self.settings.grid_remove()
            self.root.geometry("850x640")
        self.settings_toggle.configure(image=self.settings_carets[self.settings_open])

    def update_region_state(self):
        disabled = self.busy or self.fields["host"].use_config.get()
        if self.fields["host"].use_config.get():
            match = re.fullmatch(r"email-smtp\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?", self.fields["host"].configured)
            self.region.set(match[1] if match else "")
        for widget in (self.region_entry, self.region_button):
            widget.state(["disabled"] if disabled else ["!disabled"])

    def apply_region(self):
        if self.busy or self.fields["host"].use_config.get():
            return
        region = self.region.get().strip()
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
            self.status.set("Enter an SES region code, such as us-east-1.")
            return
        suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
        self.fields["host"].value.set(f"email-smtp.{region}.{suffix}")
        self.status.set("Endpoint updated for this session. Credentials must match the SES region.")

    def browse_file(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(parent=self.root, title="Choose a MIME file", filetypes=[("Email messages", "*.eml"), ("All files", "*.*")])
        if filename:
            field = self.fields["file"]
            field.use_config.set(False)
            field.toggle()
            field.value.set(filename)

    def clear_mime_from(self, *_):
        field = self.fields["sender"]
        field.configured = ""
        if field.use_config.get():
            field.value.set("")

    def toggle_debug(self):
        self.handler.setLevel(logging.DEBUG if self.debug.get() else logging.INFO)

    def set_busy(self, busy):
        self.busy = busy
        for field in self.fields.values():
            field.set_busy(busy)
        for widget in (self.recipient_entry, self.browse, self.review_button):
            widget.state(["disabled"] if busy else ["!disabled"])
        self.update_region_state()
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def review(self):
        if self.busy:
            return
        values = {key: field.value.get() for key, field in self.fields.items()}
        entered = self.recipients.get()
        use_mime_from = self.fields["sender"].use_config.get()
        self.set_busy(True)
        self.status.set("Checking settings and MIME structure\u2026")
        threading.Thread(target=self.prepare, args=(values, entered, use_mime_from), daemon=True).start()

    def prepare(self, values, entered, use_mime_from):
        try:
            smtp = core.validate_smtp(*(values[key] for key in ("host", "port", "username", "password")))
            if not values["file"].strip():
                raise core.MailProbeError("A message file is required.")
            path = Path(values["file"].strip()).expanduser().resolve()
            raw = core.load_message(path)
            sender, recipients, raw = core.prepare_gui_message(
                raw, entered, None if use_mime_from else values["sender"])
            if use_mime_from:
                self.events.put(("mime_from", sender))
            self.events.put(("ready", (smtp, sender, recipients, path, raw)))
        except Exception as error:
            self.events.put(("failed", core.safe_error(error)))

    def confirm(self, payload):
        smtp, sender, recipients, path, raw = payload
        review = (f"SMTP: {ascii(smtp[0])}:{smtp[1]} (verified STARTTLS required)\n"
                  f"File: {ascii(str(path))}\nFrom: {sender}\n"
                  f"To: {', '.join(recipients)}\n\n"
                  "These addresses are used for the SMTP envelope and outgoing headers.\n"
                  "The source file is unchanged.\n"
                  "MIME checks are basic. SES may rewrite headers.\nSend this message once?")
        if not messagebox.askyesno("Confirm send", review, parent=self.root, default=messagebox.NO):
            core.LOG.info("Cancelled; no message sent.")
            self.status.set("Cancelled; no message sent.")
            self.set_busy(False)
            return
        self.status.set("Sending\u2026 wait for the SMTP result before trying again.")
        self.recipients.set("")  # A later send must get fresh recipient input.
        threading.Thread(target=self.send, args=(smtp, sender, recipients, raw), daemon=True).start()

    def send(self, smtp, sender, recipients, raw):
        try:
            result = core.send_raw(*smtp, sender, recipients, raw)
            self.events.put(("done", result))
        except Exception as error:
            self.events.put(("failed", core.safe_error(error)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "mime_from":
                    self.fields["sender"].configured = value
                    self.fields["sender"].value.set(value)
                elif kind == "ready":
                    self.confirm(value)
                elif kind == "failed":
                    core.LOG.error("%s No automatic retry.", value)
                    self.status.set(value)
                    self.set_busy(False)
                elif kind == "done":
                    self.status.set("SMTP accepted; inbox delivery is unconfirmed." if value == 0 else "Some recipients were rejected. See log; no automatic retry.")
                    self.set_busy(False)
        except queue.Empty:
            pass
        self.poll_id = self.root.after(80, self.poll)

    def close(self):
        if self.busy:
            self.status.set("Operation in progress. Wait for its result before closing or retrying.")
            return
        self.root.after_cancel(self.poll_id)
        core.LOG.removeHandler(self.handler)
        core.LOG.setLevel(self.previous_logging[0])
        core.LOG.propagate = self.previous_logging[1]
        self.root.destroy()


def main():
    root = tk.Tk()
    MailProbeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
