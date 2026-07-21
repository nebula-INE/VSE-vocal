import pytest
from modules.data.ust_parser import UstParser, UstConverter

SAMPLE_UST = """
[#VERSION]
UST Version 1.2
[#SETTING]
Tempo=150.000
ProjectName=Test
[#0000]
Length=480
Lyric=か
NoteNum=60
Intensity=120
Flags=g-5B50
VBR=50,180,35,20,20,0,0
PBS=0;0
PBW=50,100
PBY=0,5
"""

def test_parse_ust_basic():
    parser = UstParser()
    project = parser.parse(SAMPLE_UST.splitlines())  # _parse を直接呼ぶかファイル書き込み

    assert project.tempo == 150.0
    assert len(project.notes) == 1
    note = project.notes[0]
    assert note.lyric == "か"
    assert note.note_num == 60
    assert note.intensity == 120.0
    assert note.flags == "g-5B50"

def test_parse_vibrato():
    parser = UstParser()
    project = parser.parse(SAMPLE_UST.splitlines())
    note = project.notes[0]
    
    assert note.vibrato is not None
    assert note.vibrato.length == 50.0
    assert note.vibrato.cycle == 180.0
    assert note.vibrato.depth == 35.0

def test_convert_to_note_events():
    parser = UstParser()
    project = parser.parse(SAMPLE_UST.splitlines())
    dicts = UstConverter.to_note_dicts(project)
    
    assert len(dicts) == 1
    # 秒数変換の検証 (480 ticks @ 150bpm, 480ppqn -> 0.25 * 0.4 = 0.1sec?)
    # duration_sec = (480/480) * (60/150) = 0.4초
    assert dicts[0]["duration"] == pytest.approx(0.4)
    assert dicts[0]["_ust_flags"] == "g-5B50"
    # Vibrato dict が保持されているか
    assert "_ust_vibrato" in dicts[0]
