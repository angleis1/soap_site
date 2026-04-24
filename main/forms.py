from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox
from django.core.validators import MaxLengthValidator, RegexValidator
from froala_editor.widgets import FroalaEditor
from PIL import Image
import io
from django.utils import timezone
from datetime import timedelta
from .models import User, ProfileUser, MasterClass, MasterRequest, Review, SignUpClass, MasterClassDateTime

class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        label='Электронная почта',
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )
    fio = forms.CharField(
        max_length=255,
        label='ФИО',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        help_text='Только кириллица, пробелы и дефисы. Например: Иванов Иван Петрович',
        validators=[
            RegexValidator(
                regex=r'^[а-яА-ЯёЁ\s-]+$',
                message='ФИО может содержать только кириллические буквы, пробелы и дефисы.'
            )
        ]
    )
    phone = forms.CharField(
        max_length=20,
        label='Телефон',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+7(XXX)-XXX-XX-XX'}),
        validators=[
            RegexValidator(
                regex=r'^\+7\(\d{3}\)-\d{3}-\d{2}-\d{2}$',
                message='Телефон должен быть в формате +7(XXX)-XXX-XX-XX'
            )
        ]
    )
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox,
        error_messages={"required": "Пожалуйста, подтвердите, что вы не робот."}
    )

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].max_length = 30
        self.fields[
            'username'].help_text = 'Обязательное поле. Не более 30 символов. Только буквы, цифры и символы @/./+/-/_.'
        self.fields['username'].validators.append(MaxLengthValidator(30))

        # Запрет пробелов в пароле
        self.fields['password1'].validators.append(
            RegexValidator(regex=r'^\S+$', message='Пароль не может содержать пробелы.')
        )
        self.fields['password2'].validators.append(
            RegexValidator(regex=r'^\S+$', message='Пароль не может содержать пробелы.')
        )

        # Валидация: только латиница, цифры и спецсимволы
        allowed_password_regex = r'^[a-zA-Z0-9!@#$%^&*()_+=\-[\]{};:\'"\\|,.<>/?`~]+$'
        password_validator = RegexValidator(
            regex=allowed_password_regex,
            message='Пароль может содержать только латинские буквы, цифры и символы !@#$%^&*()_+=-[]{};:\'"\\|,.<>/?`~ (без пробелов).'
        )
        self.fields['password1'].validators.append(password_validator)
        self.fields['password2'].validators.append(password_validator)

        # Подсказки под полями пароля
        self.fields[
            'password1'].help_text = 'Пароль должен содержать только латинские буквы, цифры и символы !@#$%^&*()_+=-[]{};:\'"\\|,.<>/?`~ (без пробелов).'
        self.fields['password2'].help_text = 'Для подтверждения введите, пожалуйста, пароль ещё раз.'

        # Добавляем класс form-control для всех полей, кроме captcha
        for field_name, field in self.fields.items():
            if field_name != 'captcha':
                field.widget.attrs.update({'class': 'form-control'})


class LoginFormWithCaptcha(AuthenticationForm):
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox,
        error_messages={"required": "Пожалуйста, подтвердите, что вы не робот."}
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': 'form-control'})
        self.fields['password'].widget.attrs.update({'class': 'form-control'})


class MasterClassCreateForm(forms.ModelForm):
    description = forms.CharField(widget=FroalaEditor)
    image = forms.ImageField(
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        required=True,
        label='Изображение мастер-класса (будет автоматически обрезано до 16:9)'
    )
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox,
        error_messages={"required": "Пожалуйста, подтвердите, что вы не робот."}
    )

    class Meta:
        model = MasterClass
        fields = ['name', 'category', 'description', 'duration_minutes',
                  'format', 'method_payment', 'price', 'address']
        # поля даты, времени и количества мест удалены

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if field not in [self.fields['captcha'], self.fields['description'], self.fields['image']]:
                field.widget.attrs.update({'class': 'form-control'})
        for field_name in ['category', 'format', 'method_payment']:
            self.fields[field_name].widget.attrs['class'] = 'form-select'

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            raise forms.ValidationError("Необходимо загрузить изображение.")
        if not image.name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            raise forms.ValidationError("Неподдерживаемый формат. Разрешены: PNG, JPG, JPEG, GIF, WEBP.")
        try:
            img = Image.open(io.BytesIO(image.read()))
            image.seek(0)
        except Exception as e:
            raise forms.ValidationError(f"Не удалось прочитать изображение. Возможно, файл повреждён. Ошибка: {e}")
        return image


class MasterClassDateTimeForm(forms.ModelForm):
    class Meta:
        model = MasterClassDateTime
        fields = ['date_event', 'time_event', 'count']
        widgets = {
            'date_event': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time_event': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def clean_date_event(self):
        date = self.cleaned_data['date_event']
        max_date = timezone.now().date() + timedelta(weeks=3)
        if date > max_date:
            raise forms.ValidationError(f"Дата не может быть позже чем через 3 недели (макс. {max_date})")
        return date


class ProfileUserForm(forms.ModelForm):
    email = forms.EmailField(
        label='Email',
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = ProfileUser
        fields = ['fio', 'phone', 'avatar', 'bio']
        widgets = {
            'fio': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'bio': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user:
            self.fields['email'].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.exclude(pk=self.user.pk).filter(email=email).exists():
            raise forms.ValidationError('Пользователь с таким email уже существует.')
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        try:
            validate_email(email)
        except ValidationError:
            raise forms.ValidationError('Введите корректный email.')
        return email

    def save(self, commit=True):
        profile = super().save(commit=False)
        if commit:
            profile.save()
            user = profile.user
            user.email = self.cleaned_data['email']
            user.save()
        return profile


class MasterRequestForm(forms.ModelForm):
    class Meta:
        model = MasterRequest
        fields = ['comment']
        widgets = {
            'comment': forms.Textarea(attrs={
                'rows': 3,
                'class': 'form-control',
                'placeholder': 'Почему вы хотите стать мастером?'
            }),
        }


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['text', 'rating']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 4, 'class': 'form-control', 'placeholder': 'Ваш отзыв'}),
            'rating': forms.Select(choices=[(i, i) for i in range(1, 6)], attrs={'class': 'form-select'}),
        }


class SignupStatusForm(forms.ModelForm):
    class Meta:
        model = SignUpClass
        fields = ['status']
        widgets = {'status': forms.Select(attrs={'class': 'form-select'})}