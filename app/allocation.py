# hopemeals/allocation.py
from typing import List, Tuple
from django.db import transaction
from .models import FoodDonation, ReceiverOrder, Allocation, composite_donor_rating

def choose_ngo_for_item(item_name: str):
    """
    Pick the NGO who has the most stock for this item.
    Returns (ngo_user, total_stock) or (None, 0)
    """
    by_ngo = {}
    qs = FoodDonation.objects.filter(
        item_name__iexact=item_name,
        status__in=["ACCEPTED", "PARTIAL"],
        inventory_remaining__gt=0,
    ).select_related("accepted_by", "donor")
    for d in qs:
        if not d.accepted_by:
            continue
        by_ngo.setdefault(d.accepted_by, 0)
        by_ngo[d.accepted_by] += d.inventory_remaining
    if not by_ngo:
        return None, 0
    ngo, stock = max(by_ngo.items(), key=lambda kv: kv[1])
    return ngo, stock


@transaction.atomic
def allocate_order(order: ReceiverOrder) -> List[Allocation]:
    """
    Allocate order.people_count across donations of the *same NGO* and item_name,
    prioritizing donors by composite rating (avg NGO+Receiver).
    """
    assert order.ngo, "Order must have NGO set before allocation."
    needed = order.people_count
    made: List[Allocation] = []

    donations = (FoodDonation.objects
                 .filter(item_name__iexact=order.item_name,
                         accepted_by=order.ngo,
                         status__in=["ACCEPTED", "PARTIAL"],
                         inventory_remaining__gt=0)
                 .select_related("donor"))

    # Sort by composite donor rating DESC, then fresher (earlier expires_at)
    donations = sorted(
        donations,
        key=lambda d: (composite_donor_rating(d.donor), -d.expires_at.timestamp()),
        reverse=True
    )

    for d in donations:
        if needed <= 0:
            break
        take = min(needed, d.inventory_remaining)
        if take <= 0:
            continue
        Allocation.objects.create(order=order, donation=d, quantity=take)
        d.inventory_remaining -= take
        d.status = "DELIVERED" if d.inventory_remaining == 0 else "PARTIAL"
        d.save(update_fields=["inventory_remaining", "status"])
        needed -= take

    if needed > 0:
        # Not enough inventory; rollback
        raise RuntimeError("Insufficient stock to allocate this order.")

    order.status = "ALLOCATED"
    order.save(update_fields=["status"])
    return list(order.allocations.select_related("donation", "donation__donor"))
