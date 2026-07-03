"""Tests for jar.models — dataclasses, enums, helpers."""

import pytest
from loom.models import Project, Status, TagEnum, Task, SUGGESTED_TAGS


class TestStatus:
    def test_values(self):
        assert Status.TRIAGE.value == "triage"
        assert Status.IN_PROGRESS.value == "in_progress"
        assert Status.DONE.value == "done"

    def test_from_string(self):
        assert Status("triage") is Status.TRIAGE
        assert Status("in_progress") is Status.IN_PROGRESS
        assert Status("done") is Status.DONE

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Status("flying")


class TestTagEnum:
    def test_suggested_tags_list(self):
        assert "bug" in SUGGESTED_TAGS
        assert "feature" in SUGGESTED_TAGS
        assert "chore" in SUGGESTED_TAGS
        assert "docs" in SUGGESTED_TAGS
        assert "research" in SUGGESTED_TAGS
        assert "design" in SUGGESTED_TAGS

    def test_tag_enum_values(self):
        assert TagEnum.BUG.value == "bug"
        assert TagEnum.FEATURE.value == "feature"


class TestTask:
    def test_defaults(self):
        t = Task(name="Do something")
        assert t.status == Status.TRIAGE
        assert t.tags == []
        assert t.id is None
        assert t.project_id is None
        assert t.deadline is None

    def test_tags_str_empty(self):
        t = Task(name="x")
        assert t.tags_str() == ""

    def test_tags_str_multiple(self):
        t = Task(name="x", tags=["bug", "feature"])
        assert t.tags_str() == "bug,feature"

    def test_tags_from_str_empty(self):
        assert Task.tags_from_str(None) == []
        assert Task.tags_from_str("") == []

    def test_tags_from_str_single(self):
        assert Task.tags_from_str("bug") == ["bug"]

    def test_tags_from_str_multiple(self):
        assert Task.tags_from_str("bug,feature,docs") == ["bug", "feature", "docs"]

    def test_tags_from_str_strips_whitespace(self):
        assert Task.tags_from_str(" bug , feature ") == ["bug", "feature"]

    def test_to_dict_contains_all_keys(self):
        t = Task(name="x", tags=["bug"], status=Status.IN_PROGRESS)
        d = t.to_dict()
        assert set(d.keys()) == {
            "id", "name", "description", "tags", "deadline", "project_id",
            "goal_id", "priority", "wait_until", "depends", "blocked_reason",
            "blocked_note", "urgency_score", "context_tag", "estimated_sessions",
            "actual_sessions", "handoff_note", "status", "created_at", "updated_at",
        }

    def test_to_dict_status_is_string(self):
        t = Task(name="x", status=Status.DONE)
        assert t.to_dict()["status"] == "done"

    def test_to_dict_tags_is_list(self):
        t = Task(name="x", tags=["a", "b"])
        assert t.to_dict()["tags"] == ["a", "b"]


class TestProject:
    def test_defaults(self):
        p = Project(name="My Project")
        assert p.id is None
        assert p.description is None
        assert p.start_date is None
        assert p.deployment_date is None

    def test_to_dict_contains_all_keys(self):
        p = Project(name="x")
        d = p.to_dict()
        assert set(d.keys()) == {"id", "name", "description", "start_date",
                                  "deployment_date", "goal_id", "status",
                                  "created_at", "updated_at"}
