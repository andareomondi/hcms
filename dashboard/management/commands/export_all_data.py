import getpass
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dashboard.audit_actions import AuditAction
from dashboard.export import ExportError, build_export, file_sha256
from dashboard.utils import create_audit_entry


class Command(BaseCommand):
    help = "Full data export for auditing. Run by a superuser on the server."

    def add_arguments(self, parser):
        parser.add_argument("--actor", required=True,
                            help="Username of the superuser running the export.")
        parser.add_argument("--out", default=None,
                            help="Output path. Default: EXPORT_DIR with a timestamped name.")
        parser.add_argument("--include-files", action="store_true",
                            help="Also include uploaded scan files (large).")

    def handle(self, *args, **opts):
        User = get_user_model()
        actor = User.objects.filter(username=opts["actor"], is_superuser=True).first()
        if actor is None:
            raise CommandError("Only an existing superuser can run this export.")

        # Prompted in the terminal so the password never appears in shell history.
        password = getpass.getpass("Your password: ")
        if not actor.check_password(password):
            create_audit_entry(actor, AuditAction.EXPORT_FAILED,
                               "Command export refused: wrong password.")
            raise CommandError("Password incorrect.")

        passphrase = getpass.getpass(f"Export passphrase (min {settings.EXPORT_MIN_PASSPHRASE} chars): ")
        if passphrase != getpass.getpass("Repeat passphrase: "):
            raise CommandError("Passphrases do not match.")

        name = f"hcms-export-{timezone.now():%Y%m%d-%H%M%S}.zip"
        out = opts["out"] or os.path.join(str(settings.EXPORT_DIR), name)
        os.makedirs(os.path.dirname(os.path.abspath(out)), mode=0o700, exist_ok=True)

        create_audit_entry(actor, AuditAction.EXPORT_STARTED,
                           f"Command export started to {out}"
                           f"{' with files' if opts['include_files'] else ''}.")
        try:
            manifest = build_export(out, passphrase=passphrase,
                                    include_files=opts["include_files"])
            os.chmod(out, 0o600)
            digest = file_sha256(out)
        except (ExportError, OSError) as exc:
            if os.path.exists(out):
                os.remove(out)
            create_audit_entry(actor, AuditAction.EXPORT_FAILED, f"Command export failed: {exc}")
            raise CommandError(f"Export failed: {exc}")

        total = sum(manifest["tables"].values())
        create_audit_entry(actor, AuditAction.DATA_EXPORTED,
                           f"Command export {out}: {total:,} rows across "
                           f"{len(manifest['tables'])} tables, sha256 {digest}.")
        self.stdout.write(self.style.SUCCESS(f"Wrote {out} (sha256 {digest})."))
