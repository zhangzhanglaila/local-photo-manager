"""Menu items dispatch on ``action_id``, not on the label they render.

The info panel used to decide what a user picked by comparing
``chosen.text() == "Delete"`` — the English label it had just rendered. Any
translation, an ellipsis change, or a duplicate label silently disabled the
action. ``populate_menu`` now stamps every action with its spec id, and the
panel dispatches on that id.
"""

from __future__ import annotations

import pytest

from iPhoto.gui.menus.core import MenuActionSpec, MenuContext, populate_menu

pytest.importorskip("PySide6.QtWidgets", reason="menu items are Qt objects")

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_populate_menu_stamps_each_action_with_its_id(qapp) -> None:
    menu = QMenu()
    populate_menu(
        menu,
        context=MenuContext(surface="info_panel", selection_kind="empty"),
        action_specs=[
            MenuActionSpec(action_id="delete_face", label="删除"),
            MenuActionSpec(
                action_id="not_this_person",
                label="换个人",
                children=(
                    MenuActionSpec(action_id="not_this_person:choose", label="选择他人"),
                    MenuActionSpec(action_id="not_this_person:new", label="新建人物"),
                ),
            ),
        ],
    )

    first, second = menu.actions()
    assert first.text() == "删除"
    assert first.data() == "delete_face"
    submenu_actions = second.menu().actions()
    assert [action.data() for action in submenu_actions] == [
        "not_this_person:choose",
        "not_this_person:new",
    ]


def test_info_panel_dispatches_by_id_not_by_label(qapp, monkeypatch) -> None:
    from iPhoto.gui.ui.widgets import info_panel as info_panel_module
    from iPhoto.people.records import AssetFaceAnnotation

    calls: list[str] = []
    annotation = AssetFaceAnnotation(
        face_id="face-1",
        person_id="person-1",
        display_name="Alice",
        box_x=0,
        box_y=0,
        box_w=10,
        box_h=10,
        image_width=100,
        image_height=100,
    )
    avatar = info_panel_module._FaceAvatarWidget(annotation, [])
    monkeypatch.setattr(avatar, "_prompt_new_person", lambda: calls.append("new"))
    monkeypatch.setattr(avatar, "_prompt_choose_person", lambda: calls.append("choose"))

    menu = avatar._build_context_menu()
    submenu = menu.actions()[1].menu()
    new_person_action = next(
        action for action in submenu.actions() if action.data() == "not_this_person:new"
    )
    # A localized label must not matter: dispatch reads the id.
    new_person_action.setText("Neue Person")
    avatar._handle_menu_choice(new_person_action)

    choose_action = next(
        action for action in submenu.actions() if action.data() == "not_this_person:choose"
    )
    avatar._handle_menu_choice(choose_action)

    assert calls == ["new", "choose"]
