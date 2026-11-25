from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms.models import inlineformset_factory
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from .models import (
    Bet,
    BetEntry,
    BetOption,
    BetWinner,
    Giveaway,
    GiveawayEntry,
    GiveawayWinner,
)
from .admin import (
    BetAdmin,
    BetAdminForm,
    BetOptionInlineFormSet,
    GiveawayAdminForm,
)
from .draw_algorithm import (
    participant_weight,
    participant_weights,
    select_weighted_winners,
)
from .views import close_due_giveaways


class BetFlowTests(APITestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.bet = Bet.objects.create(
            question="How many yellow cards will the match produce?",
            prize_pool="100.01",
        )
        self.low_range = BetOption.objects.create(
            bet=self.bet,
            lower_bound=0,
            upper_bound=2,
        )
        self.mid_range = BetOption.objects.create(
            bet=self.bet,
            lower_bound=3,
            upper_bound=5,
        )
        self.high_range = BetOption.objects.create(
            bet=self.bet,
            lower_bound=6,
        )

    def create_user(self, username):
        return self.user_model.objects.create_user(
            username=username,
            password="Test-password-123!",
        )

    def authenticate(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}",
        )

    def test_bet_options_format_numeric_ranges(self):
        self.assertEqual(self.low_range.label, "0–2")
        self.assertEqual(self.high_range.label, "6+")

    def test_admin_answer_ranges_must_not_overlap(self):
        formset_class = inlineformset_factory(
            Bet,
            BetOption,
            formset=BetOptionInlineFormSet,
            fields=("lower_bound", "upper_bound"),
            extra=2,
        )
        data = {
            "ranges-TOTAL_FORMS": "2",
            "ranges-INITIAL_FORMS": "0",
            "ranges-MIN_NUM_FORMS": "0",
            "ranges-MAX_NUM_FORMS": "1000",
            "ranges-0-lower_bound": "0",
            "ranges-0-upper_bound": "5",
            "ranges-1-lower_bound": "5",
            "ranges-1-upper_bound": "10",
        }
        formset = formset_class(
            data=data,
            instance=Bet(question="Test", prize_pool="10.00"),
            prefix="ranges",
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("Answer ranges must not overlap.", str(formset.non_form_errors()))

        data["ranges-1-lower_bound"] = "6"
        adjacent_formset = formset_class(
            data=data,
            instance=Bet(question="Test", prize_pool="10.00"),
            prefix="ranges",
        )
        self.assertTrue(adjacent_formset.is_valid(), adjacent_formset.errors)

    def test_readonly_answer_ranges_do_not_require_editable_fields(self):
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])
        formset = BetOptionInlineFormSet.__new__(BetOptionInlineFormSet)
        formset.instance = self.bet
        formset._errors = []
        formset.forms = [
            SimpleNamespace(cleaned_data={"id": self.low_range.pk}),
        ]

        with patch("django.forms.models.BaseModelFormSet.clean"):
            formset.clean()

    def test_bets_list_has_open_and_closed_groups_and_hides_unsettled_answer(self):
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])

        response = self.client.get(reverse("bets"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["open_bets"], [])
        self.assertEqual(response.data["closed_bets"][0]["id"], self.bet.id)
        self.assertIsNone(response.data["closed_bets"][0]["correct_option_label"])

    def test_participation_requires_auth_and_rejects_reentry(self):
        participate_url = reverse("bet-participate")
        payload = {"bet_id": self.bet.id, "option_id": self.mid_range.id}

        denied = self.client.post(participate_url, payload, format="json")
        self.assertEqual(denied.status_code, status.HTTP_401_UNAUTHORIZED)

        user = self.create_user("bet_participant")
        self.authenticate(user)
        first = self.client.post(participate_url, payload, format="json")
        second = self.client.post(
            participate_url,
            {"bet_id": self.bet.id, "option_id": self.high_range.id},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["bet"]["selected_option_label"], "3–5")
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            BetEntry.objects.get(bet=self.bet, user=user).option,
            self.mid_range,
        )

    def test_participation_rejects_an_option_from_another_bet(self):
        other_bet = Bet.objects.create(
            question="How many corners?",
            prize_pool="50.00",
        )
        user = self.create_user("foreign_option_user")
        self.authenticate(user)

        response = self.client.post(
            reverse("bet-participate"),
            {"bet_id": self.bet.id, "option_id": BetOption.objects.create(
                bet=other_bet,
                lower_bound=0,
                upper_bound=4,
            ).id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(BetEntry.objects.filter(user=user).exists())

    def test_closed_bet_rejects_new_entries_before_final_answer(self):
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])
        user = self.create_user("late_bet_participant")
        self.authenticate(user)

        response = self.client.post(
            reverse("bet-participate"),
            {"bet_id": self.bet.id, "option_id": self.mid_range.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertFalse(BetEntry.objects.filter(user=user).exists())

    def test_settlement_splits_prize_pool_evenly_down_to_the_cent(self):
        winners = [
            self.create_user("bet_winner_one"),
            self.create_user("bet_winner_two"),
        ]
        loser = self.create_user("bet_loser")
        for user, option in (
            (winners[0], self.mid_range),
            (winners[1], self.mid_range),
            (loser, self.low_range),
        ):
            BetEntry.objects.create(bet=self.bet, user=user, option=option)
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])

        self.bet.settle(self.mid_range)

        self.assertEqual(self.bet.status, Bet.Status.SETTLED)
        self.assertEqual(self.bet.correct_option, self.mid_range)
        self.assertEqual(self.bet.winner_count, 2)
        self.assertEqual(
            list(
                BetWinner.objects.filter(bet=self.bet)
                .order_by("pk")
                .values_list("payout_amount", flat=True)
            ),
            [Decimal("50.01"), Decimal("50.00")],
        )
        response = self.client.get(reverse("bets"))
        settled_bet = response.data["closed_bets"][0]
        self.assertEqual(settled_bet["correct_option_label"], "3–5")
        self.assertEqual(settled_bet["winner_count"], 2)

        self.authenticate(winners[0])
        winner_response = self.client.get(reverse("bets"))
        winner_result = winner_response.data["closed_bets"][0]
        self.assertTrue(winner_result["is_winner"])
        self.assertEqual(winner_result["my_payout"], Decimal("50.01"))

        self.authenticate(loser)
        loser_response = self.client.get(reverse("bets"))
        loser_result = loser_response.data["closed_bets"][0]
        self.assertFalse(loser_result["is_winner"])
        self.assertIsNone(loser_result["my_payout"])

    def test_settlement_requires_closed_entries_and_bet_option(self):
        with self.assertRaises(ValidationError):
            self.bet.settle(self.mid_range)

        other_bet = Bet.objects.create(
            question="Another question?",
            prize_pool="20.00",
        )
        other_option = BetOption.objects.create(bet=other_bet, lower_bound=0)
        self.bet.status = Bet.Status.CLOSED
        self.bet.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self.bet.settle(other_option)

    def test_admin_can_only_enter_answer_after_closing_entries(self):
        open_form = BetAdminForm(instance=self.bet)
        self.assertTrue(open_form.fields["answer_option"].disabled)

        self.bet.status = Bet.Status.CLOSED
        self.bet.save(update_fields=["status"])
        closed_form = BetAdminForm(instance=self.bet)
        self.assertFalse(closed_form.fields["answer_option"].disabled)
        self.assertEqual(
            list(closed_form.fields["answer_option"].queryset),
            [self.low_range, self.mid_range, self.high_range],
        )

    def test_admin_saving_correct_answer_settles_bet_and_creates_winners(self):
        user = self.create_user("admin_settled_winner")
        BetEntry.objects.create(
            bet=self.bet,
            user=user,
            option=self.mid_range,
        )
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])
        form = BetAdminForm(
            data={
                "question": self.bet.question,
                "prize_pool": str(self.bet.prize_pool),
                "answer_option": str(self.mid_range.pk),
            },
            instance=self.bet,
        )
        self.assertTrue(form.is_valid(), form.errors)

        bet_admin = BetAdmin(Bet, admin.site)
        bet_admin.save_model(Mock(), form.save(commit=False), form, change=True)

        self.bet.refresh_from_db()
        self.assertEqual(self.bet.status, Bet.Status.SETTLED)
        self.assertEqual(self.bet.correct_option, self.mid_range)
        self.assertEqual(
            BetWinner.objects.get(bet=self.bet, user=user).payout_amount,
            self.bet.prize_pool,
        )

    def test_admin_add_page_loads_with_answer_range_inline(self):
        admin_user = self.user_model.objects.create_superuser(
            username="bet_admin",
            email="bet-admin@example.test",
            password="Secure-Test-Password-12345",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin:lotteries_bet_add"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Answer ranges")

    def test_admin_close_action_stops_entry_without_settling(self):
        bet_admin = BetAdmin(Bet, admin.site)
        request = Mock()
        queryset = Bet.objects.filter(pk=self.bet.pk)

        with patch.object(bet_admin, "message_user"):
            bet_admin.close_entries(request, queryset)

        self.bet.refresh_from_db()
        self.assertEqual(self.bet.status, Bet.Status.CLOSED)
        self.assertIsNotNone(self.bet.closed_at)
        self.assertIsNone(self.bet.correct_option_id)
        self.assertIsNone(self.bet.settled_at)

    def test_admin_close_entries_checkbox_stops_bet_when_saved(self):
        form = BetAdminForm(
            data={
                "question": self.bet.question,
                "prize_pool": str(self.bet.prize_pool),
                "close_entries": "on",
            },
            instance=self.bet,
        )
        self.assertTrue(form.is_valid(), form.errors)

        bet_admin = BetAdmin(Bet, admin.site)
        bet_admin.save_model(Mock(), form.save(commit=False), form, change=True)

        self.bet.refresh_from_db()
        self.assertEqual(self.bet.status, Bet.Status.CLOSED)
        self.assertIsNotNone(self.bet.closed_at)
        self.assertIsNone(self.bet.correct_option_id)

    def test_admin_open_bet_change_page_shows_close_entries_checkbox(self):
        admin_user = self.user_model.objects.create_superuser(
            username="bet_closer",
            email="bet-closer@example.test",
            password="Secure-Test-Password-12345",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin:lotteries_bet_change", args=[self.bet.pk]),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'name="close_entries"')
        self.assertNotContains(response, 'name="answer_option"')

    def test_admin_can_set_answer_with_ranges_readonly(self):
        self.bet.status = Bet.Status.CLOSED
        self.bet.closed_at = timezone.now()
        self.bet.save(update_fields=["status", "closed_at"])
        admin_user = self.user_model.objects.create_superuser(
            username="bet_settler",
            email="bet-settler@example.test",
            password="Secure-Test-Password-12345",
        )
        self.client.force_login(admin_user)
        change_url = reverse("admin:lotteries_bet_change", args=[self.bet.pk])
        change_page = self.client.get(change_url)
        self.assertEqual(change_page.status_code, status.HTTP_200_OK)

        payload = {
            "question": self.bet.question,
            "prize_pool": str(self.bet.prize_pool),
            "answer_option": str(self.mid_range.pk),
            "_save": "Save",
        }
        for inline_admin_formset in change_page.context["inline_admin_formsets"]:
            formset = inline_admin_formset.formset
            prefix = formset.prefix
            for name, value in formset.management_form.initial.items():
                payload[f"{prefix}-{name}"] = value
            for form in formset.forms:
                for name, field in form.fields.items():
                    if field.widget.is_hidden:
                        value = form[name].value()
                        if value is not None:
                            payload[form.add_prefix(name)] = value

        response = self.client.post(change_url, payload)

        self.assertEqual(response.status_code, 302)
        self.bet.refresh_from_db()
        self.assertEqual(self.bet.status, Bet.Status.SETTLED)
        self.assertEqual(self.bet.correct_option, self.mid_range)


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

        self.assertEqual(registered_giveaway_models, [Giveaway, Bet])
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
