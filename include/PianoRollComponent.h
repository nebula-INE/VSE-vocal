// PianoRollComponent.h
//
// フェーズ3「ユーザーインターフェースの本格化」— ピアノロール／タイムライン。
// modules/gui/timeline_widget.py（PySide6版、簡易パネルの参考実装）が持っていた
// 機能をJUCEネイティブで再実装したもの:
//   - グリッド表示（テンポに応じた拍/16分音符ライン）
//   - ノートの追加（空白ダブルクリック）／削除（右クリック、Delete/Backspaceキー）
//   - ノートの移動（ドラッグ）／リサイズ（左右端ドラッグ）
//   - 複数選択（ラバーバンド選択、Shiftクリックでトグル、Ctrl/Cmd+Aで全選択）
//   - 選択範囲ループ（上部ルーラーをドラッグ）
//   - 歌詞インライン編集（ノートをダブルクリック）
//   - 水平/垂直ズーム、再生ヘッド表示
//
// 設計方針:
//   このコンポーネントは VoseAudioProcessor / ScheduledSongNote を一切知らない。
//   PianoRollNote のリストだけを扱う自己完結コンポーネントとし、
//   プロセッサとの同期は onNotesChanged コールバック経由で外側（PluginEditor）に
//   委譲する。将来 graph_editor_widget.py 相当のカーブエディタや
//   keyboard_sidebar_widget.py 相当の本格的な鍵盤サイドバーに差し替える際も、
//   このコンポーネントの公開インタフェースは変えずに済むようにしてある。
//
// 使い方（PluginEditor.h 側）:
//   pianoRoll.setTempo (processor.getSongTempo());
//   pianoRoll.setNotes (fromScheduledSongNotes (processor.getSongNotesSnapshot()));
//   pianoRoll.onNotesChanged = [this] (const std::vector<PianoRollNote>& notes, double tempo)
//   {
//       processor.setSongNotesFromEditor (toScheduledSongNotes (notes), tempo);
//   };
//   pianoRoll.setPlayheadSecondsProvider ([this] { return processor.getSongPositionSeconds(); });
//
// 注意（既知の簡略化。将来の改善候補としてコメントで明示）:
//   - 鍵盤サイドバー・時間ルーラーはスクロール内容と一体化しており、
//     DAWにあるような「スクロールしても左端/上端に固定」の挙動にはなっていない。
//     本格対応は2枚のViewportを同期させる構成が必要（今回はPoCスコープ外）。
//   - ベロシティ編集UIは未実装（PianoRollNote::velocity は保持のみ）。

#pragma once

#include <juce_gui_basics/juce_gui_basics.h>
#include "PianoRollNote.h"
#include "VoseColourIds.h"
#include <vector>
#include <functional>
#include <memory>

class PianoRollComponent : public juce::Component,
                            private juce::Timer
{
public:
    PianoRollComponent();
    ~PianoRollComponent() override = default;

    // --- 外部からのデータ設定 ---
    void setNotes (std::vector<PianoRollNote> newNotes);
    const std::vector<PianoRollNote>& getNotes() const { return notes; }

    void setTempo (double bpm);
    double getTempo() const { return tempoBpm; }

    // 再生ヘッド位置（秒）を毎フレーム問い合わせるコールバック。nullptrなら非表示。
    void setPlayheadSecondsProvider (std::function<double()> provider);

    // ループ範囲。設定されていない場合は std::nullopt 相当（hasLoopRangeで判定）。
    bool hasLoopRange() const { return loopRangeValid; }
    double getLoopStartSeconds() const { return loopStartSec; }
    double getLoopEndSeconds() const { return loopEndSec; }
    void clearLoopRange();

    // --- ズーム ---
    void setHorizontalZoom (double newPixelsPerSecond);
    void setVerticalZoom (double newPixelsPerRow);

    // ノート内容が変化するたびに呼ばれる（追加/削除/移動/リサイズ/歌詞変更）。
    // 呼び出し側（PluginEditor）はここでプロセッサ側のモデルを更新する。
    std::function<void (const std::vector<PianoRollNote>&, double tempoBpm)> onNotesChanged;

    // --- juce::Component ---
    void paint (juce::Graphics&) override;
    void resized() override;
    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp (const juce::MouseEvent&) override;
    void mouseMove (const juce::MouseEvent&) override;
    void mouseWheelMove (const juce::MouseEvent&, const juce::MouseWheelDetails&) override;
    bool keyPressed (const juce::KeyPress&) override;

private:
    // --- ジオメトリ定数 ---
    static constexpr int kLowestNote  = 24;  // C1
    static constexpr int kHighestNote = 96;  // C7
    static constexpr int kKeyboardWidth = 52;
    static constexpr int kRulerHeight   = 22;
    static constexpr int kEdgeGrabPx    = 6;
    static constexpr double kMinNoteDurationSec = 0.05;

    enum class DragMode
    {
        none,
        moveNotes,
        resizeLeft,
        resizeRight,
        rubberBand,
        loopRange
    };

    // --- 座標変換 ---
    double xToTimeSec (float x) const;
    float  timeSecToX (double t) const;
    int    yToNoteNumber (float y) const;
    float  noteNumberToY (int noteNumber) const;
    double snapTimeSec (double t) const;
    double secondsPerBeat() const { return 60.0 / juce::jmax (1.0, tempoBpm); }

    void recalculateContentSize();
    void notifyChanged();

    // --- ヒットテスト ---
    struct HitResult
    {
        int index = -1;              // notes 内のインデックス。-1ならヒットなし
        bool onLeftEdge = false;
        bool onRightEdge = false;
    };
    HitResult hitTestNote (juce::Point<float> pos) const;

    void deleteSelectedNotes();
    void selectAll();
    void deselectAll();
    void beginLyricEdit (int noteIndex);
    void commitLyricEdit();

    void timerCallback() override; // 再生ヘッド再描画用

    std::vector<PianoRollNote> notes;
    int64_t nextNoteId = 1;

    double tempoBpm = 120.0;
    double pixelsPerSecond = 90.0;
    double pixelsPerRow = 16.0;
    int    snapDivisionsPerBeat = 4; // 4 = 16分音符スナップ

    // ループ範囲
    bool   loopRangeValid = false;
    double loopStartSec = 0.0;
    double loopEndSec = 0.0;

    // ドラッグ状態
    DragMode dragMode = DragMode::none;
    juce::Point<float> dragStartPos;
    std::vector<PianoRollNote> dragOriginalNotes; // ドラッグ開始時点のスナップショット（元に戻す/差分計算用）
    juce::Rectangle<float> rubberBandRect;
    double loopDragAnchorSec = 0.0;
    bool notesChangedDuringDrag = false;

    // 歌詞インライン編集
    std::unique_ptr<juce::TextEditor> lyricEditor;
    int lyricEditingIndex = -1;

    std::function<double()> playheadProvider;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PianoRollComponent)
};
