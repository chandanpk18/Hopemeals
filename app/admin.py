from django.contrib import admin
from django.contrib.auth.models import Group,User
from .models import HelpRequest
# Register your models here.

class HelpRequestAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'status')
    search_fields = ('username', 'email')
    list_filter = ('status',)

admin.site.register(HelpRequest, HelpRequestAdmin)

from .models import Profile, FoodDonation, NGOInventory, FoodRequest, Delivery, NGORating, ReceiverRating,Food

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "avg_ngo_rating", "avg_receiver_rating")

@admin.register(FoodDonation)
class FoodDonationAdmin(admin.ModelAdmin):
    list_display = ("item_name", "donor", "status", "quantity_people", "inventory_remaining", "prepared_at", "expires_at")
    list_filter = ("status",)
    search_fields = ("item_name", "donor__username")

@admin.register(NGOInventory)
class NGOInventoryAdmin(admin.ModelAdmin):
    list_display = ("ngo", "food", "quantity_remaining", "updated_at")

@admin.register(FoodRequest)
class FoodRequestAdmin(admin.ModelAdmin):
    list_display = ("receiver", "ngo", "food", "people_count", "status", "created_at")

@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("ngo", "status", "started_at", "delivered_at")




admin.site.register(NGORating)
admin.site.register(ReceiverRating)
class FoodAdmin(admin.ModelAdmin):
    list_display = ('donor','quantity_people')

admin.site.register(Food, FoodAdmin)    