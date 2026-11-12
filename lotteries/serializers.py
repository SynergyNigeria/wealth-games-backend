import re

from django.contrib.auth import password_validation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from .models import Giveaway, GiveawayEntry, GiveawayWinner


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
