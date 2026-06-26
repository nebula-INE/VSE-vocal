cmake_minimum_required(VERSION 3.16)

project(vose_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# ソースファイルの自動収集
file(GLOB VOSE_WORLD_SOURCES CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/src/world/*.cpp"
)

# 共有ライブラリのターゲット定義
add_library(vose_core 
    SHARED
    src/vose_core.cpp
    src/vose_streaming_final.cpp
    ${VOSE_WORLD_SOURCES}
)

# WORLD ライブラリのソースを別ターゲットで管理（最適化フラグを分離するため）
# vose_core.cpp / vose_streaming_final.cpp には IEEE 754 厳密モードを維持し、
# WORLD の DSP コア（cheaptrick, d4c 等）にのみ -ffast-math を適用する。
add_library(vose_world_dsp OBJECT ${VOSE_WORLD_SOURCES})

target_include_directories(vose_world_dsp PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/world"
)
target_compile_definitions(vose_world_dsp PRIVATE WORLD_SRC)

# インクルードパスの設定
target_include_directories(vose_core PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/world"
)

target_compile_definitions(vose_core PRIVATE WORLD_SRC)

# OSおよびコンパイラに特化した最適化フラグ
if(MSVC)
    # Windows (MSVC用): 最大最適化、AVX2命令セットの強制
    target_compile_options(vose_core PRIVATE 
        /O2
        /arch:AVX2
        /Oi
        /Ot
        /fp:fast      # vose_core.cpp 本体にも適用（整合性維持）
    )
    target_compile_options(vose_world_dsp PRIVATE
        /O2
        /arch:AVX2
        /Oi
        /Ot
        /fp:fast
    )
else()
    # GCC / Clang (Linux, macOS用)
    # [FIX-FFAST-MATH] vose_core / streaming 側は IEEE 754 準拠（-O3 のみ）
    # WORLD DSP コアのみ -ffast-math を適用してリスクを最小化
    target_compile_options(vose_core PRIVATE
        -O3
        -fomit-frame-pointer
    )
    target_compile_options(vose_world_dsp PRIVATE
        -O3
        -ffast-math       # WORLDの浮動小数点演算に有効（IEEE 754厳密性を緩和）
        -fomit-frame-pointer
    )

    # CPUアーキテクチャに応じたSIMD拡張命令の自動判定
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64" OR CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64")
        target_compile_options(vose_core PRIVATE -mavx2 -mfma)
        target_compile_options(vose_world_dsp PRIVATE -mavx2 -mfma)
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "arm64" OR CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64")
        # [FIX-APPLE-NATIVE] -mcpu=apple-native は開発機専用フラグ。
        # CI（GitHub Actions の macos-latest）では x86_64 や arm64 generic ランナーが
        # 混在するため、apple-native を指定するとビルドが失敗する場合がある。
        # APPLE かつ apple-native が使えるかどうかを実行時チェックして安全にフォールバック。
        if(APPLE)
            include(CheckCXXCompilerFlag)
            check_cxx_compiler_flag("-mcpu=apple-m1" COMPILER_SUPPORTS_APPLE_M1)
            if(COMPILER_SUPPORTS_APPLE_M1)
                # apple-native は Clang 14 以降でサポート。まず試みる。
                check_cxx_compiler_flag("-mcpu=apple-native" COMPILER_SUPPORTS_APPLE_NATIVE)
                if(COMPILER_SUPPORTS_APPLE_NATIVE)
                    target_compile_options(vose_core PRIVATE -mcpu=apple-native)
                    target_compile_options(vose_world_dsp PRIVATE -mcpu=apple-native)
                else()
                    # フォールバック: 汎用 ARM64 最適化
                    target_compile_options(vose_core PRIVATE -mcpu=apple-m1)
                    target_compile_options(vose_world_dsp PRIVATE -mcpu=apple-m1)
                endif()
            endif()
        else()
            # 非 Apple の aarch64（Linux ARM サーバー等）
            target_compile_options(vose_core PRIVATE -march=native)
            target_compile_options(vose_world_dsp PRIVATE -march=native)
        endif()
    endif()
endif()

# WORLD DSP オブジェクトを vose_core にリンク
target_sources(vose_core PRIVATE $<TARGET_OBJECTS:vose_world_dsp>)

# 出力ディレクトリの設定
set_target_properties(vose_core PROPERTIES
    OUTPUT_NAME "vose_core"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
)

foreach(config DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(vose_core PROPERTIES
        ARCHIVE_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin"
        LIBRARY_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin"
        RUNTIME_OUTPUT_DIRECTORY_${config} "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    )
endforeach()
