#pragma once

#include <string>

namespace fl::core {

struct BuildInfo {
    std::string name;
    std::string version;
};

BuildInfo current_build();

}  // namespace fl::core
