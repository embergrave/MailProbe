# MailProbe

Send existing raw MIME/`.eml` files through SMTP using a small desktop window or command-line interface.

Requires **Python 3 with Tkinter/ttk**. MailProbe uses only the Python standard library.

For a new setup, copy `config.example.ini` to `config.ini` in the folder where you launch MailProbe. Replace the dummy SMTP username/password and adjust the endpoint, port, and optional message defaults. Keep your existing `config.ini` if you already have one.

From the MailProbe folder, launch the desktop interface:

```powershell
python mailprobe_gui.py
```

In the window, **From** uses the MIME header by default; uncheck **Use MIME** to override it. Enter **To** for every send and confirm the review. These addresses set the SMTP envelope and outgoing From/To headers; the source file stays unchanged.

Or use the CLI:

```powershell
python mailprobe.py
```

Your personal `config.ini` stays local and must not be committed. `config.example.ini` is the shared format/template.

Samples: `sample_emails/basic.eml` (plain text), `sample_emails/auto-generated.eml`, and `sample_emails/multipart.eml` (plain text + HTML).

Licensed under the [MIT License](LICENSE).
