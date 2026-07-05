// PluginProcessor.h
// フェーズ1 PoC v2: MIDIノートオンで StreamingVoice::pushNote、
// processBlock で StreamingVoice::pull を直接呼ぶ「本物のリアルタイム再生」版。
// (旧v1のオフラインバウンス方式は RenderEngine.h に残置、書き出し機能用に転用予定)

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "VoseBridge.h"
#include "StreamingVoice.h"

class VoseAudioProcessor : public juce::AudioProcessor
{
public:
    VoseAudioProcessor();
    ~VoseAudioProcessor() override;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "VO-SE"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock&) override {}
    void setStateInformation (const void*, int) override {}

    juce::AudioProcessorValueTreeState apvts;

private:
    juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    // MIDIノートオンに応じて streaming API へノートを積む。
    // 歌詞バインドはフェーズ2まで固定のテスト音源を使う。
    void pushTestNote (int midiNoteNumber);

    VoseCoreLibrary coreLib;
    StreamingVoice  voice;

    // pull() が要求サンプル数より少なく返した場合に備えたスクラッチバッファ
    juce::AudioBuffer<float> pullScratch;

    int64_t nextNoteId = 1;
    bool anyNoteHeld = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoseAudioProcessor)
};
