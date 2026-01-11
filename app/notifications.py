# app/notifications.py
from typing import Optional
from django.conf import settings
from django.core.mail import send_mail

try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None


# --------------- Low-level helpers ---------------

def _twilio_client() -> Optional["TwilioClient"]:
    if not TwilioClient:
        return None
    sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    if not (sid and token):
        return None
    try:
        return TwilioClient(sid, token)
    except Exception:
        return None


def send_email_notification(to_email: str, subject: str, body: str) -> None:
    if not to_email:
        return
    try:
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
        send_mail(subject, body, from_email, [to_email], fail_silently=True)
    except Exception:
        pass


def send_sms_notification(to_number: str, body: str) -> None:
    if not to_number:
        return
    client = _twilio_client()
    if not client:
        return
    from_num = getattr(settings, "TWILIO_FROM_NUMBER", "")
    if not from_num:
        return
    try:
        client.messages.create(to=to_number, from_=from_num, body=body)
    except Exception:
        pass


def notify_user(user, subject: str, body: str) -> None:
    email = getattr(user, "email", "") or ""
    phone = ""
    # optional: user.profile.phone
    prof = getattr(user, "profile", None)
    if prof:
        phone = getattr(prof, "phone", "") or ""
    send_email_notification(email, subject, body)
    if phone:
        send_sms_notification(phone, body)


# --------------- High-level helpers used by signals/views ---------------

def notify_donor_food_approved(food):
    """Donor: donation approved."""
    donor = food.donor
    subject = "[HopeMeals] Your donation was approved"
    body = (
        f"Hi {getattr(donor, 'get_full_name', lambda: '')() or donor.username},\n\n"
        f"Your donation '{getattr(food, 'item_name', 'food')}' was approved.\n"
        f"Thank you for contributing!"
    )
    notify_user(donor, subject, body)


def notify_donor_food_expired(food):
    """Donor: donation expired."""
    donor = food.donor
    subject = "[HopeMeals] Your donation expired"
    body = (
        f"Hi {getattr(donor, 'get_full_name', lambda: '')() or donor.username},\n\n"
        f"Your donation '{getattr(food, 'item_name', 'food')}' has expired."
    )
    notify_user(donor, subject, body)


def notify_receiver_request_accepted(order):
    """Receiver: their request was accepted (order created)."""
    r = order.receiver
    item = _order_item_label(order)
    subject = "[HopeMeals] Your request was accepted"
    body = (
        f"Hi {getattr(r, 'get_full_name', lambda: '')() or r.username},\n\n"
        f"Your request for '{item}' was accepted. "
        f"We'll keep you posted on delivery updates."
    )
    notify_user(r, subject, body)



def notify_receiver_order_status(order):
    """Receiver: order status changed (Picked Up / In Transit / Delivered)."""
    r = order.receiver
    item = _order_item_label(order)
    status = str(order.status).replace("_", " ").title()
    subject = f"[HopeMeals] Order update: {status}"
    body = (
        f"Hi {getattr(r, 'get_full_name', lambda: '')() or r.username},\n\n"
        f"Your order for '{item}' is now {status}."
    )
    notify_user(r, subject, body)


# app/notifications.py (add this helper near the top with the others)

def _order_item_label(order) -> str:
    """
    Return a human-friendly item label for an order, without assuming a specific schema.
    Tries common attributes first; falls back to the first allocation's donation item name.
    """
    # 1) Common direct fields people use
    for attr in ("food", "donation", "requested_food", "item", "request"):
        obj = getattr(order, attr, None)
        if obj:
            # try common name fields on that related object
            for name_attr in ("item_name", "name", "title"):
                val = getattr(obj, name_attr, None)
                if val:
                    return str(val)
            return str(obj)

    # 2) Use first allocation's donation
    try:
        alloc = order.allocations.select_related("donation").first()
        if alloc and getattr(alloc, "donation", None):
            dn = alloc.donation
            return getattr(dn, "item_name", None) or getattr(dn, "title", None) or str(dn)
    except Exception:
        pass

    # 3) Last resort
    return "your food"


