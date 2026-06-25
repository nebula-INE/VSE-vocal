// vose_streaming.h
// ============================================================
// VOSE Streaming Synthesis API
//
// 設計思想:
//   UTAUは「全ノートを確定 → まとめてレンダリング → 再生」という
//   バッチモデルを採用している。これは編集中の即時プレビューや
//   ライブパフォーマンスに対応できない根本的な制約である。
//
//   このモジュールは「ノートが入力された瞬間に合成を開始し、
//   再生カーソルより N ms 先行して PCM を供給し続ける」
//   ストリーミングモデルを実現する。
//
//   主要コンポーネント:
//     1. StreamingSynthesizer  … ノートキューと合成スレッドを管理
//     2. RingBuffer<T>         … lock-free で生産者/消費者を分離
//     3. NoteQueue             … mutex付きの安全なノート追加・更新
//
//   スレッド構成:
//     [呼び出し側スレッド]
//       streaming_render_push_note()  … ノートを随時追加
//       streaming_render_pull()       … 合成済みPCMを取り出す
//     [合成スレッド (内部)]
//       先行バッファ残量を監視し、不足したら次ノートを合成して
//       RingBuffer に書き込む
// ============================================================

#pragma once
#include <cstdint>

#ifdef _WIN32
  #define DLLEXPORT __declspec(dllexport)
#else
  #define DLLEXPORT __attribute__((visibility("default")))
#endif

// ストリーミングセッションの不透明ハンドル
typedef void* VoseStreamHandle;

// ストリーミング設定
struct VoseStreamConfig {
    int    sample_rate;          // 出力サンプルレート (通常 44100)
    int    buffer_ms;            // 先行バッファ量 [ms] (推奨: 200〜500)
    int    mode_flag;            // 0=Free(16bit), 1=Pro(32bit)
    float  initial_tempo_bpm;   // 初期テンポ（後から変更可）

    // コールバック: 合成スレッドが PCM チャンクを生成するたびに呼ばれる
    // samples     : float[] のPCMデータ (モノラル, [-1.0, 1.0])
    // sample_count: サンプル数
    // position_ms : このチャンクのストリーム先頭からのタイムスタンプ [ms]
    // user_data   : 下記 callback_user_data がそのまま渡される
    void (*on_chunk_ready)(const float* samples, int sample_count,
                           double position_ms, void* user_data);
    void* callback_user_data;
};

// ノートイベント（ストリーミング用。execute_render の NoteEvent と互換）
struct VoseStreamNote {
    const char*   wav_path;       // 音源キー (oto.ini alias)
    int           pitch_length;   // ピッチフレーム数
    const double* pitch_curve;    // ピッチカーブ [pitch_length]
    const double* gender_curve;   // ジェンダーカーブ [pitch_length]  (null = 0.5)
    const double* tension_curve;  // テンションカーブ [pitch_length]  (null = 0.5)
    const double* breath_curve;   // ブレスカーブ [pitch_length]      (null = 0.5)
    int64_t       note_id;        // 呼び出し側が管理するID（更新/削除に使用）
};

// ============================================================
// C API
// ============================================================
extern "C" {

// セッション作成
// config の on_chunk_ready が null の場合は pull モード (streaming_render_pull を使う)
DLLEXPORT VoseStreamHandle streaming_render_create(const VoseStreamConfig* config);

// ノートをキューに追加
// note_id が既存のIDと同じ場合は「そのノート以降を差し替え」する
// → カーソル位置より未来のノートをリアルタイム編集できる
DLLEXPORT void streaming_render_push_note(VoseStreamHandle h,
                                          const VoseStreamNote* note);

// 合成済みPCMサンプルを取り出す (pull モード用)
// out_buf     : 呼び出し側が確保した float[] バッファ
// max_samples : out_buf の容量
// 戻り値      : 実際に書き込んだサンプル数 (0 = まだ合成中)
DLLEXPORT int  streaming_render_pull(VoseStreamHandle h,
                                     float* out_buf, int max_samples);

// 現在の先行バッファ残量 [ms] を返す
// この値が buffer_ms を上回るまでは pull で0が返る
DLLEXPORT double streaming_render_buffered_ms(VoseStreamHandle h);

// テンポをリアルタイム変更 (次ノートの合成から反映)
DLLEXPORT void streaming_render_set_tempo(VoseStreamHandle h, float bpm);

// セッション破棄 (合成スレッドを安全に停止)
DLLEXPORT void streaming_render_destroy(VoseStreamHandle h);

} // extern "C"
