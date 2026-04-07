from datetime import datetime

from django.utils import timezone
from rest_framework import serializers

from apps.planner.models import PlannerLog, StudyPlan, StudySession
from common.utils import snake_to_frontend


class StudySessionSerializer(serializers.ModelSerializer):
    studyPlanId = serializers.UUIDField(source="study_plan_id", read_only=True)
    userId = serializers.UUIDField(source="user_id", read_only=True)
    subjectId = serializers.UUIDField(source="subject_id", read_only=True)
    assessmentId = serializers.UUIDField(source="assessment_id", read_only=True, allow_null=True)
    sessionDate = serializers.DateField(source="session_date", format="%Y-%m-%d")
    duration = serializers.IntegerField(source="duration_minutes")
    sessionType = serializers.CharField(source="session_type")
    priorityScore = serializers.FloatField(source="priority_score")
    isFavorite = serializers.BooleanField(source="is_favorite", required=False)
    isPinned = serializers.BooleanField(source="is_pinned", required=False)
    startTime = serializers.SerializerMethodField()
    endTime = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = StudySession
        fields = [
            "id",
            "studyPlanId",
            "userId",
            "subjectId",
            "assessmentId",
            "title",
            "sessionDate",
            "startTime",
            "endTime",
            "duration",
            "sessionType",
            "priorityScore",
            "status",
            "notes",
            "isFavorite",
            "isPinned",
        ]

    def _combine(self, obj, value):
        return timezone.make_aware(datetime.combine(obj.session_date, value)).isoformat()

    def get_startTime(self, obj):
        return self._combine(obj, obj.start_time)

    def get_endTime(self, obj):
        return self._combine(obj, obj.end_time)

    def get_status(self, obj):
        return snake_to_frontend(obj.status)


class StudyPlanSerializer(serializers.ModelSerializer):
    generatedForStartDate = serializers.DateField(source="generated_for_start_date", format="%Y-%m-%d")
    generatedForEndDate = serializers.DateField(source="generated_for_end_date", format="%Y-%m-%d")
    generationTrigger = serializers.CharField(source="generation_trigger")
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")
    sessions = StudySessionSerializer(many=True, read_only=True)
    aiDraft = serializers.SerializerMethodField()

    class Meta:
        model = StudyPlan
        fields = [
            "id",
            "title",
            "generatedForStartDate",
            "generatedForEndDate",
            "generationTrigger",
            "status",
            "createdAt",
            "updatedAt",
            "sessions",
            "aiDraft",
        ]

    def get_aiDraft(self, obj):
        if obj.generation_trigger != "ai_custom":
            return None
        log = obj.logs.filter(trigger_source__in=["ai_custom", "ai_custom_updated"]).order_by("-created_at").first()
        if not log:
            return None
        payload = log.input_snapshot_json
        sessions = []
        for item in payload.get("sessions", []):
            session = dict(item)
            if session.get("startTime"):
                session["startTime"] = str(session["startTime"])[:5]
            if session.get("endTime"):
                session["endTime"] = str(session["endTime"])[:5]
            if session.get("sessionDate"):
                session["sessionDate"] = str(session["sessionDate"])[:10]
            sessions.append(session)
        payload["sessions"] = sessions
        if payload.get("startDate"):
            payload["startDate"] = str(payload["startDate"])[:10]
        if payload.get("endDate"):
            payload["endDate"] = str(payload["endDate"])[:10]
        return payload


class PlannerLogSerializer(serializers.ModelSerializer):
    studyPlanId = serializers.UUIDField(source="study_plan_id", allow_null=True)
    triggerSource = serializers.CharField(source="trigger_source")
    inputSnapshot = serializers.JSONField(source="input_snapshot_json")
    outputSnapshot = serializers.JSONField(source="output_snapshot_json")
    createdAt = serializers.DateTimeField(source="created_at")

    class Meta:
        model = PlannerLog
        fields = ["id", "studyPlanId", "triggerSource", "inputSnapshot", "outputSnapshot", "status", "message", "createdAt"]


class GeneratePlanSerializer(serializers.Serializer):
    startDate = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    endDate = serializers.DateField(required=False, input_formats=["%Y-%m-%d"])
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)


class PlannerAIRequestSerializer(serializers.Serializer):
    model = serializers.CharField(required=False, allow_blank=True, max_length=64)
    question = serializers.CharField(max_length=2000)


class PlannerAIDraftRequestSerializer(serializers.Serializer):
    model = serializers.CharField(required=False, allow_blank=True, max_length=64)
    studyScope = serializers.ChoiceField(choices=["unit", "topic", "course"])
    targetName = serializers.CharField(max_length=255)
    durationValue = serializers.IntegerField(min_value=1, max_value=365)
    durationUnit = serializers.ChoiceField(choices=["hours", "days", "weeks"])
    excludedDays = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
        ),
        required=False,
        allow_empty=True,
    )
    instructions = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class PlannerAIDraftSessionSerializer(serializers.Serializer):
    tempId = serializers.CharField(required=False, allow_blank=True, max_length=64)
    title = serializers.CharField(max_length=255)
    sessionDate = serializers.DateField(input_formats=["%Y-%m-%d"], format="%Y-%m-%d")
    startTime = serializers.TimeField(input_formats=["%H:%M"], format="%H:%M")
    endTime = serializers.TimeField(input_formats=["%H:%M"], format="%H:%M")
    duration = serializers.IntegerField(min_value=1, max_value=1440)
    sessionType = serializers.ChoiceField(choices=["reading", "revision", "assignment_work", "exam_prep", "project_work"])
    notes = serializers.CharField(required=False, allow_blank=True)


class PlannerAISaveSerializer(serializers.Serializer):
    model = serializers.CharField(required=False, allow_blank=True, max_length=64)
    title = serializers.CharField(max_length=255)
    studyScope = serializers.ChoiceField(choices=["unit", "topic", "course"])
    targetName = serializers.CharField(max_length=255)
    durationValue = serializers.IntegerField(min_value=1, max_value=365)
    durationUnit = serializers.ChoiceField(choices=["hours", "days", "weeks"])
    excludedDays = serializers.ListField(
        child=serializers.ChoiceField(
            choices=[
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
        ),
        required=False,
        allow_empty=True,
    )
    instructions = serializers.CharField(required=False, allow_blank=True)
    summary = serializers.CharField(required=False, allow_blank=True)
    startDate = serializers.DateField(input_formats=["%Y-%m-%d"], format="%Y-%m-%d")
    endDate = serializers.DateField(input_formats=["%Y-%m-%d"], format="%Y-%m-%d")
    sessions = PlannerAIDraftSessionSerializer(many=True)

    def validate(self, attrs):
        if not attrs["sessions"]:
            raise serializers.ValidationError({"sessions": ["At least one session is required."]})
        return attrs


class SessionStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["planned", "completed", "missed", "skipped", "rescheduled"])


class RescheduleSessionSerializer(serializers.Serializer):
    sessionDate = serializers.DateField(input_formats=["%Y-%m-%d"])
    startTime = serializers.TimeField(input_formats=["%H:%M"])
    endTime = serializers.TimeField(input_formats=["%H:%M"], required=False)
