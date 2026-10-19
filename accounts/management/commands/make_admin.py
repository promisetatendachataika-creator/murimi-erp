"""python manage.py make_admin <WhatsApp number or username>

Lets an existing account into the staff admin (/admin). The person keeps the
password they already use, so no new password is created or shown anywhere.
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.backends import find_user


class Command(BaseCommand):
    help = "Give an existing account access to the staff admin page."

    def add_arguments(self, parser):
        parser.add_argument("login", nargs="?", help="WhatsApp number or username")

    def handle(self, *args, **options):
        login = options["login"] or input("WhatsApp number or username of the account: ").strip()
        user = find_user(login)
        if user is None:
            raise CommandError(f"No account found for '{login}'. Sign up on the website first.")
        user.is_staff = user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
        self.stdout.write(f"{user.full_name} can now log in at /admin with their usual password.")
