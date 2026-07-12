// PluginEditor.h
//
// [フェーズ3 差分]
//   - 上部: ピアノロール（PianoRollComponent）
//   - 中段: グラフエディタ（GraphEditorComponent, Pitch/Gender/Tension/Breath）
//   - その下: 音源ブラウザ（VoiceGalleryComponent, ボイスギャラリー）+ テーマ切替
//   - 最下部: 既存のデバッグ用パネル（スライダー/歌詞キュー/UST読み込みボタン等）
// という4段構成にした。VoseLookAndFeelでダーク/ライトのテーマ切り替えに対応。

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "PluginProcessor.h"
#include "PianoRollComponent.h"
#include "PianoRollBridge.h"
#include "GraphEditorComponent.h"
#include "VoiceGalleryComponent.h"
#include "VoseLookAndFeel.h"
#include <unordered_map>
#include <array>

class VoseAudioProcessorEditor : public juce::AudioProcessorEditor
{
public:
    explicit VoseAudioProcessorEditor (VoseAudioProcessor& p)
        : juce::AudioProcessorEditor (&p), processor (p)
    {
        // [フェーズ3] テーマ。エディタが破棄されるまでこのLookAndFeelが
        // コンポーネントツリー全体（子コンポーネント含む）に適用される。
        setLookAndFeel (&lookAndFeel);

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
                    voiceGallery.setSelectedByName (dir.getFileName());
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
                    refreshPianoRollFromProcessor();
                    refreshGraphEditorFromProcessor();
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

        // --- [フェーズ3] グラフエディタ ---
        for (size_t i = 0; i < kModes.size(); ++i)
        {
            auto& button = modeButtons[i];
            button.setButtonText (kModeNames[i]);
            button.setRadioGroupId (0x6706);
            button.setClickingTogglesState (true);
            button.setColour (juce::TextButton::buttonOnColourId, colourForModeButton (kModes[i]));
            button.onClick = [this, i] { graphEditor.setMode (kModes[i]); };
            addAndMakeVisible (button);
        }
        modeButtons[0].setToggleState (true, juce::dontSendNotification);

        penModeToggle.setButtonText ("Pen");
        penModeToggle.onClick = [this] { graphEditor.setPenMode (penModeToggle.getToggleState()); };
        addAndMakeVisible (penModeToggle);

        graphEditorViewport.setViewedComponent (&graphEditor, false);
        graphEditorViewport.setScrollBarsShown (false, true);
        addAndMakeVisible (graphEditorViewport);

        graphEditor.onCurvesChanged = [this] (const AutomationCurves& curves)
        {
            processor.setAutomationFromEditor (curves);
        };
        graphEditor.setPlayheadSecondsProvider ([this] { return processor.getSongPositionSeconds(); });

        // --- [フェーズ3] 音源ブラウザ（ボイスギャラリー） ---
        rescanVoicesButton.setButtonText ("音源を再スキャン");
        rescanVoicesButton.onClick = [this] { voiceGallery.rescan(); };
        addAndMakeVisible (rescanVoicesButton);

        themeToggleButton.setButtonText (lookAndFeel.isDarkMode() ? "Light" : "Dark");
        themeToggleButton.onClick = [this]
        {
            const bool newDark = ! lookAndFeel.isDarkMode();
            lookAndFeel.setDarkMode (newDark);
            themeToggleButton.setButtonText (newDark ? "Light" : "Dark");
            repaint();
        };
        addAndMakeVisible (themeToggleButton);

        voiceGalleryViewport.setViewedComponent (&voiceGallery, false);
        voiceGalleryViewport.setScrollBarsShown (false, true);
        addAndMakeVisible (voiceGalleryViewport);

        voiceGallery.onVoiceSelected = [this] (const VoiceInfo& info)
        {
            if (info.isEmbedded)
            {
                // TODO: コアDLLへの内蔵音源登録(register_all_embedded_voices)呼び出しが
                // PluginProcessor側にまだ配線されていない。ビルドパイプライン側の
                // コード生成（GeneratedVoices.h相当）と繋ぎ込む必要がある。
                statusLabel.setText ("内蔵音源への切り替えは未実装です（TODO）",
                                     juce::dontSendNotification);
                return;
            }
            processor.loadVoiceDirectory (info.directory);
            updateStatusLabel();
        };
        voiceGallery.rescan();

        refreshPianoRollFromProcessor();
        refreshGraphEditorFromProcessor();

        setSize (900, 980);
    }

    ~VoseAudioProcessorEditor() override
    {
        setLookAndFeel (nullptr); // JUCEの規約: LookAndFeelを外してから破棄する
    }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (getLookAndFeel().findColour (juce::ResizableWindow::backgroundColourId));
        g.setColour (getLookAndFeel().findColour (juce::Label::textColourId));
        g.drawFittedText ("VO-SE (Phase 3: Piano Roll / Graph Editor / Voice Gallery)",
                           getLocalBounds().removeFromTop (24), juce::Justification::centred, 1);
    }

    void resized() override
    {
        auto area = getLocalBounds().withTrimmedTop (24);

        auto pianoRollArea = area.removeFromTop (area.getHeight() - kDebugPanelHeight
                                                  - kGraphEditorHeight - kGalleryAreaHeight);
        pianoRollViewport.setBounds (pianoRollArea.reduced (4));

        auto graphArea = area.removeFromTop (kGraphEditorHeight);
        auto graphToolbar = graphArea.removeFromTop (28);
        for (auto& button : modeButtons)
        {
            button.setBounds (graphToolbar.removeFromLeft (70));
            graphToolbar.removeFromLeft (4);
        }
        graphToolbar.removeFromLeft (10);
        penModeToggle.setBounds (graphToolbar.removeFromLeft (60));

        graphEditorViewport.setBounds (graphArea.reduced (4, 2));
        graphEditor.setViewHeight (graphEditorViewport.getHeight());

        auto galleryArea = area.removeFromTop (kGalleryAreaHeight);
        auto galleryToolbar = galleryArea.removeFromTop (28);
        rescanVoicesButton.setBounds (galleryToolbar.removeFromLeft (140));
        galleryToolbar.removeFromLeft (8);
        themeToggleButton.setBounds (galleryToolbar.removeFromLeft (90));
        voiceGalleryViewport.setBounds (galleryArea.reduced (4, 2));
        voiceGallery.setSize (voiceGallery.getWidth(), voiceGalleryViewport.getHeight());

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
    static constexpr int kGraphEditorHeight = 200;
    static constexpr int kGalleryAreaHeight = 138;

    static inline const std::array<AutomationParam, 4> kModes {
        AutomationParam::pitch, AutomationParam::gender, AutomationParam::tension, AutomationParam::breath
    };
    static inline const std::array<const char*, 4> kModeNames { "Pitch", "Gender", "Tension", "Breath" };

    static juce::Colour colourForModeButton (AutomationParam p)
    {
        switch (p)
        {
            case AutomationParam::pitch:   return juce::Colour (0xff00ff7f);
            case AutomationParam::gender:  return juce::Colour (0xffe74c3c);
            case AutomationParam::tension: return juce::Colour (0xff2ecc71);
            case AutomationParam::breath:  return juce::Colour (0xfff1c40f);
        }
        return juce::Colours::grey;
    }

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

    void refreshPianoRollFromProcessor()
    {
        auto snapshot = processor.getSongNotesSnapshot();
        originalNoteMap = PianoRollBridge::buildOriginalIdMap (snapshot);

        pianoRoll.setTempo (processor.getSongTempo());
        pianoRoll.setNotes (PianoRollBridge::fromScheduledSongNotes (snapshot));
    }

    void refreshGraphEditorFromProcessor()
    {
        graphEditor.setTempo (processor.getSongTempo());
        graphEditor.setCurves (processor.getAutomationSnapshot());
    }

    VoseAudioProcessor& processor;
    VoseLookAndFeel lookAndFeel { true }; // [フェーズ3] ダークモードで開始

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

    juce::Viewport pianoRollViewport;
    PianoRollComponent pianoRoll;
    std::unordered_map<int64_t, ScheduledSongNote> originalNoteMap;

    std::array<juce::TextButton, 4> modeButtons;
    juce::ToggleButton penModeToggle;
    juce::Viewport graphEditorViewport;
    GraphEditorComponent graphEditor;

    // [フェーズ3] 音源ブラウザ + テーマ切替
    juce::TextButton rescanVoicesButton;
    juce::TextButton themeToggleButton;
    juce::Viewport voiceGalleryViewport;
    VoiceGalleryComponent voiceGallery;
};
