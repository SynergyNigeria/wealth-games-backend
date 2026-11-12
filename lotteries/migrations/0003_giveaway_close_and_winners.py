import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("lotteries", "0002_giveaway"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="giveaway",
            name="closes_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The giveaway closes automatically at this date and time.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="giveaway",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="GiveawayWinner",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "giveaway",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="winners",
                        to="lotteries.giveaway",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="giveaway_wins",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="giveawaywinner",
            constraint=models.UniqueConstraint(
                fields=("giveaway", "user"),
                name="unique_giveaway_winner_per_user",
            ),
        ),
    ]
