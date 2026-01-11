import os, json, random, requests, math
from collections import defaultdict
from app.notifications import notify_user
from app.notifications import notify_donor_food_approved
import google.generativeai as genai
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login as logs,authenticate,logout,update_session_auth_hash
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.urls import reverse
from django.db import transaction
from django.db.models import Count,Sum,Avg,F,Q,FloatField,Value,ExpressionWrapper,Case,When
from django.db.models.functions import TruncDate, Coalesce
from django.utils import timezone
from .models import Profile,HelpRequest,FoodDonation,NGOInventory,Delivery,NGORating,ReceiverRating,ReceiverOrder,Food,FoodRequest,Rating, NGOLocation
# --- Forms (new flow) ---
from .forms import CustomUserCreationForm,FoodDonationForm,NGORatingForm,ReceiverRatingForm,ReceiverOrderForm
# --- Utilities ---
from .utils_ai import parse_food_note
from .allocation import allocate_order, choose_ngo_for_item
from .notifications import send_email_notification, send_sms_notification
from .pdfs import allocation_pdf
from datetime import timedelta
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django.contrib.auth.models import User, Group
from .models import NGOLocation
from django.db.models import Avg, Q, Value, IntegerField


genai.configure(api_key="AIzaSyCaJSXCU9lcH2Q4nBSb-8Hh7CtMgS1QLJ0")


# -----------------------------
# Role checks
# -----------------------------
def is_admin(user):    return user.is_superuser
def is_ngo(user):      return user.groups.filter(name='NGO').exists()
def is_receiver(user): return user.groups.filter(name='Receiver').exists()
def is_donor(user):    return user.groups.filter(name='Donor').exists()


# -----------------------------
# Helpers for review mirroring
# -----------------------------
def _get_or_create_food_shadow(fd: FoodDonation) -> Food:
    """
    Ensure there's a legacy Food row that mirrors a FoodDonation.
    We don't persist the FK; we find-or-create by (donor, item_name, prepared_at, expires_at).
    """
    f = (Food.objects
         .filter(donor=fd.donor,
                 item_name=fd.item_name,
                 prepared_at=fd.prepared_at,
                 expires_at=fd.expires_at)
         .order_by('-id')
         .first())
    if f:
        # Update any fields that may change
        changed = False
        if f.description != fd.description:
            f.description = fd.description; changed = True
        if f.quantity_people != fd.quantity_people:
            f.quantity_people = fd.quantity_people; changed = True
        if f.image != fd.image:
            f.image = fd.image; changed = True
        # Pickup coords (legacy use DecimalFields)
        try:
            lat = float(fd.pickup_lat) if fd.pickup_lat is not None else None
            lng = float(fd.pickup_lng) if fd.pickup_lng is not None else None
            if f.pickup_lat != lat or f.pickup_lng != lng:
                f.pickup_lat = lat
                f.pickup_lng = lng
                changed = True
        except Exception:
            pass
        if changed: f.save()
        return f

    # Create fresh legacy Food
    return Food.objects.create(
        donor=fd.donor,
        item_name=fd.item_name,
        description=fd.description,
        quantity_people=fd.quantity_people,
        prepared_at=fd.prepared_at,
        expires_at=fd.expires_at,
        image=fd.image,
        pickup_lat=fd.pickup_lat,
        pickup_lng=fd.pickup_lng,
        status="POSTED",
        ngo=None,
    )


def _sync_food_rating_from_donation(fd: FoodDonation, role: str, rater_user, stars: int, comment: str = ""):
    """
    Mirror a rating event from FoodDonation -> legacy Food.Rating so old dashboards work.
    unique_together(food, rater, role) makes this idempotent.
    """
    food_shadow = _get_or_create_food_shadow(fd)
    Rating.objects.update_or_create(
        food=food_shadow,
        rater=rater_user,
        role=role,
        defaults={"stars": stars, "comment": comment},
    )


def _recompute_donor_averages(donor_user):
    """
    Keep Profile.avg_ngo_rating / avg_receiver_rating in sync with NGORating/ReceiverRating.
    """
    ngo_qs = donor_user.ngo_ratings_received.all()
    recv_qs = donor_user.receiver_ratings_received.all()
    avg_ngo = round(sum(r.stars for r in ngo_qs) / ngo_qs.count(), 2) if ngo_qs.exists() else 0.0
    avg_recv = round(sum(r.stars for r in recv_qs) / recv_qs.count(), 2) if recv_qs.exists() else 0.0
    prof, _ = Profile.objects.get_or_create(user=donor_user)
    prof.avg_ngo_rating = avg_ngo
    prof.avg_receiver_rating = avg_recv
    prof.save(update_fields=["avg_ngo_rating", "avg_receiver_rating"])


# -----------------------------
# Core pages
# -----------------------------
def index(request):
    return render(request, 'index.html')

def signup(request):
    return render(request, 'signup.html')


@login_required(login_url='login')
def dashboard(request):
    if request.user.is_superuser:
        return redirect('admins')
    elif is_ngo(request.user):
        return redirect('ngo')
    elif is_donor(request.user):
        return redirect('donor')
    else:
        return redirect('receiver')


# -----------------------------
# Registration & auth
# -----------------------------
def dregister(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Group.objects.get_or_create(name='Donor')[0].user_set.add(user)
            messages.success(request, 'User registered successfully!')
            logs(request, user)
            return redirect('dashboard')
    else:
        form = CustomUserCreationForm()
    return render(request, 'donors.html', {'form': form})

def nregister(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Group.objects.get_or_create(name='NGO')[0].user_set.add(user)
            messages.success(request, 'User registered successfully!')
            logs(request, user)
            return redirect('dashboard')
    else:
        form = CustomUserCreationForm()
    return render(request, 'ngos.html', {'form': form})

def rregister(request):
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Group.objects.get_or_create(name='Receiver')[0].user_set.add(user)
            messages.success(request, 'User registered successfully!')
            logs(request, user)
            return redirect('dashboard')
    else:
        form = CustomUserCreationForm()
    return render(request, 'receivers.html', {'form': form})

def login(request):
    if request.method == "GET":
        return render(request, 'login.html', {"form": AuthenticationForm()})
    form = AuthenticationForm(data=request.POST)
    if form.is_valid():
        user = authenticate(username=form.cleaned_data['username'],
                            password=form.cleaned_data['password'])
        if user is not None:
            logs(request, user)
            return redirect('dashboard')
    return render(request, 'login.html', {"form": form})

def signout(request):
    logout(request)
    return redirect('login')


# -----------------------------
# Profile
# -----------------------------
@login_required(login_url='login')
def profile_edit(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_profile':
            user = request.user
            user.email = request.POST.get('email', '').strip()
            name = request.POST.get('name', '').strip()
            parts = name.split(None, 1)
            user.first_name = parts[0] if parts else ''
            user.last_name  = parts[1] if len(parts) > 1 else ''
            user.save()
            phone = request.POST.get('phone', '').strip()
            if hasattr(user, 'profile'):
                user.profile.phone = phone
                user.profile.save()
            messages.success(request, "Profile updated.")
        elif action == 'change_password':
            old = request.POST.get('old_password')
            new1 = request.POST.get('new_password1')
            new2 = request.POST.get('new_password2')
            if not request.user.check_password(old):
                messages.error(request, "Current password is incorrect.")
            elif new1 != new2:
                messages.error(request, "New passwords do not match.")
            else:
                request.user.set_password(new1)
                request.user.save()
                update_session_auth_hash(request, request.user)
                messages.success(request, "Password updated.")
        return redirect('profile_edit')
    return render(request, 'profile_edit.html')


# -----------------------------
# Dashboards
# -----------------------------
# ---------------- Receiver dashboard ----------------
@login_required(login_url='login')
@user_passes_test(is_receiver)
def receiver(request):
    # Current code: stocks, orders, kpis...
    orders = (ReceiverOrder.objects
              .filter(receiver=request.user)
              .select_related("ngo")
              .prefetch_related("allocations__donation__donor")
              .order_by("-created_at"))

    counts = orders.values("status").annotate(c=Count("id"))
    kpis = {x["status"]: x["c"] for x in counts}
    for k in ("REQUESTED","APPROVED","ALLOCATED","DELIVERED"):
        kpis.setdefault(k, 0)

    my_ratings = {r.food_id: r.stars for r in ReceiverRating.objects.filter(receiver=request.user)}

    # NEW: Approved foods with ratings
    approved_food = (
    FoodDonation.objects
    .filter(
        status__in=["ACCEPTED", "PARTIAL"],
        inventory_remaining__gt=0,
        expires_at__gt=timezone.now(),
    )
    .annotate(
        # Avg NGO rating for THIS food
        ngo_avg=Coalesce(Avg("ngo_ratings__stars"), Value(0.0), output_field=FloatField()),
        # Donor’s overall NGO rating (received across all their donations)
        donor_avg=Coalesce(
            Avg("donor__ngo_ratings_received__stars"),
            Value(0.0),
            output_field=FloatField()
        ),
    )
    .order_by("-ngo_avg", "-donor_avg", "-id")
    )
    

    item_options = [
    {
        "id": f.id,
        "name": f.item_name,
        "available": f.inventory_remaining,
        "ngo_avg": f.ngo_avg,
        "donor_avg": f.donor_avg,
    }
    for f in approved_food
    ]

    return render(request, "receiver.html", {
        "orders": orders,
        "kpis": kpis,
        "item_options": item_options,
        "my_rated_ids": list(my_ratings.keys()),
        "my_ratings_list": list(my_ratings.items()),
        "approved_food": approved_food,   # pass to template
    })


@login_required(login_url='login')
@user_passes_test(is_donor)
def donor(request):
    qs = FoodDonation.objects.filter(donor=request.user)

    # ---- KPI totals (unchanged from your improved dashboard) ----
    posted    = qs.count()
    pending   = qs.filter(status="PENDING").count()
    approved  = qs.filter(status__in=["ACCEPTED", "PARTIAL"]).count()
    expired   = qs.filter(status="EXPIRED").count()
    completed = qs.filter(inventory_remaining=0).count()

    offered_people = qs.aggregate(s=Coalesce(Sum("quantity_people"), 0))["s"]
    served_people  = qs.aggregate(s=Coalesce(Sum(F("quantity_people") - F("inventory_remaining")), 0))["s"]

    approval_rate = round((approved / posted) * 100) if posted else 0
    utilization   = round((served_people / offered_people) * 100) if offered_people else 0

    totals = {
        "posted": posted,
        "pending": pending,
        "approved": approved,
        "completed": completed,
        "expired": expired,
        "offered_people": offered_people,
        "served_people": served_people,
        "approval_rate": approval_rate,
        "utilization": utilization,
    }

    # ---- Recent posts filter ----
    flt = request.GET.get("filter", "all").lower()

    base_qs = qs
    if flt == "posted":
        # actively posted: not expired and still has remaining inventory
        base_qs = base_qs.exclude(status="EXPIRED").filter(inventory_remaining__gt=0)
    elif flt == "delivered":
        # fully served/depleted
        base_qs = base_qs.filter(inventory_remaining=0)
    elif flt == "expired":
        base_qs = base_qs.filter(status="EXPIRED")
    else:
        flt = "all"  # normalize anything else

    # counts for pills
    filter_counts = {
        "all": posted,
        "posted": qs.exclude(status="EXPIRED").filter(inventory_remaining__gt=0).count(),
        "delivered": qs.filter(inventory_remaining=0).count(),
        "expired": qs.filter(status="EXPIRED").count(),
    }

    # annotate served + served percentage for progress bar
    foods = (
        base_qs.annotate(
            served=F("quantity_people") - F("inventory_remaining"),
        ).annotate(
            served_pct=Case(
                When(
                    quantity_people__gt=0,
                    then=ExpressionWrapper(
                        (F("quantity_people") - F("inventory_remaining")) * 100.0 / F("quantity_people"),
                        output_field=FloatField(),
                    ),
                ),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
        .order_by("-created_at")[:10]
    )

    # Ratings (keep as before)
    donor_food_ids = Food.objects.filter(donor=request.user).values_list("id", flat=True)
    avg_ngo  = Rating.objects.filter(food_id__in=donor_food_ids, role="NGO").aggregate(Avg("stars"))["stars__avg"] or 0
    avg_recv = Rating.objects.filter(food_id__in=donor_food_ids, role="RECEIVER").aggregate(Avg("stars"))["stars__avg"] or 0
    avg_ngo_pct  = round(avg_ngo * 20)
    avg_recv_pct = round(avg_recv * 20)
    last = qs.exclude(pickup_lat=None, pickup_lng=None).order_by("-created_at").first()
    sugg = suggested_ngos_for_anchor(getattr(last, "pickup_lat", None), getattr(last, "pickup_lng", None)) if last else []


    return render(request, "donor.html", {
        "foods": foods,
        "totals": totals,
        "avg_ngo": avg_ngo,
        "avg_recv": avg_recv,
        "avg_ngo_pct": avg_ngo_pct,
        "avg_recv_pct": avg_recv_pct,
        "current_filter": flt,
        "filter_counts": filter_counts,
        "suggested_ngos": sugg, 
    })

# ---------------- NGO dashboard ----------------
@login_required
@user_passes_test(is_ngo)
def ngo(request):
    awaiting  = FoodDonation.objects.filter(status="PENDING").count()
    approved  = FoodDonation.objects.filter(status__in=["ACCEPTED", "PARTIAL"], accepted_by=request.user, expires_at__gt=timezone.now()).count()
    requested = ReceiverOrder.objects.filter(
        status__in=["REQUESTED", "APPROVED", "ALLOCATED"]
    ).filter(
        Q(ngo=request.user) | Q(ngo__isnull=True)
    ).count()
    delivered = ReceiverOrder.objects.filter(status="DELIVERED", ngo=request.user).count()

    # Lists (show unassigned too, so NGO can claim/approve)
    awaiting_donations = (FoodDonation.objects
        .filter(status="PENDING")
        .order_by("-created_at")[:10])

    approved_food = (FoodDonation.objects
        .filter(status__in=["ACCEPTED", "PARTIAL"], accepted_by=request.user, expires_at__gt=timezone.now())
        .order_by("-created_at")[:15])

    requested_food = (ReceiverOrder.objects
        .filter(status__in=["REQUESTED", "APPROVED", "ALLOCATED"])
        .filter(Q(ngo=request.user) | Q(ngo__isnull=True))
        .select_related("receiver")
        .order_by("-created_at")[:15])

    deliveries = (Delivery.objects
        .filter(ngo=request.user)
        .exclude(status="DELIVERED")
        .select_related("order", "order__receiver")
        .order_by("-started_at")[:15])

    ngo_ratings = {r.food_id: r.stars for r in NGORating.objects.filter(ngo=request.user)}
    pending = list(
    FoodDonation.objects.filter(status="PENDING").select_related("donor")
    )

    rows = []
    for f in pending:
        allowed_ids = nearest_ngo_ids_for_donation(f, limit=2)
        rows.append({
            "food": f,
            "can_accept": request.user.id in allowed_ids,
        })
    return render(request, "ngo.html", {
        "pending_rows": rows,
        "counts": {"awaiting":awaiting,"approved":approved,"requested":requested,"delivered":delivered},
        "awaiting_donations": awaiting_donations,
        "approved_food": approved_food,
        "requested_food": requested_food,
        "deliveries": deliveries,
        "ngo_rated_ids": list(ngo_ratings.keys()),
        "ngo_ratings_list": list(ngo_ratings.items()),
    })


@login_required
@user_passes_test(is_ngo)
def ngo_location_settings(request):
    from .models import NGOLocation
    loc, _ = NGOLocation.objects.get_or_create(user=request.user)

    if request.method == "POST":
        loc.lat = request.POST.get("lat") or None
        loc.lng = request.POST.get("lng") or None
        loc.address_line = (request.POST.get("address_line") or "").strip()
        loc.save()
        messages.success(request, "Default NGO location updated.")
        return redirect("ngo")

    return render(request, "ngo_location_settings.html", {"loc": loc})

# ---------------- Donor: remove manual delivery buffer; randomize 10–45 ----------------
@login_required
@user_passes_test(is_donor)
def donor_create_food(request):
    if request.method == "POST":
        item_name = (request.POST.get("item_name") or "").strip()
        qty_people = int(request.POST.get("quantity_people", "0") or 0)
        expires_at = parse_user_datetime(request.POST.get("expires_at"))  # assuming helper

        if not item_name or qty_people <= 0 or not expires_at:
            messages.error(request, "Please fill all fields correctly.")
            return redirect("donor")

        # previously: buffer_minutes = int(request.POST["buffer_minutes"])
        buffer_minutes = random.randint(10, 45)  # NEW: randomize 10–45 minutes

        donation = FoodDonation.objects.create(
            donor=request.user,
            item_name=item_name,
            quantity_people=qty_people,
            inventory_remaining=qty_people,
            expires_at=expires_at,
            status="PENDING",
            delivery_buffer_minutes=buffer_minutes,  # keep column if model has it
        )
        messages.success(request, f"Thank you! Delivery buffer set to {buffer_minutes} minutes automatically.")
        return redirect("donor")

    # GET
    return render(request, "donor.html", {
        "auto_buffer_note": "Delivery buffer will be auto-set between 10–45 minutes.",
    })


def _haversine_km(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2): return float("inf")
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def suggested_ngos_for_anchor(anchor_lat, anchor_lng, limit=2):
    """Return closest NGOs to the anchor (lat,lng) using NGOLocation; empty list if anchor missing."""
    try:
        lat = float(anchor_lat); lng = float(anchor_lng)
    except (TypeError, ValueError):
        return []

    ngo_group = Group.objects.get(name="NGO")
    ngos = (User.objects
            .filter(groups=ngo_group)
            .select_related("ngo_location"))

    scored = []
    for u in ngos:
        loc = getattr(u, "ngo_location", None)
        if not loc or loc.lat is None or loc.lng is None:
            continue
        dist = _haversine_km(lat, lng, loc.lat, loc.lng)
        scored.append((dist, u))

    scored.sort(key=lambda x: x[0])
    return [u for _, u in scored[:limit]]

def nearest_ngo_ids_for_donation(food, limit=2):
    """Return a list of NGO user IDs nearest to this donation's pickup point."""
    try:
        anchor_lat = float(food.pickup_lat)
        anchor_lng = float(food.pickup_lng)
    except (TypeError, ValueError):
        return []
    try:
        ngo_group = Group.objects.get(name="NGO")
        ngo_users = User.objects.filter(groups=ngo_group).select_related("ngo_location")
    except Group.DoesNotExist:
        return []

    scored = []
    for u in ngo_users:
        loc = getattr(u, "ngo_location", None)
        if not loc or loc.lat is None or loc.lng is None:
            continue
        d = _haversine_km(anchor_lat, anchor_lng, loc.lat, loc.lng)
        scored.append((d, u.id))
    scored.sort(key=lambda x: x[0])
    return [uid for _, uid in scored[:limit]]

# -----------------------------
# Help & Admin
# -----------------------------
def help(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        help_text = request.POST.get('help')
        if username and email:
            HelpRequest.objects.create(username=username, email=email, help_text=help_text)
            return render(request, 'help.html', {'alert_message': "Your query is submitted successfully."})
    return render(request, 'help.html')


@login_required(login_url='login')
@user_passes_test(is_admin)
def admin(request):
    return render(request, 'admin.html')


def admin_stats_api(request):
    """
    JSON for charts using the *current* logistics flow.
    - donations_over_days -> deliveries per day (Delivery.delivered_at)
    - status_counts       -> Pending/Allocated from ReceiverOrder, Expired from FoodDonation
    - ratings_avg         -> Profile averages (not legacy Rating model)
    - offered/delivered   -> totals of people offered vs delivered
    - help_request_counts -> New vs Resolved
    """
    now = timezone.now()
    start = now - timezone.timedelta(days=13)  # last 14 days

    # 1) Deliveries per day (count)
    per_day = (
        Delivery.objects
        .filter(status="DELIVERED", delivered_at__date__gte=start.date())
        .annotate(day=TruncDate("delivered_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"].isoformat(): row["count"] for row in per_day}
    days = [(start + timezone.timedelta(days=i)).date().isoformat() for i in range(14)]
    donations_over_days = [{"date": d, "count": by_day.get(d, 0)} for d in days]

    # 2) Status buckets for donut + filters
    pending_orders   = ReceiverOrder.objects.filter(status__in=["REQUESTED", "APPROVED"]).count()
    allocated_orders = ReceiverOrder.objects.filter(status="ALLOCATED").count()
    expired_donations = FoodDonation.objects.filter(status="EXPIRED").count()
    status_counts = [
        {"status": "PENDING",   "count": pending_orders},
        {"status": "ALLOCATED", "count": allocated_orders},
        {"status": "EXPIRED",   "count": expired_donations},
    ]

    # 3) Fulfillment totals (people)
    offered_people = FoodDonation.objects.aggregate(
        s=Coalesce(Sum("quantity_people"), 0)
    )["s"]
    delivered_people = ReceiverOrder.objects.filter(status="DELIVERED").aggregate(
        s=Coalesce(Sum("people_count"), 0)
    )["s"]

    # 4) Ratings from Profile table
    ratings_avg = {
        "ngo_avg": Profile.objects.aggregate(a=Avg("avg_ngo_rating"))["a"],
        "receiver_avg": Profile.objects.aggregate(a=Avg("avg_receiver_rating"))["a"],
    }

    # 5) Help requests (New/Resolved)
    help_request_counts = list(
        HelpRequest.objects.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return JsonResponse({
        "donations_over_days": donations_over_days,

        # Donut data (and alias kept for older frontends):
        "status_counts": status_counts,
        "food_status_counts": status_counts,

        # Ratings
        "ratings_avg": ratings_avg,

        # Fulfillment: top-level keys + nested alias for compatibility
        "offered_people": offered_people,
        "delivered_people": delivered_people,
        "delivered_vs_offered": {
            "offered_people": offered_people,
            "delivered_people": delivered_people,
        },

        # Help requests
        "help_request_counts": help_request_counts,
    }, safe=False)

# -----------------------------
# Chatbot demo
# -----------------------------
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def chatbot(request):
    if request.method == "POST":
        # Support both JSON and form submissions
        user_input = ""
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return JsonResponse({"error": "Invalid JSON"}, status=400)
            user_input = (payload.get("message") or "").strip()
        else:
            user_input = (request.POST.get("message") or "").strip()

        if not user_input:
            return JsonResponse({"error": "Message is required"}, status=400)

        # (Make sure genai.configure(api_key=...) is done at startup)
        model = genai.GenerativeModel("gemini-2.5-flash")
        resp = model.generate_content(user_input)
        text = getattr(resp, "text", None) or "Sorry, no response."
        return JsonResponse({"response": text})

    # GET -> render page (sets csrftoken cookie)
    return render(request, "chatbot.html")

def check_food_quality(description):
    url = 'https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent'
    api_key = os.getenv('AIzaSyDAv4UEGoc2BuZjff7H1GdKsbxILPgIZnY')
    payload = {'contents': [{'parts': [{'text': description}]}]}
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    try:
        resp = requests.post(url, json=payload, headers=headers)
        out = resp.json()
        return out.get('candidates', [{}])[0].get('content', 'No feedback.')
    except Exception:
        return "AI check failed."


# -----------------------------
# Donor flow (FoodDonation)
# -----------------------------
@login_required
@user_passes_test(is_donor)
@transaction.atomic
def donor_post_food(request):
    if request.method == "POST":
        # If the form still requires `delivery_buffer_minutes` but it's not in the POST
        # (because we removed the input from the HTML), inject a placeholder so the form validates.
        data = request.POST.copy()
        if "delivery_buffer_minutes" not in data:
            data["delivery_buffer_minutes"] = "15"  # dummy; will be overridden below

        form = FoodDonationForm(data, request.FILES)
        if form.is_valid():
            item_name = form.cleaned_data["item_name"]
            qty = form.cleaned_data["quantity_people"]
            raw_note = form.cleaned_data["raw_note"]
            pickup_lat = form.cleaned_data["pickup_lat"]
            pickup_lng = form.cleaned_data["pickup_lng"]

            # parse your combined note into description / prepared / expiry
            desc, prepared_at, expires_at = parse_food_note(raw_note)

            # NEW: auto-random delivery buffer (10–45 minutes)
            auto_buffer = random.randint(10, 45)

            food = FoodDonation.objects.create(
                donor=request.user,
                item_name=item_name,
                description=desc,
                quantity_people=qty,
                inventory_remaining=qty,
                prepared_at=prepared_at,
                expires_at=expires_at,
                delivery_buffer_minutes=auto_buffer,  # write randomized value
                image=form.cleaned_data.get("image"),
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                status="PENDING",
            )

            # Create/update shadow Food for reviews (legacy Rating)
            _get_or_create_food_shadow(food)

            messages.success(
                request,
                f"Food posted! Delivery buffer auto-set to {auto_buffer} minutes. Waiting for NGO review."
            )
            return redirect("donor")
    else:
        # Note to show in the template that buffer is automatic now
        # GET
        form = FoodDonationForm()
        anchor_lat = request.GET.get("pickup_lat")
        anchor_lng = request.GET.get("pickup_lng")
        suggested_ngos = suggested_ngos_for_anchor(anchor_lat, anchor_lng)

    return render(request, "donor_post_food.html", {
        "form": form,
        "auto_buffer_note": "Delivery buffer is auto-set between 10–45 minutes.",
        "suggested_ngos": suggested_ngos,   # <— add
    })


# add near the top of views.py imports
import math
from django.contrib.auth.models import User, Group
from .models import NGOLocation

def _haversine_km(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def suggested_ngos_for_anchor_with_distance(anchor_lat, anchor_lng, limit=2):
    """Return [{'user': <User>, 'distance_km': float, 'address': str}] for nearest NGOs."""
    try:
        anchor_lat = float(anchor_lat)
        anchor_lng = float(anchor_lng)
    except (TypeError, ValueError):
        return []

    # get NGO users
    try:
        ngo_group = Group.objects.get(name="NGO")
        ngo_users = User.objects.filter(groups=ngo_group).select_related("ngo_location")
    except Group.DoesNotExist:
        ngo_users = User.objects.none()

    scored = []
    for u in ngo_users:
        loc = getattr(u, "ngo_location", None)
        if not loc or loc.lat is None or loc.lng is None:
            continue
        dist = _haversine_km(anchor_lat, anchor_lng, loc.lat, loc.lng)
        scored.append({
            "user": u,
            "distance_km": dist,
            "address": loc.address_line or ""
        })
    scored.sort(key=lambda d: d["distance_km"])
    return scored[:limit]


@login_required
@user_passes_test(is_donor)
def donor_food_detail(request, id):
    food = get_object_or_404(FoodDonation, id=id, donor=request.user)

    if (
    food.expires_at and
    food.expires_at <= timezone.now() and
    food.status in ("PENDING", "ACCEPTED", "PARTIAL")
    ):
        food.status = "EXPIRED"
        food.inventory_remaining = 0
        food.save(update_fields=["status", "inventory_remaining"])


    rx_qs = (ReceiverRating.objects
             .filter(food_id=food.id)
             .select_related("receiver")
             .order_by("-created_at"))
    ngo_qs = (NGORating.objects
              .filter(food_id=food.id)
              .select_related("ngo")
              .order_by("-created_at"))

    ratings = []
    for r in rx_qs:
        ratings.append({
            "stars": getattr(r, "stars", 0),
            "comment": getattr(r, "comment", ""),
            "created_at": getattr(r, "created_at", None),
            "receiver": getattr(r, "receiver", None),
            "role": "Receiver",
        })
    for r in ngo_qs:
        ratings.append({
            "stars": getattr(r, "stars", 0),
            "comment": getattr(r, "comment", ""),
            "created_at": getattr(r, "created_at", None),
            "ngo": getattr(r, "ngo", None),
            "role": "NGO",
        })

    stars_only = [r["stars"] for r in ratings if r["stars"]]
    num_ratings = len(stars_only)
    avg_stars = int(round(sum(stars_only) / num_ratings)) if num_ratings else 0  # 0–5 int

    # NEW: nearest NGOs to this donation's pickup location
    suggested = suggested_ngos_for_anchor_with_distance(food.pickup_lat, food.pickup_lng, limit=2)

    return render(request, "donor_food_detail.html", {
        "food": food,
        "ratings": ratings,
        "num_ratings": num_ratings,
        "avg_stars": avg_stars,
        "suggested_ngos": suggested,   # <-- add to context
    })


# -----------------------------
# NGO: review/accept/reject + inventory
# -----------------------------
@login_required
@user_passes_test(is_ngo)
def ngo_review_queue(request):
    now = timezone.now()
    # Expire due items
    for f in FoodDonation.objects.filter(status="PENDING"):
        f.mark_expired_if_needed()
    items = (FoodDonation.objects
             .filter(status="PENDING", expires_at__gt=now)
             .order_by("expires_at"))
    return render(request, "ngo_review_queue.html", {"items": items})


@login_required
@user_passes_test(is_ngo)
@transaction.atomic
def ngo_accept_food(request, pk):
    # Lock the row to avoid races
    food = get_object_or_404(FoodDonation.objects.select_for_update(), pk=pk)

    # Auto-expire if needed, then validate status
    food.mark_expired_if_needed()
    if food.status in ("EXPIRED", "REJECTED"):
        messages.error(request, "This donation is no longer available.")
        return redirect("ngo")

    # If already accepted/partial, handle accordingly
    if food.status in ("ACCEPTED", "PARTIAL"):
        if food.accepted_by == request.user:
            messages.info(request, "You already accepted this donation.")
            return redirect("ngo")
        messages.error(request, "Another NGO has already accepted this donation.")
        return redirect("ngo")

    # Must be pending at this point
    if food.status != "PENDING":
        messages.error(request, "This donation is not in a pending state.")
        return redirect("ngo")

    # NEW: allow only the two closest NGOs to accept
    allowed_ids = nearest_ngo_ids_for_donation(food, limit=2)
    if request.user.id not in allowed_ids:
        messages.error(request, "This donation is reserved for nearby NGOs.")
        return redirect("ngo")

    # Accept + bootstrap inventory
    food.status = "ACCEPTED"
    food.accepted_by = request.user
    food.inventory_remaining = food.quantity_people
    food.save(update_fields=["status", "accepted_by", "inventory_remaining"])

    # Send donor notification email (unchanged)
    notify_donor_food_approved(food)

    # Inventory row create/update
    NGOInventory.objects.update_or_create(
        ngo=request.user,
        food=food,
        defaults={"quantity_remaining": food.inventory_remaining}
    )

    # Mark NGO on legacy Food shadow so reviews can reference NGO too
    shadow = _get_or_create_food_shadow(food)
    if shadow.ngo != request.user:
        shadow.ngo = request.user
        shadow.save(update_fields=["ngo"])

    messages.success(request, f"Accepted '{food.item_name}' and added to your inventory.")
    return redirect("ngo")

@login_required
@user_passes_test(is_ngo)
@transaction.atomic
def ngo_reject_food(request, pk):
    food = get_object_or_404(FoodDonation.objects.select_for_update(), pk=pk)
    if food.status != "PENDING":
        messages.error(request, "This donation is not pending anymore.")
        return redirect("ngo")
    food.status = "REJECTED"
    food.save(update_fields=["status"])
    messages.success(request, "Donation rejected.")
    return redirect("ngo")


@login_required
@user_passes_test(is_ngo)
def ngo_inventory(request):
    foods = (FoodDonation.objects
             .filter(accepted_by=request.user, status__in=["ACCEPTED", "PARTIAL"])
             .order_by("expires_at"))
    for f in foods:
        f.mark_expired_if_needed()
    foods = [f for f in foods if f.status != "EXPIRED"]
    return render(request, "ngo_inventory.html", {"foods": foods})


# -----------------------------
# Receiver: browse → request (order flow)
# -----------------------------
from django.db.models import Prefetch

def receiver_browse(request):
    # Only show accepted/partial, not expired, with stock
    qs = (FoodDonation.objects
          .filter(status__in=["ACCEPTED", "PARTIAL"],
                  inventory_remaining__gt=0,
                  expires_at__gt=timezone.now())
          .select_related("donor", "accepted_by")
          .prefetch_related("ngo_ratings"))

    donations = list(qs)

    def avg_ngo(d):
        rs = [r.stars for r in d.ngo_ratings.all()]
        return sum(rs)/len(rs) if rs else 0.0

    from .models import composite_donor_rating
    def donor_avg(d):
        try:
            return composite_donor_rating(d.donor)
        except Exception:
            return 0.0

    donations.sort(key=lambda d: (avg_ngo(d), donor_avg(d)), reverse=True)

    return render(request, "receiver_browse.html", {"donations": donations})



@login_required
@user_passes_test(is_receiver)
def receiver_requests(request):
    orders = ReceiverOrder.objects.filter(receiver=request.user).order_by("-created_at")
    return render(request, "receiver_requests.html", {"orders": orders})


# New request: validate item exists and qty <= available
from django.db.models import Sum
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404

@login_required(login_url='login')
@user_passes_test(is_receiver)
def receiver_request_order(request):
    if request.method != "POST":
        return redirect("receiver")

    # read the id sent by the form
    food_id = request.POST.get("food_id")
    try:
        people_count = int(request.POST.get("people_count", "0"))
    except ValueError:
        people_count = 0

    lat = request.POST.get("delivery_lat")
    lng = request.POST.get("delivery_lng")

    if not (food_id and people_count > 0 and lat and lng):
        messages.error(request, "Please choose an item, valid quantity and pick a location on map.")
        return redirect("receiver")

    # find the selected donation
    donation = FoodDonation.objects.filter(
        id=food_id,
        status__in=["ACCEPTED", "PARTIAL"],
        inventory_remaining__gt=0,
        expires_at__gt=timezone.now()
    ).first()

    if not donation:
        messages.error(request, "Selected item is no longer available.")
        return redirect("receiver")

    item_name = donation.item_name

    # current availability (you can keep your existing logic)
    available = (
        FoodDonation.objects
        .filter(
            item_name=item_name,
            status__in=["ACCEPTED", "PARTIAL"],
            inventory_remaining__gt=0,
            expires_at__gt=timezone.now(),
        )
        .aggregate(total=Sum("inventory_remaining"))["total"] or 0
    )

    if people_count > available:
        messages.error(request, f"Only {available} available for {item_name}. Please reduce the quantity.")
        return redirect("receiver")

    # Create the order
    ReceiverOrder.objects.create(
        receiver=request.user,
        item_name=item_name,
        people_count=people_count,
        delivery_lat=lat,
        delivery_lng=lng,
        status="REQUESTED",
    )

    messages.success(request, "Request submitted.")
    return redirect("receiver")


# -----------------------------
# NGO: orders → approve & allocate (PDF)
# -----------------------------
@login_required
@user_passes_test(is_ngo)
def ngo_orders_list(request):
    orders = (ReceiverOrder.objects
    .filter(status__in=["REQUESTED", "APPROVED", "ALLOCATED", "DELIVERED"])
    .filter(Q(ngo=request.user) | Q(ngo__isnull=True))
    .select_related("receiver")
    .order_by("-created_at"))
    return render(request, "ngo_orders.html", {"orders": orders})


@login_required
@user_passes_test(is_ngo)
@transaction.atomic
def ngo_approve_order(request, order_id):
    # Allow unassigned orders to be claimed by this NGO
    order = get_object_or_404(ReceiverOrder.objects.select_for_update(), pk=order_id)

    # If another NGO already owns it, block
    if order.ngo_id and order.ngo_id != request.user.id:
        messages.error(request, "This order is handled by another NGO.")
        return redirect("ngo")

    # Claim if unassigned; bump REQUESTED → APPROVED
    changed = []
    if not order.ngo_id:
        order.ngo = request.user
        changed.append("ngo")
    if order.status == "REQUESTED":
        order.status = "APPROVED"
        changed.append("status")
    if changed:
        order.save(update_fields=changed)

    # Allocate inventory (idempotent in your allocator; raises on failure)
    try:
        allocations = allocate_order(order)  # should set status to ALLOCATED when successful
    except RuntimeError as e:
        messages.error(request, str(e))
        return redirect("ngo")

    # Ensure a Delivery row exists so it shows in "Active Deliveries"
    Delivery.objects.get_or_create(ngo=request.user, order=order)

    # ----- Notifications (email + SMS) -----
    subj = f"[HopeMeals] Your request for {order.item_name} is approved"
    body = (
        f"Hi {order.receiver.username}, your order #{order.id} for {order.people_count} people "
        f"has been approved and is being prepared."
    )

    # Receiver notifications
    try:
        recv_email = getattr(order.receiver, "email", "") or ""
        recv_phone = getattr(getattr(order.receiver, "profile", None), "phone", "") or ""
        if recv_email:
            send_email_notification(recv_email, subj, body)
        if recv_phone:
            send_sms_notification(recv_phone, body)
    except Exception:
        pass  # keep flow resilient

    # Donor notifications (optional but nice): inform each allocated donor
    try:
        for a in order.allocations.select_related("donation__donor", "donation"):
            donor = a.donation.donor
            d_email = getattr(donor, "email", "") or ""
            d_phone = getattr(getattr(donor, "profile", None), "phone", "") or ""
            d_subj = f"[HopeMeals] Allocation for your donation #{a.donation.id}"
            d_body = (
                f"Your donation '{a.donation.item_name}' has {a.quantity} people allocated "
                f"to receiver order #{order.id}. Thank you!"
            )
            if d_email:
                send_email_notification(d_email, d_subj, d_body)
            if d_phone:
                send_sms_notification(d_phone, d_body)
    except Exception:
        pass

    # Return the allocation PDF (your helper)
    return allocation_pdf(order, allocations)

@login_required
@user_passes_test(is_ngo)
def ngo_combined_map(request, order_id):
    order = get_object_or_404(ReceiverOrder, pk=order_id, ngo=request.user)
    allocations = list(order.allocations.select_related("donation"))
    pickups = [{
        "lat": float(a.donation.pickup_lat),
        "lng": float(a.donation.pickup_lng),
        "label": f"{a.donation.donor.username} ({a.quantity})"
    } for a in allocations]
    drop = {
        "lat": float(order.delivery_lat or 0),
        "lng": float(order.delivery_lng or 0),
        "label": f"{order.receiver.username}"
    }
    return render(request, "ngo_combined_map.html", {"pickups": pickups, "drop": drop, "order": order})


# -----------------------------
# Deliveries (optional live update API)
# -----------------------------
@login_required
@user_passes_test(is_ngo)
def ngo_deliveries(request):
    dels = (Delivery.objects
        .filter(ngo=request.user)
        .select_related("order", "order__receiver")
        .order_by("-started_at"))
    return render(request, "ngo_deliveries.html", {"deliveries": dels})


# in ngo_approve_order, after allocations = allocate_order(order)
from .models import Delivery
# Delivery.objects.get_or_create(ngo=request.user, order=order)
@login_required
@user_passes_test(is_ngo)
@transaction.atomic
def delivery_update_status(request, delivery_id):
    d = get_object_or_404(Delivery, pk=delivery_id, ngo=request.user)
    new_status = request.POST.get("status")  # PICKED_UP / IN_TRANSIT / DELIVERED
    if new_status not in {"PICKED_UP", "IN_TRANSIT", "DELIVERED"}:
        return HttpResponseForbidden("Bad status")

    d.status = new_status

    # Optional live location
    if "live_lat" in request.POST and "live_lng" in request.POST:
        try:
            d.live_lat = float(request.POST["live_lat"])
            d.live_lng = float(request.POST["live_lng"])
        except Exception:
            pass

    if new_status == "DELIVERED":
        d.delivered_at = timezone.now()

        # 1) mark ORDER delivered
        order = d.order
        order.status = "DELIVERED"
        order.save(update_fields=["status"])

        # 2) decrement each allocated donation
        for a in order.allocations.select_related("donation"):
            fd = a.donation
            fd.inventory_remaining = max(0, fd.inventory_remaining - a.quantity)
            fd.status = "DELIVERED" if fd.inventory_remaining == 0 else "PARTIAL"
            fd.save(update_fields=["inventory_remaining", "status"])
            # 3) keep NGOInventory in sync (if exists)
            try:
                inv = fd.inventory
                inv.quantity_remaining = fd.inventory_remaining
                inv.save(update_fields=["quantity_remaining"])
            except NGOInventory.DoesNotExist:
                pass

    d.save()
    messages.success(request, f"Delivery #{d.id} updated to {d.status}.")
    return redirect("ngo")

# Receiver order details page
# --- Order detail (shared) ---
from django.http import HttpResponseForbidden

@login_required
def _order_detail_shared(request, order_id):
    order = get_object_or_404(ReceiverOrder, pk=order_id)

    # Permission checks (same as before)...

    allocations = order.allocations.select_related("donation", "donation__donor")
    can_rate = (order.status == "DELIVERED") and is_receiver(request.user)

    delivery = getattr(order, "delivery", None)

    return render(request, "receiver_order_detail.html", {
        "order": order,
        "allocations": allocations,
        "can_rate": can_rate,
        "delivery": delivery,
        "is_ngo": is_ngo(request.user),
    })

# Receiver view (keeps existing URL working)
@login_required
@user_passes_test(is_receiver)
def receiver_order_detail(request, order_id):
    return _order_detail_shared(request, order_id)

# NEW: NGO view to open the same page
@login_required
@user_passes_test(is_ngo)
def ngo_order_detail(request, order_id):
    return _order_detail_shared(request, order_id)


# -----------------------------
# Ratings (NGO & Receiver)
# -----------------------------
@login_required
@user_passes_test(is_ngo)
@transaction.atomic
def ngo_rate_donor(request, food_pk):
    food = get_object_or_404(FoodDonation, pk=food_pk)
    form = NGORatingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        stars = form.cleaned_data["stars"]
        comment = form.cleaned_data.get("comment", "")
        NGORating.objects.create(
            donor=food.donor, food=food, ngo=request.user,
            stars=stars, comment=comment
        )
        # Mirror to legacy Food.Rating
        _sync_food_rating_from_donation(food, role="NGO", rater_user=request.user, stars=stars, comment=comment)
        _recompute_donor_averages(food.donor)
        messages.success(request, "Rated donor (NGO).")
        return redirect("ngo")
    return render(request, "rating_form.html", {"form": form, "who": "NGO"})


@login_required
@user_passes_test(is_receiver)
@transaction.atomic
def receiver_rate_donor(request, food_pk):
    food = get_object_or_404(FoodDonation, pk=food_pk)
    form = ReceiverRatingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        stars = form.cleaned_data["stars"]
        comment = form.cleaned_data.get("comment", "")
        ReceiverRating.objects.create(
            donor=food.donor, food=food, receiver=request.user,
            stars=stars, comment=comment
        )
        # Mirror to legacy Food.Rating
        _sync_food_rating_from_donation(food, role="RECEIVER", rater_user=request.user, stars=stars, comment=comment)
        _recompute_donor_averages(food.donor)
        messages.success(request, "Rated donor (Receiver).")
        return redirect("receiver")
    return render(request, "rating_form.html", {"form": form, "who": "Receiver"})


# -----------------------------
# Receiver live tracking (optional)
# -----------------------------
@login_required
@user_passes_test(is_receiver)
def get_delivery_live_location(request, delivery_pk):
    d = get_object_or_404(Delivery, pk=delivery_pk)
    return JsonResponse({"lat": d.live_lat, "lng": d.live_lng, "status": d.status})
