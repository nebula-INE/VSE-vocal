// PianoRollComponent.h
//
// modules/gui/timeline_widget.py の参考実装が手元に無かったため、
// 同等のUX（ノートの追加/移動/リサイズ/削除、グリッド表示）を
// JUCEネイティブで一から実装したもの（移植ではなく新規実装）。
//
// 【意図的に省いた機能】Undo/Redo、選択範囲ループ、複数選択、
// スナップ設定のUI（グリッドスナップは固定で有効）。
// これらは今後必要に応じて追加する。
//
// データモデルは「拍(beat)」単位で保持し、コミット時にテンポを使って
// 秒単位のScheduledSongNoteへ変換してPluginProcessorへ渡す。

#pragma once

#include <juce_gui_basics/juce_gui_basics.h>
#include "PluginProcessor.h"
#include "VoseLookAndFeel.h"

struct PianoRollNote
{
    double startBeat = 0.0;
    double lengthBeats = 1.0;
    int    noteNum = 60;
    juce::String lyric { "a" };
    juce::String flags;
    juce::String pbs, pbw, pby;
    std::optional<UstVibratoParams> vibrato;

    // GraphEditorComponent が読み書きする。nullopt=未指定（Flags/APVTSにフォールバック）。
    std::optional<double> genderOverride01;
    std::optional<double> tensionOverride01;
    std::optional<double> breathOverride01;
};

class PianoRollComponent : public juce::Component, private juce::Timer
{
public:
    explicit PianoRollComponent (VoseAudioProcessor& p) : processor (p)
    {
        setWantsKeyboardFocus (true);
        loadFromProcessor();
        startTimerHz (30); // 再生カーソルの表示更新用
    }

    void setLookAndFeelRef (VoseLookAndFeel* lf) { vlf = lf; repaint(); }

    // processor側の最新ノート列を取り込む（外部でUSTを読み込んだ直後などに呼ぶ）。
    void loadFromProcessor()
    {
        const double tempo = juce::jmax (1.0, processor.getCurrentTempo());
        auto snapshot = processor.getSongNotesSnapshot();

        notes.clear();
        for (const auto& sn : snapshot)
        {
            PianoRollNote n;
            n.startBeat   = sn.startTimeSec * tempo / 60.0;
            n.lengthBeats = juce::jmax (0.0625, sn.durationSec * tempo / 60.0);
            n.noteNum     = sn.noteNum;
            n.lyric       = sn.lyric;
            n.flags       = sn.flags;
            n.pbs = sn.pbs; n.pbw = sn.pbw; n.pby = sn.pby;
            n.vibrato = sn.vibrato;
            n.genderOverride01 = sn.genderOverride01;
            n.tensionOverride01 = sn.tensionOverride01;
            n.breathOverride01 = sn.breathOverride01;
            notes.push_back (std::move (n));
        }
        repaint();
    }

    // 編集結果をプロセッサへ反映する。
    void commitToProcessor()
    {
        const double tempo = juce::jmax (1.0, processor.getCurrentTempo());
        std::sort (notes.begin(), notes.end(),
                   [] (const auto& a, const auto& b) { return a.startBeat < b.startBeat; });

        std::vector<ScheduledSongNote> out;
        out.reserve (notes.size());
        for (const auto& n : notes)
        {
            ScheduledSongNote sn;
            sn.startTimeSec = n.startBeat * 60.0 / tempo;
            sn.durationSec  = n.lengthBeats * 60.0 / tempo;
            sn.noteNum      = n.noteNum;
            sn.lyric        = n.lyric;
            sn.flags        = n.flags;
            sn.pbs = n.pbs; sn.pbw = n.pbw; sn.pby = n.pby;
            sn.vibrato = n.vibrato;
            sn.genderOverride01 = n.genderOverride01;
            sn.tensionOverride01 = n.tensionOverride01;
            sn.breathOverride01 = n.breathOverride01;
            out.push_back (std::move (sn));
        }
        processor.setEditedNotes (std::move (out));
    }

    // GraphEditorComponent が同じノート列を直接編集できるようにする
    // （単一の情報源をPianoRollComponentが保持する設計）。
    std::vector<PianoRollNote>& getNotesForEditing() { return notes; }
    double getPixelsPerBeat() const { return pixelsPerBeat; }
    double getScrollXBeats() const { return scrollXBeats; }
    int getSelectedIndex() const { return selectedIndex; }

    void paint (juce::Graphics& g) override
    {
        const auto bg      = vlf ? vlf->colourBackground : juce::Colours::black;
        const auto surface = vlf ? vlf->colourSurface : juce::Colours::darkgrey;
        const auto border  = vlf ? vlf->colourBorder : juce::Colours::grey;
        const auto accent  = vlf ? vlf->colourAccent : juce::Colours::cyan;

        g.fillAll (bg);

        // --- 鍵盤レーン（黒鍵行を少し暗く） ---
        for (int n = lowestNote; n <= highestNote; ++n)
        {
            const auto rowY = noteNumToY (n);
            const bool isBlackKey = juce::Array<int> { 1, 3, 6, 8, 10 }.contains (n % 12);
            g.setColour (isBlackKey ? surface.darker (0.15f) : surface.darker (0.05f));
            g.fillRect (0.0f, (float) rowY, (float) getWidth(), (float) rowHeight);
        }

        // --- 拍グリッド ---
        g.setColour (border.withAlpha (0.5f));
        const double startBeatVisible = scrollXBeats;
        const double endBeatVisible   = scrollXBeats + getWidth() / pixelsPerBeat;
        for (int beat = (int) std::floor (startBeatVisible); beat <= (int) std::ceil (endBeatVisible); ++beat)
        {
            const float x = (float) beatToX ((double) beat);
            g.drawVerticalLine ((int) x, 0.0f, (float) getHeight());
        }

        // --- ノート描画 ---
        for (size_t i = 0; i < notes.size(); ++i)
        {
            const auto& n = notes[i];
            const auto r = noteBounds (n);
            const bool selected = (int) i == selectedIndex;

            g.setColour (selected ? accent : accent.withAlpha (0.75f));
            g.fillRoundedRectangle (r, 3.0f);
            g.setColour (border);
            g.drawRoundedRectangle (r, 3.0f, 1.0f);

            // 歌詞をノート内に描画（Phase3要件: 「表示するだけ」でよい）
            g.setColour (juce::Colours::black.withAlpha (0.85f));
            g.setFont (juce::Font (12.0f));
            g.drawFittedText (n.lyric, r.getSmallestIntegerContainer().reduced (2),
                               juce::Justification::centredLeft, 1);
        }

        // --- 再生カーソル ---
        if (processor.isSongPlaying())
        {
            g.setColour (juce::Colours::red);
            const float cursorX = (float) beatToX (playheadBeat);
            g.drawVerticalLine ((int) cursorX, 0.0f, (float) getHeight());
        }
    }

    void mouseDown (const juce::MouseEvent& e) override
    {
        grabKeyboardFocus();
        const int idx = findNoteAt (e.position);

        if (e.mods.isRightButtonDown())
        {
            if (idx >= 0) { notes.erase (notes.begin() + idx); selectedIndex = -1; commitToProcessor(); repaint(); }
            return;
        }

        if (idx >= 0)
        {
            selectedIndex = idx;
            const auto r = noteBounds (notes[(size_t) idx]);
            const bool onRightEdge = e.position.x > r.getRight() - 6.0f;
            dragMode = onRightEdge ? DragMode::Resize : DragMode::Move;
            dragStartPos = e.position;
            dragStartNote = notes[(size_t) idx];
        }
        else
        {
            // 空白クリック: 新規ノート追加（1拍長、グリッドスナップ）
            PianoRollNote n;
            n.startBeat   = snapToGrid (xToBeat (e.position.x));
            n.lengthBeats = 1.0;
            n.noteNum     = yToNoteNum (e.position.y);
            n.lyric       = "a";
            notes.push_back (n);
            selectedIndex = (int) notes.size() - 1;
            dragMode = DragMode::None;
            commitToProcessor();
        }
        repaint();
    }

    void mouseDrag (const juce::MouseEvent& e) override
    {
        if (selectedIndex < 0 || dragMode == DragMode::None)
            return;

        auto& n = notes[(size_t) selectedIndex];
        const double deltaBeats = xToBeat (e.position.x) - xToBeat (dragStartPos.x);

        if (dragMode == DragMode::Move)
        {
            n.startBeat = juce::jmax (0.0, snapToGrid (dragStartNote.startBeat + deltaBeats));
            n.noteNum   = yToNoteNum (e.position.y);
        }
        else if (dragMode == DragMode::Resize)
        {
            n.lengthBeats = juce::jmax (0.0625, snapToGrid (dragStartNote.lengthBeats + deltaBeats));
        }
        repaint();
    }

    void mouseUp (const juce::MouseEvent&) override
    {
        if (dragMode != DragMode::None)
            commitToProcessor();
        dragMode = DragMode::None;
    }

    void mouseDoubleClick (const juce::MouseEvent& e) override
    {
        const int idx = findNoteAt (e.position);
        if (idx >= 0)
        {
            // 歌詞のインライン編集（簡易: テキストエディタをポップアップ）
            auto editor = std::make_unique<juce::TextEditor>();
            auto* rawEditor = editor.get();
            auto r = noteBounds (notes[(size_t) idx]).getSmallestIntegerContainer();
            addAndMakeVisible (rawEditor);
            rawEditor->setBounds (r);
            rawEditor->setText (notes[(size_t) idx].lyric);
            rawEditor->selectAll();
            rawEditor->grabKeyboardFocus();

            const int capturedIdx = idx;
            rawEditor->onReturnKey = [this, capturedIdx, rawEditor]
            {
                if (capturedIdx < (int) notes.size())
                    notes[(size_t) capturedIdx].lyric = rawEditor->getText();
                commitToProcessor();
                removeChildComponent (rawEditor);
                repaint();
            };
            rawEditor->onFocusLost = rawEditor->onReturnKey;

            // editorのunique_ptrはonReturnKey実行後もラムダのキャプチャが
            // 生ポインタを使い終わるまで生存させる必要があるため、保持しておく。
            heldEditors.push_back (std::move (editor));
        }
    }

    void mouseWheelMove (const juce::MouseEvent&, const juce::MouseWheelDetails& wheel) override
    {
        scrollXBeats = juce::jmax (0.0, scrollXBeats - wheel.deltaX * 20.0);
        pixelsPerBeat = juce::jlimit (10.0, 200.0, pixelsPerBeat * (1.0 + wheel.deltaY * 0.3));
        repaint();
    }

private:
    enum class DragMode { None, Move, Resize };

    void timerCallback() override
    {
        if (processor.isSongPlaying())
        {
            // 簡易表示: processorから正確なサンプル位置をまだ公開していないため、
            // 現状は再描画のトリガーのみ（TODO: 正確な再生位置をProcessor側に公開する）。
            repaint();
        }
    }

    double beatToX (double beat) const { return (beat - scrollXBeats) * pixelsPerBeat + keyboardWidth; }
    double xToBeat (double x) const { return (x - keyboardWidth) / pixelsPerBeat + scrollXBeats; }
    double noteNumToY (int noteNum) const { return (highestNote - noteNum) * rowHeight; }
    int    yToNoteNum (double y) const { return juce::jlimit (lowestNote, highestNote, highestNote - (int) (y / rowHeight)); }
    double snapToGrid (double beats) const { return std::round (beats * 4.0) / 4.0; } // 16分音符スナップ

    juce::Rectangle<float> noteBounds (const PianoRollNote& n) const
    {
        const float x = (float) beatToX (n.startBeat);
        const float w = (float) (n.lengthBeats * pixelsPerBeat);
        const float y = (float) noteNumToY (n.noteNum);
        return { x, y, juce::jmax (2.0f, w), (float) rowHeight - 1.0f };
    }

    int findNoteAt (juce::Point<float> pos) const
    {
        for (int i = (int) notes.size() - 1; i >= 0; --i)
            if (noteBounds (notes[(size_t) i]).contains (pos))
                return i;
        return -1;
    }

    VoseAudioProcessor& processor;
    VoseLookAndFeel* vlf = nullptr;

    std::vector<PianoRollNote> notes;
    int selectedIndex = -1;

    DragMode dragMode = DragMode::None;
    juce::Point<float> dragStartPos;
    PianoRollNote dragStartNote;

    double pixelsPerBeat = 40.0;
    double scrollXBeats  = 0.0;
    double playheadBeat  = 0.0;

    static constexpr int lowestNote  = 36;
    static constexpr int highestNote = 84;
    static constexpr int rowHeight   = 14;
    static constexpr int keyboardWidth = 0; // 鍵盤ラベル列は今回省略

    std::vector<std::unique_ptr<juce::TextEditor>> heldEditors;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PianoRollComponent)
};
