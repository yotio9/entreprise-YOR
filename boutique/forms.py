from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.validators import RegexValidator

from .models import Order, Product, ReturnRequest, Review

User = get_user_model()


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(label='Adresse e-mail')
    first_name = forms.CharField(label='Prénom', max_length=150)
    last_name = forms.CharField(label='Nom', max_length=150)
    phone = forms.CharField(
        label='Téléphone',
        max_length=10,
        validators=[RegexValidator(r'^[0-9]{10}$', 'Saisissez exactement 10 chiffres.')],
        widget=forms.TextInput(attrs={
            'type': 'tel',
            'inputmode': 'numeric',
            'pattern': '[0-9]{10}',
            'maxlength': '10',
            'autocomplete': 'tel-national',
            'placeholder': '0700000000',
        }),
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Un compte existe déjà avec cette adresse e-mail.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.username = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label='Adresse e-mail')

    def clean(self):
        email = self.cleaned_data.get('username')
        if email:
            user = User.objects.filter(email__iexact=email).only('username').first()
            if user:
                self.cleaned_data['username'] = user.get_username()
        return super().clean()


class ProductForm(forms.ModelForm):
    sizes = forms.CharField(
        required=False,
        label='Tailles (séparées par des virgules)',
        widget=forms.TextInput(attrs={'placeholder': 'XS, S, M, L, XL'}),
    )

    class Meta:
        model = Product
        fields = (
            'category', 'name', 'description', 'material', 'color', 'size_guide', 'care_instructions',
            'price', 'compare_at_price', 'sizes', 'stock', 'image', 'image_url',
            'is_featured', 'is_active',
        )
        labels = {
            'category': 'Catégorie',
            'name': 'Nom de l’article',
            'description': 'Description',
            'material': 'Matière',
            'color': 'Couleur',
            'size_guide': 'Guide des tailles / mesures',
            'care_instructions': 'Conseils d’entretien',
            'price': 'Prix (FCFA)',
            'compare_at_price': 'Ancien prix (optionnel)',
            'sizes': 'Tailles (séparées par des virgules)',
            'stock': 'Stock disponible',
            'image': 'Image principale (fichier)',
            'image_url': 'Image principale (URL, optionnelle)',
            'is_featured': 'Mettre à la une',
            'is_active': 'Visible en boutique',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'size_guide': forms.Textarea(attrs={'rows': 3}),
            'care_instructions': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance.pk:
            self.initial['sizes'] = ', '.join(self.instance.sizes or [])

    def clean_sizes(self):
        value = self.cleaned_data.get('sizes')
        if isinstance(value, str):
            return [size.strip().upper() for size in value.split(',') if size.strip()]
        return value or []


class ReviewForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        label='Note',
        choices=[(value, f'{value} / 5') for value in range(5, 0, -1)],
        coerce=int,
    )

    class Meta:
        model = Review
        fields = ('rating', 'comment')
        labels = {'comment': 'Votre avis'}
        widgets = {'comment': forms.Textarea(attrs={'rows': 4, 'maxlength': 1200})}


class ReturnRequestForm(forms.ModelForm):
    class Meta:
        model = ReturnRequest
        fields = ('request_type', 'reason')
        labels = {
            'request_type': 'Type de demande',
            'reason': 'Pourquoi souhaitez-vous retourner ou échanger la commande ?',
        }
        widgets = {'reason': forms.Textarea(attrs={'rows': 4, 'maxlength': 1200})}


class FulfillmentForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ('status', 'shipping_carrier', 'tracking_number')
        labels = {
            'status': 'État de la commande',
            'shipping_carrier': 'Transporteur',
            'tracking_number': 'Numéro de suivi',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed_statuses = {
            Order.Status.CONFIRMED,
            Order.Status.PREPARING,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        }
        if self.instance.status not in allowed_statuses:
            allowed_statuses.add(self.instance.status)
        self.fields['status'].choices = [
            (value, label) for value, label in Order.Status.choices if value in allowed_statuses
        ]
