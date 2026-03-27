"""Tests for jar.filters — SortSpec, TaskFilter, ProjectFilter, SQL builders."""

import pytest
from jar.filters import (
    ProjectFilter,
    SortSpec,
    TaskFilter,
    build_project_query,
    build_task_query,
)


class TestSortSpec:
    def test_default_direction_is_asc(self):
        s = SortSpec(field="name")
        assert s.direction == "asc"

    def test_valid_desc(self):
        s = SortSpec(field="deadline", direction="desc")
        assert s.direction == "desc"

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            SortSpec(field="name", direction="sideways")

    def test_sql_fragment_asc(self):
        s = SortSpec(field="name", direction="asc")
        frag = s.sql_fragment({"name", "id"})
        assert "name ASC" in frag
        assert "NULLS LAST" in frag

    def test_sql_fragment_desc(self):
        s = SortSpec(field="deadline", direction="desc")
        frag = s.sql_fragment({"deadline", "name"})
        assert "deadline DESC" in frag
        assert "NULLS FIRST" in frag

    def test_invalid_field_raises(self):
        s = SortSpec(field="nonexistent")
        with pytest.raises(ValueError, match="Cannot sort"):
            s.sql_fragment({"name", "id"})


class TestBuildTaskQuery:
    def test_no_filter_returns_select_all(self):
        sql, params = build_task_query()
        assert sql.startswith("SELECT * FROM tasks")
        assert params == []

    def test_status_filter(self):
        sql, params = build_task_query(TaskFilter(status="todo"))
        assert "status = ?" in sql
        assert params == ["todo"]

    def test_project_id_filter(self):
        sql, params = build_task_query(TaskFilter(project_id=3))
        assert "project_id = ?" in sql
        assert 3 in params

    def test_standalone_filter(self):
        sql, params = build_task_query(TaskFilter(project_id=-1))
        assert "project_id IS NULL" in sql
        assert params == []

    def test_tag_filter_single(self):
        sql, params = build_task_query(TaskFilter(tags=["bug"]))
        assert "tags" in sql
        assert "bug" in params

    def test_tag_filter_multiple_tags_add_multiple_clauses(self):
        sql, params = build_task_query(TaskFilter(tags=["bug", "docs"]))
        # Each tag produces 4 params (exact, prefix, suffix, middle)
        assert params.count("bug") + params.count("bug,%") >= 1
        assert params.count("docs") + params.count("docs,%") >= 1

    def test_deadline_before(self):
        sql, params = build_task_query(TaskFilter(deadline_before="2025-06-01"))
        assert "deadline <= ?" in sql
        assert "2025-06-01" in params

    def test_deadline_after(self):
        sql, params = build_task_query(TaskFilter(deadline_after="2025-01-01"))
        assert "deadline >= ?" in sql

    def test_search_filter(self):
        sql, params = build_task_query(TaskFilter(search="login"))
        assert "name LIKE ?" in sql
        assert any("login" in p for p in params)

    def test_combined_filters(self):
        sql, params = build_task_query(
            TaskFilter(status="done", project_id=1, search="fix")
        )
        assert "status = ?" in sql
        assert "project_id = ?" in sql
        assert "name LIKE ?" in sql

    def test_sort_appended(self):
        sql, _ = build_task_query(sort=SortSpec("name", "asc"))
        assert "ORDER BY" in sql
        assert "name ASC" in sql

    def test_deadline_on(self):
        sql, params = build_task_query(TaskFilter(deadline_on="2025-06-15"))
        assert "deadline = ?" in sql
        assert "2025-06-15" in params

    def test_overdue_clause(self):
        sql, _ = build_task_query(TaskFilter(overdue=True))
        assert "deadline IS NOT NULL" in sql
        assert "deadline < date('now')" in sql
        assert "status NOT IN ('done', 'failed')" in sql

    def test_overdue_false_excluded(self):
        sql, _ = build_task_query(TaskFilter(overdue=False))
        assert "date('now')" not in sql

    def test_deadline_on_combined_with_status(self):
        sql, params = build_task_query(TaskFilter(status="todo", deadline_on="2025-06-15"))
        assert "status = ?" in sql
        assert "deadline = ?" in sql

    def test_no_sql_injection_via_search(self):
        # Dangerous chars end up as a literal param, not injected into SQL.
        sql, params = build_task_query(TaskFilter(search="'; DROP TABLE tasks; --"))
        assert "DROP" not in sql
        assert any("DROP" in p for p in params)


class TestBuildProjectQuery:
    def test_no_filter_returns_select_all(self):
        sql, params = build_project_query()
        assert sql.startswith("SELECT * FROM projects")
        assert params == []

    def test_search_filter(self):
        sql, params = build_project_query(ProjectFilter(search="api"))
        assert "name LIKE ?" in sql
        assert any("api" in p for p in params)

    def test_has_tasks_true(self):
        sql, _ = build_project_query(ProjectFilter(has_tasks=True))
        assert "EXISTS" in sql
        assert "tasks" in sql

    def test_has_tasks_false(self):
        sql, _ = build_project_query(ProjectFilter(has_tasks=False))
        assert "NOT EXISTS" in sql

    def test_has_tasks_none_excluded(self):
        sql, _ = build_project_query(ProjectFilter(has_tasks=None))
        assert "EXISTS" not in sql

    def test_start_before(self):
        sql, params = build_project_query(ProjectFilter(start_before="2025-06-01"))
        assert "start_date <= ?" in sql

    def test_deployment_after(self):
        sql, params = build_project_query(ProjectFilter(deployment_after="2025-01-01"))
        assert "deployment_date >= ?" in sql

    def test_sort_appended(self):
        sql, _ = build_project_query(sort=SortSpec("deployment_date", "desc"))
        assert "ORDER BY" in sql
        assert "deployment_date DESC" in sql
