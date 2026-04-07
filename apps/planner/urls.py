from django.urls import path

from apps.planner.views import (
    PlannerAIAssistantView,
    PlannerAIDraftView,
    PlannerAIModelsView,
    PlannerAIPlanUpdateView,
    PlannerAISaveView,
    PlannerCurrentView,
    PlannerGenerateView,
    PlannerLogListView,
    PlannerPlanDetailView,
    PlannerPlanListView,
    PlannerRegenerateView,
    SessionRescheduleView,
    SessionStatusView,
    SessionsTodayView,
    SessionsWeekView,
)


urlpatterns = [
    path("ai/models/", PlannerAIModelsView.as_view(), name="planner-ai-models"),
    path("ai/assistant/", PlannerAIAssistantView.as_view(), name="planner-ai-assistant"),
    path("ai/draft/", PlannerAIDraftView.as_view(), name="planner-ai-draft"),
    path("ai/save/", PlannerAISaveView.as_view(), name="planner-ai-save"),
    path("ai/plans/<uuid:plan_id>/", PlannerAIPlanUpdateView.as_view(), name="planner-ai-plan-update"),
    path("generate/", PlannerGenerateView.as_view(), name="planner-generate"),
    path("regenerate/", PlannerRegenerateView.as_view(), name="planner-regenerate"),
    path("current/", PlannerCurrentView.as_view(), name="planner-current"),
    path("plans/", PlannerPlanListView.as_view(), name="planner-plan-list"),
    path("plans/<uuid:plan_id>/", PlannerPlanDetailView.as_view(), name="planner-plan-detail"),
    path("sessions/<uuid:session_id>/status/", SessionStatusView.as_view(), name="planner-session-status"),
    path("sessions/<uuid:session_id>/reschedule/", SessionRescheduleView.as_view(), name="planner-session-reschedule"),
    path("sessions/today/", SessionsTodayView.as_view(), name="planner-sessions-today"),
    path("sessions/week/", SessionsWeekView.as_view(), name="planner-sessions-week"),
    path("logs/", PlannerLogListView.as_view(), name="planner-logs"),
]
