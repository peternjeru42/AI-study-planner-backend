import pytest
from rest_framework.test import APIClient

from apps.accounts.models import StudentProfile, User
from apps.assessments.models import Assessment
from apps.notifications.models import Notification
from apps.planner.ai_service import PlannerAIService
from apps.planner.models import StudyPlan
from apps.subjects.models import Subject


def auth_client(email, password):
    client = APIClient()
    login = client.post("/api/auth/login/", {"email": email, "password": password}, format="json")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['data']['access']}")
    return client


@pytest.mark.django_db
def test_generate_plan_creates_sessions():
    user = User.objects.create_user(email="planner@example.com", password="demo123", full_name="Planner User")
    StudentProfile.objects.create(user=user)
    subject = Subject.objects.create(user=user, name="Algorithms", code="CS201")
    Assessment.objects.create(
        user=user,
        subject=subject,
        title="Exam Prep",
        assessment_type="exam",
        due_date="2026-04-20",
        weight_percentage=30,
        estimated_hours=4,
        status="pending",
    )

    client = auth_client("planner@example.com", "demo123")
    response = client.post("/api/planner/generate/", {}, format="json")

    assert response.status_code == 201
    assert response.data["data"]["sessions"]


@pytest.mark.django_db
def test_ai_models_endpoint_returns_supported_models():
    user = User.objects.create_user(email="ai-models@example.com", password="demo123", full_name="AI Models User")
    StudentProfile.objects.create(user=user)

    client = auth_client("ai-models@example.com", "demo123")
    response = client.get("/api/planner/ai/models/")

    assert response.status_code == 200
    assert response.data["data"]
    assert any(item["recommended"] for item in response.data["data"])


@pytest.mark.django_db
def test_ai_assistant_endpoint_returns_guidance(monkeypatch, settings):
    user = User.objects.create_user(email="ai-assistant@example.com", password="demo123", full_name="AI Assistant User")
    StudentProfile.objects.create(user=user, course_name="Computer Science")
    subject = Subject.objects.create(user=user, name="Algorithms", code="CS201")
    Assessment.objects.create(
        user=user,
        subject=subject,
        title="Final Exam",
        assessment_type="exam",
        due_date="2026-04-20",
        weight_percentage=40,
        estimated_hours=5,
        status="pending",
    )
    settings.OPENAI_API_KEY = "test-key"
    settings.OPEN_AI_API_KEY = None

    def fake_request_completion(*, api_key, model, messages):
        assert api_key == "test-key"
        assert model == "gpt-5-mini"
        assert messages
        return {"choices": [{"message": {"content": "Prioritize Algorithms first and protect two focused sessions this week."}}]}

    monkeypatch.setattr(PlannerAIService, "_request_completion", fake_request_completion)

    client = auth_client("ai-assistant@example.com", "demo123")
    response = client.post(
        "/api/planner/ai/assistant/",
        {"model": "gpt-5-mini", "question": "How should I prioritize this week?"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["data"]["model"] == "gpt-5-mini"
    assert "Prioritize Algorithms" in response.data["data"]["answer"]


@pytest.mark.django_db
def test_ai_draft_endpoint_returns_normalized_sessions(monkeypatch, settings):
    user = User.objects.create_user(email="draft@example.com", password="demo123", full_name="Draft User")
    StudentProfile.objects.create(
        user=user,
        preferred_study_start_time="08:00",
        preferred_study_end_time="20:00",
        max_sessions_per_day=3,
    )
    settings.OPENAI_API_KEY = None
    settings.OPEN_AI_API_KEY = "fallback-key"

    def fake_request_completion(*, api_key, model, messages):
        assert api_key == "fallback-key"
        assert model == "gpt-5-mini"
        assert messages
        return {
            "choices": [
                {
                    "message": {
                        "content": """
                        {
                          "title": "Discrete Maths Intensive",
                          "summary": "A balanced weekly cadence that avoids Tuesday.",
                          "sessions": [
                            {
                              "title": "Sets and Logic",
                              "sessionDate": "2026-04-08",
                              "startTime": "09:00",
                              "endTime": "11:00",
                              "duration": 120,
                              "sessionType": "revision",
                              "notes": "Work through proofs and examples."
                            },
                            {
                              "title": "Recurrence Relations",
                              "sessionDate": "2026-04-09",
                              "startTime": "10:00",
                              "endTime": "12:00",
                              "duration": 120,
                              "sessionType": "revision",
                              "notes": "Focus on problem drills."
                            }
                          ]
                        }
                        """
                    }
                }
            ]
        }

    monkeypatch.setattr(PlannerAIService, "_request_completion", fake_request_completion)

    client = auth_client("draft@example.com", "demo123")
    response = client.post(
        "/api/planner/ai/draft/",
        {
            "model": "gpt-5-mini",
            "studyScope": "unit",
            "targetName": "Discrete Maths",
            "durationValue": 10,
            "durationUnit": "hours",
            "excludedDays": ["Tuesday"],
            "instructions": "Create a study plan.",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["data"]["draft"]["title"] == "Discrete Maths Intensive"
    assert len(response.data["data"]["draft"]["sessions"]) == 2
    assert response.data["data"]["draft"]["sessions"][0]["duration"] == 120


@pytest.mark.django_db
def test_ai_save_endpoint_creates_plan_sessions_and_reminders():
    user = User.objects.create_user(email="save@example.com", password="demo123", full_name="Save User")
    StudentProfile.objects.create(user=user, enable_email_notifications_simulated=True)

    client = auth_client("save@example.com", "demo123")
    response = client.post(
        "/api/planner/ai/save/",
        {
            "model": "gpt-5-mini",
            "title": "Discrete Maths Study Plan",
            "studyScope": "unit",
            "targetName": "Discrete Maths",
            "durationValue": 10,
            "durationUnit": "hours",
            "excludedDays": ["Tuesday"],
            "instructions": "Create a study plan.",
            "summary": "A structured plan for the next week.",
            "startDate": "2026-04-08",
            "endDate": "2026-04-10",
            "sessions": [
                {
                    "tempId": "draft-1",
                    "title": "Logic Foundations",
                    "sessionDate": "2026-04-08",
                    "startTime": "09:00",
                    "endTime": "11:00",
                    "duration": 120,
                    "sessionType": "revision",
                    "notes": "Start with truth tables.",
                },
                {
                    "tempId": "draft-2",
                    "title": "Graphs and Trees",
                    "sessionDate": "2026-04-10",
                    "startTime": "14:00",
                    "endTime": "16:00",
                    "duration": 120,
                    "sessionType": "revision",
                    "notes": "Practice traversal questions.",
                },
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    plan_id = response.data["data"]["plan"]["id"]
    plan = StudyPlan.objects.get(id=plan_id)
    assert plan.generation_trigger == "ai_custom"
    assert plan.sessions.count() == 2
    assert Subject.objects.filter(user=user, name="Discrete Maths").exists()
    assert Notification.objects.filter(user=user, study_session__study_plan=plan, notification_type="study_session_reminder").count() == 4


@pytest.mark.django_db
def test_ai_draft_endpoint_requires_api_key(settings):
    user = User.objects.create_user(email="missing-key@example.com", password="demo123", full_name="Missing Key User")
    StudentProfile.objects.create(user=user)
    settings.OPENAI_API_KEY = None
    settings.OPEN_AI_API_KEY = None

    client = auth_client("missing-key@example.com", "demo123")
    response = client.post(
        "/api/planner/ai/draft/",
        {
            "studyScope": "course",
            "targetName": "Algorithms",
            "durationValue": 2,
            "durationUnit": "weeks",
            "excludedDays": [],
            "instructions": "",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "OpenAI API key" in str(response.data["errors"])
