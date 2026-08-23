#include "fl_core/build_info.hpp"

namespace fl::core {

BuildInfo current_build() {
    return BuildInfo{
        .name = "fl_super_system_cpp",
        .version = "2.0.0",
    };
}

}  // namespace fl::core
