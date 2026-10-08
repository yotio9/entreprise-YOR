from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('boutique', '0008_order_loyalty_points_awarded_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='source_url',
            field=models.URLField(blank=True, max_length=2000, verbose_name='Lien de provenance'),
        ),
    ]
