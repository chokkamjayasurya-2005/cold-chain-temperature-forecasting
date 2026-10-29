from email.message import EmailMessage
import os
import smtplib
import ssl
from urllib.parse import quote, urlsplit


class EmailConfigurationError(RuntimeError):
    pass


def email_delivery_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())


def send_password_reset(email: str, token: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    production = os.getenv("APP_ENV", "development").lower() == "production"
    configured_base_url = os.getenv("APP_BASE_URL", "").strip()
    if production and not configured_base_url:
        raise EmailConfigurationError("APP_BASE_URL must be configured in production.")
    base_url = (configured_base_url or "http://127.0.0.1:8000").rstrip("/")
    if not host or not sender:
        raise EmailConfigurationError("SMTP_HOST and SMTP_FROM must be configured.")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise EmailConfigurationError("SMTP_USERNAME and SMTP_PASSWORD must be set together.")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as error:
        raise EmailConfigurationError("SMTP_PORT must be a valid port number.") from error
    parsed_base_url = urlsplit(base_url)
    if (
        not 1 <= port <= 65535
        or parsed_base_url.scheme not in {"http", "https"}
        or not parsed_base_url.netloc
        or parsed_base_url.username is not None
        or (production and parsed_base_url.scheme != "https")
    ):
        raise EmailConfigurationError("Email delivery configuration is invalid.")
    starttls_setting = os.getenv("SMTP_STARTTLS", "true").strip().lower()
    if starttls_setting not in {"true", "false"}:
        raise EmailConfigurationError("SMTP_STARTTLS must be true or false.")
    starttls = starttls_setting == "true"
    if not starttls and production:
        raise EmailConfigurationError("SMTP_STARTTLS may be disabled only in development.")

    reset_link = f"{base_url}/reset-password#token={quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "Cold Chain password reset"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "A password reset was requested for your Cold Chain account.\n\n"
        f"Use this link within 30 minutes:\n{reset_link}\n\n"
        "If you did not request this, you can ignore this message."
    )
    with smtplib.SMTP(host, port, timeout=10) as server:
        if starttls:
            server.starttls(context=ssl.create_default_context())
        if username:
            server.login(username, password)
        server.send_message(message)
