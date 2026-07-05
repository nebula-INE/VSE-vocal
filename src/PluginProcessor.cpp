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
        juce::Logger::writeToLog ("VO-SE: vose_core loaded but no streaming API found. "
                                   "Rebuild vose_core with vose_streaming_final.cpp linked.");
    }

    // 開発用デフォルト音源フォルダ。実運用ではエディタの「音源を開く」で
    // loadVoiceDirectory() を呼び直す想定（フェーズ3のUI実装で接続）。
    auto defaultVoiceDir = pluginDir.getChildFile ("voices").getChildFile ("default");
    if (defaultVoiceDir.isDirectory())
        loadVoiceDirectory (defaultVoiceDir);
}

void VoseAudioProcessor::loadVoiceDirectory (const juce::File& dir)
{
    otoDb.clear();
    const int entryCount = otoDb.loadVoiceDir (dir);

    if (entryCount == 0)
    {
        juce::Logger::writeToLog ("VO-SE: oto.ini が見つからないか0エントリでした: "
                                   + dir.getFullPathName());
        return;
    }

    const int loaded = otoDb.pushAllToCore (coreLib);
    juce::Logger::writeToLog ("VO-SE: oto.ini " + juce::String (entryCount) + "エントリ解析、"
                               + juce::String (loaded) + "件のWAVをコアへ事前登録しました。");
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

    // oto_parser.py の resolve_alias() と同じ VCV→CV→単独音→部分一致 の順で解決。
    // prevVowel は簡易実装: 直前に解決したaliasの表記そのものを渡す
    // (本来は「末尾の母音ラベル」を音素解析器で求めるべき。TODOフェーズ2後半)。
    const auto* entry = otoDb.resolveAlias (testLyric, lastResolvedVowel);

    if (entry == nullptr)
    {
        juce::Logger::writeToLog ("VO-SE: 歌詞 '" + testLyric + "' に対応するoto.iniエントリが見つかりません。"
                                   "loadVoiceDirectory() で音源フォルダを読み込んでいますか？");
        return;
    }

    std::vector<double> pitchCurve (kRes, hz);
    std::vector<double> genderCurve (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    std::vector<double> tensionCurve (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    std::vector<double> breathCurve (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    // wav_path フィールドには実パスではなく oto.ini の alias（音源キー）を渡す。
    // vose_core::find_voice_ref はこのキーで load_embedded_resource 済みのPCMを検索する。
    voice.pushNote (nextNoteId++, entry->alias, pitchCurve, genderCurve, tensionCurve, breathCurve);

    lastResolvedVowel = testLyric; // 次ノートのVCV解決用（簡易: 歌詞そのものを母音扱い）
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
