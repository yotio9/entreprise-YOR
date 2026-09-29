from django.core.management.base import BaseCommand

from boutique.models import Category, Product


class Command(BaseCommand):
    help = 'Ajoute des catégories et pièces de démonstration à la boutique.'

    def handle(self, *args, **options):
        collections = {
            'Prêt-à-porter': [
                ('Robe sculptée', 'Une ligne fluide et affirmée, pensée pour accompagner le mouvement.', '89000', ['XS', 'S', 'M', 'L'], 18, 'photo-1539109136881-3be0616acf4b'),
                ('Ensemble Séléné', 'Deux pièces aux proportions justes, à porter ensemble ou séparément.', '125000', ['S', 'M', 'L', 'XL'], 12, 'photo-1483985988355-763728e1935b'),
                ('Chemise N° 04', 'Une chemise essentielle, finitions soignées et tombé impeccable.', '62000', ['S', 'M', 'L', 'XL'], 20, 'photo-1598554747436-c9293d6a588f'),
            ],
            'Accessoires': [
                ('Sac Atelier', 'Un volume sculptural pour les essentiels du quotidien.', '79000', [], 9, 'photo-1584917865442-de89df76afd3'),
                ('Étole Lumière', 'Un voile doux et léger, à nouer selon votre humeur.', '35000', [], 15, 'photo-1601924994987-69e26d50dc26'),
            ],
            'Chaussures': [
                ('Mule Signature', 'Une silhouette épurée, équilibrée entre confort et caractère.', '98000', ['36', '37', '38', '39', '40', '41'], 8, 'photo-1543163521-1bf539c55dd2'),
            ],
        }
        created = 0
        for category_name, pieces in collections.items():
            category, _ = Category.objects.get_or_create(name=category_name)
            for name, description, price, sizes, stock, image_id in pieces:
                _, was_created = Product.objects.get_or_create(
                    category=category,
                    name=name,
                    defaults={
                        'description': description,
                        'material': 'Matière soigneusement sélectionnée.',
                        'care_instructions': 'Confier à un nettoyage délicat. Repasser sur l’envers à basse température.',
                        'price': price,
                        'sizes': sizes,
                        'stock': stock,
                        'image_url': f'https://images.unsplash.com/{image_id}?auto=format&fit=crop&w=1100&q=85',
                        'is_featured': name in ('Robe sculptée', 'Sac Atelier'),
                    },
                )
                created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f'{created} pièce(s) de démonstration ajoutée(s).'))