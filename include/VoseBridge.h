// VoseBridge.h
//
// vose_core.dll/.so/.dylib への橋渡し。
// 構造体レイアウトは include/vose_core.h（本物）と1バイトも違わないように
// 手で複製している。ヘッダ側が更新されたら必ずここも追従すること。
// 特に NoteEvent / VoseFrame は vose_core.h 側で
// #pragma pack(push, 8) ... #pragma pack(pop) に囲まれているため、
// ここでも同じ pack(8) 指定を忘れないこと（忘れるとフィールドオフセットが
// ズレて即クラッシュする）。

#pragma once

#include <cstdint>
#include <juce_core/juce_core.h>

extern "C" {

// vose_core.h より: pack指定なし（デフォルトアライメント）
struct OtoEntry {
    const char* filename;
    double      cutoff;
    char        alias[64];
    char        wav_path[512];
    double      offset;
    double      consonant;
    double      blank;
    double      preutterance;
    double      overlap;
};

#pragma pack(push, 8)

struct VoseFrame {
    double time;
    char   phoneme[8];
    double weight;
};

struct NoteEvent {
    const char* wav_path;
    double*     pitch_curve;
    int         pitch_length;

    double*     gender_curve;
    double*     tension_curve;
    double*     breath_curve;

    double*     vibrato_depth_curve;
    double*     vibrato_rate_curve;
    int         vibrato_curve_length;

    // vose_core.h の末尾に追加されたポルタメント用フィールド。
    // フェーズ1では portamento_offsets=nullptr, portamento_length=0 で無効化。
    double*     portamento_offsets;
    int         portamento_length;
};

#pragma pack(pop)

} // extern "C"

// ============================================================
// 関数ポインタ型（vose_core.h の extern "C" ブロックと1:1対応）
// ============================================================
using Fn_load_embedded_resource = void  (*)(const char*, const int16_t*, int);
using Fn_execute_render         = void  (*)(NoteEvent*, int, const char*, int);
using Fn_set_vocal_timeline     = void  (*)(const VoseFrame*, int);
using Fn_get_engine_version     = float (*)();
using Fn_clear_engine_cache     = void  (*)();

// vose_core.cpp 実装側にのみ存在する補助関数（ヘッダには型宣言がないため、
// 存在しない環境でも落ちないよう getFunction が nullptr を返す前提で扱う）
using Fn_init_official_engine   = void (*)();
using Fn_set_oto_data           = void (*)(const OtoEntry*, int);
using Fn_set_bigvgan_model      = void (*)(const char*);

// ============================================================
// VoseCoreLibrary
//
// vose_core 共有ライブラリの動的ロードとシンボル解決を1箇所に集約する。
// Python版 (_load_core_library) の探索ロジックと同じ考え方で、
// OSごとの拡張子違いを吸収する。
// ============================================================
class VoseCoreLibrary
{
public:
    bool load (const juce::File& pluginBinaryDir)
    {
       #if JUCE_WINDOWS
        const char* libName = "vose_core.dll";
       #elif JUCE_MAC
        const char* libName = "libvose_core.dylib";
       #else
        const char* libName = "libvose_core.so";
       #endif

        // 探索候補: プラグイン本体と同じフォルダ、bin/ サブフォルダ
        // (CMakeLists.txt の LIBRARY_OUTPUT_DIRECTORY が bin/ なので、
        //  開発中はここに直接置かれることが多い)
        juce::Array<juce::File> candidates {
            pluginBinaryDir.getChildFile (libName),
            pluginBinaryDir.getChildFile ("bin").getChildFile (libName)
        };

        for (auto& f : candidates)
        {
            if (f.existsAsFile() && dll.open (f.getFullPathName()))
            {
                load_embedded_resource = (Fn_load_embedded_resource) dll.getFunction ("load_embedded_resource");
                execute_render         = (Fn_execute_render)         dll.getFunction ("execute_render");
                set_vocal_timeline     = (Fn_set_vocal_timeline)     dll.getFunction ("set_vocal_timeline");
                get_engine_version     = (Fn_get_engine_version)     dll.getFunction ("get_engine_version");
                clear_engine_cache     = (Fn_clear_engine_cache)     dll.getFunction ("clear_engine_cache");

                // vose_core.h には宣言のない補助シンボル。存在すれば使う。
                init_official_engine   = (Fn_init_official_engine)   dll.getFunction ("init_official_engine");
                set_oto_data           = (Fn_set_oto_data)           dll.getFunction ("set_oto_data");
                set_bigvgan_model      = (Fn_set_bigvgan_model)      dll.getFunction ("set_bigvgan_model");

                loaded = (execute_render != nullptr);
                if (loaded && init_official_engine != nullptr)
                    init_official_engine();

                if (loaded && get_engine_version != nullptr)
                    lastKnownVersion = get_engine_version();

                return loaded;
            }
        }
        return false;
    }

    bool isLoaded() const { return loaded; }
    float getLastKnownVersion() const { return lastKnownVersion; }

    Fn_load_embedded_resource load_embedded_resource = nullptr;
    Fn_execute_render         execute_render         = nullptr;
    Fn_set_vocal_timeline     set_vocal_timeline     = nullptr;
    Fn_get_engine_version     get_engine_version     = nullptr;
    Fn_clear_engine_cache     clear_engine_cache     = nullptr;

    Fn_init_official_engine   init_official_engine   = nullptr;
    Fn_set_oto_data           set_oto_data           = nullptr;
    Fn_set_bigvgan_model      set_bigvgan_model      = nullptr;

private:
    juce::DynamicLibrary dll;
    bool  loaded = false;
    float lastKnownVersion = 0.0f;
};
