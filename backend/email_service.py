"""Emergent Resend email integration for SchedinaBar."""
import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("schedinabar.email")

EMAIL_BASE_URL = "https://integrations.emergentagent.com"  # constant, do NOT read from env
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "").strip()
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "RinoMagic").strip()


async def send_email(recipient_email: str, subject: str, html_content: str) -> bool:
    """Send an email via Emergent Resend. Returns True on success, False on failure.
    In dev when EMERGENT_EMAIL_KEY is a placeholder, we log the email instead of
    raising so the reset password flow can still be exercised."""
    if not EMAIL_KEY or EMAIL_KEY.startswith("placeholder"):
        logger.warning(
            "EMERGENT_EMAIL_KEY not configured; logging email instead of sending:"
            "\n  TO: %s\n  SUBJECT: %s\n  BODY (truncated): %s",
            recipient_email, subject, html_content[:500],
        )
        return False
    payload = {
        "to": [recipient_email],
        "subject": subject,
        "html": html_content,
        "from_name": EMAIL_FROM_NAME,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{EMAIL_BASE_URL}/api/v1/email/send",
                headers={"X-Email-Key": EMAIL_KEY},
                json=payload,
            )
        resp.raise_for_status()
        logger.info("Email sent to %s (id=%s)", recipient_email, resp.json().get("id"))
        return True
    except httpx.HTTPStatusError as e:
        logger.error("Email send failed: %s %s", e.response.status_code, e.response.text)
        return False
    except Exception as e:  # pragma: no cover
        logger.error("Email send error: %s", e)
        return False


def build_reset_email_html(reset_url: str, expires_minutes: int = 60) -> str:
    """Render the HTML body for the admin password-reset email."""
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#0F1216;padding:24px 0;font-family:Helvetica,Arial,sans-serif">
  <tr><td align="center">
    <table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0"
           style="background:#1B1F26;border-radius:16px;padding:32px;color:#F5F7FA">
      <tr><td align="center" style="padding-bottom:16px">
        <div style="font-size:40px;line-height:1">🍺</div>
        <div style="font-size:22px;font-weight:800;margin-top:6px">RinoMagic</div>
      </td></tr>
      <tr><td style="font-size:16px;line-height:1.5">
        <p>Ciao,</p>
        <p>Hai richiesto il reset della password del tuo account admin.</p>
        <p>Clicca il pulsante qui sotto per impostare una nuova password.
           Il link scade tra <b>{expires_minutes} minuti</b> e può essere usato una sola volta.</p>
      </td></tr>
      <tr><td align="center" style="padding:24px 0">
        <a href="{reset_url}" style="display:inline-block;background:#FFB300;color:#0F1216;
           padding:14px 28px;border-radius:12px;font-weight:800;text-decoration:none;font-size:16px">
          Reimposta password
        </a>
      </td></tr>
      <tr><td style="font-size:12px;color:#8A94A6">
        Se non hai richiesto tu il reset, ignora questa email — la tua password resta invariata.<br>
        Il link diretto: <span style="color:#FFB300">{reset_url}</span>
      </td></tr>
    </table>
  </td></tr>
</table>
""".strip()
