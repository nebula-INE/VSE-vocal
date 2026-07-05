// PluginProcessor.h
// フェーズ1 PoC: MIDIノートオンをトリガーに execute_render をバックグラウンド実行し、
// 完了したバッファを processBlock でストリーム再生する。
// 歌詞入力・VCV連携・ストリーミング合成はフェーズ2でここに統合する。

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "RenderEngine.h"

class VoseAudioProcessor : public juce::AudioProcessor
{
public:
    VoseAudioProcessor();
    ~VoseAudioProcessor() override = default;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
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

    // 現在鳴っているMIDIノートに対して、暫定の単一母音（"a"）テスト音源を
    // execute_render に流し込む。実際の歌詞バインドはフェーズ2で
    // MIDIノート名パースまたは内蔵歌詞トラックに置き換える。
    void triggerTestNoteRender (int midiNoteNumber);

    RenderEngine renderEngine;

    // 再生用の読み出し位置。オーディオスレッド内でのみ増加させる。
    std::atomic<int64_t> playbackPosition { 0 };
    std::atomic<bool> isNotePlaying { false };

    int lastRenderedMidiNote = -1;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoseAudioProcessor)
};
