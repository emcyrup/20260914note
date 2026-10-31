from django.apps import AppConfig


class NursingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nursing'
    verbose_name = '介護施設の記録（検温板・介護記録・連絡帳・事故報告・薬・計画書）'
