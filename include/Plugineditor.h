// PluginEditor.h
//
// [フェーズ3 差分] 上部にピアノロール（PianoRollComponent）を追加し、
// 既存のデバッグ用パネル（スライダー/歌詞キュー/UST読み込みボタン等）は
// 下部の帯に移設してそのまま残した。ピアノロールはUSTを読み込むと
// その内容で初期化され、編集するとリアルタイムでプロセッサ側の
// songNotes に反映される（PianoRollBridge.h経由）。

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "PluginProcessor.h"
#include "PianoRollComponent.h"
#include "PianoRollBridge.h"
#include <unordered_map>

class VoseAudioProcessorEditor : public juce::AudioProcessorEditor
{
public:
    explicit VoseAudioProcessorEditor (VoseAudioProcessor& p)
        : juce::AudioProcessorEditor (&p), processor (p)
    {
        setupSlider (genderSlider, genderAttach, "gender");
        setupSlider (tensionSlider, tensionAttach, "tension");
        setupSlider (breathSlider, breathAttach, "breath");

        lyricSequenceBox.setText (processor.getLyricSequenceText(), juce::dontSendNotification);
        lyricSequenceBox.setTooltip ("スペース区切りで歌詞を並べる（例: a i u e o）。"
                                      "MIDIノートオンのたびに1語ずつ消費し、最後まで行くと最初に戻ります。"
                                      "DAW側にLyricメタイベントがあればそちらが優先されます。");
        lyricSequenceBox.onTextChange = [this] { processor.setLyricSequence (lyricSequenceBox.getText()); };
        addAndMakeVisible (lyricSequenceBox);

        loadVoiceButton.onClick = [this]
        {
            fileChooser = std::make_unique<juce::FileChooser> (
                "音源フォルダを選択 (oto.iniを含むフォルダ)",
                juce::File::getSpecialLocation (juce::File::userHomeDirectory));

            fileChooser->launchAsync (juce::FileBrowserComponent::canSelectDirectories,
                                       [this] (const juce::FileChooser& fc)
            {
                auto dir = fc.getResult();
                if (dir.isDirectory())
                {
                    processor.loadVoiceDirectory (dir);
                    updateStatusLabel();
                }
            });
        };
        addAndMakeVisible (loadVoiceButton);
        addAndMakeVisible (statusLabel);
        updateStatusLabel();

        loadUstButton.onClick = [this]
        {
            ustFileChooser = std::make_unique<juce::FileChooser> (
                "USTファイルを選択", juce::File::getSpecialLocation (juce::File::userHomeDirectory),
                "*.ust");

            ustFileChooser->launchAsync (juce::FileBrowserComponent::canSelectFiles,
                                          [this] (const juce::FileChooser& fc)
            {
                auto f = fc.getResult();
                if (f.existsAsFile())
                {
                    processor.loadUstFile (f);
                    refreshPianoRollFromProcessor(); // [フェーズ3] 読み込んだUSTをピアノロールに反映
                    updateStatusLabel();
                }
            });
        };
        addAndMakeVisible (loadUstButton);

        playButton.onClick = [this] { processor.startSongPlayback(); };
        stopButton.onClick = [this] { processor.stopSongPlayback(); };
        addAndMakeVisible (playButton);
        addAndMakeVisible (stopButton);

        // --- [フェーズ3] ピアノロール ---
        pianoRollViewport.setViewedComponent (&pianoRoll, false);
        pianoRollViewport.setScrollBarsShown (true, true);
        addAndMakeVisible (pianoRollViewport);

        pianoRoll.onNotesChanged = [this] (const std::vector<PianoRollNote>& notes, double tempo)
        {
            auto scheduled = PianoRollBridge::toScheduledSongNotes (notes, &originalNoteMap);
            processor.setSongNotesFromEditor (scheduled, tempo);
            updateStatusLabel();
        };
        pianoRoll.setPlayheadSecondsProvider ([this] { return processor.getSongPositionSeconds(); });

        refreshPianoRollFromProcessor();

        setSize (900, 620);
    }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (juce::Colours::darkslategrey);
        g.setColour (juce::Colours::white);
        g.drawFittedText ("VO-SE (Phase 3: Piano Roll)", getLocalBounds().removeFromTop (24),
                           juce::Justification::centred, 1);
    }

    void resized() override
    {
        auto area = getLocalBounds().withTrimmedTop (24);

        // ピアノロールは上側の大きな領域を占有する。
        auto pianoRollArea = area.removeFromTop (area.getHeight() - kDebugPanelHeight);
        pianoRollViewport.setBounds (pianoRollArea.reduced (4));

        // 下側にデバッグパネル（既存のスライダー/歌詞キュー等）を配置。
        auto debugArea = area.reduced (20, 8);
        genderSlider.setBounds (debugArea.removeFromTop (28));
        tensionSlider.setBounds (debugArea.removeFromTop (28));
        breathSlider.setBounds (debugArea.removeFromTop (28));

        debugArea.removeFromTop (6);
        auto lyricRow = debugArea.removeFromTop (26);
        lyricSequenceBox.setBounds (lyricRow.removeFromLeft (200));
        lyricRow.removeFromLeft (8);
        loadVoiceButton.setBounds (lyricRow.removeFromLeft (160));
        lyricRow.removeFromLeft (8);
        statusLabel.setBounds (lyricRow);

        debugArea.removeFromTop (6);
        auto ustRow = debugArea.removeFromTop (26);
        loadUstButton.setBounds (ustRow.removeFromLeft (140));
        ustRow.removeFromLeft (8);
        playButton.setBounds (ustRow.removeFromLeft (70));
        ustRow.removeFromLeft (8);
        stopButton.setBounds (ustRow.removeFromLeft (70));
    }

private:
    using SliderAttachment = juce::AudioProcessorValueTreeState::SliderAttachment;

    static constexpr int kDebugPanelHeight = 140;

    void setupSlider (juce::Slider& slider, std::unique_ptr<SliderAttachment>& attach,
                       const juce::String& paramId)
    {
        slider.setSliderStyle (juce::Slider::LinearHorizontal);
        slider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 60, 20);
        addAndMakeVisible (slider);
        attach = std::make_unique<SliderAttachment> (processor.apvts, paramId, slider);
    }

    void updateStatusLabel()
    {
        statusLabel.setText (
            "読み込み済みalias数: " + juce::String (processor.getLoadedAliasCount())
                + " / USTノート数: " + juce::String (processor.getLoadedSongNoteCount())
                + "\nMIDI Lyricメタイベントがあれば優先、無ければ上の歌詞シーケンスをローテーション消費",
            juce::dontSendNotification);
    }

    // [フェーズ3] プロセッサ側の songNotes からピアノロールを初期化/再同期する。
    // UST読み込み直後や、外部（将来的なプロジェクトファイル読み込み等）から
    // songNotesが変わったタイミングで呼ぶ。
    void refreshPianoRollFromProcessor()
    {
        auto snapshot = processor.getSongNotesSnapshot();
        originalNoteMap = PianoRollBridge::buildOriginalIdMap (snapshot);

        pianoRoll.setTempo (processor.getSongTempo());
        pianoRoll.setNotes (PianoRollBridge::fromScheduledSongNotes (snapshot));
    }

    VoseAudioProcessor& processor;
    juce::Slider genderSlider, tensionSlider, breathSlider;
    std::unique_ptr<SliderAttachment> genderAttach, tensionAttach, breathAttach;

    juce::TextEditor lyricSequenceBox;
    juce::TextButton loadVoiceButton { "音源フォルダを開く..." };
    juce::Label statusLabel;
    std::unique_ptr<juce::FileChooser> fileChooser;

    juce::TextButton loadUstButton { "USTを開く..." };
    juce::TextButton playButton { "再生" };
    juce::TextButton stopButton { "停止" };
    std::unique_ptr<juce::FileChooser> ustFileChooser;

    // [フェーズ3]
    juce::Viewport pianoRollViewport;
    PianoRollComponent pianoRoll;
    std::unordered_map<int64_t, ScheduledSongNote> originalNoteMap;
};
