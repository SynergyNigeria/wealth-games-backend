from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class Giveaway(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=120, default="MR Wealth’s Giveaway")
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    number_of_winners = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    draw_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The giveaway closes automatically at this date and time.",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Giveaway"
        verbose_name_plural = "Giveaways"

    def __str__(self):
        return self.name


class Bet(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed to entries"
        SETTLED = "settled", "Settled"

    question = models.TextField()
    prize_pool = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    correct_option = models.ForeignKey(
        "BetOption",
        on_delete=models.PROTECT,
        related_name="settled_bets",
        null=True,
        blank=True,
    )
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bet"
        verbose_name_plural = "Bets"
        constraints = [
            models.CheckConstraint(
                condition=Q(prize_pool__gt=Decimal("0")),
                name="bet_prize_pool_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.status == self.Status.SETTLED:
            if self.correct_option_id is None:
                raise ValidationError(
                    {"correct_option": "A settled bet needs a correct option."},
                )
            if self.correct_option.bet_id != self.pk:
                raise ValidationError(
                    {"correct_option": "Choose an option belonging to this bet."},
                )
        elif self.correct_option_id is not None:
            raise ValidationError(
                {"correct_option": "Only settled bets can have a correct option."},
            )

    @property
    def winner_count(self):
        return self.winners.count()

    def settle(self, correct_option):
        with transaction.atomic():
            bet = Bet.objects.select_for_update().get(pk=self.pk)
            if bet.status != self.Status.CLOSED:
                raise ValidationError("Close entries before settling this bet.")
            if correct_option.bet_id != bet.pk:
                raise ValidationError("Choose an option belonging to this bet.")

            winning_entries = list(
                BetEntry.objects.filter(
                    bet=bet,
                    option=correct_option,
                ).order_by("created_at", "pk")
            )
            if winning_entries:
                total_cents = int(bet.prize_pool * 100)
                base_cents, remainder_cents = divmod(
                    total_cents,
                    len(winning_entries),
                )
                BetWinner.objects.bulk_create(
                    BetWinner(
                        bet=bet,
                        user_id=entry.user_id,
                        payout_amount=Decimal(
                            base_cents + (index < remainder_cents),
                        )
                        / 100,
                    )
                    for index, entry in enumerate(winning_entries)
                )

            bet.correct_option = correct_option
            bet.status = self.Status.SETTLED
            bet.settled_at = timezone.now()
            bet.save(update_fields=["correct_option", "status", "settled_at"])
            self.status = bet.status
            self.correct_option = correct_option
            self.settled_at = bet.settled_at
        return self

    def __str__(self):
        return self.question


class BetOption(models.Model):
    bet = models.ForeignKey(Bet, on_delete=models.CASCADE, related_name="options")
    lower_bound = models.PositiveIntegerField()
    upper_bound = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["lower_bound", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["bet", "lower_bound", "upper_bound"],
                name="unique_bet_option_range",
            ),
        ]

    def clean(self):
        super().clean()
        if self.upper_bound is not None and self.upper_bound < self.lower_bound:
            raise ValidationError(
                {"upper_bound": "The upper bound must be at least the lower bound."},
            )

    @property
    def label(self):
        if self.upper_bound is None:
            return f"{self.lower_bound}+"
        if self.upper_bound == self.lower_bound:
            return str(self.lower_bound)
        return f"{self.lower_bound}–{self.upper_bound}"

    def __str__(self):
        return self.label


class BetEntry(models.Model):
    bet = models.ForeignKey(Bet, on_delete=models.CASCADE, related_name="entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bet_entries",
    )
    option = models.ForeignKey(
        BetOption,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["bet", "user"],
                name="unique_bet_entry_per_user",
            ),
        ]

    def clean(self):
        super().clean()
        if self.option_id and self.option.bet_id != self.bet_id:
            raise ValidationError(
                {"option": "Choose an option belonging to this bet."},
            )

    def __str__(self):
        return f"{self.user} — {self.bet} ({self.option})"


class BetWinner(models.Model):
    bet = models.ForeignKey(Bet, on_delete=models.CASCADE, related_name="winners")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bet_wins",
    )
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bet", "user"],
                name="unique_bet_winner_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.bet}"


class GiveawayEntry(models.Model):
    giveaway = models.ForeignKey(
        Giveaway,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="giveaway_entries",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        verbose_name = "Giveaway participant"
        verbose_name_plural = "Giveaway participants"
        constraints = [
            models.UniqueConstraint(
                fields=["giveaway", "user"],
                name="unique_giveaway_entry_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.giveaway}"


class GiveawayWinner(models.Model):
    giveaway = models.ForeignKey(
        Giveaway,
        on_delete=models.CASCADE,
        related_name="winners",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="giveaway_wins",
    )

    class Meta:
        verbose_name = "Giveaway winner"
        verbose_name_plural = "Giveaway winners"
        constraints = [
            models.UniqueConstraint(
                fields=["giveaway", "user"],
                name="unique_giveaway_winner_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.giveaway}"
