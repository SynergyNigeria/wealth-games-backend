from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token

from .models import Giveaway, GiveawayEntry, GiveawayWinner
from .draw_algorithm import participant_weights, select_weighted_winners
from .serializers import (
    AccountSerializer,
    AuthenticatedAccountSerializer,
    GiveawaySerializer,
    ParticipateSerializer,
    RegisterSerializer,
)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_model = get_user_model()
        phone = serializer.validated_data["phone"]

        if user_model.objects.filter(username=phone).exists():
            return Response(
                {"phone": ["An account with this phone number already exists."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = user_model.objects.create_user(
                    username=phone,
                    password=serializer.validated_data["password"],
                )
        except IntegrityError:
            return Response(
                {"phone": ["An account with this phone number already exists."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            AuthenticatedAccountSerializer.from_user(user),
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = AccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["phone"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Phone number or password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(AuthenticatedAccountSerializer.from_user(user))


class LogoutView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentAccountView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"phone": request.user.username})


def close_giveaway_if_due(giveaway, participant_weight_by_id=None):
    if (
        giveaway.status != Giveaway.Status.OPEN
        or giveaway.draw_date is None
        or giveaway.draw_date > timezone.now()
    ):
        return giveaway

    with transaction.atomic():
        giveaway = Giveaway.objects.select_for_update().get(pk=giveaway.pk)
        if (
            giveaway.status == Giveaway.Status.OPEN
            and giveaway.draw_date is not None
            and giveaway.draw_date <= timezone.now()
        ):
            entries = list(
                GiveawayEntry.objects.filter(giveaway=giveaway).values_list(
                    "user_id",
                    flat=True,
                )
            )
            winner_count = min(giveaway.number_of_winners, len(entries))
            selected_user_ids = select_weighted_winners(
                participant_weight_by_id
                if participant_weight_by_id is not None
                else participant_weights(giveaway),
                winner_count,
            )
            GiveawayWinner.objects.bulk_create(
                GiveawayWinner(giveaway=giveaway, user_id=user_id)
                for user_id in selected_user_ids
            )
            giveaway.status = Giveaway.Status.CLOSED
            giveaway.closed_at = timezone.now()
            giveaway.save(update_fields=["status", "closed_at"])

    return giveaway


def close_due_giveaways():
    due_giveaways = list(
        Giveaway.objects.filter(
            status=Giveaway.Status.OPEN,
            draw_date__lte=timezone.now(),
        ).order_by("draw_date", "pk")
    )
    simultaneous_batches = {}
    for giveaway in due_giveaways:
        simultaneous_batches.setdefault(giveaway.draw_date, []).append(giveaway)

    closed_giveaways = []
    for batch in simultaneous_batches.values():
        weight_snapshots = {
            giveaway.id: participant_weights(giveaway)
            for giveaway in batch
        }
        for giveaway in batch:
            giveaway = close_giveaway_if_due(
                giveaway,
                participant_weight_by_id=weight_snapshots[giveaway.id],
            )
            if giveaway.status == Giveaway.Status.CLOSED:
                closed_giveaways.append(giveaway)
    return closed_giveaways


def current_giveaway():
    giveaway = Giveaway.objects.filter(status=Giveaway.Status.OPEN).first()
    if giveaway is not None:
        return giveaway
    return Giveaway.objects.filter(status=Giveaway.Status.CLOSED).first()


class OpenGiveawayView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [AllowAny]

    def get(self, request):
        closed_giveaways = close_due_giveaways()
        current = current_giveaway()
        open_giveaways = Giveaway.objects.filter(
            status=Giveaway.Status.OPEN,
        )
        serializer_context = {"request": request}
        giveaways_data = GiveawaySerializer(
            open_giveaways,
            many=True,
            context=serializer_context,
        ).data
        closed_giveaways_data = GiveawaySerializer(
            closed_giveaways,
            many=True,
            context=serializer_context,
        ).data
        if current is None:
            return Response({
                "giveaway": None,
                "giveaways": giveaways_data,
                "closed_giveaways": closed_giveaways_data,
            })
        serializer = GiveawaySerializer(current, context=serializer_context)
        return Response({
            "giveaway": serializer.data,
            "giveaways": giveaways_data,
            "closed_giveaways": closed_giveaways_data,
        })


class ParticipateView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ParticipateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        giveaway_id = serializer.validated_data.get("giveaway_id")
        if giveaway_id is None:
            giveaway = Giveaway.objects.filter(
                status=Giveaway.Status.OPEN,
            ).first()
        else:
            giveaway = get_object_or_404(Giveaway, pk=giveaway_id)
        if giveaway is None:
            return Response(
                {"detail": "There is no open giveaway to join."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if giveaway.status != Giveaway.Status.OPEN:
            return Response(
                {"detail": "This giveaway is closed."},
                status=status.HTTP_410_GONE,
            )

        giveaway = close_giveaway_if_due(giveaway)
        if giveaway.status != Giveaway.Status.OPEN:
            return Response(
                {"detail": "This giveaway is closed."},
                status=status.HTTP_410_GONE,
            )

        with transaction.atomic():
            giveaway = get_object_or_404(
                Giveaway.objects.select_for_update(),
                status=Giveaway.Status.OPEN,
                pk=giveaway.pk,
            )
            if giveaway.draw_date is not None and giveaway.draw_date <= timezone.now():
                return Response(
                    {"detail": "This giveaway is closed."},
                    status=status.HTTP_410_GONE,
                )
            _, created = GiveawayEntry.objects.get_or_create(
                giveaway=giveaway,
                user=request.user,
            )

        serializer = GiveawaySerializer(giveaway, context={"request": request})
        return Response(
            {"giveaway": serializer.data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
