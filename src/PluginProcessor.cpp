#include "PluginProcessor.h"
#include "PluginEditor.h"

VoseAudioProcessor::VoseAudioProcessor()
    : juce::AudioProcessor (BusesProperties().withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "PARAMS", createParameterLayout())
{
    // vose_core をプラグインバイナリと同じフォルダから探索してロードする。
    auto pluginDir = juce::File::getSpecialLocation (juce::File::currentApplicationFile)
                          .getParentDirectory();
    if (! renderEngine.loadCore (pluginDir))
    {
        // フォールバック: 開発中はカレントディレクトリも見る
        renderEngine.loadCore (juce::File::getCurrentWorkingDirectory());
    }
}

juce::AudioProcessorValueTreeState::ParameterLayout VoseAudioProcessor::createParameterLayout()
{
    using Param = juce::AudioParameterFloat;
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;

    // vo_se_engine.py の parameters["Gender"] / ["Tension"] / ["Breath"] に対応。
    // 0.5 = 変化なし、というvose_core側の規約(apply_gender_shift)に合わせてデフォルト0.5。
    params.push_back (std::make_unique<Param> (juce::ParameterID { "gender", 1 }, "Gender",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "tension", 1 }, "Tension",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "breath", 1 }, "Breath",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.0f));

    return { params.begin(), params.end() };
}

void VoseAudioProcessor::prepareToPlay (double, int)
{
    playbackPosition = 0;
    isNotePlaying = false;
}

void VoseAudioProcessor::triggerTestNoteRender (int midiNoteNumber)
{
    if (midiNoteNumber == lastRenderedMidiNote)
        return; // 同じノートで再レンダリングは無駄なので抑制
    lastRenderedMidiNote = midiNoteNumber;

    const double hz = 440.0 * std::pow (2.0, (midiNoteNumber - 69) / 12.0);
    constexpr int kRes = 128;

    VoseNoteInput note;
    // TODO(フェーズ2): oto_map 相当の解決をC++側に実装するまでは、
    // 開発用の固定サンプルパスをここに置く。
    note.wavPath = "voices/default/a.wav";
    note.pitchCurve.assign (kRes, hz);
    note.genderCurve.assign (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    note.tensionCurve.assign (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    note.breathCurve.assign (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    renderEngine.requestRender ({ note });
    playbackPosition = 0;
}

void VoseAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    // ---- MIDI処理: ここではファイルI/Oも解析も一切行わない。 ----
    // 重い処理(execute_render)は triggerTestNoteRender 経由で
    // 別スレッドに投げるだけで、processBlock 自体は即座に戻る。
    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();
        if (msg.isNoteOn())
        {
            triggerTestNoteRender (msg.getNoteNumber());
            isNotePlaying = true;
        }
        else if (msg.isNoteOff())
        {
            isNotePlaying = false;
        }
    }

    if (! isNotePlaying.load())
        return;

    const auto* rendered = renderEngine.getRenderedBuffer();
    if (rendered == nullptr || rendered->getNumSamples() == 0)
        return; // まだレンダリング中。無音を返して待つ。

    const int numOut = buffer.getNumSamples();
    const int srcChannels = rendered->getNumChannels();
    int64_t pos = playbackPosition.load();

    for (int ch = 0; ch < buffer.getNumChannels(); ++ch)
    {
        const float* src = rendered->getReadPointer (juce::jmin (ch, srcChannels - 1));
        float* dst = buffer.getWritePointer (ch);

        for (int i = 0; i < numOut; ++i)
        {
            const int64_t srcIdx = pos + i;
            dst[i] = (srcIdx < rendered->getNumSamples()) ? src[srcIdx] : 0.0f;
        }
    }

    pos += numOut;
    if (pos >= rendered->getNumSamples())
        isNotePlaying = false; // 再生し終えたら停止（ループしない）
    playbackPosition = pos;
}

juce::AudioProcessorEditor* VoseAudioProcessor::createEditor()
{
    return new VoseAudioProcessorEditor (*this);
}

// This creates new instances of the plugin
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new VoseAudioProcessor();
}
