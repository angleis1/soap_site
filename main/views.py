from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib import messages
from django.urls import reverse
from django_q.tasks import async_task
from django.db import models
from django.db.models import Q, Count, Avg
import secrets
from django.templatetags.static import static
from .models import (
    MasterClass, ProfileUser, SignUpClass, MasterRequest,
    Category, Review, Role, User, MasterClassImage, MasterClassDateTime,
    Notification
)
from .forms import (
    RegisterForm, LoginFormWithCaptcha, MasterClassCreateForm,
    ProfileUserForm, MasterRequestForm, ReviewForm, SignupStatusForm,
    MasterClassDateTimeForm
)


def index(request):
    masterclasses = MasterClass.objects.filter(status=3).prefetch_related(
        'sessions', 'master__profile'
    ).order_by('-created_at')[:9]

    home_masterclasses_data = []
    for mc in masterclasses:
        sessions = mc.sessions.filter(
            is_active=True,
            date_event__gte=timezone.now().date()
        ).order_by('date_event', 'time_event')
        first_session = sessions.first()
        sessions_count = sessions.count()

        # Аватар мастера или заглушка
        if mc.master.profile.avatar:
            avatar_url = mc.master.profile.avatar.url
        else:
            avatar_url = static('images/default-avatar.png')   # <-- исправлено

        home_masterclasses_data.append({
            'masterclass': mc,
            'first_session': first_session,
            'sessions_count': sessions_count,
            'master_avatar': avatar_url,
            'master_fio': mc.master.profile.fio,
        })

    return render(request, 'main/index.html', {
        'home_masterclasses_data': home_masterclasses_data
    })


def about(request):
    return render(request, 'main/about.html')


def register_view(request):
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            role = Role.objects.get_or_create(name='Клиент')[0]
            profile = ProfileUser.objects.create(
                user=user,
                role=role,
                fio=form.cleaned_data['fio'],
                phone=form.cleaned_data['phone']
            )
            login(request, user)
            return redirect('profile')
    else:
        form = RegisterForm()
    return render(request, 'main/register.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = LoginFormWithCaptcha(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('index')
    else:
        form = LoginFormWithCaptcha()
    return render(request, 'main/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('index')


def catalog_view(request):
    view_mode = request.GET.get('view', 'masterclasses')

    if view_mode == 'masters':
        # Получаем всех мастеров (пользователей с ролью "Мастер")
        from django.contrib.auth.models import User
        master_role = Role.objects.get(name='Мастер')
        masters = User.objects.filter(profile__role=master_role).annotate(
            masterclasses_count=Count('masterclasses', filter=Q(masterclasses__status=3)),
            avg_rating=Avg('reviews_about__rating')
        ).order_by('profile__fio')

        # Поиск по ФИО
        query = request.GET.get('q')
        if query:
            masters = masters.filter(profile__fio__icontains=query)

        paginator = Paginator(masters, 9)  # 9 мастеров на страницу
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'view_mode': view_mode,
            'page_obj': page_obj,
            'query': query,
        }
        return render(request, 'main/catalog.html', context)

    else:  # masterclasses
        masterclasses = MasterClass.objects.filter(status=3).order_by('-created_at').distinct()
        category_id = request.GET.get('category')
        if category_id:
            masterclasses = masterclasses.filter(category_id=category_id)
        format_filter = request.GET.get('format')
        if format_filter:
            masterclasses = masterclasses.filter(format=format_filter)
        query = request.GET.get('q')
        if query:
            masterclasses = masterclasses.filter(name__icontains=query)

        paginator = Paginator(masterclasses, 6)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        categories = Category.objects.all()

        context = {
            'view_mode': view_mode,
            'page_obj': page_obj,
            'categories': categories,
            'current_category': category_id,
            'current_format': format_filter,
            'query': query,
        }
        return render(request, 'main/catalog.html', context)


def masterclass_detail(request, pk):
    masterclass = get_object_or_404(MasterClass, pk=pk, status=3)
    images = masterclass.images.all()
    sessions = masterclass.sessions.filter(is_active=True, date_event__gte=timezone.now().date()).order_by('date_event', 'time_event')
    user_signup = None

    if request.user.is_authenticated:
        user_signup = SignUpClass.objects.filter(client=request.user, session__masterclass=masterclass).first()

    # Обработка записи на МК (без изменений)
    if request.method == 'POST' and request.user.is_authenticated and 'signup' in request.POST:
        session_id = request.POST.get('session')
        session = get_object_or_404(MasterClassDateTime, id=session_id, is_active=True)
        if SignUpClass.objects.filter(client=request.user, session=session).exists():
            messages.error(request, 'Вы уже записаны на этот мастер-класс в данное время.')
        elif session.available_seats() <= 0:
            messages.error(request, 'На это время нет свободных мест.')
        else:
            signup = SignUpClass.objects.create(
                client=request.user,
                session=session,
                status=2,
                confirmation_token=''
            )
            async_task('main.tasks.send_confirmation_email', signup.id)
            messages.success(request, 'Заявка отправлена мастеру. Вы получите уведомление о решении.')
        return redirect('masterclass_detail', pk=pk)

    # Последние 5 отзывов о мастере
    recent_reviews = Review.objects.filter(master=masterclass.master).select_related('client')[:5]

    # --- НОВЫЙ БЛОК: проверка права на отзыв ---
    can_review = False
    review_form = None
    if request.user.is_authenticated:
        has_attended = SignUpClass.objects.filter(
            client=request.user,
            session__masterclass=masterclass,
            status=5  # статус "Посещено"
        ).exists()
        already_reviewed = Review.objects.filter(
            client=request.user,
            master=masterclass.master
        ).exists()
        can_review = has_attended and not already_reviewed
        if can_review:
            from .forms import ReviewForm  # импорт в начале файла уже есть, но для порядка
            review_form = ReviewForm()
    # --- КОНЕЦ БЛОКА ---

    context = {
        'masterclass': masterclass,
        'images': images,
        'sessions': sessions,
        'user_signup': user_signup,
        'recent_reviews': recent_reviews,
        'master': masterclass.master,
        'can_review': can_review,
        'review_form': review_form,
    }
    return render(request, 'main/masterclass_detail.html', context)


@login_required
def add_review(request, pk):
    masterclass = get_object_or_404(MasterClass, pk=pk, status=3)
    has_attended = SignUpClass.objects.filter(
        client=request.user,
        session__masterclass=masterclass,
        status=5
    ).exists()
    # Проверяем, оставлял ли пользователь уже отзыв этому мастеру
    already_reviewed = Review.objects.filter(
        client=request.user,
        master=masterclass.master
    ).exists()

    if not has_attended or already_reviewed:
        messages.error(request, 'Вы не можете оставить отзыв.')
        return redirect('masterclass_detail', pk=pk)

    if request.method == 'POST':
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.client = request.user
            review.master = masterclass.master  # ← исправлено
            review.save()

            # Уведомление мастеру
            master = masterclass.master
            if master != request.user:
                Notification.objects.create(
                    user=master,
                    message=f'Новый отзыв на ваш мастер-класс "{masterclass.name}" от {request.user.profile.fio}.',
                    link=reverse('masterclass_detail', args=[masterclass.id])
                )

            messages.success(request, 'Отзыв добавлен.')
    return redirect('masterclass_detail', pk=pk)


def confirm_signup(request, token):
    signup = get_object_or_404(SignUpClass, confirmation_token=token, status=1)
    signup.status = 2
    signup.confirmation_token = ''
    signup.save()
    messages.success(request, 'Ваша запись подтверждена!')
    return render(request, 'main/confirm_signup.html', {'signup': signup})


@login_required
def profile_view(request):
    profile = request.user.profile
    active_request = MasterRequest.objects.filter(client=request.user, status=1).first()
    master_requests = MasterRequest.objects.filter(client=request.user).order_by('-created_at')
    signups = SignUpClass.objects.filter(client=request.user).select_related('session', 'session__masterclass')
    my_masterclasses = MasterClass.objects.filter(master=request.user) if profile.is_master() else None
    notifications = Notification.objects.filter(user=request.user, is_read=False)

    can_apply = not profile.is_master() and not request.user.is_staff and not active_request

    if request.method == 'POST':
        form = ProfileUserForm(request.POST, request.FILES, instance=profile, user=request.user)
        master_form = MasterRequestForm(request.POST) if can_apply else None

        if 'update_profile' in request.POST:
            if form.is_valid():
                form.save()
                messages.success(request, 'Профиль обновлён.')
                return redirect('profile')
        elif 'master_request' in request.POST:
            if can_apply and master_form and master_form.is_valid():
                master_request = master_form.save(commit=False)
                master_request.client = request.user
                master_request.save()
                messages.success(request, 'Заявка на роль мастера отправлена.')
                return redirect('profile')
            else:
                messages.error(request, 'Вы не можете подать заявку на роль мастера.')
                return redirect('profile')
        else:
            return redirect('profile')
    else:
        form = ProfileUserForm(instance=profile, user=request.user)
        master_form = MasterRequestForm() if can_apply else None

    context = {
        'profile': profile,
        'form': form,
        'master_form': master_form,
        'signups': signups,
        'my_masterclasses': my_masterclasses,
        'active_request': active_request,
        'master_requests': master_requests,
        'notifications': notifications,
    }
    return render(request, 'main/profile.html', context)


def master_profile(request, user_id):
    master_user = get_object_or_404(User, id=user_id, profile__role__name='Мастер')
    masterclasses = MasterClass.objects.filter(master=master_user, status=3)

    # Аннотируем первую будущую дату для каждого мастер-класса
    from django.db.models import OuterRef, Subquery
    from django.utils import timezone
    masterclasses = masterclasses.annotate(
        first_session_date=Subquery(
            MasterClassDateTime.objects.filter(
                masterclass=OuterRef('pk'),
                is_active=True,
                date_event__gte=timezone.now().date()
            ).order_by('date_event', 'time_event').values('date_event')[:1]
        )
    )

    # --- Отзывы и рейтинг ---
    reviews = Review.objects.filter(master=master_user).select_related('client')
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0
    reviews_count = reviews.count()
    # --- Конец блока ---

    # Проверка, может ли текущий пользователь оставить отзыв
    can_review = False
    if request.user.is_authenticated and request.user != master_user:
        has_attended = SignUpClass.objects.filter(
            client=request.user,
            session__masterclass__master=master_user,
            status=5
        ).exists()
        already_reviewed = Review.objects.filter(client=request.user, master=master_user).exists()
        can_review = has_attended and not already_reviewed

    context = {
        'master': master_user,
        'masterclasses': masterclasses,
        'reviews': reviews,
        'avg_rating': avg_rating,
        'reviews_count': reviews_count,
        'can_review': can_review,
    }
    return render(request, 'main/master_profile.html', context)


@login_required
def create_masterclass(request):
    if not request.user.profile.is_master():
        messages.error(request, 'У вас нет прав для создания мастер-классов.')
        return redirect('catalog')

    if request.method == 'POST':
        form = MasterClassCreateForm(request.POST, request.FILES)
        if form.is_valid():
            masterclass = form.save(commit=False)
            masterclass.master = request.user
            masterclass.status = 2
            masterclass.save()
            image = form.cleaned_data['image']
            MasterClassImage.objects.create(masterclass=masterclass, image=image)
            messages.success(request, 'Мастер-класс отправлен на модерацию. После одобрения вы сможете добавить даты и время проведения в профиле.')
            return redirect('profile')
        else:
            print("Ошибки формы:", form.errors)
    else:
        form = MasterClassCreateForm()

    return render(request, 'main/create_masterclass.html', {'form': form})


@login_required
def manage_sessions(request, masterclass_id):
    masterclass = get_object_or_404(MasterClass, id=masterclass_id, master=request.user)
    if request.method == 'POST':
        form = MasterClassDateTimeForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.masterclass = masterclass
            session.save()
            messages.success(request, 'Новое время добавлено.')
            return redirect('manage_sessions', masterclass_id=masterclass.id)
    else:
        form = MasterClassDateTimeForm()
    sessions = masterclass.sessions.all().order_by('date_event', 'time_event')
    return render(request, 'main/manage_sessions.html', {
        'masterclass': masterclass,
        'form': form,
        'sessions': sessions,
    })


@login_required
def edit_session(request, session_id):
    session = get_object_or_404(MasterClassDateTime, id=session_id, masterclass__master=request.user)
    if request.method == 'POST':
        form = MasterClassDateTimeForm(request.POST, instance=session)
        if form.is_valid():
            form.save()
            messages.success(request, 'Слот обновлён.')
            return redirect('manage_sessions', masterclass_id=session.masterclass.id)
    else:
        form = MasterClassDateTimeForm(instance=session)
    return render(request, 'main/edit_session.html', {'form': form, 'session': session})


@login_required
def delete_session(request, session_id):
    session = get_object_or_404(MasterClassDateTime, id=session_id, masterclass__master=request.user)
    if request.method == 'POST':
        session.delete()
        messages.success(request, 'Слот удалён.')
    return redirect('manage_sessions', masterclass_id=session.masterclass.id)


@login_required
def moderate_signups(request):
    user = request.user
    profile = user.profile
    if not (profile.is_master() or user.is_staff):
        messages.error(request, 'Доступ запрещён.')
        return redirect('profile')
    if user.is_staff:
        signups = SignUpClass.objects.all().select_related('client', 'session', 'session__masterclass').order_by('-created_at')
    else:
        signups = SignUpClass.objects.filter(session__masterclass__master=user).select_related('client', 'session').order_by('-created_at')
    return render(request, 'main/moderate_signups.html', {'signups': signups})


@login_required
def moderate_signup_detail(request, signup_id):
    signup = get_object_or_404(SignUpClass, id=signup_id)
    user = request.user
    if not (user.is_staff or (user.profile.is_master() and signup.session.masterclass.master == user)):
        messages.error(request, 'Нет прав для просмотра этой заявки.')
        return redirect('moderate_signups')
    if request.method == 'POST':
        new_status = request.POST.get('status')
        comment = request.POST.get('comment', '')
        if new_status in ['3', '4']:
            if new_status == '4' and not comment:
                messages.error(request, 'При отклонении необходимо указать причину.')
                return redirect('moderate_signup_detail', signup_id=signup.id)
            # При одобрении увеличиваем booked
            if new_status == '3':
                if signup.session.available_seats() <= 0:
                    messages.error(request, 'Нет свободных мест для одобрения.')
                    return redirect('moderate_signup_detail', signup_id=signup.id)
                signup.session.booked += 1
                signup.session.save()
            signup.status = int(new_status)
            signup.master_comment = comment
            signup.save()
            if new_status == '3':
                async_task('main.tasks.send_signup_approved_email', signup.id)
                messages.success(request, 'Заявка одобрена.')
            else:
                async_task('main.tasks.send_signup_rejected_email', signup.id)
                messages.warning(request, 'Заявка отклонена.')
            return redirect('moderate_signups')
    context = {'signup': signup}
    return render(request, 'main/moderate_signup_detail.html', context)


@login_required
def mark_attended(request, signup_id):
    signup = get_object_or_404(SignUpClass, id=signup_id)
    user = request.user
    if not (user.is_staff or (user.profile.is_master() and signup.session.masterclass.master == user)):
        messages.error(request, 'Нет прав для выполнения действия.')
        return redirect('moderate_signups')
    if signup.status == 3:
        signup.status = 5
        signup.save()
        async_task('main.tasks.send_thank_you_email', signup.id)
        messages.success(request, 'Участник отмечен как посетивший.')
    else:
        messages.error(request, 'Нельзя отметить эту заявку как посещённую (она должна быть одобрена).')
    return redirect('moderate_signups')


@login_required
def manage_signups(request):
    return redirect('moderate_signups')


@login_required
def change_signup_status(request, signup_id):
    signup = get_object_or_404(SignUpClass, id=signup_id)
    user = request.user
    profile = user.profile
    if not (user.is_staff or (profile.is_master() and signup.session.masterclass.master == user)):
        messages.error(request, 'Нет прав для изменения статуса.')
        return redirect('moderate_signups')
    if request.method == 'POST':
        form = SignupStatusForm(request.POST, instance=signup)
        if form.is_valid():
            form.save()
            messages.success(request, 'Статус записи обновлён.')
    return redirect('moderate_signups')


# ==================== Административные заявки ====================

@staff_member_required
def master_requests_list(request):
    requests = MasterRequest.objects.all().select_related('client').order_by('-created_at')
    return render(request, 'main/master_requests_list.html', {'requests': requests})


@staff_member_required
def master_request_detail(request, request_id):
    master_request = get_object_or_404(MasterRequest, id=request_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        admin_comment = request.POST.get('admin_comment', '')
        if new_status in ['2', '3']:
            master_request.status = int(new_status)
            master_request.admin_comment = admin_comment
            master_request.processed_at = timezone.now()
            master_request.save()
            if new_status == '2':
                role_master = Role.objects.get(name='Мастер')
                master_request.client.profile.role = role_master
                master_request.client.profile.save()
                async_task('main.tasks.send_master_request_approved_email', master_request.id)
                messages.success(request, f'Заявка #{master_request.id} одобрена.')
            else:
                async_task('main.tasks.send_master_request_rejected_email', master_request.id)
                messages.warning(request, f'Заявка #{master_request.id} отклонена.')
            return redirect('master_requests_list')
    context = {'master_request': master_request}
    return render(request, 'main/master_request_detail.html', context)


@staff_member_required
def moderate_masterclasses(request):
    masterclasses = MasterClass.objects.filter(status=2).select_related('master', 'category').order_by('-created_at')
    return render(request, 'main/moderate_masterclasses.html', {'masterclasses': masterclasses})


@staff_member_required
def moderate_masterclass_detail(request, pk):
    mc = get_object_or_404(MasterClass, pk=pk, status=2)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        comment = request.POST.get('comment', '')
        if new_status in ['3', '6']:
            mc.status = int(new_status)
            mc.comment = comment
            mc.save()
            async_task('main.tasks.send_masterclass_moderation_email', mc.id, new_status)
            messages.success(request, f'Мастер-класс "{mc.name}" {"одобрен" if new_status == "3" else "отклонён"}.')
            return redirect('moderate_masterclasses')
    context = {'masterclass': mc}
    return render(request, 'main/moderate_masterclass_detail.html', context)


def privacy(request):
    return render(request, 'main/privacy.html')


# ==================== Уведомления ====================
@login_required
def mark_notifications_read(request):
    if request.method == 'POST':
        request.user.notifications.filter(is_read=False).update(is_read=True)
        messages.success(request, 'Все уведомления отмечены как прочитанные.')
    return redirect('profile')

def master_reviews(request, user_id):
    master = get_object_or_404(User, id=user_id, profile__role__name='Мастер')
    reviews = Review.objects.filter(master=master).select_related('client')
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0
    paginator = Paginator(reviews, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    context = {
        'master': master,
        'page_obj': page_obj,
        'avg_rating': avg_rating,
    }
    return render(request, 'main/master_reviews.html', context)