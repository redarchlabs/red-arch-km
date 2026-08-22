"""The two disclosure containers — ``tab_group`` and ``accordion``.

These are pure-schema tests: what an author may write, what the defaults mean,
and that an old config keeps its old behaviour. The open/closed *behaviour* is
client-side and covered by ``ui/src/components/forms/LayoutContainers.test.tsx``.
"""

from __future__ import annotations

import pytest
from api.schemas.form import FormConfig
from api.schemas.form_elements import (
    MAX_TREE_DEPTH,
    AccordionElement,
    TabGroupElement,
    iter_elements,
    tree_depth,
)
from pydantic import ValidationError


class TestTabGroup:
    def test_defaults_to_the_first_tab(self) -> None:
        el = TabGroupElement(tabs=[{"label": "A"}, {"label": "B"}])
        assert el.default_tab == 0

    def test_an_author_may_open_on_a_later_tab(self) -> None:
        el = TabGroupElement(tabs=[{"label": "A"}, {"label": "B"}], default_tab=1)
        assert el.default_tab == 1

    def test_a_negative_default_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TabGroupElement(tabs=[{"label": "A"}], default_tab=-1)

    def test_an_out_of_range_default_is_stored_not_rejected(self) -> None:
        """Tabs get deleted after the default is chosen. Rejecting the stored
        value would make the *unrelated* edit that removed a tab fail to save;
        the renderer clamps instead."""
        el = TabGroupElement(tabs=[{"label": "A"}], default_tab=5)
        assert el.default_tab == 5

    def test_an_unknown_key_is_still_a_422(self) -> None:
        with pytest.raises(ValidationError):
            TabGroupElement(tabs=[], defaultTab=1)


class TestAccordion:
    def test_defaults_match_the_old_single_open_behaviour(self) -> None:
        """Before these options existed the renderer hard-coded "first pane open,
        one at a time". Configs written then carry neither key, so the defaults
        have to reproduce exactly that."""
        el = AccordionElement(panes=[{"label": "A"}, {"label": "B"}])
        assert el.multi is False
        assert el.default_open == [0]

    def test_a_stack_can_start_fully_collapsed(self) -> None:
        el = AccordionElement(panes=[{"label": "A"}], default_open=[])
        assert el.default_open == []

    def test_several_panes_may_start_open(self) -> None:
        el = AccordionElement(panes=[{"label": "A"}, {"label": "B"}], multi=True, default_open=[0, 1])
        assert el.multi is True
        assert el.default_open == [0, 1]

    def test_a_negative_index_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AccordionElement(panes=[{"label": "A"}], default_open=[-1])

    def test_an_out_of_range_index_is_stored_not_rejected(self) -> None:
        el = AccordionElement(panes=[{"label": "A"}], default_open=[0, 9])
        assert el.default_open == [0, 9]


class TestNesting:
    """Both containers hold the full element union, so a console can be tabs at
    the top and accordions inside — the shape the walkers must cope with."""

    CONFIG = {
        "version": 2,
        "elements": [
            {
                "type": "tab_group",
                "id": "t1",
                "default_tab": 1,
                "tabs": [
                    {"label": "Motion", "elements": [{"type": "label", "text": "pose"}]},
                    {
                        "label": "Voice",
                        "elements": [
                            {
                                "type": "accordion",
                                "id": "a1",
                                "multi": True,
                                "default_open": [0],
                                "panes": [
                                    {"label": "Speak", "elements": [{"type": "button", "label": "Say"}]},
                                    {"label": "Ask", "elements": [{"type": "label", "text": "kb"}]},
                                ],
                            }
                        ],
                    },
                ],
            }
        ],
    }

    def test_a_tab_group_round_trips_through_the_form_config(self) -> None:
        cfg = FormConfig.model_validate(self.CONFIG)
        tabs = cfg.elements[0]
        assert isinstance(tabs, TabGroupElement)
        assert tabs.default_tab == 1
        acc = tabs.tabs[1].elements[0]
        assert isinstance(acc, AccordionElement)
        assert acc.multi is True

    def test_iter_elements_reaches_inside_a_closed_pane(self) -> None:
        """Validation walks the whole tree, not just what happens to be on
        screen — a button buried in tab 2, pane 2 is still validated and still
        counts against any traversal that looks for it."""
        cfg = FormConfig.model_validate(self.CONFIG)
        types = [getattr(el, "type", None) for el, _ in iter_elements(cfg.elements)]
        assert types == ["tab_group", "label", "accordion", "button", "label"]

    def test_depth_counts_both_containers(self) -> None:
        """Tabs sit at 0, their children at 1, the accordion's panes at 2 — well
        inside ``MAX_TREE_DEPTH``, so a console can nest a level or two further
        before the layout validator objects."""
        cfg = FormConfig.model_validate(self.CONFIG)
        assert tree_depth(cfg.elements) == 2
        assert tree_depth(cfg.elements) < MAX_TREE_DEPTH
