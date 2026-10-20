"""python manage.py reset_password [WhatsApp number or username]

For when someone (even the owner) has forgotten their password and can't use
"Forgot password?". Gives the account a temporary password, shown once here;
the person must choose their own password the next time they log in.
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.backends import find_user
from erp_core.api import temporary_password


class Command(BaseCommand):
    help = "Give an account a temporary password (they choose a new one at their next log-in)."

    def add_arguments(self, parser):
        parser.add_argument("login", nargs="?", help="WhatsApp number or username")

    def handle(self, *args, **options):
        login = options["login"] or input("WhatsApp number or username of the account: ").strip()
        user = find_user(login)
        if user is None:
            raise CommandError(f"No account found for '{login}'.")
        password = temporary_password()
        user.set_password(password)
        user.must_change_password = True
        user.is_active = True
        user.save(update_fields=["password", "must_change_password", "is_active"])
        self.stdout.write("")
        self.stdout.write(f"  Account:            {user.full_name} ({user.phone}{', ' + user.username if user.username else ''})")
        self.stdout.write(f"  Temporary password: {password}")
        self.stdout.write("")
        self.stdout.write("  Log in with it now. Murimi will ask you to choose your own password straight away.")
