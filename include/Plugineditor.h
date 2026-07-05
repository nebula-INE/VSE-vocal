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
        setSize (320, 200);
    }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (juce::Colours::darkslategrey);
        g.setColour (juce::Colours::white);
        g.drawFittedText ("VO-SE (Phase 1 PoC)", getLocalBounds().removeFromTop (30),
                           juce::Justification::centred, 1);
    }

    void resized() override
    {
        auto area = getLocalBounds().reduced (20).withTrimmedTop (30);
        genderSlider.setBounds (area.removeFromTop (40));
        tensionSlider.setBounds (area.removeFromTop (40));
        breathSlider.setBounds (area.removeFromTop (40));
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

    VoseAudioProcessor& processor;
    juce::Slider genderSlider, tensionSlider, breathSlider;
    std::unique_ptr<SliderAttachment> genderAttach, tensionAttach, breathAttach;
};
