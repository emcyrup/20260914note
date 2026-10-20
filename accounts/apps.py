from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        from . import security  # noqa: F401  ログインの記録（合図の受け手を登録）
