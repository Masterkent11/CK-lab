cmake_minimum_required(VERSION 3.24)
project(EmbroideryAutomationFixture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(EMBROIDERY_SANITIZERS "Enable address and undefined behavior sanitizers" OFF)
include(CTest)

if(BUILD_TESTING)
  include(FetchContent)
  FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG 52eb8108c5bdec04579160ae17225d66034bd723
  )
  set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)

  add_executable(toolchain_test tests/fixture/toolchain_test.cpp)
  target_link_libraries(toolchain_test PRIVATE GTest::gtest_main)
  if(EMBROIDERY_SANITIZERS)
    if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU" OR WIN32)
      message(FATAL_ERROR "This sanitizer build requires Clang or GCC on a supported Unix runner")
    endif()
    target_compile_options(toolchain_test PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(toolchain_test PRIVATE -fsanitize=address,undefined)
  endif()

  include(GoogleTest)
  gtest_discover_tests(toolchain_test DISCOVERY_MODE PRE_TEST)
endif()
