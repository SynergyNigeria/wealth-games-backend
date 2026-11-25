from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .models import (
    Bet,
    BetEntry,
    BetOption,
    BetWinner,
    Giveaway,
    GiveawayEntry,
    GiveawayWinner,
)


class BetOptionInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        if self.instance.pk and (
            self.instance.status != Bet.Status.OPEN
            or self.instance.entries.exists()
        ):
            return

        options = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE", False)
        ]
        if not options:
            raise ValidationError("Add at least one answer range.")

        options.sort(key=lambda option: option["lower_bound"])
        previous_upper = None
        for index, option in enumerate(options):
            lower = option["lower_bound"]
            upper = option["upper_bound"]
            if index and (previous_upper is None or lower <= previous_upper):
                raise ValidationError("Answer ranges must not overlap.")
            previous_upper = upper


class BetOptionInline(admin.TabularInline):
    model = BetOption
    formset = BetOptionInlineFormSet
    extra = 4
    ordering = ("lower_bound",)
    fields = ("lower_bound", "upper_bound")
    verbose_name = "Answer range"
    verbose_name_plural = "Answer ranges"


class BetEntryInline(admin.TabularInline):
    model = BetEntry
    extra = 0
    fields = ("user", "option", "created_at")
    readonly_fields = fields
    can_delete = False


class BetWinnerInline(admin.TabularInline):
    model = BetWinner
    extra = 0
    fields = ("user", "payout_amount")
    readonly_fields = fields
    can_delete = False


class BetAdminForm(forms.ModelForm):
    close_entries = forms.BooleanField(
        required=False,
        label="Close entries now",
        help_text="Stop accepting entries for this bet when you save.",
    )
    answer_option = forms.ModelChoiceField(
        queryset=BetOption.objects.none(),
        required=False,
        label="Correct answer",
        help_text="Available after entries are closed.",
    )

    class Meta:
        model = Bet
        fields = ("question", "prize_pool")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "answer_option" in self.fields:
            if self.instance.pk:
                self.fields["answer_option"].queryset = self.instance.options.all()
            if (
                not self.instance.pk
                or self.instance.status != Bet.Status.CLOSED
            ):
                self.fields["answer_option"].disabled = True

    def clean_answer_option(self):
        option = self.cleaned_data["answer_option"]
        if option and self.instance.status != Bet.Status.CLOSED:
            raise forms.ValidationError(
                "Close entries before selecting the correct answer.",
            )
        return option


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


@admin.register(Bet)
class BetAdmin(admin.ModelAdmin):
    form = BetAdminForm
    list_display = (
        "question",
        "prize_pool",
        "status",
        "entry_count",
        "closed_at",
        "settled_at",
    )
    list_filter = ("status",)
    search_fields = ("question",)
    readonly_fields = (
        "status",
        "closed_at",
        "settled_at",
        "correct_answer",
        "created_at",
    )
    fields = (
        "question",
        "prize_pool",
        "status",
        "closed_at",
        "settled_at",
        "correct_answer",
        "answer_option",
        "created_at",
    )
    inlines = (BetOptionInline, BetEntryInline, BetWinnerInline)
    actions = ("close_entries",)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.status != Bet.Status.CLOSED:
            readonly_fields.append("answer_option")
        if obj and obj.entries.exists():
            readonly_fields.extend(("question", "prize_pool"))
        return tuple(readonly_fields)

    def get_fields(self, request, obj=None):
        fields = [
            "question",
            "prize_pool",
            "status",
            "closed_at",
            "settled_at",
            "correct_answer",
        ]
        if obj is None:
            return (*fields, "created_at")
        if obj.status == Bet.Status.OPEN:
            fields.append("close_entries")
        elif obj.status == Bet.Status.CLOSED:
            fields.append("answer_option")
        return (*fields, "created_at")

    def get_inline_instances(self, request, obj=None):
        inline_instances = super().get_inline_instances(request, obj)
        if obj and (obj.status != Bet.Status.OPEN or obj.entries.exists()):
            for inline in inline_instances:
                if isinstance(inline, BetOptionInline):
                    inline.readonly_fields = ("lower_bound", "upper_bound")
                    inline.extra = 0
                    inline.max_num = 0
                    inline.can_delete = False
        return inline_instances

    @admin.display(description="Correct answer")
    def correct_answer(self, bet):
        return bet.correct_option.label if bet.correct_option_id else "Not entered"

    @admin.display(description="Entries")
    def entry_count(self, bet):
        return bet.entries.count()

    @admin.action(description="Close selected bets to new entries")
    def close_entries(self, request, queryset):
        open_bets = queryset.filter(status=Bet.Status.OPEN)
        closed_count = open_bets.update(
            status=Bet.Status.CLOSED,
            closed_at=timezone.now(),
        )
        self.message_user(
            request,
            f"Closed entries for {closed_count} bet(s). You can enter the answer "
            "after closing.",
        )

    def save_model(self, request, obj, form, change):
        answer_option = form.cleaned_data.get("answer_option")
        if form.cleaned_data.get("close_entries"):
            obj.status = Bet.Status.CLOSED
            obj.closed_at = timezone.now()
        super().save_model(request, obj, form, change)
        if answer_option:
            obj.settle(answer_option)
