from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import Giveaway, GiveawayEntry, GiveawayWinner
from .admin import GiveawayAdminForm
from .draw_algorithm import (
    participant_weight,
    participant_weights,
    select_weighted_winners,
)
from .views import close_due_giveaways


class GiveawayFlowTests(APITestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.giveaway = Giveaway.objects.create(
            name="Community Giveaway",
            amount="2500000.00",
            number_of_winners=3,
        )

    def test_registration_login_and_phone_normalization(self):
        weak_password = self.client.post(
            reverse("register"),
            {"phone": "08012345678", "password": "short"},
            format="json",
        )
        self.assertEqual(weak_password.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", weak_password.data)

        response = self.client.post(
            reverse("register"),
            {"phone": "+234 801 234 5678", "password": "N0tCommon-Password-42"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["phone"], "2348012345678")
        self.assertTrue(self.user_model.objects.filter(username="2348012345678").exists())

        duplicate = self.client.post(
            reverse("register"),
            {"phone": "2348012345678", "password": "N0tCommon-Password-42"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

        login = self.client.post(
            reverse("login"),
            {"phone": "2348012345678", "password": "N0tCommon-Password-42"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertEqual(login.data["token"], response.data["token"])

    def test_participation_requires_login_and_counts_once(self):
        participate_url = reverse("participate")
        denied = self.client.post(participate_url, format="json")
        self.assertEqual(denied.status_code, status.HTTP_401_UNAUTHORIZED)

        user = self.user_model.objects.create_user(
            username="08012345678",
            password="N0tCommon-Password-42",
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}",
        )

        first = self.client.post(participate_url, format="json")
        second = self.client.post(participate_url, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["giveaway"]["participants_count"], 1)
        self.assertTrue(first.data["giveaway"]["has_joined"])
        self.assertEqual(
            GiveawayEntry.objects.filter(giveaway=self.giveaway, user=user).count(),
            1,
        )

    def test_public_giveaway_does_not_expose_users(self):
        user = self.user_model.objects.create_user(
            username="08012345678",
            password="N0tCommon-Password-42",
        )
        GiveawayEntry.objects.create(giveaway=self.giveaway, user=user)

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["giveaway"]["participants_count"], 1)
        self.assertNotIn("entries", response.data["giveaway"])
        self.assertNotIn("phone", response.data["giveaway"])

    def test_no_open_giveaway_returns_empty_state(self):
        self.giveaway.delete()

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(
            response.data,
            {"giveaway": None, "giveaways": [], "closed_giveaways": []},
        )

    def test_public_giveaway_lists_all_open_giveaways_newest_first(self):
        older_giveaway = self.giveaway
        newer_giveaway = Giveaway.objects.create(
            name="Newer Giveaway",
            amount="100000.00",
            number_of_winners=1,
        )

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(
            [item["id"] for item in response.data["giveaways"]],
            [newer_giveaway.id, older_giveaway.id],
        )
        self.assertEqual(response.data["giveaway"]["id"], newer_giveaway.id)

    def test_due_giveaway_closes_and_returns_result_while_another_is_active(self):
        self.giveaway.draw_date = timezone.now() - timedelta(seconds=1)
        self.giveaway.save()
        active_giveaway = Giveaway.objects.create(
            name="Still Active",
            amount="100000.00",
            number_of_winners=1,
            draw_date=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(response.data["giveaway"]["id"], active_giveaway.id)
        self.assertEqual(
            [item["id"] for item in response.data["giveaways"]],
            [active_giveaway.id],
        )
        self.assertEqual(
            [item["id"] for item in response.data["closed_giveaways"]],
            [self.giveaway.id],
        )

    def test_participation_joins_the_requested_giveaway(self):
        older_giveaway = self.giveaway
        newer_giveaway = Giveaway.objects.create(
            name="Newer Giveaway",
            amount="100000.00",
            number_of_winners=1,
        )
        user = self.user_model.objects.create_user(
            username="08012345678",
            password="Test-password-123!",
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}",
        )

        response = self.client.post(
            reverse("participate"),
            {"giveaway_id": older_giveaway.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["giveaway"]["has_joined"])
        self.assertEqual(
            GiveawayEntry.objects.filter(giveaway=older_giveaway, user=user).count(),
            1,
        )
        self.assertEqual(
            GiveawayEntry.objects.filter(giveaway=newer_giveaway, user=user).count(),
            0,
        )

    def test_legacy_lottery_api_is_removed(self):
        response = self.client.get("/api/lotteries/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_giveaway_exposes_its_draw_date(self):
        draw_date = timezone.now() + timedelta(days=1)
        self.giveaway.draw_date = draw_date
        self.giveaway.save()

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(
            parse_datetime(response.data["giveaway"]["draw_date"]),
            draw_date,
        )

    def test_admin_draw_date_uses_a_datetime_picker(self):
        field = GiveawayAdminForm().fields["draw_date"]

        self.assertEqual(field.widget.input_type, "datetime-local")
        self.assertEqual(field.widget.format, "%Y-%m-%dT%H:%M")
        self.assertEqual(field.widget.attrs["data-timezone"], "Africa/Lagos")
        self.assertIn(
            "lotteries/admin/giveaway_datetime.js",
            GiveawayAdminForm().media._js,
        )

    def test_admin_draw_date_uses_nigerian_local_time(self):
        self.assertEqual(settings.TIME_ZONE, "Africa/Lagos")
        entered_time = datetime(2026, 11, 10, 18, 30)

        cleaned_time = GiveawayAdminForm().fields["draw_date"].clean(
            entered_time.strftime("%Y-%m-%dT%H:%M"),
        )

        self.assertEqual(
            timezone.localtime(cleaned_time).replace(tzinfo=None),
            entered_time,
        )

    def test_admin_only_registers_giveaway(self):
        registered_giveaway_models = [
            model
            for model in admin.site._registry
            if model._meta.app_label == "lotteries"
        ]

        self.assertEqual(registered_giveaway_models, [Giveaway])
        self.assertEqual(Giveaway._meta.verbose_name_plural, "Giveaways")

    def test_scheduled_close_selects_and_publishes_winners_once(self):
        users = [
            self.user_model.objects.create_user(
                username=f"0801234567{digit}",
                password="N0tCommon-Password-42",
            )
            for digit in range(3)
        ]
        GiveawayEntry.objects.bulk_create(
            GiveawayEntry(giveaway=self.giveaway, user=user)
            for user in users
        )
        self.giveaway.draw_date = timezone.now() - timedelta(seconds=1)
        self.giveaway.save()

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(response.data["giveaway"]["status"], Giveaway.Status.CLOSED)
        self.assertEqual(len(response.data["giveaway"]["winners"]), 3)
        self.giveaway.refresh_from_db()
        self.assertEqual(self.giveaway.winners.count(), 3)
        self.assertIsNotNone(self.giveaway.closed_at)
        self.assertTrue(
            all("phone" not in winner for winner in response.data["giveaway"]["winners"]),
        )

        self.giveaway.refresh_from_db()
        winner = self.giveaway.winners.first().user
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=winner).key}",
        )
        winner_response = self.client.get(reverse("open-giveaway"))
        self.assertTrue(winner_response.data["giveaway"]["is_winner"])
        self.assertEqual(
            sum(
                item["is_current_user"]
                for item in winner_response.data["giveaway"]["winners"]
            ),
            1,
        )

    def test_scheduled_close_with_no_participants_has_no_winners(self):
        self.giveaway.draw_date = timezone.now() - timedelta(seconds=1)
        self.giveaway.save()

        response = self.client.get(reverse("open-giveaway"))

        self.assertEqual(response.data["giveaway"]["status"], Giveaway.Status.CLOSED)
        self.assertEqual(response.data["giveaway"]["winners"], [])

    def test_participation_is_rejected_after_scheduled_close(self):
        self.giveaway.draw_date = timezone.now() - timedelta(seconds=1)
        self.giveaway.save()
        user = self.user_model.objects.create_user(
            username="08012345678",
            password="N0tCommon-Password-42",
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}",
        )

        response = self.client.post(reverse("participate"), format="json")

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertEqual(GiveawayEntry.objects.filter(user=user).count(), 0)
        self.assertEqual(self.giveaway.winners.count(), 0)

    def test_participant_weights_apply_first_entry_and_consecutive_win_rules(self):
        first_timer = self.user_model.objects.create_user(
            username="08011111111",
            password="Test-password-123!",
        )
        repeat_winner = self.user_model.objects.create_user(
            username="08022222222",
            password="Test-password-123!",
        )
        regular = self.user_model.objects.create_user(
            username="08033333333",
            password="Test-password-123!",
        )
        interrupted_streak = self.user_model.objects.create_user(
            username="08044444444",
            password="Test-password-123!",
        )

        previous_giveaways = [
            Giveaway.objects.create(
                name=f"Previous Giveaway {index}",
                amount="100000.00",
                number_of_winners=1,
                status=Giveaway.Status.CLOSED,
                closed_at=timezone.now() - timedelta(days=3 - index),
            )
            for index in range(3)
        ]
        for user in (repeat_winner, regular, interrupted_streak):
            GiveawayEntry.objects.create(
                giveaway=previous_giveaways[0],
                user=user,
            )
        for user in (repeat_winner, regular, interrupted_streak):
            GiveawayEntry.objects.create(
                giveaway=previous_giveaways[1],
                user=user,
            )
        GiveawayEntry.objects.create(
            giveaway=previous_giveaways[2],
            user=interrupted_streak,
        )
        GiveawayWinner.objects.create(
            giveaway=previous_giveaways[0],
            user=repeat_winner,
        )
        GiveawayWinner.objects.create(
            giveaway=previous_giveaways[1],
            user=repeat_winner,
        )
        GiveawayWinner.objects.create(
            giveaway=previous_giveaways[0],
            user=interrupted_streak,
        )
        GiveawayWinner.objects.create(
            giveaway=previous_giveaways[2],
            user=interrupted_streak,
        )

        GiveawayEntry.objects.bulk_create(
            GiveawayEntry(giveaway=self.giveaway, user=user)
            for user in (first_timer, repeat_winner, regular, interrupted_streak)
        )

        weights = participant_weights(self.giveaway)

        self.assertEqual(weights[first_timer.id], 1.2)
        self.assertEqual(weights[repeat_winner.id], 0.05)
        self.assertEqual(weights[regular.id], 1.0)
        self.assertEqual(weights[interrupted_streak.id], 1.0)

    def test_first_entry_boost_is_global_across_open_giveaways(self):
        user = self.user_model.objects.create_user(
            username="08055555555",
            password="Test-password-123!",
        )
        earlier_giveaway = Giveaway.objects.create(
            name="Earlier Open Giveaway",
            amount="100000.00",
            number_of_winners=1,
        )
        GiveawayEntry.objects.create(giveaway=earlier_giveaway, user=user)
        GiveawayEntry.objects.create(giveaway=self.giveaway, user=user)

        self.assertEqual(participant_weights(earlier_giveaway)[user.id], 1.2)
        self.assertEqual(participant_weights(self.giveaway)[user.id], 1.0)

    def test_weighted_draw_returns_distinct_winners(self):
        random_source = Mock()
        random_source.random.side_effect = [0.34, 0.9]

        winners = select_weighted_winners(
            {10: 1.0, 20: 1.0, 30: 1.0},
            winner_count=2,
            random_source=random_source,
        )

        self.assertEqual(winners, [20, 30])
        self.assertEqual(len(winners), len(set(winners)))

    def test_repeat_winner_weight_takes_precedence_over_first_entry_boost(self):
        self.assertEqual(
            participant_weight(is_first_entry=True, consecutive_wins=2),
            0.05,
        )

    def test_same_time_giveaways_use_the_same_history_snapshot(self):
        repeat_winner = self.user_model.objects.create_user(
            username="08066666666",
            password="Test-password-123!",
        )
        previous_giveaway = Giveaway.objects.create(
            name="Previous Giveaway",
            amount="100000.00",
            number_of_winners=1,
            status=Giveaway.Status.CLOSED,
            closed_at=timezone.now() - timedelta(days=1),
        )
        GiveawayEntry.objects.create(
            giveaway=previous_giveaway,
            user=repeat_winner,
        )
        GiveawayWinner.objects.create(
            giveaway=previous_giveaway,
            user=repeat_winner,
        )

        draw_date = timezone.now() - timedelta(seconds=1)
        simultaneous_giveaway = Giveaway.objects.create(
            name="Simultaneous Giveaway",
            amount="100000.00",
            number_of_winners=1,
            draw_date=draw_date,
        )
        self.giveaway.number_of_winners = 1
        self.giveaway.draw_date = draw_date
        self.giveaway.save()
        GiveawayEntry.objects.create(
            giveaway=self.giveaway,
            user=repeat_winner,
        )
        GiveawayEntry.objects.create(
            giveaway=simultaneous_giveaway,
            user=repeat_winner,
        )

        observed_weights = []

        def select_first_weighted_winner(weights, winner_count):
            observed_weights.append(weights[repeat_winner.id])
            return [repeat_winner.id]

        with patch(
            "lotteries.views.select_weighted_winners",
            side_effect=select_first_weighted_winner,
        ):
            close_due_giveaways()

        self.assertEqual(observed_weights, [1.0, 1.0])
