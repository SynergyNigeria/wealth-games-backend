from django.core.validators import MinValueValidator
from django.conf import settings
from django.db import models


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
