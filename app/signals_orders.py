# app/signals_orders.py
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from .models import ReceiverOrder
from .notifications import (
    notify_receiver_order_status,
    notify_receiver_request_accepted,
)

@receiver(pre_save, sender=ReceiverOrder)
def _capture_old_status(sender, instance: ReceiverOrder, **kwargs):
    """
    Before saving, fetch the existing status (if any) and store it on the instance.
    """
    if instance.pk:
        try:
            old = sender.objects.only("status").get(pk=instance.pk)
            instance._old_status = old.status
        except sender.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=ReceiverOrder)
def _notify_on_create_or_status_change(sender, instance: ReceiverOrder, created: bool, **kwargs):
    """
    - On create -> 'request accepted'
    - On status change -> order status update
    """
    if created:
        notify_receiver_request_accepted(instance)
        return

    old_status = getattr(instance, "_old_status", None)
    if old_status is not None and old_status != instance.status:
        notify_receiver_order_status(instance)
