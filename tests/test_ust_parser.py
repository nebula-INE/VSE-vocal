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

@pytest.mark.unit
def test_parse_ust_basic(tmp_path):
    ust_file = tmp_path / "test.ust"
    ust_file.write_text(SAMPLE_UST, encoding="cp932")

    parser = UstParser()
    project = parser.load(str(ust_file))

    assert project.tempo == 150.0
    assert len(project.notes) == 1
    note = project.notes[0]
    assert note.lyric == "か"
    assert note.note_num == 60
    assert note.intensity == 120.0
    assert note.flags == "g-5B50"

@pytest.mark.unit
def test_parse_vibrato(tmp_path):
    ust_file = tmp_path / "test.ust"
    ust_file.write_text(SAMPLE_UST, encoding="cp932")

    parser = UstParser()
    project = parser.load(str(ust_file))
    note = project.notes[0]

    assert note.vibrato is not None
    assert note.vibrato.length == 50.0
    assert note.vibrato.cycle == 180.0
    assert note.vibrato.depth == 35.0

@pytest.mark.unit
def test_convert_to_note_events(tmp_path):
    ust_file = tmp_path / "test.ust"
    ust_file.write_text(SAMPLE_UST, encoding="cp932")

    parser = UstParser()
    project = parser.load(str(ust_file))
    dicts = UstConverter.to_note_dicts(project)

    assert len(dicts) == 1
    assert dicts[0]["duration"] == pytest.approx(0.4)
    assert dicts[0]["_ust_flags"] == "g-5B50"
    assert "_ust_vibrato" in dicts[0]
