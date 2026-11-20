import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.core.management import call_command
from django_apscheduler import util
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJobExecution

logger = logging.getLogger(__name__)

def run_daily_backups():
    try:
        logger.info("Starting daily database and media backup...")
        # --clean removes old backups based on your DBBACKUP_CLEANUP_KEEP setting
        call_command("dbbackup", clean=True)
        call_command("mediabackup", clean=True)
        logger.info("Daily backup completed successfully.")
    except Exception as e:
        logger.error(f"Backup failed: {e}")

@util.close_old_connections
def delete_old_job_executions(max_age=604_800):
    DjangoJobExecution.objects.delete_old_job_executions(max_age)

def start():
    scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
    scheduler.add_jobstore(DjangoJobStore(), "default")

    # Runs every day at 2:00 AM (adjust hour/minute as needed)
    scheduler.add_job(
        run_daily_backups,
        trigger=CronTrigger(hour="02", minute="00"),
        id="daily_db_and_media_backup",
        max_instances=1,
        replace_existing=True,
    )

    # Cleans up old scheduler logs every Monday
    scheduler.add_job(
        delete_old_job_executions,
        trigger=CronTrigger(day_of_week="mon", hour="00", minute="00"),
        id="delete_old_job_executions",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.start()
