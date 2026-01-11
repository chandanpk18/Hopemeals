# app/expires_food.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from .models import FoodDonation
from .notifications import notify_user

class Command(BaseCommand):
    help = "Mark expired foods based on expires_at and notify donors"

    def handle(self, *args, **kwargs):
        now = timezone.now()
        qs = FoodDonation.objects.filter(
            status__in=["PENDING", "ACCEPTED", "PARTIAL"],
            expires_at__lt=now
        ).select_related("donor")
        count = 0
        for f in qs:
            f.status = "EXPIRED"
            f.inventory_remaining = 0
            f.save(update_fields=["status", "inventory_remaining"])
            donor = f.donor
            notify_user(
                donor,
                "[HopeMeals] Your donation expired",
                (
                    f"Hi {donor.get_full_name() or donor.username},\n\n"
                    f"Your donation '{f.item_name}' has expired and is no longer available."
                )
            )
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Expired {count} items"))
