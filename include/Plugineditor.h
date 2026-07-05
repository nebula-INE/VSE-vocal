#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "PluginProcessor.h"

class VoseAudioProcessorEditor : public juce::AudioProcessorEditor
{
public:
    explicit VoseAudioProcessorEditor (VoseAudioProcessor& p)
        : juce::AudioProcessorEditor (&p), processor (p)
    {
        setupSlider (genderSlider, genderAttach, "gender");
        setupSlider (tensionSlider, tensionAttach, "tension");
        setupSlider (breathSlider, breathAttach, "breath");

        lyricBox.setText (processor.getTestLyric(), juce::dontSendNotification);
        lyricBox.onTextChange = [this] { processor.setTestLyric (lyricBox.getText()); };
        addAndMakeVisible (lyricBox);

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

        setSize (360, 260);
    }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (juce::Colours::darkslategrey);
        g.setColour (juce::Colours::white);
        g.drawFittedText ("VO-SE (Phase 2 PoC)", getLocalBounds().removeFromTop (30),
                           juce::Justification::centred, 1);
    }

    void resized() override
    {
        auto area = getLocalBounds().reduced (20).withTrimmedTop (30);
        genderSlider.setBounds (area.removeFromTop (36));
        tensionSlider.setBounds (area.removeFromTop (36));
        breathSlider.setBounds (area.removeFromTop (36));

        area.removeFromTop (8);
        auto lyricRow = area.removeFromTop (28);
        lyricBox.setBounds (lyricRow.removeFromLeft (100));
        lyricRow.removeFromLeft (8);
        loadVoiceButton.setBounds (lyricRow);

        area.removeFromTop (8);
        statusLabel.setBounds (area.removeFromTop (40));
    }

private:
    using SliderAttachment = juce::AudioProcessorValueTreeState::SliderAttachment;

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
                + "\n(MIDIノートオンで上のテスト歌詞を発音します)",
            juce::dontSendNotification);
    }

    VoseAudioProcessor& processor;
    juce::Slider genderSlider, tensionSlider, breathSlider;
    std::unique_ptr<SliderAttachment> genderAttach, tensionAttach, breathAttach;

    juce::TextEditor lyricBox;
    juce::TextButton loadVoiceButton { "音源フォルダを開く..." };
    juce::Label statusLabel;
    std::unique_ptr<juce::FileChooser> fileChooser;
};
