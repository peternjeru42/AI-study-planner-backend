from datetime import timedelta

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView

from apps.planner.models import PlannerLog, StudyPlan, StudySession
from apps.planner.serializers import (
    PlannerAIDraftRequestSerializer,
    PlannerAISaveSerializer,
    GeneratePlanSerializer,
    PlannerAIRequestSerializer,
    PlannerLogSerializer,
    RescheduleSessionSerializer,
    SessionStatusUpdateSerializer,
    StudyPlanSerializer,
    StudySessionSerializer,
)
from apps.planner.ai_service import PlannerAIService
from apps.planner.services import PlannerService
from apps.progress.services import ProgressService
from common.permissions import IsAdmin, IsStudent
from common.utils import api_success


class PlannerGenerateView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = GeneratePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan, sessions = PlannerService.generate_plan(user=request.user, **serializer.validated_data)
        return api_success(
            {"plan": StudyPlanSerializer(plan).data, "sessions": StudySessionSerializer(sessions, many=True).data},
            "Study plan generated successfully.",
            201,
        )


class PlannerRegenerateView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = GeneratePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan, sessions = PlannerService.generate_plan(user=request.user, regenerate=True, trigger="preferences_changed", **serializer.validated_data)
        return api_success(
            {"plan": StudyPlanSerializer(plan).data, "sessions": StudySessionSerializer(sessions, many=True).data},
            "Study plan regenerated successfully.",
            201,
        )


class PlannerCurrentView(APIView):
    permission_classes = [IsStudent]

    def get(self, request):
        plan = PlannerService.current_plan(request.user)
        data = StudyPlanSerializer(plan).data if plan else None
        return api_success(data, "Current plan fetched successfully.")


class PlannerPlanListView(APIView):
    permission_classes = [IsStudent]

    def get(self, request):
        queryset = StudyPlan.objects.filter(user=request.user).prefetch_related("sessions")
        return api_success(StudyPlanSerializer(queryset, many=True).data, "Study plans fetched successfully.")


class PlannerPlanDetailView(APIView):
    permission_classes = [IsStudent]

    def get(self, request, plan_id):
        plan = get_object_or_404(StudyPlan.objects.prefetch_related("sessions"), id=plan_id, user=request.user)
        return api_success(StudyPlanSerializer(plan).data, "Study plan fetched successfully.")


class SessionStatusView(APIView):
    permission_classes = [IsStudent]

    def patch(self, request, session_id):
        session = get_object_or_404(StudySession, id=session_id, user=request.user)
        serializer = SessionStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data["status"]
        if status_value == "completed":
            ProgressService.mark_session_complete(session)
        elif status_value == "skipped":
            ProgressService.mark_session_skip(session)
        else:
            session.status = status_value
            session.save(update_fields=["status", "updated_at"])
        return api_success(StudySessionSerializer(session).data, "Study session status updated successfully.")


class SessionRescheduleView(APIView):
    permission_classes = [IsStudent]

    def patch(self, request, session_id):
        session = get_object_or_404(StudySession, id=session_id, user=request.user)
        serializer = RescheduleSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = PlannerService.reschedule_session(session=session, **serializer.validated_data)
        return api_success(StudySessionSerializer(session).data, "Study session rescheduled successfully.")


class SessionsTodayView(APIView):
    permission_classes = [IsStudent]

    def get(self, request):
        today = timezone.localdate()
        queryset = StudySession.objects.filter(user=request.user, session_date=today).order_by("start_time")
        return api_success(StudySessionSerializer(queryset, many=True).data, "Today's sessions fetched successfully.")


class SessionsWeekView(APIView):
    permission_classes = [IsStudent]

    def get(self, request):
        start = timezone.localdate()
        end = start + timedelta(days=6)
        queryset = StudySession.objects.filter(user=request.user, session_date__range=(start, end)).order_by("session_date", "start_time")
        return api_success(StudySessionSerializer(queryset, many=True).data, "Weekly sessions fetched successfully.")


class PlannerLogListView(APIView):
    def get_permissions(self):
        if self.request.user.role == "admin":
            return [IsAdmin()]
        return [IsStudent()]

    def get(self, request):
        queryset = PlannerLog.objects.all() if request.user.role == "admin" else PlannerLog.objects.filter(user=request.user)
        paginator = Paginator(queryset, int(request.query_params.get("pageSize", 20)))
        page = paginator.get_page(request.query_params.get("page", 1))
        data = {
            "results": PlannerLogSerializer(page.object_list, many=True).data,
            "count": paginator.count,
            "numPages": paginator.num_pages,
            "page": page.number,
        }
        return api_success(data, "Planner logs fetched successfully.")


class PlannerAIModelsView(APIView):
    permission_classes = [IsStudent]

    def get(self, request):
        return api_success(PlannerAIService.supported_models(), "AI models fetched successfully.")


class PlannerAIAssistantView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = PlannerAIRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = PlannerAIService.study_assistant(user=request.user, **serializer.validated_data)
        return api_success(data, "AI study guidance generated successfully.")


class PlannerAIDraftView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = PlannerAIDraftRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = PlannerAIService.generate_custom_plan_draft(
            user=request.user,
            study_scope=serializer.validated_data["studyScope"],
            target_name=serializer.validated_data["targetName"],
            duration_value=serializer.validated_data["durationValue"],
            duration_unit=serializer.validated_data["durationUnit"],
            excluded_days=serializer.validated_data.get("excludedDays", []),
            instructions=serializer.validated_data.get("instructions", ""),
            model=serializer.validated_data.get("model"),
        )
        return api_success(data, "AI draft plan generated successfully.")


class PlannerAISaveView(APIView):
    permission_classes = [IsStudent]

    def post(self, request):
        serializer = PlannerAISaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan, sessions = PlannerService.save_custom_plan(user=request.user, draft=serializer.validated_data)
        return api_success(
            {"plan": StudyPlanSerializer(plan).data, "sessions": StudySessionSerializer(sessions, many=True).data},
            "AI study plan saved successfully.",
            201,
        )


class PlannerAIPlanUpdateView(APIView):
    permission_classes = [IsStudent]

    def patch(self, request, plan_id):
        serializer = PlannerAISaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan, sessions = PlannerService.save_custom_plan(user=request.user, draft=serializer.validated_data, plan_id=plan_id)
        return api_success(
            {"plan": StudyPlanSerializer(plan).data, "sessions": StudySessionSerializer(sessions, many=True).data},
            "AI study plan updated successfully.",
        )

# Create your views here.
