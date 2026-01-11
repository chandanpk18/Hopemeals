from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    path('',views.index,name='home'),   
    
    # signup
    path('dregister', views.dregister, name='donors'),
    path('nregister', views.nregister, name='ngos'),
    path('rregister', views.rregister, name='receivers'),
    path('signup', views.signup, name='signup'),

    # account
    path('login', views.login, name='login'),
    path('logout',views.signout,name='logout'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('profile',views.profile_edit,name='profile'),

    # chatbot and the contact us
    path('chatbot',views.chatbot,name='chatbot'),
    path('help', views.help, name='help'),
    
    # Dashboard
    path('admins', views.admin, name='admins'),
    path('ngo', views.ngo, name='ngo'),
    path('receiver', views.receiver, name='receiver'),
    path('donor', views.donor, name='donor'),
        # Donor
    path("donor/food/<int:id>/", views.donor_food_detail, name="donor_food_detail"),
    # path("donor/history/", views.donor_history, name="donor_history"),
    path("donor/food/new/", views.donor_post_food, name="donor_food_create"),

    # NGO
    path("ngo/settings/location/", views.ngo_location_settings, name="ngo_location_settings"),
    path("ngo/review/", views.ngo_review_queue, name="ngo_review_queue"),
    path("ngo/accept/<int:pk>/", views.ngo_accept_food, name="ngo_accept_food"),
    path("ngo/reject/<int:pk>/", views.ngo_reject_food, name="ngo_reject_food"),
    path("ngo/inventory/", views.ngo_inventory, name="ngo_inventory"),
    path("ngo/orders/", views.ngo_orders_list, name="ngo_orders"),
    path("ngo/orders/<int:order_id>/approve/", views.ngo_approve_order, name="ngo_approve_order"),
    path("ngo/orders/<int:order_id>/map/", views.ngo_combined_map, name="ngo_combined_map"),

    # Receiver (order-based flow)
    path("receiver/browse/", views.receiver_browse, name="receiver_browse"),
    path("receiver/request/", views.receiver_request_order, name="receiver_request_order"),
    path("receiver/requests/", views.receiver_requests, name="receiver_requests"),

    # Ratings (FoodDonation-based)
    path("rate/ngo/<int:food_pk>/", views.ngo_rate_donor, name="ngo_rate_donor"),
    path("rate/receiver/<int:food_pk>/", views.receiver_rate_donor, name="receiver_rate_donor"),

    # Ratings
   
    path("api/admin/stats/", views.admin_stats_api, name="admin_stats_api"),
    # NGO delivery tracking
    path("ngo/deliveries/", views.ngo_deliveries, name="ngo_deliveries"),

    # Receiver order detail
    path("receiver/orders/<int:order_id>/", views.receiver_order_detail, name="receiver_order_detail"),
    # NGO can open the order's status page
    path("ngo/orders/<int:order_id>/status/", views.ngo_order_detail, name="ngo_order_detail"),

    # Delivery status updates (order-based Delivery model)
    path("ngo/delivery/<int:delivery_id>/status/", views.delivery_update_status, name="delivery_update_status"),


] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    