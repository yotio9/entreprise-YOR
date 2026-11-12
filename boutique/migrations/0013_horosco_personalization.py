from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('boutique', '0012_category_image'),
	]

	operations = [
		migrations.AddField(
			model_name='product',
			name='zodiac_sign',
			field=models.CharField(
				blank=True,
				choices=[
					('aries', 'Bélier'),
					('taurus', 'Taureau'),
					('gemini', 'Gémeaux'),
					('cancer', 'Cancer'),
					('leo', 'Lion'),
					('virgo', 'Vierge'),
					('libra', 'Balance'),
					('scorpio', 'Scorpion'),
					('sagittarius', 'Sagittaire'),
					('capricorn', 'Capricorne'),
					('aquarius', 'Verseau'),
					('pisces', 'Poissons'),
				],
				max_length=12,
			),
		),
		migrations.AddField(
			model_name='orderitem',
			name='personalization',
			field=models.JSONField(blank=True, default=dict),
		),
	]
