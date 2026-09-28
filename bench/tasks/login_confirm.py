"""Local password login, then a confirm() dialog that must be accepted to delete a draft."""

from . import is_local_page, outcome

URL = "{local}/login_confirm.html"
GOAL = "Sign in as ada with the password lovelace-1815, then delete the draft and confirm the deletion."


def verify(page, extracted=None):
    text = page.get("text", "")
    checks = {
        "page": is_local_page(page, "login_confirm.html"),
        "signed_in": "Signed in as ada" in text,
        "deleted": "Draft deleted." in text and "Harbour survey notes" not in text,
        "no_dialog_left": "dialog" not in page,
    }
    return outcome(checks)
