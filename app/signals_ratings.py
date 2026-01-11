# app/signals_ratings.py (create this file)
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import NGORating, ReceiverRating
from .notifications import notify_user
from .models import ReceiverOrder


User = get_user_model()

@receiver(post_save, sender=NGORating)
def notify_donor_ngo_rated(sender, instance, created, **kwargs):
    if not created:
        return
    donor = instance.donor
    ngo = instance.ngo
    notify_user(
        donor,
        "[HopeMeals] NGO rated your donation",
        (
            f"Hi {donor.get_full_name() or donor.username},\n\n"
            f"{ngo.get_full_name() or ngo.username} rated your donation "
            f"'{instance.food.item_name}' with {instance.stars} stars.\n"
            f"Comment: {instance.comment or '—'}"
        ),
    )

@receiver(post_save, sender=ReceiverRating)
def notify_donor_receiver_rated(sender, instance, created, **kwargs):
    if not created:
        return
    donor = instance.donor
    receiver = instance.receiver
    notify_user(
        donor,
        "[HopeMeals] Receiver rated your donation",
        (
            f"Hi {donor.get_full_name() or donor.username},\n\n"
            f"{receiver.get_full_name() or receiver.username} rated your donation "
            f"'{instance.food.item_name}' with {instance.stars} stars.\n"
            f"Comment: {instance.comment or '—'}"
        ),
    )



