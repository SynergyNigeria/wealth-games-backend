from django import forms
from django.conf import settings
from django.contrib import admin

from .models import Giveaway, GiveawayEntry, GiveawayWinner


class GiveawayEntryInline(admin.TabularInline):
    model = GiveawayEntry
    extra = 0
    readonly_fields = ("user", "joined_at")
    can_delete = False


class GiveawayWinnerInline(admin.TabularInline):
    model = GiveawayWinner
    extra = 0
    readonly_fields = ("user",)
    can_delete = False


class GiveawayAdminForm(forms.ModelForm):
    draw_date = forms.DateTimeField(
        required=True,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "data-timezone": settings.TIME_ZONE,
            },
            format="%Y-%m-%dT%H:%M",
        ),
        help_text=(
            "The giveaway closes and its temporary random draw runs at this "
            f"date and time ({settings.TIME_ZONE}). Use the quick-set buttons "
            "to choose 30 minutes or 1 hour from now."
        ),
    )

    class Meta:
        model = Giveaway
        fields = "__all__"

    class Media:
        js = ("lotteries/admin/giveaway_datetime.js",)


@admin.register(Giveaway)
class GiveawayAdmin(admin.ModelAdmin):
    form = GiveawayAdminForm
    list_display = ("name", "amount", "number_of_winners", "status", "draw_date", "participant_count")
    list_filter = ("status",)
    search_fields = ("name", "description")
    readonly_fields = ("status", "closed_at", "created_at")
    inlines = (GiveawayEntryInline, GiveawayWinnerInline)

    @admin.display(description="Participants")
    def participant_count(self, giveaway):
        return giveaway.entries.count()
