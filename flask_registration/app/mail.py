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
