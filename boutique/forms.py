from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.validators import RegexValidator

from .models import Product

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
            'category', 'name', 'description', 'material', 'care_instructions',
            'price', 'compare_at_price', 'sizes', 'stock', 'image', 'image_url',
            'is_featured', 'is_active',
        )
        labels = {
            'category': 'Catégorie',
            'name': 'Nom de l’article',
            'description': 'Description',
            'material': 'Matière',
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
