import json
from datetime import date, datetime, timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.assessments.models import Assessment
from apps.assessments.services import AssessmentService
from apps.notifications.models import Notification
from apps.notifications.services import NotificationService
from apps.planner.models import PlannerLog, StudyPlan, StudySession
from apps.progress.services import ProgressService
from apps.subjects.models import Subject
from common.utils import parse_time_string


class PlannerService:
    SESSION_TYPE_MAP = {
        "assignment": "assignment_work",
        "cat": "revision",
        "quiz": "revision",
        "exam": "exam_prep",
        "project": "project_work",
        "presentation": "project_work",
    }

    @staticmethod
    def _time_add(base_time, minutes):
        dt = datetime.combine(date.today(), base_time) + timedelta(minutes=minutes)
        return dt.time()

    @staticmethod
    def _coerce_date(value):
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    @staticmethod
    def _coerce_time(value):
        if hasattr(value, "hour") and hasattr(value, "minute"):
            return value
        return parse_time_string(str(value)[:5])

    @staticmethod
    def _unique_subject_code(*, user, base):
        seed = slugify(base).replace("-", "").upper()[:8] or "STUDY"
        candidate = seed
        index = 1
        while Subject.objects.filter(user=user, code=candidate).exists():
            index += 1
            suffix = str(index)
            candidate = f"{seed[: max(1, 8 - len(suffix))]}{suffix}"
        return candidate

    @classmethod
    def ensure_subject_for_target(cls, *, user, target_name, study_scope):
        existing = (
            Subject.objects.filter(user=user, is_active=True)
            .filter(name__iexact=target_name)
            .order_by("name")
            .first()
        )
        if existing:
            return existing

        return Subject.objects.create(
            user=user,
            name=target_name,
            code=cls._unique_subject_code(user=user, base=target_name),
            instructor_name="",
            semester="",
            description=f"Auto-created for AI custom {study_scope} planning.",
        )

    @staticmethod
    def _schedule_session_reminders(session):
        profile = session.user.student_profile
        reminder_at = timezone.make_aware(
            datetime.combine(
                PlannerService._coerce_date(session.session_date),
                PlannerService._coerce_time(session.start_time),
            )
        ) - timedelta(hours=1)
        now = timezone.now()
        is_due_now = reminder_at <= now
        status = "sent" if is_due_now else "queued"
        scheduled_for = None if is_due_now else reminder_at
        sent_at = now if is_due_now else None
        message = f"Reminder: {session.title} starts at {session.start_time.strftime('%H:%M')}."

        notifications = [
            NotificationService.create_notification(
                user=session.user,
                title="Study session reminder",
                message=message,
                notification_type="study_session_reminder",
                channel="in_app",
                status=status,
                subject=session.subject,
                study_session=session,
                scheduled_for=scheduled_for,
            )
        ]

        if profile.enable_email_notifications_simulated:
            email_notification = NotificationService.create_notification(
                user=session.user,
                title="Study session reminder",
                message=message,
                notification_type="study_session_reminder",
                channel="email",
                status=status,
                subject=session.subject,
                study_session=session,
                scheduled_for=scheduled_for,
                is_simulated=True,
            )
            if is_due_now:
                email_notification.sent_at = sent_at
                email_notification.save(update_fields=["sent_at"])
            notifications.append(email_notification)

        if is_due_now:
            for notification in notifications:
                notification.sent_at = sent_at
                notification.save(update_fields=["sent_at"])
        return notifications

    @staticmethod
    def _delete_plan_sessions_and_reminders(plan):
        session_ids = list(plan.sessions.values_list("id", flat=True))
        if session_ids:
            Notification.objects.filter(study_session_id__in=session_ids).delete()
        plan.sessions.all().delete()

    @staticmethod
    def _json_safe(value):
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))

    @classmethod
    def _build_sessions(cls, *, user, plan, assessments, start_date, end_date):
        profile = user.student_profile
        sessions = []
        current_date = max(start_date, timezone.localdate())
        current_time = profile.preferred_study_start_time
        sessions_per_day = 0

        for assessment in assessments:
            if current_date > end_date:
                break
            sessions_needed = max(1, int(round((float(assessment.estimated_hours) * 60) / profile.preferred_session_length_minutes + 0.49)))
            for index in range(sessions_needed):
                while True:
                    if current_date > end_date:
                        break
                    if not profile.weekend_available and current_date.weekday() >= 5:
                        current_date += timedelta(days=1)
                        current_time = profile.preferred_study_start_time
                        sessions_per_day = 0
                        continue
                    if sessions_per_day >= profile.max_sessions_per_day:
                        current_date += timedelta(days=1)
                        current_time = profile.preferred_study_start_time
                        sessions_per_day = 0
                        continue
                    end_time = cls._time_add(current_time, profile.preferred_session_length_minutes)
                    if end_time > profile.preferred_study_end_time:
                        current_date += timedelta(days=1)
                        current_time = profile.preferred_study_start_time
                        sessions_per_day = 0
                        continue
                    break

                if current_date > end_date:
                    break

                session = StudySession.objects.create(
                    study_plan=plan,
                    user=user,
                    subject=assessment.subject,
                    assessment=assessment,
                    title=f"{assessment.title} - Session {index + 1}",
                    session_date=current_date,
                    start_time=current_time,
                    end_time=end_time,
                    duration_minutes=profile.preferred_session_length_minutes,
                    session_type=cls.SESSION_TYPE_MAP.get(assessment.assessment_type, "revision"),
                    priority_score=assessment.calculated_priority_score,
                    status="planned",
                )
                sessions.append(session)
                sessions_per_day += 1
                current_time = cls._time_add(end_time, profile.preferred_break_length_minutes)
        return sessions

    @classmethod
    @transaction.atomic
    def generate_plan(cls, *, user, startDate=None, endDate=None, title="", trigger="manual", regenerate=False):
        start_date = startDate or timezone.localdate()
        end_date = endDate or (start_date + timedelta(days=13))

        active_plan = StudyPlan.objects.filter(user=user, status="active").first()
        if regenerate and active_plan:
            active_plan.status = "archived"
            active_plan.save(update_fields=["status", "updated_at"])
            active_plan.sessions.filter(status="planned", session_date__gte=timezone.localdate()).update(status="rescheduled")

        assessments = list(
            Assessment.objects.filter(user=user, status__in=["pending", "in_progress", "overdue"])
            .select_related("subject")
            .order_by("due_date", "due_time")
        )
        for assessment in assessments:
            AssessmentService.refresh_assessment(assessment)
        assessments.sort(key=lambda item: item.calculated_priority_score, reverse=True)

        plan = StudyPlan.objects.create(
            user=user,
            title=title or f"Study Plan {start_date.isoformat()} to {end_date.isoformat()}",
            generated_for_start_date=start_date,
            generated_for_end_date=end_date,
            generation_trigger=trigger,
            status="active",
        )

        sessions = cls._build_sessions(
            user=user,
            plan=plan,
            assessments=assessments,
            start_date=start_date,
            end_date=end_date,
        )
        PlannerLog.objects.create(
            user=user,
            study_plan=plan,
            trigger_source=trigger,
            input_snapshot_json={
                "assessmentIds": [str(item.id) for item in assessments],
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
            output_snapshot_json={"sessionIds": [str(item.id) for item in sessions], "sessionsCreated": len(sessions)},
            status="completed",
            message=f"Generated {len(sessions)} sessions.",
        )
        ProgressService.sync_all_subject_progress(user)
        NotificationService.plan_generated(user, plan)
        return plan, sessions

    @staticmethod
    def current_plan(user):
        return StudyPlan.objects.filter(user=user, status="active").prefetch_related("sessions").first()

    @staticmethod
    def reschedule_session(*, session, sessionDate, startTime, endTime=None):
        session.session_date = sessionDate
        session.start_time = startTime
        session.end_time = endTime or PlannerService._time_add(startTime, session.duration_minutes)
        session.status = "rescheduled"
        session.save(update_fields=["session_date", "start_time", "end_time", "status", "updated_at"])
        return session

    @classmethod
    @transaction.atomic
    def save_custom_plan(cls, *, user, draft, plan_id=None):
        subject = cls.ensure_subject_for_target(
            user=user,
            target_name=draft["targetName"],
            study_scope=draft["studyScope"],
        )

        if plan_id:
            plan = StudyPlan.objects.get(id=plan_id, user=user)
            cls._delete_plan_sessions_and_reminders(plan)
            plan.title = draft["title"]
            plan.generated_for_start_date = cls._coerce_date(draft["startDate"])
            plan.generated_for_end_date = cls._coerce_date(draft["endDate"])
            plan.generation_trigger = "ai_custom"
            plan.status = "active"
            plan.save(
                update_fields=[
                    "title",
                    "generated_for_start_date",
                    "generated_for_end_date",
                    "generation_trigger",
                    "status",
                    "updated_at",
                ]
            )
            trigger_source = "ai_custom_updated"
        else:
            plan = StudyPlan.objects.create(
                user=user,
                title=draft["title"],
                generated_for_start_date=cls._coerce_date(draft["startDate"]),
                generated_for_end_date=cls._coerce_date(draft["endDate"]),
                generation_trigger="ai_custom",
                status="active",
            )
            trigger_source = "ai_custom"

        sessions = []
        for item in draft["sessions"]:
            session = StudySession.objects.create(
                study_plan=plan,
                user=user,
                subject=subject,
                title=item["title"],
                session_date=cls._coerce_date(item["sessionDate"]),
                start_time=cls._coerce_time(item["startTime"]),
                end_time=cls._coerce_time(item["endTime"]),
                duration_minutes=item["duration"],
                session_type=item.get("sessionType") or "revision",
                priority_score=50,
                status="planned",
                notes=item.get("notes", ""),
            )
            sessions.append(session)
            cls._schedule_session_reminders(session)

        PlannerLog.objects.create(
            user=user,
            study_plan=plan,
            trigger_source=trigger_source,
            input_snapshot_json=cls._json_safe(draft),
            output_snapshot_json={"planId": str(plan.id), "sessionIds": [str(item.id) for item in sessions], "sessionsCreated": len(sessions)},
            status="completed",
            message=f"Saved AI custom plan with {len(sessions)} sessions.",
        )
        NotificationService.plan_generated(user, plan)
        return plan, sessions
