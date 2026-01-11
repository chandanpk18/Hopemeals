from django.conf import settings
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.auth.models import User
from datetime import timedelta
from django.contrib.auth import get_user_model

# Create your models here.
#this is for customer support table
class HelpRequest(models.Model):
    STATUS_CHOICES = [
        ('New', 'New'),
        ('Resolved', 'Resolved'),
    ]
    username = models.CharField(max_length=100)
    email = models.EmailField()
    help_text = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='New')

    def __str__(self):
        return self.username
    
ROLE_DONOR = "Donor"
ROLE_NGO = "NGO"
ROLE_RECEIVER = "Receiver"

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=30, blank=True)
    # Store running averages for convenience (also computable dynamically)
    avg_ngo_rating = models.FloatField(default=0.0)
    avg_receiver_rating = models.FloatField(default=0.0)

    def __str__(self):
        return f"Profile({self.user.username})"

class FoodDonation(models.Model):
    STATUS = (
        ("PENDING", "Pending NGO review"),
        ("ACCEPTED", "Accepted by NGO / In inventory"),
        ("REJECTED", "Rejected by NGO"),
        ("EXPIRED", "Expired"),
        ("DELIVERED", "Fully delivered"),
        ("PARTIAL", "Partially delivered"),
    )

    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="donations")
    item_name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    # Quantity in people-servings
    quantity_people = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    inventory_remaining = models.PositiveIntegerField(default=0)

    # Prepared time and computed expiry
    prepared_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    # 30-minute buffer to ensure food can be delivered before expiry
    delivery_buffer_minutes = models.PositiveIntegerField(default=30)

    image = models.ImageField(upload_to="food_images/", blank=True, null=True)

    # Geolocation for pickup (Donor selects on map)
    pickup_lat = models.FloatField()
    pickup_lng = models.FloatField()

    status = models.CharField(max_length=12, choices=STATUS, default="PENDING")

    # If accepted, which NGO
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="accepted_foods")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Link to legacy Food for reviews (created automatically)
    food_shadow = models.OneToOneField(
        'Food',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='donation_shadow'
    )

    def __str__(self):
        return f"{self.item_name} by {self.donor.username} ({self.status})"

    def mark_expired_if_needed(self):
        if self.status in ("PENDING", "ACCEPTED", "PARTIAL") and timezone.now() > self.expires_at:
            self.status = "EXPIRED"
            self.inventory_remaining = 0
            self.save(update_fields=["status", "inventory_remaining"])
            return True
        return False

    def ensure_food_shadow(self):
        """
        Create or update the legacy Food row that mirrors this donation.
        Used so reviews (Rating) can target Food while logistics use FoodDonation.
        """
        from .models import Food  # local import to avoid circulars
        if self.food_shadow and self.food_shadow_id:
            # update basic fields if changed
            f = self.food_shadow
            f.item_name = self.item_name
            f.description = self.description
            f.quantity_people = self.quantity_people
            f.prepared_at = self.prepared_at
            f.expires_at = self.expires_at
            f.image = self.image
            f.pickup_lat = self.pickup_lat
            f.pickup_lng = self.pickup_lng
            # keep status as-is (POSTED/COMPLETED) – acceptance is tracked on FoodDonation
            f.save()
            return f

        f = Food.objects.create(
            donor=self.donor,
            item_name=self.item_name,
            description=self.description,
            quantity_people=self.quantity_people,
            prepared_at=self.prepared_at,
            expires_at=self.expires_at,
            image=self.image,
            pickup_lat=self.pickup_lat,
            pickup_lng=self.pickup_lng,
            status="POSTED",
            ngo=None,
        )
        self.food_shadow = f
        self.save(update_fields=["food_shadow"])
        return f

    def accepted_shadow_touch(self, ngo_user):
        if not self.food_shadow_id:
            self.ensure_food_shadow()
        if self.food_shadow and self.food_shadow.ngo != ngo_user:
            self.food_shadow.ngo = ngo_user
            self.food_shadow.save(update_fields=["ngo"])


class NGOInventory(models.Model):
    ngo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ngo_inventory")
    food = models.OneToOneField(FoodDonation, on_delete=models.CASCADE, related_name="inventory")
    quantity_remaining = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Inventory({self.ngo.username}: {self.food.item_name} -> {self.quantity_remaining})"


class NGORating(models.Model):
    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ngo_ratings_received")
    food = models.ForeignKey(FoodDonation, on_delete=models.CASCADE, related_name="ngo_ratings")
    ngo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ngo_ratings_given")
    stars = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ReceiverRating(models.Model):
    donor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="receiver_ratings_received")
    food = models.ForeignKey(FoodDonation, on_delete=models.CASCADE, related_name="receiver_ratings")
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="receiver_ratings_given")
    stars = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

User = get_user_model()

class Food(models.Model):
    donor = models.ForeignKey(User, on_delete=models.CASCADE, related_name="foods")
    item_name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    quantity_people = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    prepared_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    image = models.ImageField(upload_to="foods/", blank=True, null=True)

    # FREE MAP: no key needed, just store coords
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=[("POSTED","POSTED"),("EXPIRED","EXPIRED"),("COMPLETED","COMPLETED")],
        default="POSTED"
    )
    ngo = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_foods")

    created_at = models.DateTimeField(auto_now_add=True)

    def remaining_people(self):
        from django.db.models import Sum
        taken = self.requests.filter(status__in=["ACCEPTED","DELIVERED"]).aggregate(
            Sum("people_count")
        )["people_count__sum"] or 0
        return max(0, self.quantity_people - taken)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.item_name} ({self.donor})"

class Rating(models.Model):
    """
    Separate ratings:
      - role = 'NGO' rating for donor's food
      - role = 'RECEIVER' rating for donor's food
    """
    ROLE_CHOICES = [("NGO","NGO"),("RECEIVER","RECEIVER")]
    food = models.ForeignKey(Food, on_delete=models.CASCADE, related_name="ratings")
    rater = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    stars = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("food","rater","role")
    
class FoodRequest(models.Model):
    food = models.ForeignKey(Food, on_delete=models.CASCADE, related_name="requests")
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name="food_requests")
    ngo = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_requests")

    people_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    # Receiver's delivery location (Leaflet picker)
    delivery_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=[("REQUESTED", "REQUESTED"), ("ACCEPTED", "ACCEPTED"), ("DELIVERED", "DELIVERED"),
                 ("REJECTED", "REJECTED")],
        default="REQUESTED"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Simple guard: ensure quantity <= remaining at save (also validated in the view)
    def clean(self):
        if self.people_count < 1:
            raise ValidationError("People count must be >= 1")
        if self.pk is None:  # only on create
            if self.people_count > self.food.remaining_people():
                raise ValidationError("Requested quantity exceeds available inventory.")

class Delivery(models.Model):
    STATUS = (
        ("PICKED_UP", "Picked up from donor"),
        ("IN_TRANSIT", "In transit"),
        ("DELIVERED", "Delivered"),
    )
    ngo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deliveries")
    order = models.OneToOneField('ReceiverOrder', on_delete=models.CASCADE, related_name="delivery")

    status = models.CharField(max_length=12, choices=STATUS, default="PICKED_UP")
    live_lat = models.FloatField(null=True, blank=True)
    live_lng = models.FloatField(null=True, blank=True)
    route_json = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Delivery(Order #{self.order_id} -> {self.order.receiver.username})"

# hopemeals/models.py  (APPEND at end)
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver

def composite_donor_rating(user):
    """
    Composite = average of donor's avg NGO rating and avg Receiver rating.
    Falls back to 0.0 if none.
    """
    try:
        p = user.profile
        have = []
        if p.avg_ngo_rating > 0:
            have.append(p.avg_ngo_rating)
        if p.avg_receiver_rating > 0:
            have.append(p.avg_receiver_rating)
        return round(sum(have)/len(have), 3) if have else 0.0
    except Profile.DoesNotExist:
        return 0.0


class ReceiverOrder(models.Model):
    """
    A receiver's request that can be satisfied by *multiple* FoodDonations
    of the same item_name that belong to the SAME NGO.
    """
    STATUS = (
        ("REQUESTED", "Requested"),
        ("APPROVED", "Approved"),
        ("ALLOCATED", "Allocated"),
        ("DELIVERED", "Delivered"),
        ("REJECTED", "Rejected"),
    )

    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    ngo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders_to_fulfil")
    item_name = models.CharField(max_length=120)
    people_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    delivery_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    delivery_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    status = models.CharField(max_length=12, choices=STATUS, default="REQUESTED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.people_count < 1:
            raise ValidationError("People count must be >= 1")

    def total_allocated(self):
        return self.allocations.aggregate(s=Sum("quantity"))["s"] or 0


class Allocation(models.Model):
    """
    How an order is split across donor donations.
    """
    order = models.ForeignKey(ReceiverOrder, on_delete=models.CASCADE, related_name="allocations")
    donation = models.ForeignKey(FoodDonation, on_delete=models.CASCADE, related_name="allocations")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    created_at = models.DateTimeField(auto_now_add=True)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


class NGOLocation(models.Model):
    # One-to-one keeps at most one default location per NGO user
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ngo_location"
    )
    # default (primary) location
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    address_line = models.CharField(max_length=255, blank=True)  # optional: human-readable address
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["lat", "lng"]),
        ]

    def __str__(self):
        return f"NGOLocation({self.user.username})"
