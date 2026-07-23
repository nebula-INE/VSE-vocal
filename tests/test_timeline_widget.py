import pytest
from PySide6.QtCore import Qt
from modules.gui.timeline_widget import TimelineWidget

@pytest.mark.unit
def test_add_note(qtbot):
    widget = TimelineWidget()
    qtbot.addWidget(widget)

    # 初期ノート数は 0
    assert len(widget.notes_list) == 0

    # ダブルクリックでノート追加をシミュレート (pos: x=100, y=100)
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=(100, 100), delay=100)
    qtbot.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=(100, 100))

    # ノートが 1 つ増えているか
    assert len(widget.notes_list) == 1
    note = widget.notes_list[0]
    # y=100 の位置に対応する MIDI ノート番号が 60 であることを確認
    assert note.note_number == 60
