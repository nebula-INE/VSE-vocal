// PluginProcessor.h
// フェーズ1 PoC v2: MIDIノートオンで StreamingVoice::pushNote、
// processBlock で StreamingVoice::pull を直接呼ぶ「本物のリアルタイム再生」版。
// (旧v1のオフラインバウンス方式は RenderEngine.h に残置、書き出し機能用に転用予定)

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "VoseBridge.h"
#include "StreamingVoice.h"
#include "OtoDatabase.h"
#include "VowelClassifier.h"
#include "UstParser.h"
#include "UstFlags.h"
#include "PitchCurveBuilder.h"
#include <deque>

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

    // 優先3: 内蔵歌詞キューUI（生MIDIキーボード演奏用のフォールバック経路）。
    // スペース区切りの文字列を受け取り、ノートオンのたびに1語ずつ
    // ローテーションで消費する（テスト時にループし続けられるように）。
    // UIスレッド(message thread)から呼ばれるので、オーディオスレッドとの
    // 共有には短時間のSpinLockを使う（頻度が低いので実用上問題ない）。
    void setLyricSequence (const juce::String& spaceSeparatedText);
    juce::String getLyricSequenceText() const;

    // --- UST曲再生（優先1/3のライブMIDIとは独立した経路） ---
    // ロード成功時 true。songNotes を構築するだけで自動再生はしない。
    bool loadUstFile (const juce::File& ustFile);
    void startSongPlayback();  // 曲頭から再生開始
    void stopSongPlayback();
    bool isSongPlaying() const { return songPlaying; }
    int  getLoadedSongNoteCount() const { return (int) songNotes.size(); }

    juce::AudioProcessorValueTreeState apvts;

private:
    juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    // ノートオンに応じて次の歌詞を1つ確定し、streaming APIへノートを積む。
    void pushNote (int midiNoteNumber);

    // USTスケジューラから呼ばれる版。歌詞はキュー消費ではなくUST側の指定を使う。
    void pushSongNote (const ScheduledSongNote& note);

    // pushNote/pushSongNote の共通部分（VCV解決 + streaming_render_push_note呼び出し）。
    // pitchCurveHz / genderCurve / tensionCurve / breathCurve は呼び出し側で
    // 組み立て済みのカーブをそのまま使う。
    // (MIDI経由は常にAPVTSのグローバル値、UST経由はFlags上書きがあればそちら優先)
    // portamentoOffsetsCents はネイティブAPI経由で渡す別カーブ（省略可、その場合0セント）。
    void resolveAndPushNote (const std::vector<double>& pitchCurveHz, const juce::String& lyric,
                              const std::vector<double>& genderCurve,
                              const std::vector<double>& tensionCurve,
                              const std::vector<double>& breathCurve,
                              const std::vector<double>& portamentoOffsetsCents = {});

    // 優先順位: 1) MIDI Lyric/Textメタイベント由来のキュー（同一ブロック内で
    // オーディオスレッドのみが読み書きするのでロック不要）
    // 2) 内蔵歌詞キューUI（ロック付き、ローテーション）
    juce::String consumeNextLyric();

    VoseCoreLibrary  coreLib;
    StreamingVoice   voice;
    OtoDatabase      otoDb;
    VowelClassifier  vowelClassifier;

    // 優先1: MIDI Lyric/Textメタイベント由来。processBlock内でのみ触るため
    // 単一スレッド前提でロック不要（オーディオスレッド専有）。
    std::deque<juce::String> midiLyricQueue;

    // 優先3: 内蔵歌詞キューUI。message threadから書き込まれるためロックが要る。
    juce::SpinLock    lyricLock;
    juce::StringArray lyricSequence { "a" };
    int               lyricSequenceIndex = 0;

    juce::String prevLyric; // VcvResolver.resolve() の prev_lyric と同じ役割

    // --- UST曲再生用スケジューラ状態（オーディオスレッドがprocessBlock内で
    // サンプル数から自前で経過時間を積算する。ホストのトランスポートには
    // 同期しないシンプルな内部クロック方式。TODO: AudioPlayHead同期） ---
    std::vector<ScheduledSongNote> songNotes;
    size_t songNoteCursor = 0;
    bool   songPlaying = false;
    double songPositionSec = 0.0;
    double currentSampleRate = 44100.0;

    // pull() が要求サンプル数より少なく返した場合に備えたスクラッチバッファ
    juce::AudioBuffer<float> pullScratch;

    int64_t nextNoteId = 1;
    bool anyNoteHeld = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoseAudioProcessor)
};
