# hopemeals/forms.py
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    Profile,
    FoodDonation,
    ReceiverOrder,      # NEW
    NGORating,
    ReceiverRating,
)

User = get_user_model()


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=15, required=True, help_text="Enter your phone number")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "password1", "password2", "phone")

    def save(self, commit=True):
        user = super().save(commit=commit)
        phone = self.cleaned_data.get("phone", "").strip()
        prof, _ = Profile.objects.get_or_create(user=user)
        prof.phone = phone
        prof.save()
        return user


class FoodDonationForm(forms.ModelForm):
    raw_note = forms.CharField(
        label="Describe the food and when it was prepared",
        widget=forms.Textarea(attrs={"placeholder": "e.g., 3 trays of veg biryani prepared at 6:30 PM today; contains nuts."}),
        required=True,
    )

    class Meta:
        model = FoodDonation
        fields = [
            "item_name",
            "quantity_people",
            "image",
            "pickup_lat",
            "pickup_lng",
            "delivery_buffer_minutes",
        ]
        widgets = {
            "pickup_lat": forms.HiddenInput(),
            "pickup_lng": forms.HiddenInput(),
        }


class NGORatingForm(forms.ModelForm):
    class Meta:
        model = NGORating
        fields = ["stars", "comment"]


class ReceiverRatingForm(forms.ModelForm):
    class Meta:
        model = ReceiverRating
        fields = ["stars", "comment"]


from django import forms
from .models import ReceiverOrder

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django import forms
from .models import ReceiverOrder

class ReceiverOrderForm(forms.ModelForm):
    # match your model's DecimalField
    delivery_lat = forms.DecimalField(
        required=False, max_digits=9, decimal_places=6, widget=forms.HiddenInput()
    )
    delivery_lng = forms.DecimalField(
        required=False, max_digits=9, decimal_places=6, widget=forms.HiddenInput()
    )

    class Meta:
        model = ReceiverOrder
        fields = ["item_name", "people_count", "delivery_lat", "delivery_lng"]
        widgets = {
            "delivery_lat": forms.HiddenInput(),
            "delivery_lng": forms.HiddenInput(),
        }

    def clean(self):
        cleaned = super().clean()
        lat = cleaned.get("delivery_lat")
        lng = cleaned.get("delivery_lng")
        if lat in (None, "") or lng in (None, ""):
            raise forms.ValidationError("Please set a delivery location on the map.")

        # make sure they are exactly to 6 dp (eliminates float noise)
        q = Decimal("0.000001")
        try:
            cleaned["delivery_lat"] = Decimal(lat).quantize(q, rounding=ROUND_HALF_UP)
            cleaned["delivery_lng"] = Decimal(lng).quantize(q, rounding=ROUND_HALF_UP)
        except (InvalidOperation, TypeError, ValueError):
            raise forms.ValidationError("Invalid coordinates.")
        return cleaned


from .models import Food, FoodRequest


class FoodForm(forms.ModelForm):
    class Meta:
        model = Food
        fields = ["item_name","description","quantity_people","prepared_at","expires_at","image","pickup_lat","pickup_lng"]
        widgets = {
            "prepared_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "pickup_lat": forms.HiddenInput(),
            "pickup_lng": forms.HiddenInput(),
        }

class FoodRequestForm(forms.ModelForm):
    class Meta:
        model = FoodRequest
        fields = ["people_count","delivery_lat","delivery_lng"]
        widgets = {
            "delivery_lat": forms.HiddenInput(),
            "delivery_lng": forms.HiddenInput(),
        }