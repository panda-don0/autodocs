import pytest

import docs_sync as ds
import parent_page as pp

SUMMARY = "Builds and sends the monthly customer reports, used by operations staff."
OTHER = pp.build_entry("alpha", "Alpha", "Alpha does something for somebody else.")


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeConfluence:
    """The Confluence REST API at the boundary: a parent page with a version."""

    def __init__(self, body, put_statuses=None, changes_between=None):
        self.body = body
        self.version = 7
        self.put_statuses = list(put_statuses or [])
        self.changes_between = list(changes_between or [])
        self.puts = []

    def get(self, url, headers=None, timeout=None):
        if "expand=ancestors" in url:
            return FakeResponse(200, {"ancestors": [{"id": "100"}, {"id": "200"}]})
        return FakeResponse(
            200,
            {
                "title": "Portal",
                "body": {"storage": {"value": self.body}},
                "version": {"number": self.version},
            },
        )

    def put(self, url, headers=None, json=None, timeout=None):
        self.puts.append(json)
        status = self.put_statuses.pop(0) if self.put_statuses else 200
        if status == 409 and self.changes_between:
            self.body = self.changes_between.pop(0)
            self.version += 1
        if status == 200:
            self.body = json["body"]["storage"]["value"]
            self.version += 1
        return FakeResponse(status)


@pytest.fixture
def confluence(monkeypatch):
    def make(body, **kwargs):
        fake = FakeConfluence(body, **kwargs)
        monkeypatch.setattr(ds.requests, "get", fake.get)
        monkeypatch.setattr(ds.requests, "put", fake.put)
        return fake

    return make


@pytest.fixture
def model(monkeypatch):
    calls = []

    def make(answer):
        def fake_call(model_name, prompt):
            calls.append(prompt)
            return answer

        monkeypatch.setattr(ds, "call_claude", fake_call)
        return calls

    return make


def run(changed=False):
    ds.sync_parent_entry(
        base_url="https://example.atlassian.net",
        headers={},
        model="m",
        service="reports",
        page_id="300",
        configured_parent_id="",
        child_title="Reports",
        child_body="<p>Reports page body.</p>",
        child_changed=changed,
    )


def test_first_write_adds_section_to_the_direct_parent(confluence, model):
    """A parent with no section gets one; the direct parent is resolved from ancestors."""
    page = confluence("<p>Hand-written overview.</p>")
    calls = model(SUMMARY)
    run()
    assert len(calls) == 1 and "Existing entry: <none>" in calls[0]
    assert len(page.puts) == 1
    assert page.puts[0]["id"] == "200"
    assert page.puts[0]["version"] == {"number": 8}
    assert page.body.startswith("<p>Hand-written overview.</p>")
    assert pp.existing_entry_text(page.body, "reports") == SUMMARY


def test_current_entry_and_unchanged_child_does_nothing(confluence, model):
    """No model call and no save when the entry exists and the service page did not change."""
    page = confluence(pp.build_section([pp.build_entry("reports", "Reports", SUMMARY)]))
    calls = model(SUMMARY)
    run(changed=False)
    assert calls == [] and page.puts == []


def test_clash_rereads_and_keeps_the_other_services_change(confluence, model):
    """A save rejected because another run wrote first is redone on the newer page."""
    concurrent = pp.build_section([OTHER])
    page = confluence(
        "<p>Overview.</p>", put_statuses=[409, 200], changes_between=[concurrent]
    )
    model(SUMMARY)
    run(changed=True)
    assert len(page.puts) == 2
    assert pp.existing_entry_text(page.body, "alpha") is not None
    assert pp.existing_entry_text(page.body, "reports") == SUMMARY


def test_two_clashes_stop_the_run(confluence, model):
    """A page that keeps changing fails loudly rather than retrying forever."""
    confluence(
        "<p>Overview.</p>",
        put_statuses=[409, 409],
        changes_between=["<p>A</p>", "<p>B</p>"],
    )
    model(SUMMARY)
    with pytest.raises(SystemExit):
        run(changed=True)


def test_no_update_with_no_entry_fails(confluence, model):
    """The model may not decline when the service has no entry yet."""
    page = confluence("<p>Overview.</p>")
    model(ds.NO_UPDATE_MARKER)
    with pytest.raises(SystemExit):
        run(changed=True)
    assert page.puts == []


def test_no_update_with_existing_entry_leaves_page(confluence, model):
    """An accurate existing entry is left alone when the model says so."""
    page = confluence(pp.build_section([pp.build_entry("reports", "Reports", SUMMARY)]))
    model(ds.NO_UPDATE_MARKER)
    run(changed=True)
    assert page.puts == []


def test_marked_up_answer_is_refused(confluence, model):
    """A summary containing markup never reaches the page."""
    page = confluence("<p>Overview.</p>")
    model("<p>Builds and sends the monthly customer reports for staff.</p>")
    with pytest.raises(SystemExit):
        run(changed=True)
    assert page.puts == []
