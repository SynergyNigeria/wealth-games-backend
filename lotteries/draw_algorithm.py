import math
import secrets

from django.db.models import F, Window
from django.db.models.functions import RowNumber

from .models import GiveawayEntry, GiveawayWinner


BASE_WEIGHT = 1.0
NEW_ENTRANT_WEIGHT = 1.2
REPEAT_WINNER_WEIGHT = 0.05
CONSECUTIVE_WIN_THRESHOLD = 2


def participant_weight(*, is_first_entry, consecutive_wins):
    if consecutive_wins >= CONSECUTIVE_WIN_THRESHOLD:
        return REPEAT_WINNER_WEIGHT
    if is_first_entry:
        return NEW_ENTRANT_WEIGHT
    return BASE_WEIGHT


def participant_weights(giveaway):
    participant_ids = list(
        GiveawayEntry.objects.filter(giveaway=giveaway)
        .order_by("user_id")
        .values_list("user_id", flat=True)
    )
    if not participant_ids:
        return {}

    first_entries = (
        GiveawayEntry.objects.filter(user_id__in=participant_ids)
        .annotate(
            entry_rank=Window(
                expression=RowNumber(),
                partition_by=[F("user_id")],
                order_by=[F("joined_at").asc(), F("pk").asc()],
            ),
        )
        .filter(entry_rank=1)
        .values_list("user_id", "giveaway_id")
    )
    first_giveaway_by_user = dict(first_entries)

    recent_completed_entries = (
        GiveawayEntry.objects.filter(
            user_id__in=participant_ids,
            giveaway__status="closed",
            giveaway__closed_at__isnull=False,
        )
        .exclude(giveaway=giveaway)
        .annotate(
            giveaway_rank=Window(
                expression=RowNumber(),
                partition_by=[F("user_id")],
                order_by=[
                    F("giveaway__closed_at").desc(),
                    F("giveaway_id").desc(),
                ],
            ),
        )
        .filter(giveaway_rank__lte=CONSECUTIVE_WIN_THRESHOLD)
        .values_list("user_id", "giveaway_id")
    )
    recent_entries_by_user = {user_id: [] for user_id in participant_ids}
    for user_id, giveaway_id in recent_completed_entries:
        recent_entries_by_user[user_id].append(giveaway_id)

    previous_giveaway_ids = {
        giveaway_id
        for giveaway_ids in recent_entries_by_user.values()
        for giveaway_id in giveaway_ids
    }
    previous_wins = set(
        GiveawayWinner.objects.filter(
            user_id__in=participant_ids,
            giveaway_id__in=previous_giveaway_ids,
        ).values_list("user_id", "giveaway_id")
    )

    weights = {}
    for user_id in participant_ids:
        recent_entries = recent_entries_by_user[user_id]
        consecutive_wins = 0
        for previous_giveaway_id in recent_entries:
            if (user_id, previous_giveaway_id) not in previous_wins:
                break
            consecutive_wins += 1

        weights[user_id] = participant_weight(
            is_first_entry=first_giveaway_by_user[user_id] == giveaway.id,
            consecutive_wins=consecutive_wins,
        )

    return weights


def select_weighted_winners(participant_weight_by_id, winner_count, random_source=None):
    if winner_count < 0:
        raise ValueError("winner_count must not be negative.")

    remaining = list(participant_weight_by_id.items())
    for _, weight in remaining:
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("Participant weights must be finite and positive.")

    random_source = random_source or secrets.SystemRandom()
    winners = []
    for _ in range(min(winner_count, len(remaining))):
        total_weight = sum(weight for _, weight in remaining)
        target = random_source.random() * total_weight
        cumulative_weight = 0.0
        selected_index = len(remaining) - 1
        for index, (_, weight) in enumerate(remaining):
            cumulative_weight += weight
            if target < cumulative_weight:
                selected_index = index
                break
        winners.append(remaining.pop(selected_index)[0])

    return winners
