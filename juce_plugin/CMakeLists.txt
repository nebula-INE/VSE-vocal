# ==============================================================================
# juce_plugin/CMakeLists.txt
#
# VO-SE vocal JUCE Plugin ビルド設定（完全自動・最適化版）
# ==============================================================================

cmake_minimum_required(VERSION 3.22)

project(VoseVocalPlugin VERSION 0.1.0 LANGUAGES CXX)

# ========================
# C++ 標準設定
# ========================
# vose_core のC++20対応と同期
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ========================
# JUCE フレームワークの取得
# ========================
include(FetchContent)
FetchContent_Declare(
    JUCE
    GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
    GIT_TAG 8.0.5  # <- 7.0.12 から 8.0.5 に変更
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(JUCE)

# ========================
# 外部ライブラリ検出 (macOS 用 iconv)
# ========================
if(APPLE)
    find_package(Iconv REQUIRED)
endif()

# ========================
# プラグインフォーマット定義
# ========================
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


# iconv ライブラリを検索する
find_library(ICONV_LIBRARY NAMES iconv)

# ターゲット（VoseVocalの共有コードターゲット）にリンクさせる
if(ICONV_LIBRARY)
    target_link_libraries(VoseVocal PRIVATE ${ICONV_LIBRARY})
else()
    # 念のため、見つからなかった場合は直にフラグとして渡すフォールバック
    target_link_libraries(VoseVocal PRIVATE "-liconv")
endif()
# ========================
# ソースファイル定義
# ========================
# 1. 必須の基本フレームワーク/UIソース
target_sources(VoseVocal
    PRIVATE
        src/PluginProcessor.cpp
        src/PluginEditor.cpp
        src/PianoRollComponent.cpp
        src/GraphEditorComponent.cpp
)

# 2. オプションソースの自動検出ロジック
# ファイルがプロジェクト内に存在する場合のみ、自動的にビルド対象に追加します。
set(VOSE_OPTIONAL_SOURCES
    src/OtoDatabase.cpp
    src/UstParser.cpp
    src/VowelClassifier.cpp
    src/StreamingVoice.cpp
    src/PitchCurveBuilder.cpp
    src/VoseBridge.cpp
)

foreach(SOURCE_FILE ${VOSE_OPTIONAL_SOURCES})
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${SOURCE_FILE}")
        target_sources(VoseVocal PRIVATE ${SOURCE_FILE})
        message(STATUS "[VO-SE] Optional source detected and added: ${SOURCE_FILE}")
    endif()
endforeach()

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
        # 不要なモジュールを無効化してバイナリサイズを極限まで削減
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_VST3_CAN_REPLACE_VST2=0
)

# ========================
# Linux 固有設定
# ========================
if(UNIX AND NOT APPLE)
    target_compile_definitions(VoseVocal PUBLIC JUCE_ALSA=1)
endif()

# ========================
# macOS 固有設定
# ========================
if(APPLE)
    # 互換性を担保するため、最小動作OSバージョンを macOS 11.0 (Big Sur) に設定
    if(TARGET VoseVocal_SharedCode)
        set_target_properties(VoseVocal_SharedCode PROPERTIES
            MACOSX_DEPLOYMENT_TARGET "11.0"
        )
    endif()
endif()

# ========================
# Windows 固有設定
# ========================
if(WIN32)
    # MSVC特有の非推奨関数警告（POSIX互換関数など）を抑制し、ビルドログをクリーンに保つ
    target_compile_options(VoseVocal PRIVATE /wd4996)
endif()

# ========================
# JUCE ライブラリのリンク
# ========================
target_link_libraries(VoseVocal
    PRIVATE
        juce::juce_audio_utils
        juce::juce_audio_processors
        juce::juce_gui_basics
        juce::juce_dsp
    PUBLIC
        iconv                                 # <- PRIVATE から PUBLIC の先頭に移動
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags
)

# ========================
# macOS 用追加リンク（LTO対策を含む iconv の完全結合）
# ========================
if(APPLE)
    # 共有コードのスタティックライブラリとメインメタターゲットにリンク
    if(TARGET VoseVocal_SharedCode)
        target_link_libraries(VoseVocal_SharedCode PRIVATE Iconv::Iconv)
    endif()
    target_link_libraries(VoseVocal PRIVATE Iconv::Iconv)
    
    # 各出力バイナリへのリンク波及を保証（LTOによるデッドコードストリップ対策）
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
# ビルド構成のデバッグ出力
# ========================
message(STATUS "=======================================================")
message(STATUS "[VO-SE] Plugin Build Configuration:")
message(STATUS "  - CMAKE_BUILD_TYPE:    ${CMAKE_BUILD_TYPE}")
message(STATUS "  - PLUGIN_FORMATS:      ${VOSE_PLUGIN_FORMATS}")
message(STATUS "  - SOURCE_DIR:          ${CMAKE_CURRENT_SOURCE_DIR}")
message(STATUS "=======================================================")
