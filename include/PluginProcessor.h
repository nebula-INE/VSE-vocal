// PluginProcessor.h
// フェーズ1 PoC v2: MIDIノートオンで StreamingVoice::pushNote、
// processBlock で StreamingVoice::pull を直接呼ぶ「本物のリアルタイム再生」版。
// (旧v1のオフラインバウンス方式は RenderEngine.h に残置、書き出し機能用に転用予定)

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "VoseBridge.h"
#include "StreamingVoice.h"
#include "OtoDatabase.h"

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

    // エディタ(UI)から呼ばれる。音源フォルダを選び直すたびに
    // oto.iniの再パース + load_embedded_resource + set_oto_data をやり直す。
    void loadVoiceDirectory (const juce::File& dir);
    int  getLoadedAliasCount() const { return otoDb.size(); }

    // テスト用の歌詞をUIから変更できるようにする（フェーズ2 PoC）。
    // MIDIノート名からの歌詞バインドはフェーズ2後半のTODO。
    void setTestLyric (const juce::String& lyric) { testLyric = lyric; }
    juce::String getTestLyric() const { return testLyric; }

    juce::AudioProcessorValueTreeState apvts;

private:
    juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    // MIDIノートオンに応じて streaming API へノートを積む。
    // oto.ini解決済みのaliasを wav_path (音源キー) として渡す。
    void pushTestNote (int midiNoteNumber);

    VoseCoreLibrary coreLib;
    StreamingVoice  voice;
    OtoDatabase     otoDb;

    juce::String testLyric { "a" };
    juce::String lastResolvedVowel; // VCV解決用に前ノートの末尾母音を保持（フェーズ2簡易版）

    // pull() が要求サンプル数より少なく返した場合に備えたスクラッチバッファ
    juce::AudioBuffer<float> pullScratch;

    int64_t nextNoteId = 1;
    bool anyNoteHeld = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoseAudioProcessor)
};
