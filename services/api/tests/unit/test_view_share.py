"""Unit tests for anonymous view access — the parts that decide what a leaked
link can do. These are pure over (config, view row); the wiring is exercised by
the router integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.schemas.form import FormConfig
from api.services.form_layout import collect_workflow_ids
from api.services.form_service import FormNotFoundError
from api.services.view_share import ViewShareError, share_is_live, unsupported_elements


class _View:
    """Just the columns the guard reads."""

    def __init__(self, **kw):
        self.public_token_hash = kw.get("token", "hash")
        self.is_active = kw.get("is_active", True)
        self.public_expires_at = kw.get("expires_at")


WF_BUTTON = uuid.uuid4()
WF_PAD = uuid.uuid4()
WF_ROW = uuid.uuid4()
WF_CHAT = uuid.uuid4()
WF_ELSEWHERE = uuid.uuid4()


def _elements(payload):
    return FormConfig.model_validate({"version": 2, "elements": payload}).elements


def test_allow_list_covers_every_place_a_view_can_start_a_workflow():
    """The allow-list IS the anonymous permission, so a workflow reachable from
    the page in any way must appear — a miss here silently breaks the page, and
    an over-collection would widen what a leaked link can do."""
    els = _elements(
        [
            {
                "type": "panel",
                "title": "Deep",
                "elements": [
                    {
                        "type": "columns",
                        "columns": [
                            {
                                "span": 1,
                                "elements": [
                                    {
                                        "type": "button",
                                        "label": "Go",
                                        "action": {"kind": "run_workflow", "workflow_id": str(WF_BUTTON), "inputs": {}},
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "type": "record_list",
                        "entity": "thing",
                        "fields": [],
                        "row_workflow_id": str(WF_ROW),
                    },
                ],
            },
            {
                "type": "puzzle_pad",
                "kind": "choices",
                "spec": {"options": [{"value": "A"}, {"value": "B"}]},
                "on_complete": {"kind": "run_workflow", "workflow_id": str(WF_PAD), "inputs": {}},
            },
            {"type": "chat", "answer_workflow_id": str(WF_CHAT)},
        ]
    )
    assert collect_workflow_ids(els) == {WF_BUTTON, WF_ROW, WF_PAD, WF_CHAT}


def test_allow_list_excludes_workflows_the_page_does_not_reference():
    """The point of the whole mechanism: a token grants what the PAGE offers."""
    action = {"kind": "run_workflow", "workflow_id": str(WF_BUTTON), "inputs": {}}
    els = _elements([{"type": "button", "label": "Go", "action": action}])
    assert WF_ELSEWHERE not in collect_workflow_ids(els)


def test_allow_list_ignores_non_workflow_buttons():
    """A link or submit button confers nothing."""
    els = _elements(
        [
            {"type": "button", "label": "Away", "action": {"kind": "link", "href": "/x"}},
            {"type": "button", "label": "Save", "action": {"kind": "submit"}},
        ]
    )
    assert collect_workflow_ids(els) == set()


def test_allow_list_is_empty_for_a_page_with_no_actions():
    """A read-only board grants no ability to change anything."""
    assert collect_workflow_ids(_elements([{"type": "label", "text": "Status", "variant": "heading"}])) == set()


def test_a_disabled_workflow_is_forbidden_not_missing():
    """The two rejections a share link can give must not look alike.

    Folding "this workflow is switched off" into ``FormNotFoundError`` made a button
    that plainly exists answer 404, which reads as a broken app — and cost an
    afternoon of hunting for a missing workflow that was sitting right there with
    ``enabled = false``. The status codes are the only signal the operator gets, so
    they have to differ.
    """
    from api.routers.views import _ERROR_STATUS

    assert _ERROR_STATUS[ViewShareError] == 403
    assert _ERROR_STATUS[FormNotFoundError] == 404


def test_sharing_is_off_without_a_token():
    assert share_is_live(_View(token=None)) is False


def test_sharing_follows_the_view_being_active():
    """Deactivating a view must also close its public door."""
    assert share_is_live(_View(is_active=False)) is False


def test_sharing_expires():
    now = datetime.now(UTC)
    assert share_is_live(_View(expires_at=now + timedelta(hours=1)), now) is True
    assert share_is_live(_View(expires_at=now - timedelta(seconds=1)), now) is False
    assert share_is_live(_View(expires_at=None), now) is True


@pytest.mark.parametrize(
    "element,expected",
    [
        ({"type": "record_list", "entity": "thing", "fields": []}, ["record_list"]),
        ({"type": "chat"}, ["chat"]),
        ({"type": "label", "text": "hi", "variant": "paragraph"}, []),
    ],
)
def test_unsupported_elements_are_reported_at_enable_time(element, expected):
    """These fetch from authenticated endpoints themselves, so they render empty
    for a visitor. Better to say so while the admin is deciding than to let them
    find a blank panel on a tablet in front of an audience."""
    assert unsupported_elements({"elements": [element]}) == expected


def test_unsupported_elements_finds_them_when_nested():
    cfg = {"elements": [{"type": "panel", "elements": [{"type": "chat"}]}]}
    assert unsupported_elements(cfg) == ["chat"]
