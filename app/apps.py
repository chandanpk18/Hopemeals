# app/apps.py
from django.apps import AppConfig

class AppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app'

    def ready(self):
        import app.signals  # if you already have this
        import app.signals_ratings  # if you added ratings notifications
        import app.signals_orders   # <-- make sure this line exists
