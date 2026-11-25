from django.contrib import admin
from django.urls import path
from rest_framework.views import APIView
from rest_framework.response import Response

from lotteries.views import (
    CurrentAccountView,
    BetListView,
    BetParticipateView,
    LoginView,
    LogoutView,
    OpenGiveawayView,
    ParticipateView,
    RegisterView,
)


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health"),
    path("api/auth/register/", RegisterView.as_view(), name="register"),
    path("api/auth/login/", LoginView.as_view(), name="login"),
    path("api/auth/logout/", LogoutView.as_view(), name="logout"),
    path("api/auth/me/", CurrentAccountView.as_view(), name="current-account"),
    path("api/giveaway/", OpenGiveawayView.as_view(), name="open-giveaway"),
    path("api/giveaway/participate/", ParticipateView.as_view(), name="participate"),
    path("api/bets/", BetListView.as_view(), name="bets"),
    path(
        "api/bets/participate/",
        BetParticipateView.as_view(),
        name="bet-participate",
    ),
]
