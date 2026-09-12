from django.contrib.auth.models import AbstractUser
from django.db import models


class StaffAccount(AbstractUser):
    """
    職員アカウント。Djangoの標準UserをベースにFacility紐づけ・権限区分を追加する。
    """

    ROLE_ADMIN = 'admin'
    ROLE_CHILD_DEV_MANAGER = 'child_dev_manager'  # 児童発達支援管理責任者
    ROLE_STAFF = 'staff'
    ROLE_OFFICE = 'office'

    ROLE_CHOICES = [
        (ROLE_ADMIN, '管理者'),
        (ROLE_CHILD_DEV_MANAGER, '児発管'),
        (ROLE_STAFF, '職員'),
        (ROLE_OFFICE, '事務'),
    ]

    facility = models.ForeignKey(
        'facilities.Facility',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='staff_accounts',
        verbose_name='所属施設',
    )
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_STAFF,
        verbose_name='権限区分',
    )
    display_name = models.CharField(max_length=50, blank=True, verbose_name='表示名')

    class Meta:
        verbose_name = '職員アカウント'
        verbose_name_plural = '職員アカウント'

    def __str__(self):
        return self.display_name or self.username

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_child_dev_manager(self):
        return self.role == self.ROLE_CHILD_DEV_MANAGER
