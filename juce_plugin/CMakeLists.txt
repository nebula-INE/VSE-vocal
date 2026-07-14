# juce_plugin/CMakeLists.txt
#
# VO-SE vocal JUCE Plugin ビルド設定（完全修正版）
#
# ビルド方法:
#   cmake -B juce_plugin/build -S juce_plugin -DCMAKE_BUILD_TYPE=Release
#   cmake --build juce_plugin/build --config Release --parallel
#
# トラブルシューティング:
#   1. PluginEditor.h のファイル名確認（小文字e ではなく大文字E）
#   2. include/ の全 .h ファイルが存在することを確認
#   3. src/ の全対応 .cpp ファイルが存在することを確認
#   4. 不要な .cpp はコメントアウトしている箇所で管理

cmake_minimum_required(VERSION 3.22)

project(VoseVocalPlugin VERSION 0.1.0 LANGUAGES CXX)

# ========================
# C++ 標準設定
# ========================
# vose_core のC++20対応と合わせるため、ここでもC++20を指定
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ========================
# JUCE フレームワークの取得
# ========================
# GitHub Actions の release.yml で `juce_plugin/build/_deps` をキャッシュしているため、
# 初回以降はFetchContentが高速化される。
include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 7.0.12
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

# ========================
# macOS 用の外部ライブラリ検出
# ========================
if(APPLE)
    # LTO最適化時にも確実に libiconv.dylib を解決するための標準パッケージ検出
    find_package(Iconv REQUIRED)
endif()

# ========================
# プラグインフォーマット定義
# ========================
# 全プラットフォーム: VST3, Standalone
# macOS のみ追加: AU
set(VOSE_PLUGIN_FORMATS VST3 Standalone)
if(APPLE)
    list(APPEND VOSE_PLUGIN_FORMATS AU)
endif()

# ========================
# JUCE プラグイン定義
# ========================
juce_add_plugin(VoseVocal
    COMPANY_NAME              "VO-SE Project"
    PLUGIN_MANUFACTURER_CODE  Vose
    PLUGIN_CODE               Vse1
    FORMATS                   ${VOSE_PLUGIN_FORMATS}
    PRODUCT_NAME              "VO-SE vocal"
    NEEDS_MIDI_INPUT          TRUE
    IS_SYNTH                  TRUE
    COPY_PLUGIN_AFTER_BUILD   FALSE
)

# ========================
# ソースファイル定義
# ========================
# 必須ファイル: PluginProcessor, PluginEditor, UIコンポーネ項
# オプション: OtoDatabase, UstParser, VowelClassifier, StreamingVoice, 
#             PitchCurveBuilder, VoseBridge の .cpp（存在時のみ有効化）

target_sources(VoseVocal
    PRIVATE
        # === 必須 DSP/UI フレームワーク ===
        src/PluginProcessor.cpp
        src/PluginEditor.cpp
        src/PianoRollComponent.cpp
        src/GraphEditorComponent.cpp
        
        # === オプション: 以下のファイルが存在する場合は有効化してください ===
        # src/OtoDatabase.cpp
        # src/UstParser.cpp
        # src/VowelClassifier.cpp
        # src/StreamingVoice.cpp
        # src/PitchCurveBuilder.cpp
        # src/VoseBridge.cpp
)

# ========================
# インクルードディレクトリ
# ========================
target_include_directories(VoseVocal
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

# ========================
# コンパイル定義（全プラットフォーム共通）
# ========================
target_compile_definitions(VoseVocal
    PUBLIC
        # WebView, Curl, VST2互換性無効化（プラグインサイズ削減）
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

# ========================
# Linux 固有設定
# ========================
if(UNIX AND NOT APPLE)
    # ALSA（Advanced Linux Sound Architecture）を有効化
    target_compile_definitions(VoseVocal PUBLIC JUCE_ALSA=1)
    # JACK サポート有効化（オプション）
    # target_compile_definitions(VoseVocal PUBLIC JUCE_JACK=1)
endif()

# ========================
# macOS 固有設定
# ========================
if(APPLE)
    # 最小動作OSバージョン設定（有効化する場合はコメントアウト `#` を外してください）
    # set_target_properties(VoseVocal_SharedCode PROPERTIES
    #     MACOSX_DEPLOYMENT_TARGET 11.0
    # )
endif()

# ========================
# Windows 固有設定
# ========================
if(WIN32)
    # Windows ビルド時の特定の警告を無視（必要に応じて）
    # target_compile_options(VoseVocal PRIVATE /wd4996)
endif()

# ========================
# JUCE ライブラリのリンク
# ========================
target_link_libraries(VoseVocal
    PRIVATE
        # === コア オーディオ/MIDIプロセッシング ===
        juce::juce_audio_utils
        juce::juce_audio_processors
        
        # === GUI ===
        juce::juce_gui_basics
        
        # === DSP ===
        juce::juce_dsp
    PUBLIC
        # === ビルドフラグ推奨事項 ===
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags      # Link-Time Optimization
        juce::juce_recommended_warning_flags
)

# ========================
# macOS 用追加リンク（システム iconv を確実に結合する）
# ========================
if(APPLE)
    # 共有スタティックライブラリ、およびメインのメタターゲットにリンク
    target_link_libraries(VoseVocal_SharedCode PRIVATE Iconv::Iconv)
    target_link_libraries(VoseVocal PRIVATE Iconv::Iconv)
    
    # LTO最適化によるリンク漏れを防ぐため、各出力ターゲットに個別に直接リンクを波及させる
    if(TARGET VoseVocal_VST3)
        target_link_libraries(VoseVocal_VST3 PRIVATE Iconv::Iconv)
    endif()
    if(TARGET VoseVocal_AU)
        target_link_libraries(VoseVocal_AU PRIVATE Iconv::Iconv)
    endif()
    if(TARGET VoseVocal_Standalone)
        target_link_libraries(VoseVocal_Standalone PRIVATE Iconv::Iconv)
    endif()
endif()

# ========================
# オプション設定（デバッグ出力）
# ========================
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    message(STATUS "VoseVocal Plugin ビルド設定:")
    message(STATUS "  CMAKE_BUILD_TYPE: ${CMAKE_BUILD_TYPE}")
    message(STATUS "  VOSE_PLUGIN_FORMATS: ${VOSE_PLUGIN_FORMATS}")
    message(STATUS "  Source dir: ${CMAKE_CURRENT_SOURCE_DIR}")
endif()
