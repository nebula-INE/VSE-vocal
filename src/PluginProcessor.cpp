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

    // vcv_resolver.py の VcvResolver.resolve_note() と同じ手順:
    //   1. 音源にVCVエイリアスが存在する場合のみ、前ノートの歌詞から末尾母音を判定
    //   2. resolveAlias(lyric, prevVowel) で VCV→CV→単独音→部分一致 の順に解決
    juce::String prevVowel;
    if (otoDb.hasVcv() && prevLyric.isNotEmpty())
        prevVowel = vowelClassifier.trailingVowel (prevLyric);

    const auto* entry = otoDb.resolveAlias (testLyric, prevVowel);

    if (entry == nullptr)
    {
        // Python版のフォールバック（entryが無ければlyricそのものをaliasとして使う）
        // に倣うが、C++側はload_embedded_resource未登録のキーを渡しても
        // find_voice_refが失敗するだけで済む（クラッシュはしない）。
        juce::Logger::writeToLog ("VO-SE: 歌詞 '" + testLyric + "' に対応するoto.iniエントリが見つかりません。"
                                   "loadVoiceDirectory() で音源フォルダを読み込んでいますか？");
    }

    const juce::String aliasToUse = (entry != nullptr) ? entry->alias : testLyric;

    std::vector<double> pitchCurve (kRes, hz);
    std::vector<double> genderCurve (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    std::vector<double> tensionCurve (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    std::vector<double> breathCurve (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    // wav_path フィールドには実パスではなく oto.ini の alias（音源キー）を渡す。
    // vose_core::find_voice_ref / g_oto_db はこのキーで検索する。
    voice.pushNote (nextNoteId++, aliasToUse, pitchCurve, genderCurve, tensionCurve, breathCurve);

    prevLyric = testLyric; // 次ノートのVCV解決用（VcvResolver.resolve()のprev_lyric更新と同じ）
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
