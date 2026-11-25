import re

from django.contrib.auth import password_validation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from .models import (
    Bet,
    BetEntry,
    BetOption,
    BetWinner,
    Giveaway,
    GiveawayEntry,
    GiveawayWinner,
)


class AccountSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32, trim_whitespace=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_phone(self, value):
        if not re.fullmatch(r"[+\d\s().-]+", value):
            raise serializers.ValidationError("Enter a valid phone number.")

        digits = re.sub(r"\D", "", value)
        if digits.startswith("00"):
            digits = digits[2:]
        if not 7 <= len(digits) <= 15:
            raise serializers.ValidationError("Enter a valid phone number.")
        return digits


class RegisterSerializer(AccountSerializer):
    def validate(self, attrs):
        user = get_user_model()(username=attrs["phone"])
        try:
            password_validation.validate_password(attrs["password"], user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages}) from error
        return attrs


class ParticipateSerializer(serializers.Serializer):
    giveaway_id = serializers.IntegerField(min_value=1, required=False)


class BetParticipateSerializer(serializers.Serializer):
    bet_id = serializers.IntegerField(min_value=1)
    option_id = serializers.IntegerField(min_value=1)


class BetOptionSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)

    class Meta:
        model = BetOption
        fields = ["id", "lower_bound", "upper_bound", "label"]


class BetSerializer(serializers.ModelSerializer):
    options = BetOptionSerializer(many=True, read_only=True)
    entries_count = serializers.IntegerField(source="entries.count", read_only=True)
    selected_option_id = serializers.SerializerMethodField()
    selected_option_label = serializers.SerializerMethodField()
    correct_option_label = serializers.SerializerMethodField()
    is_winner = serializers.SerializerMethodField()
    winner_count = serializers.IntegerField(read_only=True)
    winners = serializers.SerializerMethodField()
    my_payout = serializers.SerializerMethodField()

    class Meta:
        model = Bet
        fields = [
            "id",
            "question",
            "prize_pool",
            "status",
            "closed_at",
            "settled_at",
            "created_at",
            "options",
            "entries_count",
            "selected_option_id",
            "selected_option_label",
            "correct_option_label",
            "is_winner",
            "winner_count",
            "winners",
            "my_payout",
        ]

    def get_entry(self, bet):
        user = self.context["request"].user
        if not user.is_authenticated:
            return None
        return BetEntry.objects.filter(bet=bet, user=user).select_related(
            "option",
        ).first()

    def get_selected_option_id(self, bet):
        entry = self.get_entry(bet)
        return entry.option_id if entry else None

    def get_selected_option_label(self, bet):
        entry = self.get_entry(bet)
        return entry.option.label if entry else None

    def get_correct_option_label(self, bet):
        if bet.status != Bet.Status.SETTLED or not bet.correct_option_id:
            return None
        return bet.correct_option.label

    def get_is_winner(self, bet):
        user = self.context["request"].user
        return user.is_authenticated and BetWinner.objects.filter(
            bet=bet,
            user=user,
        ).exists()

    def get_winners(self, bet):
        if bet.status != Bet.Status.SETTLED:
            return []
        request_user = self.context["request"].user
        return [
            {
                "display_name": f"•••• {winner.user.username[-4:]}",
                "is_current_user": request_user.is_authenticated
                and winner.user_id == request_user.id,
                "payout_amount": winner.payout_amount,
            }
            for winner in BetWinner.objects.filter(
                bet=bet,
            ).select_related("user").order_by("pk")
        ]

    def get_my_payout(self, bet):
        user = self.context["request"].user
        if not user.is_authenticated:
            return None
        winner = BetWinner.objects.filter(bet=bet, user=user).first()
        return winner.payout_amount if winner else None


class GiveawaySerializer(serializers.ModelSerializer):
    participants_count = serializers.IntegerField(source="entries.count", read_only=True)
    has_joined = serializers.SerializerMethodField()
    winners = serializers.SerializerMethodField()
    is_winner = serializers.SerializerMethodField()

    class Meta:
        model = Giveaway
        fields = [
            "id",
            "name",
            "description",
            "amount",
            "number_of_winners",
            "draw_date",
            "closed_at",
            "status",
            "participants_count",
            "has_joined",
            "winners",
            "is_winner",
        ]

    def get_has_joined(self, giveaway):
        user = self.context["request"].user
        return user.is_authenticated and GiveawayEntry.objects.filter(
            giveaway=giveaway,
            user=user,
        ).exists()

    def get_winners(self, giveaway):
        request_user = self.context["request"].user
        return [
            {
                "display_name": f"•••• {winner.user.username[-4:]}",
                "is_current_user": request_user.is_authenticated
                and winner.user_id == request_user.id,
            }
            for winner in GiveawayWinner.objects.filter(
                giveaway=giveaway,
            ).select_related("user")
        ]

    def get_is_winner(self, giveaway):
        user = self.context["request"].user
        return user.is_authenticated and GiveawayWinner.objects.filter(
            giveaway=giveaway,
            user=user,
        ).exists()


class AuthenticatedAccountSerializer(serializers.Serializer):
    token = serializers.CharField()
    phone = serializers.CharField()

    @classmethod
    def from_user(cls, user):
        token, _ = Token.objects.get_or_create(user=user)
        return {"token": token.key, "phone": user.username}
