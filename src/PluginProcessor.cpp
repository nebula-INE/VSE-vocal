#include "PluginProcessor.h"
#include "PluginEditor.h"
#include <algorithm>

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
    currentSampleRate = sampleRate;

    // StreamingSynthesizer はサンプルレート依存の内部バッファ(RingBuffer)を
    // コンストラクタで確保するので、サンプルレートが分かるここで作り直す。
    if (coreLib.supportsStreaming())
        voice.start (coreLib, sampleRate, /*bufferMs*/ 500);
}

void VoseAudioProcessor::releaseResources()
{
    voice.stop();
}

void VoseAudioProcessor::setLyricSequence (const juce::String& spaceSeparatedText)
{
    auto tokens = juce::StringArray::fromTokens (spaceSeparatedText, " \t\n\r", "");
    tokens.removeEmptyStrings();
    if (tokens.isEmpty())
        tokens.add ("a");

    const juce::SpinLock::ScopedLockType sl (lyricLock);
    lyricSequence = tokens;
    lyricSequenceIndex = 0;
}

juce::String VoseAudioProcessor::getLyricSequenceText() const
{
    const juce::SpinLock::ScopedLockType sl (lyricLock);
    return lyricSequence.joinIntoString (" ");
}

juce::String VoseAudioProcessor::consumeNextLyric()
{
    // 優先1: MIDI Lyric/Textメタイベント由来（processBlock内でのみ読み書きするので
    // ロック不要。ここは必ずオーディオスレッドから呼ばれる前提）。
    if (! midiLyricQueue.empty())
    {
        auto lyric = midiLyricQueue.front();
        midiLyricQueue.pop_front();
        return lyric;
    }

    // 優先3: 内蔵歌詞キューUI。ローテーションしてテスト時にループさせ続ける。
    const juce::SpinLock::ScopedLockType sl (lyricLock);
    if (lyricSequence.isEmpty())
        return "a";

    const auto lyric = lyricSequence[lyricSequenceIndex % lyricSequence.size()];
    lyricSequenceIndex = (lyricSequenceIndex + 1) % lyricSequence.size();
    return lyric;
}

void VoseAudioProcessor::pushNote (int midiNoteNumber)
{
    constexpr int kRes = 128;
    const double hz = 440.0 * std::pow (2.0, (midiNoteNumber - 69) / 12.0);
    std::vector<double> flatCurve (kRes, hz); // ライブMIDIにはポルタメント/ビブラート情報が無いので一定ピッチ

    // MIDI経由には per-note Flags 相当の情報源が無いので、常にAPVTSのグローバル値を使う。
    std::vector<double> genderCurve (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    std::vector<double> tensionCurve (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    std::vector<double> breathCurve (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    resolveAndPushNote (flatCurve, consumeNextLyric(), genderCurve, tensionCurve, breathCurve);
}

void VoseAudioProcessor::pushSongNote (const ScheduledSongNote& note)
{
    constexpr int kRes = 128;
    const double durationMs = note.durationSec * 1000.0;

    // ビブラートはここで焼き込む（ネイティブ側は簡易モデルでUSTのVBRを
    // 再現できず、かつVoseStreamNoteにはそもそも経路が無いため）。
    auto pitchCurve = vose_pitch::buildVibratoPitchCurveHz (note.noteNum, durationMs, note.vibrato, kRes);

    // ポルタメントはネイティブの portamento_offsets 経由で渡す（忠実度の劣化なし）。
    auto portamentoCents = vose_pitch::buildPortamentoCentsCurve (note.pbs, note.pbw, note.pby, durationMs, kRes);

    // UST の Flags（例: "g-5B50"）でノート単位の上書きがあればそちらを優先し、
    // 無ければAPVTSのグローバル値にフォールバックする。
    const auto flagOverrides = parseUstFlags (note.flags);

    const double genderVal  = flagOverrides.gender01.value_or  ((double) apvts.getRawParameterValue ("gender")->load());
    const double tensionVal = flagOverrides.tension01.value_or ((double) apvts.getRawParameterValue ("tension")->load());
    const double breathVal  = flagOverrides.breath01.value_or  ((double) apvts.getRawParameterValue ("breath")->load());

    std::vector<double> genderCurve (kRes, genderVal);
    std::vector<double> tensionCurve (kRes, tensionVal);
    std::vector<double> breathCurve (kRes, breathVal);

    // [フェーズ3] グラフエディタで打ち込んだPitch/Gender/Tension/Breathオートメーションが
    // あれば、ノート単位の固定値（Flags/APVTS）よりもこちらを優先してサンプリングする。
    // 曲頭からの絶対時刻(note.startTimeSec起点)でサンプリングするため、UST/ピアノロールの
    // 時間軸とグラフエディタの時間軸は同じ「曲頭からの秒数」で揃っている前提。
    const int denom = juce::jmax (1, kRes - 1);
    const bool hasGender  = automation.hasPoints (AutomationParam::gender);
    const bool hasTension = automation.hasPoints (AutomationParam::tension);
    const bool hasBreath  = automation.hasPoints (AutomationParam::breath);
    const bool hasPitch   = automation.hasPoints (AutomationParam::pitch);

    if (hasGender || hasTension || hasBreath || hasPitch)
    {
        for (int j = 0; j < kRes; ++j)
        {
            const double tAbsSec = note.startTimeSec + (note.durationSec * ((double) j / (double) denom));

            if (hasGender)
                genderCurve[(size_t) j] = *automation.evaluate (AutomationParam::gender, tAbsSec);
            if (hasTension)
                tensionCurve[(size_t) j] = *automation.evaluate (AutomationParam::tension, tAbsSec);
            if (hasBreath)
                breathCurve[(size_t) j] = *automation.evaluate (AutomationParam::breath, tAbsSec);

            if (hasPitch)
            {
                const double semitoneOffset = AutomationRanges::pitchValueToSemitones (
                    *automation.evaluate (AutomationParam::pitch, tAbsSec));
                pitchCurve[(size_t) j] *= std::pow (2.0, semitoneOffset / 12.0);
            }
        }
    }

    resolveAndPushNote (pitchCurve, note.lyric, genderCurve, tensionCurve, breathCurve, portamentoCents);
}

void VoseAudioProcessor::resolveAndPushNote (const std::vector<double>& pitchCurveHz, const juce::String& lyric,
                                              const std::vector<double>& genderCurve,
                                              const std::vector<double>& tensionCurve,
                                              const std::vector<double>& breathCurve,
                                              const std::vector<double>& portamentoOffsetsCents)
{
    // vcv_resolver.py の VcvResolver.resolve_note() と同じ手順:
    //   1. 音源にVCVエイリアスが存在する場合のみ、前ノートの歌詞から末尾母音を判定
    //   2. resolveAlias(lyric, prevVowel) で VCV→CV→単独音→部分一致 の順に解決
    juce::String prevVowel;
    if (otoDb.hasVcv() && prevLyric.isNotEmpty())
        prevVowel = vowelClassifier.trailingVowel (prevLyric);

    const auto* entry = otoDb.resolveAlias (lyric, prevVowel);

    if (entry == nullptr)
    {
        juce::Logger::writeToLog ("VO-SE: 歌詞 '" + lyric + "' に対応するoto.iniエントリが見つかりません。"
                                   "loadVoiceDirectory() で音源フォルダを読み込んでいますか？");
    }

    const juce::String aliasToUse = (entry != nullptr) ? entry->alias : lyric;

    // wav_path フィールドには実パスではなく oto.ini の alias（音源キー）を渡す。
    // vose_core::find_voice_ref / g_oto_db はこのキーで検索する。
    voice.pushNote (nextNoteId++, aliasToUse, pitchCurveHz, genderCurve, tensionCurve, breathCurve,
                     portamentoOffsetsCents);

    prevLyric = lyric; // 次ノートのVCV解決用（VcvResolver.resolve()のprev_lyric更新と同じ）
}

bool VoseAudioProcessor::loadUstFile (const juce::File& ustFile)
{
    UstParser parser;
    auto project = parser.load (ustFile);

    if (project.notes.empty())
    {
        juce::Logger::writeToLog ("VO-SE: UST読み込み失敗、またはノートが0件でした: "
                                   + ustFile.getFullPathName());
        return false;
    }

    songNotes = UstParser::toScheduledNotes (project);
    songTempo = project.tempo; // [フェーズ3] ピアノロールのグリッド表示用に保持
    songNoteCursor = 0;
    songPositionSec = 0.0;
    songPlaying = false;

    juce::Logger::writeToLog ("VO-SE: UST読み込み完了。" + juce::String ((int) songNotes.size()) + "ノート、"
                               + "テンポ=" + juce::String (project.tempo));
    return true;
}

void VoseAudioProcessor::startSongPlayback()
{
    songNoteCursor = 0;
    songPositionSec = 0.0;
    prevLyric.clear();
    songPlaying = ! songNotes.empty();
}

void VoseAudioProcessor::stopSongPlayback()
{
    songPlaying = false;
}

// [フェーズ3] ピアノロールでの編集結果をプロセッサに反映する。
// songNotes は再生中のオーディオスレッドから読まれるため、書き換え前に
// 必ず停止する（PluginProcessor.h のコメント参照：ロックフリー設計の前提を
// 崩さないための単純な対策。真にリアルタイムな編集反映が必要になったら
// ダブルバッファ化を検討する）。
void VoseAudioProcessor::setSongNotesFromEditor (std::vector<ScheduledSongNote> newNotes, double tempo)
{
    stopSongPlayback();

    std::sort (newNotes.begin(), newNotes.end(),
               [] (const ScheduledSongNote& a, const ScheduledSongNote& b)
               { return a.startTimeSec < b.startTimeSec; });

    songNotes = std::move (newNotes);
    songTempo = juce::jmax (1.0, tempo);
    songNoteCursor = 0;
    songPositionSec = 0.0;
}

void VoseAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    // ---- MIDI処理 ----
    // 優先1: Lyric(type=5)/Text(type=1)メタイベントを見つけたらキューに積む。
    // 標準MIDIのカラオケ形式（DAWのピアノロールで歌詞を打ち込む方式）はこれで拾える。
    // 優先3: 見つからなければ内蔵歌詞キューUIをローテーション消費する(pushNote内)。
    //
    // pushNote は streaming_render_push_note を呼ぶだけで、合成そのものは
    // vose_core側のワーカースレッドが行うため、ここはノンブロッキング。
    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();

        if (msg.isTextMetaEvent())
        {
            const int metaType = msg.getMetaEventType();
            if (metaType == 5 || metaType == 1) // 5=Lyric, 1=Text
                midiLyricQueue.push_back (msg.getTextFromTextMetaEvent());
        }
        else if (msg.isNoteOn())
        {
            pushNote (msg.getNoteNumber());
            anyNoteHeld = true;
        }
        else if (msg.isNoteOff())
        {
            anyNoteHeld = false;
        }
    }

    // ---- UST曲スケジューラ: ホストトランスポート非同期の簡易内部クロック ----
    // このブロックが表す時間窓 [songPositionSec, songPositionSec+blockDurationSec)
    // に開始時刻が入るノートを全部トリガーする。songNotesは開始時刻順に
    // 並んでいる前提（UstParser::toScheduledNotesおよび
    // VoseAudioProcessor::setSongNotesFromEditorが単調増加で構築するため保証される）。
    if (songPlaying)
    {
        const double blockDurationSec = (double) buffer.getNumSamples() / currentSampleRate;
        const double windowEnd = songPositionSec + blockDurationSec;

        while (songNoteCursor < songNotes.size() && songNotes[songNoteCursor].startTimeSec < windowEnd)
        {
            const auto& sn = songNotes[songNoteCursor];
            if (! sn.lyric.trim().equalsIgnoreCase ("R")) // 休符は発音しない
            {
                pushSongNote (sn);
                anyNoteHeld = true;
            }
            ++songNoteCursor;
        }

        songPositionSec = windowEnd;
        if (songNoteCursor >= songNotes.size())
            songPlaying = false; // 曲の最後まで再生したら自動停止
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
