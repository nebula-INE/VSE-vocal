import pytest
from PySide6.QtCore import Qt
from modules.gui.timeline_widget import TimelineWidget

@pytest.mark.unit
def test_add_note(qtbot):
    widget = TimelineWidget()
    qtbot.addWidget(widget)
    
    # 初期ノート数 0
    assert len(widget.notes_list) == 0
    
    # ダブルクリックでノート追加をシミュレート (pos: x=100, y=100)
    # QTest.mouseClick 대신 qtbot.mouseClick 사용
    qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=(100, 100), delay=100)
    qtbot.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=(100, 100))
    
    # ノートが1つ増えているか
    assert len(widget.notes_list) == 1
    note = widget.notes_list[0]
    assert note.note_number == 60  # y=100 기준 계산

@pytest.mark.unit
def test_undo_redo(qtbot):
    widget = TimelineWidget()
    qtbot.addWidget(widget)
    
    # Undo/Redo 스택이 비어있어야 함
    assert len(widget.history.undo_stack) == 0
    
    # 노트 추가
    qtbot.mouseDClick(widget, Qt.MouseButton.LeftButton, pos=(100, 100))
    
    # Undo 실행 (Ctrl+Z)
    widget.undo()
    assert len(widget.notes_list) == 0
    
    # Redo 실행 (Ctrl+Y)
    widget.redo()
    assert len(widget.notes_list) == 1
