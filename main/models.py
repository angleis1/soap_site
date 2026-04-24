from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill

class Role(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="Название роли")
    def __str__(self): return self.name

class ProfileUser(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.ForeignKey(Role, on_delete=models.PROTECT, verbose_name="Роль")
    fio = models.CharField(max_length=255, verbose_name="ФИО")
    phone = models.CharField(max_length=20, verbose_name="Телефон")
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name="Аватар")
    bio = models.TextField(blank=True, verbose_name="О себе")
    def __str__(self): return self.fio
    def is_master(self): return self.role.name == 'Мастер'

class Category(models.Model):
    name = models.CharField(max_length=255, verbose_name="Название категории")
    def __str__(self): return self.name

class MasterClass(models.Model):
    FORMAT_CHOICES = [(1, "Онлайн"), (2, "Очная встреча")]
    METHOD_PAYMENT_CHOICES = [(1, "Банковская карта"), (2, "Онлайн перевод")]
    STATUS_CHOICES = [
        (1, "Черновик"), (2, "На модерации"), (3, "Опубликован"),
        (4, "Идёт проведение"), (5, "Завершён"), (6, "Отклонён"),
    ]
    name = models.CharField(max_length=255, verbose_name="Название")
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    description = models.TextField(verbose_name="Описание")
    duration_minutes = models.PositiveIntegerField(verbose_name="Длительность (мин)")
    format = models.IntegerField(choices=FORMAT_CHOICES, verbose_name="Формат")
    method_payment = models.IntegerField(choices=METHOD_PAYMENT_CHOICES, verbose_name="Способ оплаты")
    price = models.DecimalField(max_digits=7, decimal_places=2, verbose_name="Стоимость")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    status = models.IntegerField(choices=STATUS_CHOICES, default=1, verbose_name="Статус")
    master = models.ForeignKey(User, on_delete=models.CASCADE, related_name='masterclasses')
    address = models.CharField(max_length=255, blank=True, verbose_name="Адрес")
    show_on_home = models.BooleanField(default=False, verbose_name="Показывать на главной")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.name

class MasterClassDateTime(models.Model):
    masterclass = models.ForeignKey(MasterClass, on_delete=models.CASCADE, related_name='sessions')
    date_event = models.DateField(verbose_name="Дата проведения")
    time_event = models.TimeField(verbose_name="Время начала")
    count = models.PositiveIntegerField(verbose_name="Количество мест")
    booked = models.PositiveIntegerField(default=0, verbose_name="Занято мест")
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('masterclass', 'date_event', 'time_event')
        ordering = ['date_event', 'time_event']

    def __str__(self):
        return f"{self.masterclass.name} – {self.date_event} {self.time_event}"

    def available_seats(self):
        return self.count - self.booked

class MasterClassImage(models.Model):
    masterclass = models.ForeignKey(MasterClass, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='masterclass_images/')
    caption = models.CharField(max_length=200, blank=True)
    image_16_9 = ImageSpecField(
        source='image',
        processors=[ResizeToFill(1920, 1080)],
        format='JPEG',
        options={'quality': 85}
    )
    def __str__(self): return f"Image for {self.masterclass.name}"

class SignUpClass(models.Model):
    STATUS_SIGN_CHOICES = [
        (1, "Ожидает подтверждения"),
        (2, "Подтверждена (ожидает решения мастера)"),
        (3, "Одобрена мастером"),
        (4, "Отменена"),
        (5, "Посещено"),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='signups')
    session = models.ForeignKey(MasterClassDateTime, on_delete=models.CASCADE, related_name='signups')
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.IntegerField(choices=STATUS_SIGN_CHOICES, default=1)
    confirmation_token = models.CharField(max_length=64, blank=True, null=True)
    master_comment = models.TextField(blank=True, verbose_name="Комментарий мастера")

    class Meta:
        unique_together = ('client', 'session')

    def __str__(self):
        return f"{self.client.username} – {self.session}"

class Review(models.Model):
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    master = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews_about')
    text = models.TextField()
    rating = models.DecimalField(max_digits=2, decimal_places=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('client', 'master')
        ordering = ['-created_at']

class MasterRequest(models.Model):
    STATUS_MASTER_CHOICES = [
        (1, "На рассмотрении"), (2, "Одобрена"), (3, "Отклонена"),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='master_requests')
    comment = models.TextField(blank=True, verbose_name="Комментарий клиента")
    status = models.IntegerField(choices=STATUS_MASTER_CHOICES, default=1)
    admin_comment = models.TextField(blank=True, verbose_name="Комментарий администратора")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    def __str__(self): return f"Заявка от {self.client.username}"

# ==================== Новая модель уведомлений ====================
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255, verbose_name="Текст уведомления")
    link = models.CharField(max_length=255, blank=True, verbose_name="Ссылка")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"

    def __str__(self):
        return f"Уведомление для {self.user.username}: {self.message}"