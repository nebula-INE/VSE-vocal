#include "PluginProcessor.h"
#include "PluginEditor.h"

VoseAudioProcessor::VoseAudioProcessor()
    : juce::AudioProcessor (BusesProperties().withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "PARAMS", createParameterLayout())
{
    auto pluginDir = juce::File::getSpecialLocation (juce::File::currentApplicationFile)
                          .getParentDirectory();
    if (! coreLib.load (pluginDir))
        coreLib.load (juce::File::getCurrentWorkingDirectory());

    if (coreLib.isLoaded() && ! coreLib.supportsStreaming())
    {
        // ビルドされている vose_core が古く streaming_render_* を
        // エクスポートしていない場合はここに来る。
        // その場合は RenderEngine.h のオフラインバウンス方式にフォールバックすること
        // (このPoCではまだ未接続。TODOフェーズ2)。
        juce::Logger::writeToLog ("VO-SE: vose_core loaded but no streaming API found. "
                                   "Rebuild vose_core with vose_streaming_final.cpp linked.");
    }
}

VoseAudioProcessor::~VoseAudioProcessor()
{
    voice.stop();
}

juce::AudioProcessorValueTreeState::ParameterLayout VoseAudioProcessor::createParameterLayout()
{
    using Param = juce::AudioParameterFloat;
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;

    params.push_back (std::make_unique<Param> (juce::ParameterID { "gender", 1 }, "Gender",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "tension", 1 }, "Tension",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "breath", 1 }, "Breath",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.0f));

    return { params.begin(), params.end() };
}

void VoseAudioProcessor::prepareToPlay (double sampleRate, int samplesPerBlock)
{
    pullScratch.setSize (2, samplesPerBlock);
    anyNoteHeld = false;

    // StreamingSynthesizer はサンプルレート依存の内部バッファ(RingBuffer)を
    // コンストラクタで確保するので、サンプルレートが分かるここで作り直す。
    if (coreLib.supportsStreaming())
        voice.start (coreLib, sampleRate, /*bufferMs*/ 500);
}

void VoseAudioProcessor::releaseResources()
{
    voice.stop();
}

void VoseAudioProcessor::pushTestNote (int midiNoteNumber)
{
    const double hz = 440.0 * std::pow (2.0, (midiNoteNumber - 69) / 12.0);
    constexpr int kRes = 128;

    // TODO(フェーズ2): oto_map 相当の解決をC++側に実装するまでは、
    // 開発用の固定サンプルパスをここに置く。
    static const juce::String kTestWavPath = "voices/default/a.wav";

    std::vector<double> pitchCurve (kRes, hz);
    std::vector<double> genderCurve (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    std::vector<double> tensionCurve (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    std::vector<double> breathCurve (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    voice.pushNote (nextNoteId++, kTestWavPath, pitchCurve, genderCurve, tensionCurve, breathCurve);
}

void VoseAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    // ---- MIDI処理: pushNote は内部で streaming_render_push_note を呼ぶだけで、
    // 合成そのものは vose_core 側のワーカースレッドが行う。ここはノンブロッキング。
    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();
        if (msg.isNoteOn())
        {
            pushTestNote (msg.getNoteNumber());
            anyNoteHeld = true;
        }
        else if (msg.isNoteOff())
        {
            anyNoteHeld = false;
        }
    }

    if (! voice.isActive())
        return;

    const int numOut = buffer.getNumSamples();

    // pull() はモノラルPCMを返す前提（RingBuffer<float>1本）。
    // ステレオ出力には両チャンネルへ同じ値を複製する。
    pullScratch.setSize (1, numOut, false, false, true);
    float* mono = pullScratch.getWritePointer (0);

    const int got = voice.pull (mono, numOut);
    if (got <= 0)
        return; // まだバッファが埋まっていない（発音直後など）。無音で待つ。

    for (int ch = 0; ch < buffer.getNumChannels(); ++ch)
    {
        float* dst = buffer.getWritePointer (ch);
        for (int i = 0; i < got; ++i)
            dst[i] = mono[i];
    }
}

juce::AudioProcessorEditor* VoseAudioProcessor::createEditor()
{
    return new VoseAudioProcessorEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new VoseAudioProcessor();
}
