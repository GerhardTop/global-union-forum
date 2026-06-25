import os
import resend as _resend

FROM_ADDRESS = "Global Union Forum <noreply@globalunionforum.org>"


def send_email(to: str, subject: str, html: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        print("[MAIL] RESEND_API_KEY niet ingesteld — e-mail niet verstuurd", flush=True)
        return False
    _resend.api_key = api_key
    try:
        _resend.Emails.send({
            "from": FROM_ADDRESS,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        print(f"[MAIL] OK → {to}", flush=True)
        return True
    except Exception as e:
        print(f"[MAIL] FOUT → {to}: {e}", flush=True)
        return False


def send_error_email(error: str, traceback_str: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        print("[MAIL] send_error_email: RESEND_API_KEY niet ingesteld", flush=True)
        return
    _resend.api_key = api_key
    html = (
        f"<h2>500 error op Global Union Forum</h2>"
        f"<p><strong>Fout:</strong> {error}</p>"
        f"<pre style='background:#f5f5f5;padding:16px;font-size:12px;'>{traceback_str}</pre>"
    )
    try:
        _resend.Emails.send({
            "from": FROM_ADDRESS,
            "to": ["top.gerhard@gmail.com"],
            "subject": "⚠️ Global Union Forum — 500 error",
            "html": html,
        })
        print("[MAIL] send_error_email OK", flush=True)
    except Exception as e:
        print(f"[MAIL] send_error_email FOUT: {e}", flush=True)
