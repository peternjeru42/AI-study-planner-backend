import json
from datetime import datetime, timedelta
from urllib import error, request

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.assessments.models import Assessment
from apps.planner.models import StudySession
from apps.subjects.models import Subject


class PlannerAIService:
    MODEL_OPTIONS = [
        {
            "id": "gpt-5-mini",
            "label": "GPT-5 mini",
            "description": "Recommended balance of quality, speed, and cost for study coaching.",
            "recommended": True,
        },
        {
            "id": "gpt-5",
            "label": "GPT-5",
            "description": "Highest-quality reasoning for more detailed planning advice.",
            "recommended": False,
        },
        {
            "id": "gpt-5-nano",
            "label": "GPT-5 nano",
            "description": "Fastest and cheapest option for short planning prompts.",
            "recommended": False,
        },
    ]
    MODEL_IDS = {item["id"] for item in MODEL_OPTIONS}
    API_URL = "https://api.openai.com/v1/chat/completions"

    @classmethod
    def supported_models(cls):
        return cls.MODEL_OPTIONS

    @staticmethod
    def _api_key():
        return getattr(settings, "OPENAI_API_KEY", None) or getattr(settings, "OPEN_AI_API_KEY", None)

    @staticmethod
    def _extract_text(payload):
        choices = payload.get("choices") or []
        if not choices:
            raise ValidationError("OpenAI returned no choices.")

        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            fragments = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    fragments.append(item["text"])
            if fragments:
                return "\n".join(fragments).strip()

        raise ValidationError("OpenAI returned an unexpected response format.")

    @staticmethod
    def _extract_json(text):
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValidationError("OpenAI did not return valid JSON for the study plan draft.") from exc

    @staticmethod
    def _parse_date(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValidationError({"sessions": ["Each session must include a valid sessionDate in YYYY-MM-DD format."]}) from exc

    @staticmethod
    def _parse_time(value, field_name):
        try:
            return datetime.strptime(value, "%H:%M").time()
        except (TypeError, ValueError) as exc:
            raise ValidationError({"sessions": [f"Each session must include a valid {field_name} in HH:MM format."]}) from exc

    @classmethod
    def _normalize_custom_plan_draft(cls, *, user, payload, study_scope, target_name, duration_value, duration_unit, excluded_days, instructions, model):
        profile = user.student_profile
        sessions = payload.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            raise ValidationError({"sessions": ["OpenAI returned an empty study plan."]})

        excluded = {day.lower() for day in excluded_days}
        normalized_sessions = []

        for index, item in enumerate(sessions, start=1):
            if not isinstance(item, dict):
                raise ValidationError({"sessions": ["OpenAI returned an invalid session payload."]})

            session_date = cls._parse_date(item.get("sessionDate"))
            if session_date.strftime("%A").lower() in excluded:
                raise ValidationError({"excludedDays": [f"Generated session falls on excluded day: {session_date.strftime('%A')}"]})
            if not profile.weekend_available and session_date.weekday() >= 5:
                raise ValidationError({"sessions": ["Generated session falls on a weekend even though weekends are unavailable."]})

            start_time = cls._parse_time(item.get("startTime"), "startTime")
            end_time_value = item.get("endTime")
            duration_value_raw = item.get("duration")

            if duration_value_raw in (None, "") and end_time_value in (None, ""):
                raise ValidationError({"sessions": ["Each session must include either duration or endTime."]})

            if duration_value_raw in (None, ""):
                end_time = cls._parse_time(end_time_value, "endTime")
                duration_minutes = int(
                    (datetime.combine(session_date, end_time) - datetime.combine(session_date, start_time)).total_seconds() / 60
                )
            else:
                duration_minutes = int(duration_value_raw)
                if duration_minutes <= 0:
                    raise ValidationError({"sessions": ["Session duration must be greater than zero."]})
                end_time = cls._parse_time(end_time_value, "endTime") if end_time_value else (
                    datetime.combine(session_date, start_time) + timedelta(minutes=duration_minutes)
                ).time()

            if end_time <= start_time:
                raise ValidationError({"sessions": ["Session end time must be after start time."]})

            if start_time < profile.preferred_study_start_time or end_time > profile.preferred_study_end_time:
                raise ValidationError(
                    {
                        "sessions": [
                            f"Generated session '{item.get('title') or f'Session {index}'}' falls outside the preferred study window."
                        ]
                    }
                )

            normalized_sessions.append(
                {
                    "tempId": item.get("tempId") or f"draft-session-{index}",
                    "title": item.get("title") or f"{target_name} Session {index}",
                    "sessionDate": session_date.isoformat(),
                    "startTime": start_time.strftime("%H:%M"),
                    "endTime": end_time.strftime("%H:%M"),
                    "duration": duration_minutes,
                    "sessionType": item.get("sessionType") or "revision",
                    "notes": item.get("notes") or "",
                }
            )

        normalized_sessions.sort(key=lambda item: (item["sessionDate"], item["startTime"]))
        start_date = normalized_sessions[0]["sessionDate"]
        end_date = normalized_sessions[-1]["sessionDate"]

        return {
            "model": model,
            "promptSummary": payload.get("summary") or f"Study {target_name} for {duration_value} {duration_unit}.",
            "draft": {
                "title": payload.get("title") or f"{target_name} Study Plan",
                "studyScope": study_scope,
                "targetName": target_name,
                "durationValue": duration_value,
                "durationUnit": duration_unit,
                "excludedDays": excluded_days,
                "instructions": instructions,
                "summary": payload.get("summary") or "",
                "startDate": start_date,
                "endDate": end_date,
                "sessions": normalized_sessions,
            },
        }

    @classmethod
    def _build_context(cls, user):
        profile = user.student_profile
        today = timezone.localdate()
        subjects = list(Subject.objects.filter(user=user, is_active=True).order_by("name")[:8])
        assessments = list(
            Assessment.objects.filter(user=user, status__in=["pending", "in_progress", "overdue"])
            .select_related("subject")
            .order_by("due_date", "due_time")[:10]
        )
        sessions = list(
            StudySession.objects.filter(user=user, session_date__range=(today, today + timedelta(days=7)))
            .select_related("subject", "assessment")
            .order_by("session_date", "start_time")[:12]
        )

        return {
            "student": {
                "name": user.full_name,
                "courseName": profile.course_name,
                "yearOfStudy": profile.year_of_study,
                "institutionName": profile.institution_name,
                "timezone": profile.timezone,
                "preferences": {
                    "studyStart": profile.preferred_study_start_time.strftime("%H:%M"),
                    "studyEnd": profile.preferred_study_end_time.strftime("%H:%M"),
                    "sessionLengthMinutes": profile.preferred_session_length_minutes,
                    "breakLengthMinutes": profile.preferred_break_length_minutes,
                    "maxSessionsPerDay": profile.max_sessions_per_day,
                    "weekendAvailable": profile.weekend_available,
                },
            },
            "subjects": [{"name": item.name, "code": item.code, "semester": item.semester or ""} for item in subjects],
            "assessments": [
                {
                    "title": item.title,
                    "subject": item.subject.name,
                    "type": item.assessment_type,
                    "status": item.status,
                    "dueDate": item.due_date.isoformat(),
                    "dueTime": item.due_time.strftime("%H:%M") if item.due_time else None,
                    "estimatedHours": float(item.estimated_hours),
                    "weightPercentage": float(item.weight_percentage),
                }
                for item in assessments
            ],
            "sessions": [
                {
                    "title": item.title,
                    "subject": item.subject.name if item.subject else None,
                    "sessionDate": item.session_date.isoformat(),
                    "startTime": item.start_time.strftime("%H:%M"),
                    "endTime": item.end_time.strftime("%H:%M"),
                    "durationMinutes": item.duration_minutes,
                    "status": item.status,
                }
                for item in sessions
            ],
        }

    @classmethod
    def _request_completion(cls, *, api_key, model, messages):
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
            }
        ).encode("utf-8")
        req = request.Request(
            cls.API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            try:
                message = json.loads(detail).get("error", {}).get("message") or "OpenAI request failed."
            except json.JSONDecodeError:
                message = "OpenAI request failed."
            raise ValidationError(message) from exc
        except error.URLError as exc:
            raise ValidationError("Unable to reach OpenAI from the backend service.") from exc

    @classmethod
    def study_assistant(cls, *, user, question, model=None):
        if model and model not in cls.MODEL_IDS:
            raise ValidationError({"model": "Unsupported model selected."})

        api_key = cls._api_key()
        if not api_key:
            raise ValidationError("OpenAI API key is not configured.")

        selected_model = model or getattr(settings, "OPENAI_DEFAULT_MODEL", "gpt-5-mini")
        if selected_model not in cls.MODEL_IDS:
            selected_model = "gpt-5-mini"

        context = cls._build_context(user)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an academic study coach inside StudyFlow. "
                    "Use only the provided student context. "
                    "Be practical, structured, and concise. "
                    "Give advice tailored to workload, deadlines, and study preferences. "
                    "Prefer short sections with flat bullet points."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Student context:\n{json.dumps(context, indent=2)}\n\n"
                    f"Question: {question}\n\n"
                    "Respond with:\n"
                    "1. A short direct answer.\n"
                    "2. A prioritized action plan.\n"
                    "3. Any schedule risks you notice.\n"
                ),
            },
        ]

        payload = cls._request_completion(api_key=api_key, model=selected_model, messages=messages)
        return {
            "model": selected_model,
            "question": question,
            "answer": cls._extract_text(payload),
            "contextSummary": {
                "subjectsCount": len(context["subjects"]),
                "assessmentsCount": len(context["assessments"]),
                "sessionsCount": len(context["sessions"]),
            },
        }

    @classmethod
    def generate_custom_plan_draft(
        cls,
        *,
        user,
        study_scope,
        target_name,
        duration_value,
        duration_unit,
        excluded_days,
        instructions="",
        model=None,
    ):
        if model and model not in cls.MODEL_IDS:
            raise ValidationError({"model": "Unsupported model selected."})

        api_key = cls._api_key()
        if not api_key:
            raise ValidationError("OpenAI API key is not configured.")

        selected_model = model or getattr(settings, "OPENAI_DEFAULT_MODEL", "gpt-5-mini")
        if selected_model not in cls.MODEL_IDS:
            selected_model = "gpt-5-mini"

        context = cls._build_context(user)
        profile = user.student_profile
        excluded_days_text = ", ".join(excluded_days) if excluded_days else "none"
        example = "For instance I want to study Discrete maths unit for 10 hours a week excluding Tuesdays, create a study plan."

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a study-plan generator inside StudyFlow. "
                    "Return valid JSON only. Do not add markdown, explanation, or code fences. "
                    "Build a realistic study plan that respects the user's study window, max sessions per day, and excluded days."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Student context:\n{json.dumps(context, indent=2)}\n\n"
                    f"Create a study plan draft for this {study_scope}: {target_name}\n"
                    f"Target duration: {duration_value} {duration_unit}\n"
                    f"Excluded days: {excluded_days_text}\n"
                    f"Extra instructions: {instructions or 'none'}\n"
                    f"Example intent: {example}\n\n"
                    f"Use only times between {profile.preferred_study_start_time.strftime('%H:%M')} and "
                    f"{profile.preferred_study_end_time.strftime('%H:%M')}. "
                    f"Use no more than {profile.max_sessions_per_day} sessions per day. "
                    "Return JSON with this shape:\n"
                    "{"
                    '"title":"string",'
                    '"summary":"string",'
                    '"sessions":['
                    '{'
                    '"title":"string",'
                    '"sessionDate":"YYYY-MM-DD",'
                    '"startTime":"HH:MM",'
                    '"endTime":"HH:MM",'
                    '"duration":90,'
                    '"sessionType":"reading|revision|assignment_work|exam_prep|project_work",'
                    '"notes":"string"'
                    "}"
                    "]"
                    "}\n"
                    "Return 3 to 14 sessions."
                ),
            },
        ]

        payload = cls._request_completion(api_key=api_key, model=selected_model, messages=messages)
        draft_payload = cls._extract_json(cls._extract_text(payload))
        return cls._normalize_custom_plan_draft(
            user=user,
            payload=draft_payload,
            study_scope=study_scope,
            target_name=target_name,
            duration_value=duration_value,
            duration_unit=duration_unit,
            excluded_days=excluded_days,
            instructions=instructions,
            model=selected_model,
        )
