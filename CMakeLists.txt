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

# インクルードパスの設定
target_include_directories(vose_core PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/include/world"
)

target_compile_definitions(vose_core PRIVATE WORLD_SRC)

# 🚀 【新規追加】OSおよびコンパイラに特化した超高速化フラグの注入
if(MSVC)
    # Windows (MSVC用): 最大最適化、AVX2命令セットの強制、インライン展開の最大化
    target_compile_options(vose_core PRIVATE 
        /O2           # 最大最適化 (実行速度優先)
        /arch:AVX2    # AVX2 命令セットの有効化 (ベクトル化)
        /Oi           # 組み込み関数の生成
        /Ot           # コードスピードを優先
        /fp:fast      # 浮動小数点演算の高速化
    )
else()
    # GCC / Clang (Linux, macOS用): 最大最適化と高速数学演算の有効化
    target_compile_options(vose_core PRIVATE 
        -O3 
        -ffast-math   # IEEE 754厳密性を犠牲に計算を圧倒的に高速化 (WORLDに極めて有効)
        -fomit-frame-pointer
    )
    
    # CPUアーキテクチャに応じたSIMD拡張命令の自動判定
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64" OR CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64")
        # Intel / AMD の 64bit環境用
        target_compile_options(vose_core PRIVATE -mavx2 -mfma)
    elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "arm64" OR CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64")
        # Apple Silicon (M1/M2/M3/M4/M5) 用のネイティブCPU最適化
        if(APPLE)
            target_compile_options(vose_core PRIVATE -mcpu=apple-native)
        endif()
    endif()
endif()

# 出力ディレクトリの設定 (binへの集約)
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
