cmake_minimum_required(VERSION 3.16)
project(d2r_casc_tools LANGUAGES CXX)

set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_subdirectory(CascLib)

add_executable(list_casc src/list_casc.cpp)
target_link_libraries(list_casc PRIVATE casc)

add_executable(extract_d2r src/extract_d2r.cpp)
target_link_libraries(extract_d2r PRIVATE casc)
