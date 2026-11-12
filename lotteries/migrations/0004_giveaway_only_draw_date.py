from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("lotteries", "0003_giveaway_close_and_winners"),
    ]

    operations = [
        migrations.DeleteModel(name="Lottery"),
        migrations.RenameField(
            model_name="giveaway",
            old_name="closes_at",
            new_name="draw_date",
        ),
    ]
