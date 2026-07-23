#include "fl_core/build_info.hpp"

namespace fl::core {

BuildInfo current_build() {
    return BuildInfo{
        .name = "fl_super_system_cpp",
        .version = "0.2.0-milestone2",
    };
}

}  // namespace fl::core
