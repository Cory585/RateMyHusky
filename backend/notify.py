"""Team email through Resend, same sender and inbox as the feedback route."""

import os

import requests as http_requests


def send_team_email(subject, text, post=None):
    """True when Resend accepted the email. Never raises: a failed email must not fail a rating."""
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        return False
    post = post or http_requests.post
    try:
        resp = post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "RateMyHusky <feedback@ratemyhusky.com>",
                "to": ["feedback@ratemyhusky.com"],
                "subject": subject,
                "text": text,
            },
            timeout=10,
        )
        return bool(resp.ok)
    except Exception as e:
        print(f"[notify] Resend request error: {type(e).__name__}")
        return False
